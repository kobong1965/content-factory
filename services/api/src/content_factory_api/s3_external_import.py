"""Local administrative import of externally reviewed, source-bound reports.

No network entry point and no model invocation. Completed rows become visible
only after all immutable evidence files have been persisted. Existing queue,
report, candidate and Skill validation remains authoritative downstream.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from content_factory_contracts import validate_or_raise

from .s3_queue import AnalysisTaskQueue, AnalysisTaskRecord, _now_iso


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _persist_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"Imported evidence changed; refusing overwrite: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write fully, then publish with a non-overwriting rename on Windows. A
    # interrupted pre-commit leaves only unreferenced files, reusable on retry.
    from uuid import uuid4
    temporary = path.with_name(path.name + "." + uuid4().hex + ".partial")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        import os
        os.fsync(handle.fileno())
    try:
        temporary.rename(path)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError(f"Imported evidence changed concurrently: {path}")
        temporary.unlink()


def import_reviewed_case(
    queue: AnalysisTaskQueue, *, report_path: Path, media_result_path: Path, workspace: Path,
) -> AnalysisTaskRecord:
    """Register one already-authorized external review, never approve its claims.

    Callers must have explicit user authority to import these case results.
    Approval as a reusable Skill remains a separate operation in ViralSkillStore.
    The managed source must already exist (this function never imports media).
    """
    report_path = Path(report_path).resolve(strict=True)
    media_result_path = Path(media_result_path).resolve(strict=True)
    workspace = Path(workspace).resolve()
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    validate_or_raise("analysis", report)
    if (report["fixture_data"] or report["status"] != "accepted"
            or report["processing"]["api_mode"] != "external_review"
            or report["processing"].get("purpose", "analysis") != "analysis"):
        raise ValueError("Only authorized non-fixture external analysis reviews can be imported")
    if not report["review"].get("reviewer") or not report["review"].get("note"):
        raise ValueError("External review requires explicit reviewer and scope note")
    media = json.loads(media_result_path.read_text(encoding="utf-8"))
    original = Path(media["source"]["managed_original_path"]).resolve(strict=True)
    digest = _digest(original)
    if media.get("fixture_data") or digest != media["source"]["sha256"]:
        raise ValueError("Managed original hash mismatch")
    if report["video_id"] != "video_" + digest[:24]:
        raise ValueError("External report is not bound to this media hash")
    import_key = hashlib.sha256(b"external-review-v1:" + digest.encode() + report_bytes).hexdigest()
    task_id = "analysis_task_" + import_key[:32]
    target = workspace / "tasks" / task_id
    if not target.resolve().is_relative_to(workspace):
        raise ValueError("Import directory escapes workspace")
    files: list[tuple[Path, bytes]] = []
    for index, frame in enumerate(report["keyframes"]):
        source = Path(frame["local_path"]).resolve(strict=True)
        if not source.is_relative_to(report_path.parent):
            raise ValueError("Evidence frame escapes case package")
        destination = target / "keyframes" / f"frame-{index:04d}{source.suffix.lower()}"
        files.append((destination, source.read_bytes()))
        frame["local_path"] = str(destination)
    # The real original is already managed by S2. Keep its original filename
    # as source_uri for human-readable attribution; the input freezes its hash.
    result_path = target / "analysis-report.json"
    input_path = target / "input.json"
    files.extend([
        (result_path, _encode(report)),
        (input_path, _encode({"external_review": True, "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
                              "source_sha256": digest, "source_package": str(report_path.parent),
                              "review_scope": report["review"], "metric_snapshots": [], "comments": []})),
    ])
    validate_or_raise("analysis", report)
    # One short transaction prevents concurrent importers from interleaving.
    # All file bytes are prepared before taking the database lock.
    with queue._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT * FROM analysis_tasks WHERE task_id=?", (task_id,)).fetchone()
        for destination, content in files:
            _persist_immutable(destination, content)
        if existing is not None:
            if existing["result_path"] != str(result_path):
                raise ValueError("Existing imported task path changed")
            return queue._row_to_record(existing)
        now = _now_iso()
        connection.execute(
            """INSERT INTO analysis_tasks (
                task_id, fixture_data, media_task_id, model_purpose, status, progress, current_step, attempt_count,
                max_attempts, metric_count, comment_count, ocr_result_path, result_path, error,
                media_result_path, input_path, workspace_path, created_at, updated_at, available_at,
                trace_id, idempotency_key, segment_total, segment_completed, recovering, cancel_requested,
                retry_wait_seconds, deadline_at, summary_attempt_count, retry_count
            ) VALUES (?, 0, ?, 'analysis', 'succeeded', 100, 'completed', 0,
                      1, 0, 0, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?, 0, 0)""",
            (task_id, media["task_id"], str(result_path), str(media_result_path), str(input_path), str(target),
             now, now, now, "trace_" + import_key[32:], import_key, now),
        )
    record = queue.get(task_id)
    assert record is not None
    record.to_dict()
    return record
