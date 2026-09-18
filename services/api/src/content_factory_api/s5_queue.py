"""SQLite-backed recoverable queue for S5 script generation."""

from __future__ import annotations

import json
import os
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

from .s3_settings import GatewayConfig, GatewaySettingsError
from .s5_generation import ScriptArtifacts, process_script_generation


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class ScriptTaskRecord:
    schema_version: str
    fixture_data: bool
    task_id: str
    product_id: str
    product_revision: int
    source_analysis_id: str
    source_analysis_revision: int
    pattern_id: str
    content_goal: str
    target_audience: str
    version_count: int
    status: str
    progress: int
    current_step: str
    attempt_count: int
    max_attempts: int
    result_path: str | None
    script_id: str | None
    error: str | None
    created_at: str
    updated_at: str
    input_path: str
    workspace_path: str

    def to_dict(self) -> dict[str, Any]:
        payload = {
            key: getattr(self, key)
            for key in (
                "schema_version", "fixture_data", "task_id", "product_id", "product_revision",
                "source_analysis_id", "source_analysis_revision", "pattern_id", "content_goal",
                "target_audience", "version_count", "status", "progress", "current_step",
                "attempt_count", "max_attempts", "script_id", "error", "created_at", "updated_at",
            )
        }
        payload["input_ref"] = f"script_input_{self.task_id.removeprefix('script_task_')}"
        payload["result_ref"] = (
            f"script_result_{self.script_id.removeprefix('script_')}" if self.script_id and self.result_path else None
        )
        validate_or_raise("script_task", payload)
        return payload


TaskProcessor = Callable[[ScriptTaskRecord, Callable[[str, int], None]], ScriptArtifacts]
TaskPreflight = Callable[[ScriptTaskRecord, Mapping[str, Any]], None]


class ScriptTaskQueue:
    def __init__(self, database_path: str | Path, *, retry_base_seconds: float = 2.0) -> None:
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
                CREATE TABLE IF NOT EXISTS script_tasks (
                    task_id TEXT PRIMARY KEY,
                    fixture_data INTEGER NOT NULL,
                    product_id TEXT NOT NULL,
                    product_revision INTEGER NOT NULL,
                    source_analysis_id TEXT NOT NULL,
                    source_analysis_revision INTEGER NOT NULL,
                    pattern_id TEXT NOT NULL,
                    content_goal TEXT NOT NULL,
                    target_audience TEXT NOT NULL,
                    version_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    current_step TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    result_path TEXT,
                    script_id TEXT,
                    error TEXT,
                    input_path TEXT NOT NULL,
                    workspace_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    available_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_script_tasks_script_id ON script_tasks(script_id)"
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> ScriptTaskRecord:
        return ScriptTaskRecord(
            schema_version="1.0.0", fixture_data=bool(row["fixture_data"]), task_id=row["task_id"],
            product_id=row["product_id"], product_revision=row["product_revision"],
            source_analysis_id=row["source_analysis_id"], source_analysis_revision=row["source_analysis_revision"],
            pattern_id=row["pattern_id"], content_goal=row["content_goal"], target_audience=row["target_audience"],
            version_count=row["version_count"], status=row["status"], progress=row["progress"],
            current_step=row["current_step"], attempt_count=row["attempt_count"], max_attempts=row["max_attempts"],
            result_path=row["result_path"], script_id=row["script_id"], error=row["error"],
            created_at=row["created_at"], updated_at=row["updated_at"], input_path=row["input_path"],
            workspace_path=row["workspace_path"],
        )

    def recover_interrupted(self) -> int:
        """Recover running jobs after adopting any committed script package."""

        now = _now_iso()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM script_tasks WHERE status='running' ORDER BY created_at"
            ).fetchall()
        for row in rows:
            task = self._record(row)
            committed = self._committed_result(task)
            if committed is not None:
                self.complete(task.task_id, committed)
                continue
            with self._connect() as connection:
                connection.execute(
                    """UPDATE script_tasks SET
                       status=CASE WHEN attempt_count < max_attempts THEN 'pending' ELSE 'failed' END,
                       current_step=CASE WHEN attempt_count < max_attempts THEN 'queued' ELSE 'failed' END,
                       progress=CASE WHEN attempt_count < max_attempts THEN 0 ELSE progress END,
                       error=CASE WHEN attempt_count < max_attempts THEN NULL ELSE '任务在最后一次生成时被中断，请手动重试' END,
                       available_at=?, updated_at=? WHERE task_id=? AND status='running'""",
                    (now, now, task.task_id),
                )
        return len(rows)

    @staticmethod
    def _committed_result(task: ScriptTaskRecord) -> ScriptArtifacts | None:
        """Validate and reconstruct the final bookkeeping for a committed script."""

        try:
            workspace = Path(task.workspace_path).expanduser().resolve()
            result_path = (workspace / "script-package.json").resolve()
            input_path = Path(task.input_path).expanduser().resolve()
            if (
                not result_path.is_relative_to(workspace)
                or not input_path.is_relative_to(workspace)
                or not result_path.is_file()
                or not input_path.is_file()
            ):
                return None
            script = json.loads(result_path.read_text(encoding="utf-8"))
            frozen = json.loads(input_path.read_text(encoding="utf-8"))
            if not isinstance(script, Mapping) or not isinstance(frozen, Mapping):
                return None
            product = frozen["product"]
            analysis = frozen["analysis"]
            validate_or_raise("script", script, related={"product": product, "analysis": analysis})
            generation = script["generation"]
            if (
                generation["task_id"] != task.task_id
                or script["revision"] != 1
                or script["fixture_data"] is not task.fixture_data
                or script["product_id"] != task.product_id
                or script["product_revision"] != task.product_revision
                or script["source_analysis_id"] != task.source_analysis_id
                or script["source_analysis_revision"] != task.source_analysis_revision
                or script["pattern_id"] != task.pattern_id
                or script["content_goal"] != task.content_goal
                or script["target_audience"] != task.target_audience
                or len(script["versions"]) != task.version_count
            ):
                return None
            script_id = script["script_id"]
            if not isinstance(script_id, str) or not script_id.strip():
                return None

            # result.json is committed first. Recreate deterministic revision
            # metadata when the process stopped between the remaining atomic
            # writes, so the recovered script is fully editable/auditable.
            _atomic_json(workspace / "revisions" / "script-r0001.json", script)
            _atomic_json(workspace / "revision-log.json", [{
                "revision": 1,
                "action": "generated",
                "actor": f"AI · {generation['model']}",
                "created_at": script["created_at"],
                "review_status": script["review"]["status"],
            }])
            return ScriptArtifacts(script_id=script_id, result_path=result_path)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def enqueue(self, *, workspace_path: str | Path, input_payload: Mapping[str, Any], max_attempts: int = 2) -> ScriptTaskRecord:
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts 必须在 1—5 之间")
        product = input_payload["product"]
        analysis = input_payload["analysis"]
        pattern = input_payload["pattern"]
        request = input_payload["request"]
        task_id = f"script_task_{uuid4().hex}"
        workspace = Path(workspace_path).expanduser().resolve()
        task_directory = (workspace / "tasks" / task_id).resolve()
        if not task_directory.is_relative_to(workspace):
            raise ValueError("脚本任务目录越界")
        input_path = task_directory / "input.json"
        _atomic_json(input_path, input_payload)
        now = _now_iso()
        fixture_data = bool(product.get("fixture_data") or analysis.get("fixture_data"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO script_tasks (
                    task_id, fixture_data, product_id, product_revision, source_analysis_id,
                    source_analysis_revision, pattern_id, content_goal, target_audience, version_count,
                    status, progress, current_step, attempt_count, max_attempts, result_path, script_id,
                    error, input_path, workspace_path, created_at, updated_at, available_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 'queued', 0, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, int(fixture_data), product["product_id"], product["revision"], analysis["analysis_id"],
                    analysis["revision"], pattern["id"], request["content_goal"], request["target_audience"],
                    request["version_count"], max_attempts, str(input_path), str(task_directory), now, now, now,
                ),
            )
        record = self.get(task_id)
        assert record is not None
        record.to_dict()
        return record

    def get(self, task_id: str) -> ScriptTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM script_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._record(row) if row else None

    def list(self, *, limit: int = 200) -> list[ScriptTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM script_tasks ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def completed(self) -> list[ScriptTaskRecord]:
        """Return every persisted script result; UI pagination must not define data ownership."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM script_tasks
                   WHERE status='completed' AND script_id IS NOT NULL AND result_path IS NOT NULL
                   ORDER BY updated_at DESC"""
            ).fetchall()
        return [self._record(row) for row in rows]

    def completed_for_script(self, script_id: str) -> ScriptTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM script_tasks
                   WHERE status='completed' AND script_id=? AND result_path IS NOT NULL
                   ORDER BY updated_at DESC LIMIT 1""",
                (script_id,),
            ).fetchone()
        return self._record(row) if row else None

    def counts(self) -> dict[str, int]:
        counts = {status: 0 for status in ("pending", "running", "retry_wait", "completed", "failed")}
        with self._connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) amount FROM script_tasks GROUP BY status").fetchall()
        for row in rows:
            counts[row["status"]] = row["amount"]
        return counts

    def retry(self, task_id: str) -> ScriptTaskRecord:
        now = _now_iso()
        with self._connect() as connection:
            # Serialize the read/check/budget increment. Without the write
            # lock, two operators can both observe `failed` and each grant an
            # extra attempt, even pushing max_attempts past the contract cap.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM script_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise LookupError("找不到这个脚本任务")
            if row["status"] != "failed":
                raise ValueError("只有失败任务需要手动重试")
            if row["max_attempts"] >= 5:
                raise ValueError("这个任务已经达到 5 次安全上限，请重新建立生成任务")
            cursor = connection.execute(
                """UPDATE script_tasks SET status='pending', current_step='queued', progress=0,
                   max_attempts=max_attempts+1, error=NULL, available_at=?, updated_at=?
                   WHERE task_id=? AND status='failed' AND max_attempts < 5""",
                (now, now, task_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ValueError("任务已被其他请求重试，请刷新后查看")
            refreshed = connection.execute(
                "SELECT * FROM script_tasks WHERE task_id=?", (task_id,),
            ).fetchone()
            connection.commit()
        assert refreshed is not None
        return self._record(refreshed)

    def claim_next(self) -> ScriptTaskRecord | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _now_iso()
            row = connection.execute(
                """SELECT * FROM script_tasks WHERE status IN ('pending','retry_wait')
                   AND attempt_count < max_attempts AND available_at <= ? ORDER BY created_at ASC LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """UPDATE script_tasks SET status='running', current_step='prepare', progress=1,
                   attempt_count=attempt_count+1, error=NULL, updated_at=? WHERE task_id=?""",
                (now, row["task_id"]),
            )
            connection.commit()
            refreshed = connection.execute("SELECT * FROM script_tasks WHERE task_id=?", (row["task_id"],)).fetchone()
            assert refreshed is not None
            return self._record(refreshed)
        finally:
            connection.close()

    def update_progress(self, task_id: str, step: str, progress: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE script_tasks SET current_step=?, progress=?, updated_at=? WHERE task_id=? AND status='running'",
                (step, max(0, min(99, progress)), _now_iso(), task_id),
            )

    def complete(self, task_id: str, artifacts: ScriptArtifacts) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE script_tasks SET status='completed', current_step='completed', progress=100,
                   result_path=?, script_id=?, error=NULL, updated_at=? WHERE task_id=? AND status='running'""",
                (str(artifacts.result_path), artifacts.script_id, _now_iso(), task_id),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("脚本任务完成时状态已发生变化")

    def fail(self, task_id: str, error: Exception) -> None:
        task = self.get(task_id)
        if task is None:
            return
        safe_error = str(error).strip().replace("\r", " ").replace("\n", " ")[:500] or "未知错误"
        can_retry = bool(getattr(error, "retryable", True)) and task.attempt_count < task.max_attempts
        now = datetime.now(UTC)
        delay = min(30.0, self.retry_base_seconds * (2 ** max(0, task.attempt_count - 1))) if can_retry else 0
        available_at = now + timedelta(seconds=delay)
        with self._connect() as connection:
            connection.execute(
                """UPDATE script_tasks SET status=?, current_step=?, progress=?, error=?, available_at=?, updated_at=?
                   WHERE task_id=? AND status='running'""",
                (
                    "retry_wait" if can_retry else "failed", "queued" if can_retry else "failed",
                    0 if can_retry else task.progress, safe_error,
                    available_at.isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z"), task_id,
                ),
            )

    def _next_retry_delay(self) -> float | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT MIN(available_at) available_at FROM script_tasks
                   WHERE status='retry_wait' AND attempt_count < max_attempts"""
            ).fetchone()
        value = row["available_at"] if row else None
        if not value:
            return None
        try:
            available_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return 0
        return max(0.0, (available_at - datetime.now(UTC)).total_seconds())

    def run_pending(
        self, *, gateway_config: GatewayConfig, max_workers: int = 2,
        processor: TaskProcessor | None = None, preflight: TaskPreflight | None = None,
    ) -> list[ScriptTaskRecord]:
        worker_count = max(1, min(max_workers, 2))
        processed_ids: set[str] = set()

        def default_processor(task: ScriptTaskRecord, callback: Callable[[str, int], None]) -> ScriptArtifacts:
            payload = json.loads(Path(task.input_path).read_text(encoding="utf-8"))
            snapshot = payload.pop("_gateway_model_snapshot", None)
            pinned_model_id = snapshot.get("model_id") if isinstance(snapshot, dict) else payload.pop("_gateway_model_id", None)
            task_config = (
                gateway_config.for_model_purpose(pinned_model_id, "script")
                if isinstance(pinned_model_id, str)
                else gateway_config.for_purpose("script")
            )
            if isinstance(snapshot, dict) and not task_config.matches_snapshot(snapshot):
                raise GatewaySettingsError("任务绑定的模型配置已变更，请明确重新创建任务后再运行")
            return process_script_generation(
                task_id=task.task_id, input_payload=payload, task_directory=task.workspace_path,
                config=task_config, progress=callback,
            )

        execute = processor or default_processor

        def guarded_processor(
            task: ScriptTaskRecord,
            callback: Callable[[str, int], None],
        ) -> ScriptArtifacts:
            # A queued request freezes the evidence used to generate it, but
            # permission to reuse that evidence remains live. Validate the
            # frozen inputs immediately before any processor (and therefore
            # before the remote model call) so a disabled or superseded Skill
            # cannot slip through while it was waiting in the queue.
            if preflight is not None:
                payload = json.loads(Path(task.input_path).read_text(encoding="utf-8"))
                if not isinstance(payload, Mapping):
                    raise ValueError("脚本任务的冻结输入格式无效")
                preflight(task, payload)
            return execute(task, callback)

        def submit(pool: ThreadPoolExecutor, active: dict[Future[ScriptArtifacts], ScriptTaskRecord]) -> bool:
            task = self.claim_next()
            if task is None:
                return False
            processed_ids.add(task.task_id)
            callback = lambda step, value, task_id=task.task_id: self.update_progress(task_id, step, value)
            active[pool.submit(guarded_processor, task, callback)] = task
            return True

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="script-s5") as pool:
            active: dict[Future[ScriptArtifacts], ScriptTaskRecord] = {}
            while True:
                while len(active) < worker_count and submit(pool, active):
                    pass
                if active:
                    completed, _ = wait(active, return_when=FIRST_COMPLETED)
                    for future in completed:
                        task = active.pop(future)
                        try:
                            self.complete(task.task_id, future.result())
                        except Exception as exc:
                            self.fail(task.task_id, exc)
                    continue
                delay = self._next_retry_delay()
                if delay is None:
                    break
                time.sleep(max(0.01, min(delay, 30.0)))
        return [task for task_id in processed_ids if (task := self.get(task_id)) is not None]
