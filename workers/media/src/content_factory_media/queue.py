"""SQLite-backed recoverable S2 media task queue."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from content_factory_contracts import validate_or_raise

from .pipeline import MediaPipeline, ProgressCallback


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class MediaTaskRecord:
    schema_version: str
    fixture_data: bool
    task_id: str
    status: str
    progress: int
    current_step: str
    source_name: str
    source_path: str
    workspace_path: str
    attempt_count: int
    max_attempts: int
    error: str | None
    result_path: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        validate_or_raise("media_task", payload)
        return payload


TaskProcessor = Callable[[MediaTaskRecord, ProgressCallback], str | Path]


class MediaTaskQueue:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
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
                CREATE TABLE IF NOT EXISTS media_tasks (
                    task_id TEXT PRIMARY KEY,
                    fixture_data INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    current_step TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    workspace_path TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    error TEXT,
                    result_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _committed_result(task: MediaTaskRecord) -> Path | None:
        """Return a fully committed pipeline result belonging to ``task``.

        ``MediaPipeline`` atomically writes result.json after every referenced
        artifact.  A process can still stop after that rename and before the
        queue completion transaction, so startup must validate and adopt the
        result instead of consuming another attempt or reporting false failure.
        """

        try:
            workspace = Path(task.workspace_path).expanduser().resolve()
            task_directory = (workspace / "tasks" / task.task_id).resolve()
            if not task_directory.is_relative_to(workspace):
                return None
            result_path = (task_directory / "result.json").resolve()
            if not result_path.is_file() or not result_path.is_relative_to(task_directory):
                return None
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                return None
            validate_or_raise("media_result", payload)
            if (
                payload.get("task_id") != task.task_id
                or payload.get("fixture_data") is not task.fixture_data
                or payload.get("source", {}).get("original_name") != task.source_name
            ):
                return None
            referenced_paths = [payload["source"]["managed_original_path"]]
            referenced_paths.extend(value for value in payload["artifacts"].values() if value is not None)
            referenced_paths.extend(shot["keyframe_path"] for shot in payload["shots"])
            for value in referenced_paths:
                artifact = Path(value).expanduser().resolve()
                if not artifact.is_relative_to(workspace) or not artifact.is_file():
                    return None
            return result_path
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def recover_interrupted(self) -> int:
        """Recover stale running tasks once when a worker service starts."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM media_tasks WHERE status = 'running' ORDER BY created_at"
            ).fetchall()
        now = _now_iso()
        for row in rows:
            task = self._row_to_record(row)
            committed_result = self._committed_result(task)
            if committed_result is not None:
                self.complete(task.task_id, committed_result)
                continue
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE media_tasks
                    SET status = CASE
                            WHEN attempt_count < max_attempts THEN 'pending'
                            ELSE 'failed'
                        END,
                        current_step = CASE
                            WHEN attempt_count < max_attempts THEN 'recovered'
                            ELSE 'failed'
                        END,
                        progress = CASE
                            WHEN attempt_count < max_attempts THEN 0
                            ELSE progress
                        END,
                        error = CASE
                            WHEN attempt_count < max_attempts THEN NULL
                            ELSE '任务在最后一次处理时被中断，已达到自动尝试上限，请重新导入视频'
                        END,
                        updated_at = ?
                    WHERE task_id = ? AND status = 'running'
                    """,
                    (now, task.task_id),
                )
        return len(rows)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MediaTaskRecord:
        return MediaTaskRecord(
            schema_version="1.0.0",
            fixture_data=bool(row["fixture_data"]),
            task_id=row["task_id"],
            status=row["status"],
            progress=row["progress"],
            current_step=row["current_step"],
            source_name=row["source_name"],
            source_path=row["source_path"],
            workspace_path=row["workspace_path"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            error=row["error"],
            result_path=row["result_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def enqueue(
        self,
        source_path: str | Path,
        workspace_path: str | Path,
        *,
        fixture_data: bool = False,
        max_attempts: int = 2,
        source_name: str | None = None,
    ) -> MediaTaskRecord:
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts 必须在 1—5 之间")
        source = Path(source_path).expanduser().resolve()
        workspace = Path(workspace_path).expanduser().resolve()
        task_id = f"media_{uuid4().hex}"
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO media_tasks (
                    task_id, fixture_data, status, progress, current_step, source_name,
                    source_path, workspace_path, attempt_count, max_attempts, error,
                    result_path, created_at, updated_at
                ) VALUES (?, ?, 'pending', 0, 'queued', ?, ?, ?, 0, ?, NULL, NULL, ?, ?)
                """,
                (
                    task_id,
                    int(fixture_data),
                    (source_name or source.name)[:255],
                    str(source),
                    str(workspace),
                    max_attempts,
                    now,
                    now,
                ),
            )
        task = self.get(task_id)
        assert task is not None
        task.to_dict()
        return task

    def get(self, task_id: str) -> MediaTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM media_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def list(self, *, limit: int = 100) -> list[MediaTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM media_tasks ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def counts(self) -> dict[str, int]:
        result = {status: 0 for status in ("pending", "running", "retry_wait", "completed", "failed")}
        with self._connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM media_tasks GROUP BY status").fetchall()
        for row in rows:
            result[row["status"]] = row["count"]
        return result

    def claim_next(self) -> MediaTaskRecord | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM media_tasks
                WHERE status IN ('pending', 'retry_wait') AND attempt_count < max_attempts
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            now = _now_iso()
            connection.execute(
                """
                UPDATE media_tasks
                SET status = 'running', current_step = 'probe', progress = 1,
                    attempt_count = attempt_count + 1, error = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (now, row["task_id"]),
            )
            connection.commit()
            refreshed = connection.execute(
                "SELECT * FROM media_tasks WHERE task_id = ?", (row["task_id"],)
            ).fetchone()
            assert refreshed is not None
            return self._row_to_record(refreshed)
        finally:
            connection.close()

    def update_progress(self, task_id: str, step: str, progress: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE media_tasks
                SET current_step = ?, progress = ?, updated_at = ?
                WHERE task_id = ? AND status = 'running'
                """,
                (step, max(0, min(99, progress)), _now_iso(), task_id),
            )

    def complete(self, task_id: str, result_path: str | Path) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE media_tasks
                SET status = 'completed', current_step = 'completed', progress = 100,
                    error = NULL, result_path = ?, updated_at = ?
                WHERE task_id = ? AND status = 'running'
                """,
                (str(Path(result_path).resolve()), _now_iso(), task_id),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("任务完成时状态已发生变化")

    def fail(self, task_id: str, error: Exception) -> None:
        task = self.get(task_id)
        if task is None:
            return
        safe_error = str(error).strip().replace("\r", " ").replace("\n", " ")[:500] or "未知错误"
        can_retry = task.attempt_count < task.max_attempts
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE media_tasks
                SET status = ?, current_step = ?, progress = ?, error = ?, updated_at = ?
                WHERE task_id = ? AND status = 'running'
                """,
                (
                    "retry_wait" if can_retry else "failed",
                    "queued" if can_retry else "failed",
                    0 if can_retry else task.progress,
                    safe_error,
                    _now_iso(),
                    task_id,
                ),
            )

    def run_pending(
        self,
        *,
        max_workers: int = 4,
        asr_model_path: str | Path | None = None,
        processor: TaskProcessor | None = None,
    ) -> list[MediaTaskRecord]:
        worker_count = max(1, min(max_workers, 4))
        processed_ids: set[str] = set()

        def default_processor(task: MediaTaskRecord, callback: ProgressCallback) -> Path:
            pipeline = MediaPipeline(asr_model_path=asr_model_path)
            return pipeline.process(
                task.source_path,
                task.workspace_path,
                task.task_id,
                fixture_data=task.fixture_data,
                original_name=task.source_name,
                progress=callback,
            )

        execute = processor or default_processor

        def submit_next(pool: ThreadPoolExecutor, active: dict[Future[str | Path], MediaTaskRecord]) -> bool:
            task = self.claim_next()
            if task is None:
                return False
            processed_ids.add(task.task_id)
            callback = lambda step, value, task_id=task.task_id: self.update_progress(task_id, step, value)
            active[pool.submit(execute, task, callback)] = task
            return True

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="media-s2") as pool:
            active: dict[Future[str | Path], MediaTaskRecord] = {}
            for _ in range(worker_count):
                if not submit_next(pool, active):
                    break
            while active:
                completed, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in completed:
                    task = active.pop(future)
                    try:
                        self.complete(task.task_id, future.result())
                    except Exception as exc:  # the queue must record worker failures
                        self.fail(task.task_id, exc)
                    submit_next(pool, active)

        return [task for task_id in processed_ids if (task := self.get(task_id)) is not None]
