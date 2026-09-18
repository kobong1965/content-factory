"""SQLite-backed, recoverable S7 rendering queue (maximum two workers)."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from content_factory_contracts import validate_or_raise


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RenderTaskRecord:
    schema_version: str
    fixture_data: bool
    task_id: str
    project_id: str
    project_revision: int
    variant_id: str
    status: str
    progress: int
    current_step: str
    attempt_count: int
    max_attempts: int
    output_id: str | None
    error: str | None
    created_at: str
    updated_at: str
    snapshot_path: str
    workspace_path: str

    def to_dict(self) -> dict[str, Any]:
        payload = {
            key: getattr(self, key)
            for key in (
                "schema_version", "fixture_data", "task_id", "project_id", "project_revision", "variant_id",
                "status", "progress", "current_step", "attempt_count", "max_attempts", "output_id", "error",
                "created_at", "updated_at",
            )
        }
        validate_or_raise("render_task", payload)
        return payload

    def snapshot(self) -> dict[str, Any]:
        try:
            payload = json.loads(Path(self.snapshot_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("渲染快照无法读取") from exc
        if not isinstance(payload, dict):
            raise ValueError("渲染快照格式错误")
        return payload


Processor = Callable[[RenderTaskRecord, Callable[[str, int], None]], str]
Reconciler = Callable[[RenderTaskRecord], str | None]


class RenderQueue:
    def __init__(self, database_path: str | Path, *, retry_base_seconds: float = 1.0) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        if not 0 < retry_base_seconds <= 30:
            raise ValueError("retry_base_seconds 必须大于 0 且不超过 30 秒")
        self.retry_base_seconds = retry_base_seconds
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS render_tasks (
                    task_id TEXT PRIMARY KEY, fixture_data INTEGER NOT NULL, project_id TEXT NOT NULL,
                    project_revision INTEGER NOT NULL, variant_id TEXT NOT NULL, status TEXT NOT NULL,
                    progress INTEGER NOT NULL, current_step TEXT NOT NULL, attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL, output_id TEXT, error TEXT, snapshot_path TEXT NOT NULL,
                    workspace_path TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    available_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_render_active_variant
                   ON render_tasks(project_id, project_revision, variant_id)
                   WHERE status IN ('pending','running','retry_wait')"""
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> RenderTaskRecord:
        return RenderTaskRecord(
            schema_version="1.0.0", fixture_data=bool(row["fixture_data"]), task_id=row["task_id"],
            project_id=row["project_id"], project_revision=row["project_revision"], variant_id=row["variant_id"],
            status=row["status"], progress=row["progress"], current_step=row["current_step"],
            attempt_count=row["attempt_count"], max_attempts=row["max_attempts"], output_id=row["output_id"],
            error=row["error"], snapshot_path=row["snapshot_path"], workspace_path=row["workspace_path"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def enqueue(
        self, project: Mapping[str, Any], variant_id: str, *, snapshot_path: str | Path,
        workspace_path: str | Path, max_attempts: int = 2,
    ) -> RenderTaskRecord:
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts 必须在 1—5 之间")
        if not any(item.get("id") == variant_id for item in project.get("variants", [])):
            raise ValueError("剪辑版本不存在")
        snapshot = Path(snapshot_path).resolve()
        workspace = Path(workspace_path).resolve()
        if not snapshot.is_file():
            raise ValueError("渲染快照不存在")
        workspace.mkdir(parents=True, exist_ok=True)
        task_id = f"render_task_{uuid4().hex}"
        created = _now_iso()
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO render_tasks VALUES(?,?,?,?,?,'pending',0,'queued',0,?,NULL,NULL,?,?,?,?,?)",
                    (task_id, int(bool(project.get("fixture_data"))), project["project_id"], project["revision"],
                     variant_id, max_attempts, str(snapshot), str(workspace), created, created, created),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("这个工程版本正在渲染，不需要重复排队") from exc
        task = self.get(task_id)
        assert task is not None
        return task

    def get(self, task_id: str) -> RenderTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM render_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._record(row) if row is not None else None

    def list(self, *, limit: int = 500) -> list[RenderTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM render_tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._record(row) for row in rows]

    def recover_interrupted(self, reconcile: Reconciler | None = None) -> int:
        """Recover running renders after checking for a committed output first."""

        now = _now_iso()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM render_tasks WHERE status='running' ORDER BY created_at",
            ).fetchall()
        for row in rows:
            task = self._record(row)
            output_id: str | None = None
            reconciliation_failed = False
            if reconcile is not None:
                try:
                    output_id = reconcile(task)
                    if output_id is not None and not output_id.strip():
                        raise ValueError("恢复器返回了空成片编号")
                except Exception:
                    reconciliation_failed = True
            if output_id is not None:
                self._complete(task.task_id, output_id)
                continue
            if reconciliation_failed:
                with self._connect() as connection:
                    connection.execute(
                        """UPDATE render_tasks SET status='failed', current_step='failed',
                           error='恢复时发现已登记成片不完整，请重新渲染', updated_at=?
                           WHERE task_id=? AND status='running'""",
                        (now, task.task_id),
                    )
                continue
            with self._connect() as connection:
                connection.execute(
                    """UPDATE render_tasks SET
                       status=CASE WHEN attempt_count < max_attempts THEN 'pending' ELSE 'failed' END,
                       current_step=CASE WHEN attempt_count < max_attempts THEN 'queued' ELSE 'failed' END,
                       progress=CASE WHEN attempt_count < max_attempts THEN 0 ELSE progress END,
                       error=CASE WHEN attempt_count < max_attempts THEN NULL ELSE '渲染在最后一次处理时被中断，请手动重试' END,
                       available_at=?, updated_at=? WHERE task_id=? AND status='running'""",
                    (now, now, task.task_id),
                )
        return len(rows)

    def retry(self, task_id: str) -> RenderTaskRecord:
        task = self.get(task_id)
        if task is None:
            raise LookupError("找不到这个渲染任务")
        if task.status != "failed":
            raise ValueError("只有失败的渲染任务可以重试")
        if not Path(task.snapshot_path).is_file():
            raise ValueError("渲染快照已不存在，请重新发起渲染")
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE render_tasks SET status='pending',progress=0,current_step='queued',attempt_count=0,output_id=NULL,error=NULL,available_at=?,updated_at=? WHERE task_id=?",
                (now, now, task_id),
            )
        retried = self.get(task_id)
        assert retried is not None
        return retried

    def _claim(self) -> RenderTaskRecord | None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT task_id FROM render_tasks WHERE status IN ('pending','retry_wait') AND available_at<=? ORDER BY created_at LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE render_tasks SET status='running',current_step='prepare',progress=1,attempt_count=attempt_count+1,updated_at=? WHERE task_id=?",
                (now, row["task_id"]),
            )
        return self.get(row["task_id"])

    def _progress(self, task_id: str, step: str, progress: int) -> None:
        allowed = {"prepare", "render_clips", "concat", "subtitles", "audio", "package"}
        safe_step = step if step in allowed else "prepare"
        with self._connect() as connection:
            connection.execute(
                "UPDATE render_tasks SET current_step=?,progress=?,updated_at=? WHERE task_id=? AND status='running'",
                (safe_step, max(1, min(99, progress)), _now_iso(), task_id),
            )

    def _complete(self, task_id: str, output_id: str) -> None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE render_tasks SET status='completed',current_step='completed',progress=100,output_id=?,error=NULL,updated_at=? WHERE task_id=?",
                (output_id, now, task_id),
            )

    def _fail(self, task_id: str, error: Exception) -> None:
        task = self.get(task_id)
        if task is None:
            return
        retry = task.attempt_count < task.max_attempts
        available = datetime.now(UTC) + timedelta(seconds=self.retry_base_seconds * (2 ** max(0, task.attempt_count - 1)))
        message = str(error).strip()
        for private_path in (task.snapshot_path, task.workspace_path):
            for path_form in {private_path, private_path.replace("\\", "/")}:
                message = message.replace(path_form, "本机受控文件")
        message = message[:500] or "渲染失败"
        with self._connect() as connection:
            connection.execute(
                "UPDATE render_tasks SET status=?,current_step=?,error=?,available_at=?,updated_at=? WHERE task_id=?",
                ("retry_wait" if retry else "failed", "queued" if retry else "failed", message,
                 available.isoformat().replace("+00:00", "Z"), _now_iso(), task_id),
            )

    def _next_delay(self) -> float | None:
        with self._connect() as connection:
            row = connection.execute("SELECT available_at FROM render_tasks WHERE status='retry_wait' ORDER BY available_at LIMIT 1").fetchone()
        if row is None:
            return None
        value = datetime.fromisoformat(row["available_at"].replace("Z", "+00:00"))
        return max(0.0, (value - datetime.now(UTC)).total_seconds())

    def run_pending(self, processor: Processor, *, max_workers: int = 2) -> list[RenderTaskRecord]:
        worker_count = max(1, min(max_workers, 2))
        processed: set[str] = set()
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="render-s7") as pool:
            active: dict[Future[str], RenderTaskRecord] = {}
            while True:
                while len(active) < worker_count:
                    task = self._claim()
                    if task is None:
                        break
                    processed.add(task.task_id)
                    callback = lambda step, value, task_id=task.task_id: self._progress(task_id, step, value)
                    active[pool.submit(processor, task, callback)] = task
                if active:
                    done, _ = wait(active, return_when=FIRST_COMPLETED)
                    for future in done:
                        task = active.pop(future)
                        try:
                            self._complete(task.task_id, future.result())
                        except Exception as exc:
                            self._fail(task.task_id, exc)
                    continue
                delay = self._next_delay()
                if delay is None:
                    break
                time.sleep(max(0.01, min(delay, 30.0)))
        return [task for task_id in processed if (task := self.get(task_id)) is not None]

    def counts(self) -> dict[str, int]:
        counts = {name: 0 for name in ("pending", "running", "retry_wait", "completed", "failed")}
        with self._connect() as connection:
            rows = connection.execute("SELECT status,COUNT(*) AS value FROM render_tasks GROUP BY status").fetchall()
        for row in rows:
            counts[str(row["status"])] = int(row["value"])
        return counts
