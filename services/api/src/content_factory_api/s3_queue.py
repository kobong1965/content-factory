"""SQLite-backed recoverable queue for S3 deep-analysis tasks."""

from __future__ import annotations

import json
import os
import hashlib
import random
import sqlite3
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from content_factory_contracts import validate_or_raise

from .s3_analysis import (
    PROMPT_VERSION,
    AnalysisArtifacts,
    AnalysisProcessingError,
    process_analysis,
)
from .s3_settings import GatewayConfig, GatewaySettingsError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def re_full_hex(value: str) -> bool:
    return len(value) == 32 and all(character in "0123456789abcdef" for character in value)


def _safe_idempotency_payload(value: Any) -> Any:
    """Remove credentials before deriving a deterministic analysis identity."""

    if isinstance(value, Mapping):
        return {
            str(key): _safe_idempotency_payload(item)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in ("api_key", "authorization", "secret"))
        }
    if isinstance(value, list):
        return [_safe_idempotency_payload(item) for item in value]
    return value


def _analysis_idempotency_key(media_result_path: str | Path, input_payload: Mapping[str, Any]) -> str:
    path = Path(media_result_path).expanduser().resolve()
    source_hash = ""
    try:
        media_payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(media_payload, dict) and isinstance(media_payload.get("source"), dict):
            candidate = media_payload["source"].get("sha256")
            if isinstance(candidate, str):
                source_hash = candidate
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    if not source_hash:
        try:
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            source_hash = hashlib.sha256(f"{path}:{content_hash}".encode("utf-8")).hexdigest()
        except OSError:
            source_hash = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    identity = {
        "source_hash": source_hash,
        "prompt_version": PROMPT_VERSION,
        "input": _safe_idempotency_payload(input_payload),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class AnalysisTaskRecord:
    schema_version: str
    fixture_data: bool
    task_id: str
    media_task_id: str
    model_purpose: Literal["analysis", "video_review"]
    status: str
    progress: int
    current_step: str
    attempt_count: int
    max_attempts: int
    metric_count: int
    comment_count: int
    ocr_result_path: str | None
    result_path: str | None
    error: str | None
    created_at: str
    updated_at: str
    media_result_path: str
    input_path: str
    workspace_path: str
    trace_id: str
    idempotency_key: str
    segment_total: int
    segment_completed: int
    current_segment: int | None
    last_completed_segment: int | None
    last_checkpoint_at: str | None
    next_retry_at: str | None
    recovering: bool
    cancel_requested: bool
    diagnostic_code: str | None
    worker_id: str | None
    lease_expires_at: str | None
    retry_wait_seconds: float
    deadline_at: str
    summary_attempt_count: int
    retry_count: int

    def to_dict(self) -> dict[str, Any]:
        payload = {
            key: getattr(self, key)
            for key in (
                "schema_version", "fixture_data", "task_id", "media_task_id", "model_purpose",
                "status", "progress",
                "current_step", "attempt_count", "max_attempts", "metric_count", "comment_count",
                "ocr_result_path", "result_path", "error", "created_at", "updated_at",
                "trace_id", "idempotency_key", "segment_total", "segment_completed",
                "current_segment", "last_completed_segment", "last_checkpoint_at",
                "next_retry_at", "recovering", "cancel_requested", "diagnostic_code", "retry_count",
            )
        }
        validate_or_raise("analysis_task", payload)
        return payload


TaskProcessor = Callable[[AnalysisTaskRecord, Callable[[str, int], None]], AnalysisArtifacts]


@dataclass(frozen=True)
class AnalysisSegmentRecord:
    task_id: str
    segment_index: int
    segment_key: str
    start_ms: int
    end_ms: int
    status: str
    attempt_count: int
    max_attempts: int
    next_retry_at: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    result_path: str | None
    checkpoint_identity: str | None
    error: str | None
    outcome: str | None
    provider_request_id: str | None
    updated_at: str
    retry_wait_seconds: float


class AnalysisTaskQueue:
    def __init__(
        self,
        database_path: str | Path,
        *,
        retry_base_seconds: float = 2.0,
        retry_jitter_ratio: float = 0.2,
        retry_random: Callable[[], float] | None = None,
        segment_retry_wait_limit_seconds: float = 600.0,
        task_retry_wait_limit_seconds: float = 1_200.0,
        task_deadline_seconds: float = 21_600.0,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        if not 0 < retry_base_seconds <= 30:
            raise ValueError("retry_base_seconds 必须大于 0 且不超过 30 秒")
        if not 0 <= retry_jitter_ratio <= 0.5:
            raise ValueError("retry_jitter_ratio 必须在 0—0.5 之间")
        if segment_retry_wait_limit_seconds <= 0 or task_retry_wait_limit_seconds <= 0:
            raise ValueError("重试累计等待上限必须大于 0")
        if not 60 <= task_deadline_seconds <= 86_400:
            raise ValueError("任务期限必须在 60—86400 秒之间")
        self.retry_base_seconds = retry_base_seconds
        self.retry_jitter_ratio = retry_jitter_ratio
        self.retry_random = retry_random or random.random
        self.segment_retry_wait_limit_seconds = segment_retry_wait_limit_seconds
        self.task_retry_wait_limit_seconds = task_retry_wait_limit_seconds
        self.task_deadline_seconds = task_deadline_seconds
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _retry_delay(
        self,
        *,
        attempt_count: int,
        retry_after_seconds: float | None,
        accumulated_wait_seconds: float,
        wait_limit_seconds: float,
    ) -> float | None:
        """Return a bounded jittered delay, or None when the wait budget is exhausted."""

        exponential = min(32.0, self.retry_base_seconds * (2 ** max(0, attempt_count - 1)))
        random_value = max(0.0, min(1.0, float(self.retry_random())))
        jitter_factor = 1.0 - self.retry_jitter_ratio + (2.0 * self.retry_jitter_ratio * random_value)
        jittered = exponential * jitter_factor
        requested = max(0.0, float(retry_after_seconds or 0.0))
        delay = min(300.0, max(jittered, requested))
        if accumulated_wait_seconds + delay > wait_limit_seconds:
            return None
        return delay

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
                CREATE TABLE IF NOT EXISTS analysis_tasks (
                    task_id TEXT PRIMARY KEY,
                    fixture_data INTEGER NOT NULL,
                    media_task_id TEXT NOT NULL,
                    model_purpose TEXT NOT NULL DEFAULT 'analysis',
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    current_step TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    metric_count INTEGER NOT NULL,
                    comment_count INTEGER NOT NULL,
                    ocr_result_path TEXT,
                    result_path TEXT,
                    error TEXT,
                    media_result_path TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    workspace_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    available_at TEXT NOT NULL
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(analysis_tasks)").fetchall()}
            if "available_at" not in columns:
                connection.execute("ALTER TABLE analysis_tasks ADD COLUMN available_at TEXT")
                connection.execute("UPDATE analysis_tasks SET available_at=updated_at WHERE available_at IS NULL")
            if "model_purpose" not in columns:
                connection.execute(
                    "ALTER TABLE analysis_tasks ADD COLUMN model_purpose TEXT NOT NULL DEFAULT 'analysis'"
                )
                rows = connection.execute("SELECT task_id, input_path FROM analysis_tasks").fetchall()
                for row in rows:
                    try:
                        payload = json.loads(Path(row["input_path"]).read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict) and payload.get("_gateway_purpose") == "video_review":
                        connection.execute(
                            "UPDATE analysis_tasks SET model_purpose='video_review' WHERE task_id=?",
                            (row["task_id"],),
                        )
            additions = {
                "trace_id": "TEXT",
                "idempotency_key": "TEXT",
                "segment_total": "INTEGER NOT NULL DEFAULT 0",
                "segment_completed": "INTEGER NOT NULL DEFAULT 0",
                "current_segment": "INTEGER",
                "last_completed_segment": "INTEGER",
                "last_checkpoint_at": "TEXT",
                "next_retry_at": "TEXT",
                "recovering": "INTEGER NOT NULL DEFAULT 0",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "diagnostic_code": "TEXT",
                "worker_id": "TEXT",
                "lease_expires_at": "TEXT",
                "retry_wait_seconds": "REAL NOT NULL DEFAULT 0",
                "deadline_at": "TEXT",
                "summary_attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
            }
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(analysis_tasks)").fetchall()}
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE analysis_tasks ADD COLUMN {name} {declaration}")
            rows = connection.execute(
                "SELECT task_id FROM analysis_tasks WHERE trace_id IS NULL OR idempotency_key IS NULL"
            ).fetchall()
            for row in rows:
                task_id = str(row["task_id"])
                suffix = task_id.removeprefix("analysis_task_")
                trace_id = f"trace_{suffix}" if re_full_hex(suffix) else f"trace_{uuid4().hex}"
                legacy_key = hashlib.sha256(f"legacy:{task_id}".encode("utf-8")).hexdigest()
                connection.execute(
                    "UPDATE analysis_tasks SET trace_id=COALESCE(trace_id, ?), "
                    "idempotency_key=COALESCE(idempotency_key, ?) WHERE task_id=?",
                    (trace_id, legacy_key, task_id),
                )
            legacy_deadline = (
                datetime.now(UTC) + timedelta(seconds=self.task_deadline_seconds)
            ).isoformat().replace("+00:00", "Z")
            connection.execute(
                "UPDATE analysis_tasks SET deadline_at=? WHERE deadline_at IS NULL",
                (legacy_deadline,),
            )
            # Recreate the partial index so legacy `completed` rows and new
            # canonical `succeeded` rows both prevent duplicate billing.
            connection.execute("DROP INDEX IF EXISTS analysis_tasks_active_idempotency")
            connection.execute(
                "CREATE UNIQUE INDEX analysis_tasks_active_idempotency "
                "ON analysis_tasks(idempotency_key) "
                "WHERE status IN ('pending','running','retry_wait','completed','succeeded')"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_segments (
                    task_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL,
                    segment_key TEXT NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 4,
                    available_at TEXT NOT NULL,
                    next_retry_at TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    result_path TEXT,
                    checkpoint_identity TEXT,
                    error TEXT,
                    outcome TEXT,
                    provider_request_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, segment_index),
                    FOREIGN KEY (task_id) REFERENCES analysis_tasks(task_id)
                )
                """
            )
            segment_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(analysis_segments)").fetchall()
            }
            segment_additions = {
                "retry_wait_seconds": "REAL NOT NULL DEFAULT 0",
                "checkpoint_identity": "TEXT",
            }
            for name, declaration in segment_additions.items():
                if name not in segment_columns:
                    connection.execute(f"ALTER TABLE analysis_segments ADD COLUMN {name} {declaration}")
            # Early versions made segment_key globally unique. That prevents a
            # new task from using the same deterministic segment identity after
            # an exhausted/failed task. Rebuild once without deleting history.
            has_global_segment_key_unique = False
            for index_row in connection.execute("PRAGMA index_list(analysis_segments)").fetchall():
                if not bool(index_row["unique"]):
                    continue
                index_name = str(index_row["name"]).replace('"', '""')
                indexed_columns = [
                    item["name"]
                    for item in connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
                ]
                if indexed_columns == ["segment_key"]:
                    has_global_segment_key_unique = True
                    break
            if has_global_segment_key_unique:
                connection.execute(
                    """
                    CREATE TABLE analysis_segments_without_global_key_unique (
                        task_id TEXT NOT NULL,
                        segment_index INTEGER NOT NULL,
                        segment_key TEXT NOT NULL,
                        start_ms INTEGER NOT NULL,
                        end_ms INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 4,
                        available_at TEXT NOT NULL,
                        next_retry_at TEXT,
                        lease_owner TEXT,
                        lease_expires_at TEXT,
                        result_path TEXT,
                        checkpoint_identity TEXT,
                        error TEXT,
                        outcome TEXT,
                        provider_request_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        retry_wait_seconds REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY (task_id, segment_index),
                        FOREIGN KEY (task_id) REFERENCES analysis_tasks(task_id)
                    )
                    """
                )
                connection.execute(
                    """INSERT INTO analysis_segments_without_global_key_unique (
                           task_id, segment_index, segment_key, start_ms, end_ms, status,
                           attempt_count, max_attempts, available_at, next_retry_at,
                           lease_owner, lease_expires_at, result_path, checkpoint_identity, error, outcome,
                           provider_request_id, created_at, updated_at, retry_wait_seconds
                       )
                       SELECT task_id, segment_index, segment_key, start_ms, end_ms, status,
                           attempt_count, max_attempts, available_at, next_retry_at,
                           lease_owner, lease_expires_at, result_path, checkpoint_identity, error, outcome,
                           provider_request_id, created_at, updated_at, retry_wait_seconds
                       FROM analysis_segments"""
                )
                connection.execute("DROP TABLE analysis_segments")
                connection.execute(
                    "ALTER TABLE analysis_segments_without_global_key_unique RENAME TO analysis_segments"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS analysis_segments_claimable "
                "ON analysis_segments(task_id, status, available_at, segment_index)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS analysis_segments_key_lookup "
                "ON analysis_segments(segment_key)"
            )
            connection.execute("UPDATE analysis_tasks SET max_attempts=5 WHERE max_attempts>5")
            connection.execute("UPDATE analysis_segments SET max_attempts=5 WHERE max_attempts>5")
            legacy_checkpoints = connection.execute(
                """SELECT task_id, segment_index FROM analysis_segments
                   WHERE status='succeeded' AND checkpoint_identity IS NULL"""
            ).fetchall()
            for checkpoint in legacy_checkpoints:
                connection.execute(
                    """UPDATE analysis_segments SET checkpoint_identity=?
                       WHERE task_id=? AND segment_index=? AND status='succeeded'
                         AND checkpoint_identity IS NULL""",
                    (
                        f"checkpoint_{uuid4().hex}",
                        checkpoint["task_id"],
                        checkpoint["segment_index"],
                    ),
                )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AnalysisTaskRecord:
        return AnalysisTaskRecord(
            schema_version="1.0.0",
            fixture_data=bool(row["fixture_data"]),
            task_id=row["task_id"], media_task_id=row["media_task_id"],
            model_purpose=row["model_purpose"], status=row["status"],
            progress=row["progress"], current_step=row["current_step"], attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"], metric_count=row["metric_count"], comment_count=row["comment_count"],
            ocr_result_path=row["ocr_result_path"], result_path=row["result_path"], error=row["error"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            media_result_path=row["media_result_path"], input_path=row["input_path"], workspace_path=row["workspace_path"],
            trace_id=row["trace_id"], idempotency_key=row["idempotency_key"],
            segment_total=row["segment_total"], segment_completed=row["segment_completed"],
            current_segment=row["current_segment"], last_completed_segment=row["last_completed_segment"],
            last_checkpoint_at=row["last_checkpoint_at"], next_retry_at=row["next_retry_at"],
            recovering=bool(row["recovering"]), cancel_requested=bool(row["cancel_requested"]),
            diagnostic_code=row["diagnostic_code"],
            worker_id=row["worker_id"], lease_expires_at=row["lease_expires_at"],
            retry_wait_seconds=float(row["retry_wait_seconds"] or 0),
            deadline_at=row["deadline_at"],
            summary_attempt_count=int(row["summary_attempt_count"] or 0),
            retry_count=int(row["retry_count"] or 0),
        )

    def recover_interrupted(self, *, force: bool = False) -> int:
        """Recover expired leases; force is reserved for tests/confirmed single-owner shutdowns."""

        now = _now_iso()
        with self._connect() as connection:
            if force:
                connection.execute(
                    """UPDATE analysis_segments SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                       available_at=?, next_retry_at=NULL, updated_at=?
                       WHERE status='running'""",
                    (now, now),
                )
                cursor = connection.execute(
                    """UPDATE analysis_tasks SET status='pending', current_step='recovered', recovering=1,
                       worker_id=NULL, lease_expires_at=NULL, available_at=?, next_retry_at=NULL, updated_at=?
                       WHERE status='running' AND cancel_requested=0 AND deadline_at > ?""",
                    (now, now, now),
                )
            else:
                connection.execute(
                    """UPDATE analysis_segments SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                       available_at=?, next_retry_at=NULL, updated_at=?
                       WHERE status='running'
                         AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                    (now, now, now),
                )
                cursor = connection.execute(
                    """UPDATE analysis_tasks SET status='pending', current_step='recovered', recovering=1,
                       worker_id=NULL, lease_expires_at=NULL, available_at=?, next_retry_at=NULL, updated_at=?
                       WHERE status='running' AND cancel_requested=0 AND deadline_at > ?
                         AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                    (now, now, now, now),
                )
            connection.execute(
                """UPDATE analysis_tasks SET status='failed', current_step='failed',
                   error='任务超过最长可恢复期限', diagnostic_code='task_deadline_exceeded',
                   worker_id=NULL, lease_expires_at=NULL, next_retry_at=NULL, updated_at=?
                   WHERE status='running' AND cancel_requested=0 AND deadline_at <= ?""",
                (now, now),
            )
        return cursor.rowcount

    def enqueue(
        self,
        *,
        media_task_id: str,
        media_result_path: str | Path,
        workspace_path: str | Path,
        input_payload: Mapping[str, Any],
        fixture_data: bool,
        max_attempts: int = 2,
    ) -> AnalysisTaskRecord:
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts 必须在 1—5 之间")
        model_purpose = input_payload.get("_gateway_purpose", "analysis")
        if not isinstance(model_purpose, str) or model_purpose not in {"analysis", "video_review"}:
            raise ValueError("分析任务的模型用途无效")
        idempotency_key = _analysis_idempotency_key(media_result_path, input_payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM analysis_tasks WHERE idempotency_key=?
                   AND status IN ('pending','running','retry_wait','completed','succeeded')
                   ORDER BY created_at DESC LIMIT 1""",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._row_to_record(existing)
        task_id = f"analysis_task_{uuid4().hex}"
        trace_id = f"trace_{uuid4().hex}"
        workspace = Path(workspace_path).expanduser().resolve()
        task_directory = (workspace / "tasks" / task_id).resolve()
        if not task_directory.is_relative_to(workspace):
            raise ValueError("分析任务目录越界")
        input_path = task_directory / "input.json"
        _atomic_json(input_path, input_payload)
        metric_count = len(input_payload.get("metric_snapshots", [])) if isinstance(input_payload.get("metric_snapshots"), list) else 0
        comment_count = len(input_payload.get("comments", [])) if isinstance(input_payload.get("comments"), list) else 0
        now = _now_iso()
        deadline_at = (
            datetime.now(UTC) + timedelta(seconds=self.task_deadline_seconds)
        ).isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT * FROM analysis_tasks WHERE idempotency_key=?
                   AND status IN ('pending','running','retry_wait','completed','succeeded')
                   ORDER BY created_at DESC LIMIT 1""",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._row_to_record(existing)
            connection.execute(
                """
                INSERT INTO analysis_tasks (
                    task_id, fixture_data, media_task_id, model_purpose, status, progress, current_step, attempt_count,
                    max_attempts, metric_count, comment_count, ocr_result_path, result_path, error,
                    media_result_path, input_path, workspace_path, created_at, updated_at, available_at,
                    trace_id, idempotency_key, segment_total, segment_completed, recovering, cancel_requested,
                    retry_wait_seconds, deadline_at, summary_attempt_count, retry_count
                ) VALUES (?, ?, ?, ?, 'pending', 0, 'queued', 0, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?,
                          ?, ?, 0, 0, 0, 0, 0, ?, 0, 0)
                """,
                (
                    task_id, int(fixture_data), media_task_id, model_purpose,
                    max_attempts, metric_count, comment_count,
                    str(Path(media_result_path).expanduser().resolve()), str(input_path), str(task_directory), now, now, now,
                    trace_id, idempotency_key, deadline_at,
                ),
            )
            connection.commit()
        record = self.get(task_id)
        assert record is not None
        record.to_dict()
        return record

    def get(self, task_id: str) -> AnalysisTaskRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM analysis_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def list(self, *, limit: int = 100) -> list[AnalysisTaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM analysis_tasks ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def completed_reports(self) -> list[AnalysisTaskRecord]:
        """Return every persisted report source without applying the UI's 500-row cap."""

        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM analysis_tasks
                   WHERE status IN ('completed','succeeded') AND result_path IS NOT NULL
                   ORDER BY created_at ASC"""
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def counts(self) -> dict[str, int]:
        result = {
            status: 0
            for status in ("pending", "running", "retry_wait", "succeeded", "completed", "failed", "cancelled")
        }
        with self._connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) count FROM analysis_tasks GROUP BY status").fetchall()
        for row in rows:
            result[row["status"]] = row["count"]
        return result

    def retry(self, task_id: str) -> AnalysisTaskRecord:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM analysis_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise LookupError("找不到这个分析任务")
            if row["status"] != "failed":
                connection.rollback()
                raise ValueError("只有失败任务需要手动重试")
            if int(row["attempt_count"]) >= 5 or int(row["summary_attempt_count"] or 0) >= 5:
                connection.rollback()
                raise ValueError("这个任务已经达到 5 次安全上限，请重新建立分析任务")
            exhausted_segment = connection.execute(
                """SELECT 1 FROM analysis_segments
                   WHERE task_id=? AND status IN ('failed','running','retry_wait')
                     AND attempt_count>=5 LIMIT 1""",
                (task_id,),
            ).fetchone()
            if exhausted_segment is not None:
                connection.rollback()
                raise ValueError("这个任务的片段已经达到 5 次安全上限，请重新建立分析任务")
            cursor = connection.execute(
                """UPDATE analysis_tasks SET status='pending',
                   current_step=CASE
                     WHEN segment_total>0 AND segment_completed=segment_total THEN 'summarizing'
                     ELSE 'queued' END,
                   progress=CASE WHEN segment_total > 0 THEN MAX(5, CAST(segment_completed * 75.0 / segment_total AS INTEGER)) ELSE 0 END,
                   max_attempts=MIN(5, max_attempts+1), error=NULL, diagnostic_code=NULL, recovering=1,
                   cancel_requested=0, current_segment=NULL, next_retry_at=NULL, retry_wait_seconds=0,
                   worker_id=NULL, lease_expires_at=NULL,
                   deadline_at=?, available_at=?, updated_at=?
                   WHERE task_id=? AND status='failed' AND max_attempts=?
                     AND attempt_count=? AND summary_attempt_count=? AND updated_at=?""",
                (
                    (datetime.now(UTC) + timedelta(seconds=self.task_deadline_seconds)).isoformat().replace("+00:00", "Z"),
                    now,
                    now,
                    task_id,
                    row["max_attempts"],
                    row["attempt_count"],
                    row["summary_attempt_count"],
                    row["updated_at"],
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("任务状态已变化，未重复增加重试预算")
            connection.execute(
                """UPDATE analysis_segments SET status='pending', error=NULL, outcome=NULL,
                   available_at=?, next_retry_at=NULL, lease_owner=NULL, lease_expires_at=NULL,
                   retry_wait_seconds=0,
                   max_attempts=MIN(5, CASE
                     WHEN attempt_count >= MIN(max_attempts, 5) THEN attempt_count+1
                     ELSE MIN(max_attempts, 5) END),
                   updated_at=?
                   WHERE task_id=? AND status IN ('failed','running','retry_wait') AND attempt_count<5""",
                (now, now, task_id),
            )
            connection.execute(
                "UPDATE analysis_segments SET max_attempts=5 WHERE task_id=? AND max_attempts>5",
                (task_id,),
            )
            connection.commit()
        record = self.get(task_id)
        assert record is not None
        return record

    def claim_next(self) -> AnalysisTaskRecord | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _now_iso()
            connection.execute(
                """UPDATE analysis_tasks SET status='pending', current_step='recovered', recovering=1,
                   worker_id=NULL, lease_expires_at=NULL, available_at=?, next_retry_at=NULL, updated_at=?
                   WHERE status='running' AND cancel_requested=0 AND lease_expires_at IS NOT NULL
                     AND lease_expires_at <= ? AND deadline_at > ?""",
                (now, now, now, now),
            )
            connection.execute(
                """UPDATE analysis_tasks SET status='failed', current_step='failed',
                   error='任务超过最长可恢复期限', diagnostic_code='task_deadline_exceeded',
                   next_retry_at=NULL, worker_id=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE status IN ('pending','retry_wait') AND cancel_requested=0 AND deadline_at <= ?""",
                (now, now),
            )
            row = connection.execute(
                """SELECT * FROM analysis_tasks
                   WHERE status IN ('pending','retry_wait')
                     AND (
                       (
                         current_step='summarizing'
                         AND summary_attempt_count < max_attempts
                         AND attempt_count < max_attempts
                       )
                       OR (
                         current_step<>'summarizing'
                         AND (
                           attempt_count < max_attempts
                           OR EXISTS (
                             SELECT 1 FROM analysis_segments s
                             WHERE s.task_id=analysis_tasks.task_id
                               AND s.status IN ('pending','retry_wait')
                               AND s.attempt_count < s.max_attempts
                           )
                         )
                       )
                     )
                     AND COALESCE(available_at, created_at) <= ?
                   ORDER BY created_at ASC LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            task_worker_id = f"task-worker-{uuid4().hex}"
            connection.execute(
                """UPDATE analysis_tasks SET status='running', current_step='ocr', progress=1,
                   attempt_count=attempt_count + CASE
                     WHEN (segment_total=0 OR current_step='summarizing')
                       AND attempt_count < max_attempts THEN 1 ELSE 0 END,
                   error=NULL, diagnostic_code=NULL, next_retry_at=NULL,
                   worker_id=?, lease_expires_at=?, updated_at=? WHERE task_id=?""",
                (
                    task_worker_id,
                    (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                    now,
                    row["task_id"],
                ),
            )
            connection.commit()
            refreshed = connection.execute("SELECT * FROM analysis_tasks WHERE task_id=?", (row["task_id"],)).fetchone()
            assert refreshed is not None
            return self._row_to_record(refreshed)
        finally:
            connection.close()

    def update_progress(self, task_id: str, step: str, progress: int, *, worker_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE analysis_tasks SET current_step=?, progress=?,
                   lease_expires_at=?, updated_at=?
                   WHERE task_id=? AND status='running' AND worker_id=?""",
                (
                    step,
                    max(0, min(99, progress)),
                    (datetime.now(UTC) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                    _now_iso(),
                    task_id,
                    worker_id,
                ),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("任务租约已变化，拒绝旧工作线程更新进度")

    def renew_task_lease(self, task_id: str, *, worker_id: str, lease_seconds: int = 300) -> bool:
        """Heartbeat a live task and its current segment under the same task-owner CAS."""

        lease_seconds = max(30, min(lease_seconds, 900))
        now = _now_iso()
        lease_expires_at = (
            datetime.now(UTC) + timedelta(seconds=lease_seconds)
        ).isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE analysis_tasks SET lease_expires_at=?, updated_at=?
                   WHERE task_id=? AND status='running' AND cancel_requested=0 AND worker_id=?""",
                (lease_expires_at, now, task_id, worker_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.execute(
                """UPDATE analysis_segments SET lease_expires_at=?, updated_at=?
                   WHERE task_id=? AND status='running'""",
                (lease_expires_at, now, task_id),
            )
            connection.commit()
            return True

    def complete(self, task_id: str, artifacts: AnalysisArtifacts, *, worker_id: str) -> None:
        task = self.get(task_id)
        if task is None:
            return
        if task.status != "running" or task.worker_id != worker_id:
            raise RuntimeError("任务租约已变化，拒绝旧工作线程提交结果")
        if datetime.fromisoformat(task.deadline_at.replace("Z", "+00:00")) <= datetime.now(UTC):
            raise AnalysisProcessingError(
                "任务超过最长可恢复期限",
                diagnostic_code="task_deadline_exceeded",
            )
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE analysis_tasks SET status='succeeded', current_step='completed', progress=100,
                   ocr_result_path=?, result_path=?, error=NULL, diagnostic_code=NULL, current_segment=NULL,
                   recovering=0, next_retry_at=NULL, worker_id=NULL, lease_expires_at=NULL,
                   last_checkpoint_at=?, updated_at=?
                   WHERE task_id=? AND status='running' AND cancel_requested=0 AND worker_id=?""",
                (
                    str(artifacts.ocr_result_path), str(artifacts.report_path), _now_iso(), _now_iso(),
                    task_id, worker_id,
                ),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("分析任务完成时状态已发生变化")

    def fail(self, task_id: str, error: Exception, *, worker_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_tasks WHERE task_id=? AND status='running' AND worker_id=?",
                (task_id, worker_id),
            ).fetchone()
        if row is None:
            return
        task = self._row_to_record(row)
        if task.status == "cancelled" or task.cancel_requested:
            return
        safe_error = str(error).strip().replace("\r", " ").replace("\n", " ")[:500] or "未知错误"
        with self._connect() as connection:
            segment_state = connection.execute(
                """SELECT
                       SUM(CASE WHEN status='retry_wait' THEN 1 ELSE 0 END) retrying,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed
                   FROM analysis_segments WHERE task_id=?""",
                (task_id,),
            ).fetchone()
        retrying_segment = bool(segment_state and int(segment_state["retrying"] or 0))
        failed_segment = bool(segment_state and int(segment_state["failed"] or 0))
        summary_failure = (
            task.segment_total > 0
            and task.segment_completed == task.segment_total
            and task.current_step in {"summarizing", "validate", "persist"}
        )
        if failed_segment:
            can_retry = False
            safe_error = task.error or safe_error
            diagnostic_code = task.diagnostic_code or getattr(error, "diagnostic_code", None)
        elif retrying_segment:
            can_retry = bool(getattr(error, "retryable", True))
            diagnostic_code = getattr(error, "diagnostic_code", None)
        elif summary_failure:
            can_retry = (
                bool(getattr(error, "retryable", True))
                and task.summary_attempt_count + 1 < task.max_attempts
                and task.attempt_count < task.max_attempts
            )
            diagnostic_code = getattr(error, "diagnostic_code", None)
        else:
            can_retry = bool(getattr(error, "retryable", True)) and task.attempt_count < task.max_attempts
            diagnostic_code = getattr(error, "diagnostic_code", None)
        now = datetime.now(UTC)
        supplied_delay = getattr(error, "retry_after_seconds", None)
        delay = (
            self._retry_delay(
                attempt_count=(task.summary_attempt_count + 1 if summary_failure else task.attempt_count),
                retry_after_seconds=supplied_delay,
                accumulated_wait_seconds=task.retry_wait_seconds,
                wait_limit_seconds=self.task_retry_wait_limit_seconds,
            )
            if can_retry else 0.0
        )
        deadline = datetime.fromisoformat(task.deadline_at.replace("Z", "+00:00"))
        if can_retry and delay is None:
            can_retry = False
            diagnostic_code = "retry_wait_budget_exhausted"
            safe_error = "任务累计重试等待已达安全上限"
            delay_seconds = 0.0
        elif can_retry and now + timedelta(seconds=delay) >= deadline:
            can_retry = False
            diagnostic_code = "task_deadline_exceeded"
            safe_error = "任务将超过最长可恢复期限"
            delay_seconds = 0.0
        else:
            delay_seconds = float(delay or 0.0)
        available_at = now + timedelta(seconds=delay_seconds if can_retry else 0)
        retry_step = "summarizing" if summary_failure else "queued"
        with self._connect() as connection:
            connection.execute(
                """UPDATE analysis_tasks SET status=?, current_step=?, progress=?, error=?, diagnostic_code=?,
                   available_at=?, next_retry_at=?, retry_wait_seconds=retry_wait_seconds+?,
                   summary_attempt_count=summary_attempt_count+?,
                   retry_count=retry_count+?,
                   current_segment=NULL, worker_id=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE task_id=? AND status='running' AND worker_id=?""",
                (
                    "retry_wait" if can_retry else "failed", retry_step if can_retry else "failed",
                    max(task.progress, 5 if task.segment_completed else 0) if can_retry else task.progress,
                    safe_error, diagnostic_code,
                    available_at.isoformat().replace("+00:00", "Z"),
                    available_at.isoformat().replace("+00:00", "Z") if can_retry else None,
                    delay_seconds if can_retry else 0.0,
                    1 if summary_failure else 0,
                    1 if can_retry else 0,
                    now.isoformat().replace("+00:00", "Z"), task_id, worker_id,
                ),
            )

    @staticmethod
    def _segment_from_row(row: sqlite3.Row) -> AnalysisSegmentRecord:
        return AnalysisSegmentRecord(
            task_id=row["task_id"],
            segment_index=row["segment_index"],
            segment_key=row["segment_key"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            status=row["status"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            next_retry_at=row["next_retry_at"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            result_path=row["result_path"],
            checkpoint_identity=row["checkpoint_identity"],
            error=row["error"],
            outcome=row["outcome"],
            provider_request_id=row["provider_request_id"],
            updated_at=row["updated_at"],
            retry_wait_seconds=float(row["retry_wait_seconds"] or 0),
        )

    def prepare_segments(self, task_id: str, segments: list[Mapping[str, Any]]) -> list[AnalysisSegmentRecord]:
        if not segments:
            raise ValueError("分析任务至少需要一个片段")
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT idempotency_key FROM analysis_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if task is None:
                connection.rollback()
                raise LookupError("找不到这个分析任务")
            existing = connection.execute(
                "SELECT * FROM analysis_segments WHERE task_id=? ORDER BY segment_index",
                (task_id,),
            ).fetchall()
            if existing:
                supplied = [
                    (
                        int(item["segment_index"]),
                        str(item["segment_key"]),
                        int(item["start_ms"]),
                        int(item["end_ms"]),
                    )
                    for item in segments
                ]
                persisted = [
                    (row["segment_index"], row["segment_key"], row["start_ms"], row["end_ms"])
                    for row in existing
                ]
                if supplied != persisted:
                    connection.rollback()
                    raise RuntimeError("持久化片段计划与当前输入不一致")
                connection.commit()
                return [self._segment_from_row(row) for row in existing]
            for expected_index, item in enumerate(segments):
                index = int(item["segment_index"])
                start_ms = int(item["start_ms"])
                end_ms = int(item["end_ms"])
                if index != expected_index or start_ms < 0 or end_ms <= start_ms:
                    connection.rollback()
                    raise ValueError("片段顺序或时间范围无效")
                key = str(item.get("segment_key") or "")
                if not key:
                    key = hashlib.sha256(
                        f"{task['idempotency_key']}:{index}:{start_ms}:{end_ms}".encode("utf-8")
                    ).hexdigest()
                connection.execute(
                    """INSERT INTO analysis_segments (
                           task_id, segment_index, segment_key, start_ms, end_ms, status,
                           attempt_count, max_attempts, available_at, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'pending', 0, 4, ?, ?, ?)""",
                    (task_id, index, key, start_ms, end_ms, now, now, now),
                )
            connection.execute(
                """UPDATE analysis_tasks SET segment_total=?, segment_completed=0,
                   current_step='segmenting', updated_at=? WHERE task_id=?""",
                (len(segments), now, task_id),
            )
            connection.commit()
        return self.list_segments(task_id)

    def list_segments(self, task_id: str) -> list[AnalysisSegmentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM analysis_segments WHERE task_id=? ORDER BY segment_index",
                (task_id,),
            ).fetchall()
        return [self._segment_from_row(row) for row in rows]

    def claim_segment(
        self,
        task_id: str,
        *,
        worker_id: str,
        task_worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> AnalysisSegmentRecord | None:
        lease_seconds = max(10, min(lease_seconds, 900))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _now_iso()
            task = connection.execute(
                "SELECT status, cancel_requested, worker_id FROM analysis_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if task is None or bool(task["cancel_requested"]) or task["status"] == "cancelled":
                connection.commit()
                return None
            if task_worker_id is not None and task["worker_id"] != task_worker_id:
                connection.commit()
                return None
            connection.execute(
                """UPDATE analysis_segments SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                   available_at=?, next_retry_at=NULL, updated_at=?
                   WHERE task_id=? AND status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?""",
                (now, now, task_id, now),
            )
            active = connection.execute(
                "SELECT 1 FROM analysis_segments WHERE task_id=? AND status='running' LIMIT 1",
                (task_id,),
            ).fetchone()
            if active is not None:
                connection.commit()
                return None
            row = connection.execute(
                """SELECT * FROM analysis_segments WHERE task_id=?
                   AND status IN ('pending','retry_wait') AND attempt_count < max_attempts
                   AND available_at <= ? ORDER BY segment_index LIMIT 1""",
                (task_id, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            lease_expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
            cursor = connection.execute(
                """UPDATE analysis_segments SET status='running', attempt_count=attempt_count+1,
                   lease_owner=?, lease_expires_at=?, next_retry_at=NULL, error=NULL, outcome=NULL, updated_at=?
                   WHERE task_id=? AND segment_index=? AND status IN ('pending','retry_wait')""",
                (worker_id, lease_expires, now, task_id, row["segment_index"]),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.execute(
                """UPDATE analysis_tasks SET current_step='segment', current_segment=?,
                   recovering=0, updated_at=? WHERE task_id=?""",
                (row["segment_index"], now, task_id),
            )
            connection.commit()
            refreshed = connection.execute(
                "SELECT * FROM analysis_segments WHERE task_id=? AND segment_index=?",
                (task_id, row["segment_index"]),
            ).fetchone()
            assert refreshed is not None
            return self._segment_from_row(refreshed)
        finally:
            connection.close()

    def complete_segment(
        self,
        task_id: str,
        segment_index: int,
        *,
        worker_id: str,
        result_path: str | Path,
        provider_request_id: str | None = None,
        task_worker_id: str | None = None,
    ) -> None:
        resolved = Path(result_path).expanduser().resolve()
        if not resolved.is_file():
            raise RuntimeError("片段结果文件不存在，不能提交检查点")
        checkpoint_identity = f"checkpoint_{uuid4().hex}"
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task_row = connection.execute(
                "SELECT workspace_path, status, worker_id FROM analysis_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                connection.rollback()
                raise LookupError("找不到片段所属的分析任务")
            workspace = Path(task_row["workspace_path"]).expanduser().resolve()
            if not resolved.is_relative_to(workspace):
                connection.rollback()
                raise RuntimeError("片段结果不在任务工作区内，拒绝提交检查点")
            if task_worker_id is not None and (
                task_row["status"] != "running" or task_row["worker_id"] != task_worker_id
            ):
                connection.rollback()
                raise RuntimeError("任务租约已变化，拒绝旧工作线程提交片段")
            cursor = connection.execute(
                """UPDATE analysis_segments SET status='succeeded', result_path=?, checkpoint_identity=?, error=NULL,
                   outcome='completed', provider_request_id=?, next_retry_at=NULL,
                   lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE task_id=? AND segment_index=? AND status='running' AND lease_owner=?""",
                (
                    str(resolved),
                    checkpoint_identity,
                    provider_request_id,
                    now,
                    task_id,
                    segment_index,
                    worker_id,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("片段完成时租约已变更，拒绝重复提交")
            aggregate = connection.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) completed
                   FROM analysis_segments WHERE task_id=?""",
                (task_id,),
            ).fetchone()
            total = int(aggregate["total"] or 0)
            completed = int(aggregate["completed"] or 0)
            progress = 35 + round(43 * completed / max(1, total))
            connection.execute(
                """UPDATE analysis_tasks SET segment_total=?, segment_completed=?, current_segment=NULL,
                   last_completed_segment=?, last_checkpoint_at=?, progress=MAX(progress, ?), updated_at=?
                   WHERE task_id=?""",
                (total, completed, segment_index, now, progress, now, task_id),
            )
            connection.commit()

    def reject_segment_checkpoint(
        self,
        task_id: str,
        segment_index: int,
        *,
        reason: str,
        expected_result_path: str | Path,
        expected_provider_request_id: str | None,
        expected_updated_at: str,
        expected_checkpoint_identity: str,
        diagnostic_code: str,
        task_worker_id: str,
    ) -> AnalysisSegmentRecord:
        """Atomically revoke an invalid succeeded checkpoint without losing retry history."""

        safe_reason = reason.strip().replace("\r", " ").replace("\n", " ")[:500] or "检查点合同无效"
        safe_diagnostic_code = diagnostic_code.strip()[:100] or "model_output_invalid"
        expected_path = str(Path(expected_result_path).expanduser().resolve())
        if not task_worker_id:
            raise RuntimeError("任务租约身份缺失，拒绝无所有者隔离的检查点作废请求")
        if not expected_checkpoint_identity:
            raise RuntimeError("检查点身份缺失，拒绝作废无法确认代际的结果")
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task_row = connection.execute(
                "SELECT status, cancel_requested, worker_id FROM analysis_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                connection.rollback()
                raise LookupError("找不到需要作废检查点的分析任务")
            if task_row["status"] != "running" or bool(task_row["cancel_requested"]):
                connection.rollback()
                raise RuntimeError("任务已取消或执行状态已变化，拒绝作废检查点")
            if task_row["worker_id"] != task_worker_id:
                connection.rollback()
                raise RuntimeError("任务租约已变化，拒绝旧工作线程作废检查点")
            row = connection.execute(
                "SELECT * FROM analysis_segments WHERE task_id=? AND segment_index=?",
                (task_id, segment_index),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise LookupError("找不到需要作废的分析片段")
            if row["status"] != "succeeded":
                connection.rollback()
                raise RuntimeError("只有已成功提交的检查点可以被合同校验作废")
            if (
                row["result_path"] != expected_path
                or row["provider_request_id"] != expected_provider_request_id
                or row["updated_at"] != expected_updated_at
                or row["checkpoint_identity"] != expected_checkpoint_identity
            ):
                connection.rollback()
                raise RuntimeError("检查点版本已变化，拒绝用旧验证结果作废新检查点")

            can_retry = int(row["attempt_count"]) < int(row["max_attempts"])
            status = "retry_wait" if can_retry else "failed"
            outcome = "retryable_failure" if can_retry else "permanent_failure"
            cursor = connection.execute(
                """UPDATE analysis_segments SET status=?, available_at=?, next_retry_at=?,
                   lease_owner=NULL, lease_expires_at=NULL, result_path=NULL,
                   checkpoint_identity=NULL, provider_request_id=NULL, error=?, outcome=?, updated_at=?
                   WHERE task_id=? AND segment_index=? AND status='succeeded'
                      AND result_path=? AND provider_request_id IS ? AND updated_at=?
                      AND checkpoint_identity=?""",
                (
                    status,
                    now,
                    now if can_retry else None,
                    safe_reason,
                    outcome,
                    now,
                    task_id,
                    segment_index,
                    expected_path,
                    expected_provider_request_id,
                    expected_updated_at,
                    expected_checkpoint_identity,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("检查点版本已变化，未作废任何结果")
            aggregate = connection.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) completed,
                          MAX(CASE WHEN status='succeeded' THEN segment_index END) last_completed,
                          MAX(CASE WHEN status='succeeded' THEN updated_at END) last_checkpoint_at
                   FROM analysis_segments WHERE task_id=?""",
                (task_id,),
            ).fetchone()
            total = int(aggregate["total"] or 0)
            completed = int(aggregate["completed"] or 0)
            progress = 35 + round(43 * completed / max(1, total))
            task_cursor = connection.execute(
                """UPDATE analysis_tasks SET segment_total=?, segment_completed=?,
                   current_segment=NULL, last_completed_segment=?, last_checkpoint_at=?, current_step='segment',
                   progress=?, error=?, diagnostic_code=?, updated_at=?
                   WHERE task_id=? AND status='running' AND cancel_requested=0 AND worker_id=?""",
                (
                    total,
                    completed,
                    aggregate["last_completed"],
                    aggregate["last_checkpoint_at"],
                    progress,
                    safe_reason,
                    safe_diagnostic_code,
                    now,
                    task_id,
                    task_worker_id,
                ),
            )
            if task_cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("任务状态已变化，未作废任何结果")
            connection.commit()
            refreshed = connection.execute(
                "SELECT * FROM analysis_segments WHERE task_id=? AND segment_index=?",
                (task_id, segment_index),
            ).fetchone()
            assert refreshed is not None
            return self._segment_from_row(refreshed)

    def fail_segment(
        self,
        task_id: str,
        segment_index: int,
        *,
        worker_id: str,
        error: Exception,
        task_worker_id: str | None = None,
    ) -> None:
        safe_error = str(error).strip().replace("\r", " ").replace("\n", " ")[:500] or "未知错误"
        now_dt = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if task_worker_id is not None:
                task_row = connection.execute(
                    "SELECT status, worker_id FROM analysis_tasks WHERE task_id=?",
                    (task_id,),
                ).fetchone()
                if (
                    task_row is None
                    or task_row["status"] != "running"
                    or task_row["worker_id"] != task_worker_id
                ):
                    connection.rollback()
                    return
            row = connection.execute(
                "SELECT * FROM analysis_segments WHERE task_id=? AND segment_index=?",
                (task_id, segment_index),
            ).fetchone()
            if row is None or row["status"] != "running" or row["lease_owner"] != worker_id:
                connection.rollback()
                return
            retryable = bool(getattr(error, "retryable", True)) and row["attempt_count"] < row["max_attempts"]
            retry_after = float(getattr(error, "retry_after_seconds", 0) or 0)
            computed_delay = self._retry_delay(
                attempt_count=int(row["attempt_count"]),
                retry_after_seconds=retry_after,
                accumulated_wait_seconds=float(row["retry_wait_seconds"] or 0),
                wait_limit_seconds=self.segment_retry_wait_limit_seconds,
            ) if retryable else None
            if retryable and computed_delay is None:
                retryable = False
                safe_error = "该片段累计重试等待已达安全上限"
                outcome = "permanent_failure"
                diagnostic_code = "segment_retry_wait_budget_exhausted"
            else:
                outcome = getattr(error, "outcome", "retryable_failure" if retryable else "permanent_failure")
                diagnostic_code = getattr(error, "diagnostic_code", None)
            delay = computed_delay if retryable and computed_delay is not None else 0.0
            available = (now_dt + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
            status = "retry_wait" if retryable else "failed"
            connection.execute(
                """UPDATE analysis_segments SET status=?, available_at=?, next_retry_at=?,
                   lease_owner=NULL, lease_expires_at=NULL, error=?, outcome=?,
                   retry_wait_seconds=retry_wait_seconds+?, updated_at=?
                   WHERE task_id=? AND segment_index=?""",
                (
                    status,
                    available,
                    available if retryable else None,
                    safe_error,
                    outcome,
                    delay if retryable else 0.0,
                    now_dt.isoformat().replace("+00:00", "Z"),
                    task_id,
                    segment_index,
                ),
            )
            connection.execute(
                """UPDATE analysis_tasks SET current_segment=NULL, error=?, diagnostic_code=?,
                   next_retry_at=?, updated_at=? WHERE task_id=?""",
                (
                    safe_error,
                    diagnostic_code,
                    available if retryable else None,
                    now_dt.isoformat().replace("+00:00", "Z"),
                    task_id,
                ),
            )
            connection.commit()

    def cancel(self, task_id: str) -> AnalysisTaskRecord:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM analysis_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise LookupError("找不到这个分析任务")
            if row["status"] in {"completed", "succeeded"}:
                connection.rollback()
                raise ValueError("已完成的分析任务不能取消")
            connection.execute(
                """UPDATE analysis_tasks SET status='cancelled', current_step='cancelled',
                   cancel_requested=1, current_segment=NULL, next_retry_at=NULL,
                   worker_id=NULL, lease_expires_at=NULL, error=NULL, updated_at=? WHERE task_id=?""",
                (now, task_id),
            )
            connection.execute(
                """UPDATE analysis_segments SET status='cancelled',
                   lease_owner=NULL, lease_expires_at=NULL, next_retry_at=NULL, updated_at=?
                   WHERE task_id=? AND status<>'succeeded'""",
                (now, task_id),
            )
            connection.commit()
        record = self.get(task_id)
        assert record is not None
        return record

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested, status FROM analysis_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return row is None or bool(row["cancel_requested"]) or row["status"] == "cancelled"

    def next_retry_delay(self) -> float | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT MIN(COALESCE(available_at, created_at)) AS available_at
                   FROM analysis_tasks
                   WHERE status='retry_wait'
                     AND (
                       (
                         current_step='summarizing'
                         AND summary_attempt_count < max_attempts
                         AND attempt_count < max_attempts
                       )
                       OR (
                         current_step<>'summarizing'
                         AND (
                           attempt_count < max_attempts
                           OR EXISTS (
                             SELECT 1 FROM analysis_segments s
                             WHERE s.task_id=analysis_tasks.task_id
                               AND s.status IN ('pending','retry_wait')
                               AND s.attempt_count < s.max_attempts
                           )
                         )
                       )
                     )"""
            ).fetchone()
        value = row["available_at"] if row else None
        if not value:
            return None
        try:
            available_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        return max(0.0, (available_at - datetime.now(UTC)).total_seconds())

    def run_pending(
        self,
        *,
        gateway_config: GatewayConfig,
        max_workers: int = 2,
        processor: TaskProcessor | None = None,
    ) -> list[AnalysisTaskRecord]:
        worker_count = max(1, min(max_workers, 2))
        processed_ids: set[str] = set()

        def default_processor(task: AnalysisTaskRecord, callback: Callable[[str, int], None]) -> AnalysisArtifacts:
            payload = json.loads(Path(task.input_path).read_text(encoding="utf-8"))
            snapshot = payload.pop("_gateway_model_snapshot", None)
            purpose = payload.pop("_gateway_purpose", task.model_purpose)
            if not isinstance(purpose, str) or purpose not in {"analysis", "video_review"}:
                raise GatewaySettingsError("分析任务的模型用途已损坏")
            if purpose != task.model_purpose:
                raise GatewaySettingsError("分析任务的模型用途与持久化记录不一致")
            pinned_model_id = snapshot.get("model_id") if isinstance(snapshot, dict) else payload.pop("_gateway_model_id", None)
            task_config = (
                gateway_config.for_model_purpose(pinned_model_id, purpose)
                if isinstance(pinned_model_id, str)
                else gateway_config.for_purpose(purpose)
            )
            if isinstance(snapshot, dict) and not task_config.matches_snapshot(snapshot):
                raise GatewaySettingsError("任务绑定的模型配置已变更，请明确重新创建任务后再运行")
            return process_analysis(
                media_result_path=task.media_result_path,
                input_payload=payload,
                task_directory=task.workspace_path,
                config=task_config,
                purpose=purpose,
                progress=callback,
                task_id=task.task_id,
                task_worker_id=task.worker_id,
                trace_id=task.trace_id,
                checkpoint_store=self,
            )

        execute = processor or default_processor

        def submit_next(pool: ThreadPoolExecutor, active: dict[Future[AnalysisArtifacts], AnalysisTaskRecord]) -> bool:
            task = self.claim_next()
            if task is None:
                return False
            if not task.worker_id:
                raise RuntimeError("任务领取后缺少租约标识")
            processed_ids.add(task.task_id)
            callback = lambda step, value, task=task: self.update_progress(
                task.task_id,
                step,
                value,
                worker_id=task.worker_id or "",
            )
            active[pool.submit(execute, task, callback)] = task
            return True

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="analysis-s3") as pool:
            active: dict[Future[AnalysisArtifacts], AnalysisTaskRecord] = {}
            while True:
                while len(active) < worker_count and submit_next(pool, active):
                    pass
                if active:
                    completed, _ = wait(active, timeout=30.0, return_when=FIRST_COMPLETED)
                    # A completed short task must not starve the heartbeat of a
                    # still-running long task. Renew every unfinished owner
                    # before this loop can call claim_next() again.
                    for future, task in active.items():
                        if future not in completed and task.worker_id:
                            self.renew_task_lease(task.task_id, worker_id=task.worker_id)
                    if not completed:
                        continue
                    for future in completed:
                        task = active.pop(future)
                        worker_id = task.worker_id or ""
                        try:
                            self.complete(task.task_id, future.result(), worker_id=worker_id)
                        except Exception as exc:
                            self.fail(task.task_id, exc, worker_id=worker_id)
                    continue
                retry_delay = self.next_retry_delay()
                if retry_delay is None:
                    break
                time.sleep(max(0.01, min(retry_delay, 30.0)))
        return [task for task_id in processed_ids if (task := self.get(task_id)) is not None]
