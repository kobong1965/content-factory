from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from content_factory_api.s3_analysis import AnalysisArtifacts
from content_factory_api.s3_gateway import GatewayError
from content_factory_api.s3_queue import AnalysisTaskQueue
from content_factory_api.s3_reports import ReportConflictError, review_report, update_report
from content_factory_api.s3_settings import GatewayConfig


CONFIG = GatewayConfig("http://127.0.0.1:9999/v1", "fixture-model", "responses", "fixture-secret", "2026-08-29T06:00:00Z")


def _enqueue(queue: AnalysisTaskQueue, tmp_path: Path, index: int = 1):
    media_result = tmp_path / f"media-{index}.json"
    media_result.write_text("{}", encoding="utf-8")
    return queue.enqueue(
        media_task_id=f"media_{index:032x}", media_result_path=media_result,
        workspace_path=tmp_path / "analysis", input_payload={"metric_snapshots": [], "comments": []}, fixture_data=True,
    )


def _artifacts(task, _callback) -> AnalysisArtifacts:
    task_dir = Path(task.workspace_path)
    task_dir.mkdir(parents=True, exist_ok=True)
    report = task_dir / "analysis-report.json"
    ocr = task_dir / "ocr-result.json"
    report.touch(); ocr.touch()
    return AnalysisArtifacts("analysis_fixture", report, ocr)


def test_analysis_queue_limits_cloud_concurrency_to_two(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    for index in range(1, 5):
        _enqueue(queue, tmp_path, index)
    lock = threading.Lock()
    active = 0
    peak = 0

    def processor(task, callback):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        callback("gateway", 55)
        time.sleep(0.03)
        with lock:
            active -= 1
        return _artifacts(task, callback)

    queue.run_pending(gateway_config=CONFIG, max_workers=9, processor=processor)

    assert peak == 2
    assert queue.counts()["succeeded"] == 4


def test_retryable_gateway_failure_is_retried_once(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.02)
    task = _enqueue(queue, tmp_path)
    calls = 0

    def processor(record, callback):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise GatewayError("限流", retryable=True, status_code=429)
        return _artifacts(record, callback)

    started = time.monotonic()
    queue.run_pending(gateway_config=CONFIG, processor=processor)

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert completed.attempt_count == 2
    assert time.monotonic() - started >= 0.015


def test_non_retryable_key_failure_stops_immediately(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=lambda _task, _callback: (_ for _ in ()).throw(GatewayError("密钥无效", retryable=False, status_code=401)),
    )

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.attempt_count == 1


def test_failed_analysis_can_be_manually_retried_after_configuration_fix(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    queue.run_pending(
        gateway_config=CONFIG,
        processor=lambda _task, _callback: (_ for _ in ()).throw(
            GatewayError("格式不兼容", retryable=False, status_code=400)
        ),
    )

    retried = queue.retry(task.task_id)

    assert retried.status == "pending"
    assert retried.current_step == "queued"
    assert retried.progress == 0
    assert retried.error is None
    assert retried.attempt_count == 1
    assert retried.max_attempts == 3


def test_report_revision_and_review_use_optimistic_lock(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures" / "analysis.valid.json"
    report_path = tmp_path / "analysis-report.json"
    shutil.copy2(fixture, report_path)
    current = json.loads(report_path.read_text(encoding="utf-8"))
    changes = {"summary": {**current["summary"], "video_type": "brand_content"}}

    updated = update_report(report_path, expected_revision=1, changes=changes)
    reviewed = review_report(report_path, expected_revision=2, status="reviewed", reviewer="运营甲", note="证据已复核")

    assert updated["revision"] == 2 and updated["status"] == "draft"
    assert reviewed["revision"] == 3 and reviewed["review"]["reviewer"] == "运营甲"
    assert (tmp_path / "revisions" / "analysis-report-r0001.json").is_file()
    with pytest.raises(ReportConflictError):
        update_report(report_path, expected_revision=1, changes=changes)


def test_existing_s3_database_is_migrated_for_retry_schedule(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    AnalysisTaskQueue(database)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE analysis_tasks DROP COLUMN available_at")

    AnalysisTaskQueue(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(analysis_tasks)")}
    assert "available_at" in columns
