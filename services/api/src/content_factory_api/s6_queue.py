"""Recoverable two-worker S6 material ingestion queue."""

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
class MaterialImportRecord:
    task_id: str
    fixture_data: bool
    product_id: str
    source_script_id: str | None
    source_name: str
    status: str
    progress: int
    current_step: str
    attempt_count: int
    max_attempts: int
    material_id: str | None
    error: str | None
    created_at: str
    updated_at: str
    source_path: str
    workspace_path: str
    input_json: str

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "1.0.0", "fixture_data": self.fixture_data, "task_id": self.task_id,
            "product_id": self.product_id, "source_script_id": self.source_script_id,
            "source_name": self.source_name, "status": self.status, "progress": self.progress,
            "current_step": self.current_step, "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts, "material_id": self.material_id, "error": self.error,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }
        validate_or_raise("material_import_task", payload)
        return payload

    def input(self) -> dict[str, Any]:
        payload = json.loads(self.input_json)
        if not isinstance(payload, dict):
            raise ValueError("素材任务输入格式错误")
        return payload


Processor = Callable[[MaterialImportRecord, Callable[[str, int], None]], str]
Reconciler = Callable[[MaterialImportRecord], str | None]


class MaterialImportQueue:
    def __init__(self, database_path: str | Path, *, retry_base_seconds: float = 1.0) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.retry_base_seconds = retry_base_seconds
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
                CREATE TABLE IF NOT EXISTS material_import_tasks (
                    task_id TEXT PRIMARY KEY, fixture_data INTEGER NOT NULL, product_id TEXT NOT NULL,
                    source_script_id TEXT, source_name TEXT NOT NULL, status TEXT NOT NULL,
                    progress INTEGER NOT NULL, current_step TEXT NOT NULL, attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL, material_id TEXT, error TEXT, source_path TEXT NOT NULL,
                    workspace_path TEXT NOT NULL, input_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, available_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> MaterialImportRecord:
        return MaterialImportRecord(
            task_id=row["task_id"], fixture_data=bool(row["fixture_data"]), product_id=row["product_id"],
            source_script_id=row["source_script_id"], source_name=row["source_name"], status=row["status"],
            progress=row["progress"], current_step=row["current_step"], attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"], material_id=row["material_id"], error=row["error"],
            created_at=row["created_at"], updated_at=row["updated_at"], source_path=row["source_path"],
            workspace_path=row["workspace_path"], input_json=row["input_json"],
        )

    def recover_interrupted(self, reconcile: Reconciler | None = None) -> int:
        """Recover running jobs, reconciling committed material side effects first.

        A worker can stop after the material transaction commits but before the
        queue transaction records completion.  ``reconcile`` returns that
        committed material id when it can be safely reused.  A reconciliation
        exception means a persisted side effect exists but is not safe to
        reuse, so the task is failed instead of running FFmpeg over it again.
        """

        now = _now_iso()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM material_import_tasks WHERE status='running' ORDER BY created_at",
            ).fetchall()
        for row in rows:
            task = self._record(row)
            material_id: str | None = None
            reconciliation_failed = False
            if reconcile is not None:
                try:
                    material_id = reconcile(task)
                    if material_id is not None and not material_id.strip():
                        raise ValueError("恢复器返回了空素材编号")
                except Exception:
                    reconciliation_failed = True
            if material_id is not None:
                self._complete(task.task_id, material_id)
                continue
            if reconciliation_failed:
                with self._connect() as connection:
                    connection.execute(
                        """UPDATE material_import_tasks SET status='failed', current_step='failed',
                           error='恢复时发现已落库素材不完整，请重新导入', updated_at=?
                           WHERE task_id=? AND status='running'""",
                        (now, task.task_id),
                    )
                continue
            with self._connect() as connection:
                connection.execute(
                    """UPDATE material_import_tasks SET
                       status=CASE WHEN attempt_count < max_attempts THEN 'pending' ELSE 'failed' END,
                       current_step=CASE WHEN attempt_count < max_attempts THEN 'queued' ELSE 'failed' END,
                       progress=CASE WHEN attempt_count < max_attempts THEN 0 ELSE progress END,
                       error=CASE WHEN attempt_count < max_attempts THEN NULL ELSE '任务在最后一次处理时被中断，请手动重试' END,
                       available_at=?, updated_at=? WHERE task_id=? AND status='running'""",
                    (now, now, task.task_id),
                )
        return len(rows)

    def enqueue(
        self, source_path: str | Path, workspace_path: str | Path, *, fixture_data: bool,
        source_name: str, product_id: str, source_script_id: str | None, input_payload: Mapping[str, Any],
        max_attempts: int = 2,
    ) -> MaterialImportRecord:
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts 必须在 1—5 之间")
        source = Path(source_path).resolve()
        workspace = Path(workspace_path).resolve()
        if not source.is_file():
            raise ValueError("待导入视频不存在")
        workspace.mkdir(parents=True, exist_ok=True)
        task_id = f"material_task_{uuid4().hex}"
        created = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO material_import_tasks VALUES (?, ?, ?, ?, ?, 'pending', 0, 'queued', 0, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)",
                (task_id, int(fixture_data), product_id, source_script_id, source_name, max_attempts, str(source),
                 str(workspace), json.dumps(dict(input_payload), ensure_ascii=False, separators=(",", ":")),
                 created, created, created),
            )
        record = self.get(task_id)
        assert record is not None
        return record

    def get(self, task_id: str) -> MaterialImportRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM material_import_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._record(row) if row is not None else None

    def list(self, *, limit: int = 500) -> list[MaterialImportRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM material_import_tasks ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def retry(self, task_id: str) -> MaterialImportRecord:
        task = self.get(task_id)
        if task is None:
            raise LookupError("找不到这个素材任务")
        if task.status != "failed":
            raise ValueError("只有失败的素材任务可以重试")
        if not Path(task.source_path).is_file():
            raise ValueError("原上传文件已不存在，请重新上传")
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE material_import_tasks SET status='pending', progress=0, current_step='queued', error=NULL, attempt_count=0, available_at=?, updated_at=? WHERE task_id=?",
                (now, now, task_id),
            )
        retried = self.get(task_id)
        assert retried is not None
        return retried

    def _claim(self) -> MaterialImportRecord | None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT task_id FROM material_import_tasks WHERE status IN ('pending','retry_wait') AND available_at<=? ORDER BY created_at LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE material_import_tasks SET status='running', current_step='probe', progress=1, attempt_count=attempt_count+1, updated_at=? WHERE task_id=?",
                (now, row["task_id"]),
            )
        return self.get(row["task_id"])

    def _progress(self, task_id: str, step: str, progress: int) -> None:
        safe_step = "archive" if step == "finalize" else step
        with self._connect() as connection:
            connection.execute(
                "UPDATE material_import_tasks SET current_step=?, progress=?, updated_at=? WHERE task_id=? AND status='running'",
                (safe_step, min(progress, 99), _now_iso(), task_id),
            )

    def _complete(self, task_id: str, material_id: str) -> None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE material_import_tasks SET status='completed', current_step='completed', progress=100, material_id=?, error=NULL, updated_at=? WHERE task_id=?",
                (material_id, now, task_id),
            )

    def _fail(self, task_id: str, error: Exception) -> None:
        task = self.get(task_id)
        if task is None:
            return
        retry = bool(getattr(error, "retryable", True)) and task.attempt_count < task.max_attempts
        now_dt = datetime.now(UTC)
        available = now_dt + timedelta(seconds=self.retry_base_seconds * (2 ** max(0, task.attempt_count - 1)))
        message = str(error).strip()
        for private_path in (task.source_path, task.workspace_path):
            for path_form in {private_path, private_path.replace("\\", "/")}:
                message = message.replace(path_form, "本机受控文件")
        message = message[:500] or "素材处理失败"
        with self._connect() as connection:
            connection.execute(
                "UPDATE material_import_tasks SET status=?, current_step=?, error=?, available_at=?, updated_at=? WHERE task_id=?",
                ("retry_wait" if retry else "failed", "queued" if retry else "failed", message,
                 available.isoformat().replace("+00:00", "Z"), _now_iso(), task_id),
            )

    def _next_delay(self) -> float | None:
        with self._connect() as connection:
            row = connection.execute("SELECT available_at FROM material_import_tasks WHERE status='retry_wait' ORDER BY available_at LIMIT 1").fetchone()
        if row is None:
            return None
        value = datetime.fromisoformat(row["available_at"].replace("Z", "+00:00"))
        return max(0.0, (value - datetime.now(UTC)).total_seconds())

    def run_pending(self, processor: Processor, *, max_workers: int = 2) -> list[MaterialImportRecord]:
        workers = max(1, min(max_workers, 2))
        processed: set[str] = set()
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="s6-material") as pool:
            active: dict[Future[str], MaterialImportRecord] = {}
            while True:
                while len(active) < workers:
                    task = self._claim()
                    if task is None:
                        break
                    processed.add(task.task_id)
                    active[pool.submit(processor, task, lambda step, value, task_id=task.task_id: self._progress(task_id, step, value))] = task
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
            rows = connection.execute("SELECT status, COUNT(*) AS value FROM material_import_tasks GROUP BY status").fetchall()
        for row in rows:
            counts[row["status"]] = row["value"]
        return counts
