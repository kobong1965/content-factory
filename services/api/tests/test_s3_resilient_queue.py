from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import content_factory_api.s3_queue as s3_queue_module
from content_factory_api.s3_gateway import GatewayError
from content_factory_api.s3_analysis import AnalysisArtifacts, _segment_attempt_checkpoint_path
from content_factory_api.s3_queue import AnalysisTaskQueue


def _enqueue(queue: AnalysisTaskQueue, tmp_path: Path):
    media_result = tmp_path / "media-result.json"
    media_result.write_text(
        json.dumps({"source": {"sha256": "a" * 64}}),
        encoding="utf-8",
    )
    return queue.enqueue(
        media_task_id="media_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        media_result_path=media_result,
        workspace_path=tmp_path / "analysis",
        input_payload={
            "metric_snapshots": [],
            "comments": [],
            "_gateway_purpose": "analysis",
            "_gateway_model_snapshot": {
                "model_id": "fixture",
                "model": "fixture-model",
                "provider": "openai_compatible",
                "api_mode": "chat_completions",
            },
        },
        fixture_data=True,
    )


def _segments() -> list[dict[str, int | str]]:
    return [
        {"segment_index": 0, "start_ms": 0, "end_ms": 60_000, "segment_key": "segment-a"},
        {"segment_index": 1, "start_ms": 60_000, "end_ms": 120_000, "segment_key": "segment-b"},
    ]


def test_duplicate_submission_reuses_the_same_active_job(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")

    first = _enqueue(queue, tmp_path)
    second = _enqueue(queue, tmp_path)

    assert second.task_id == first.task_id
    assert second.idempotency_key == first.idempotency_key
    assert len(queue.list()) == 1


def test_segment_claim_is_atomic_and_completed_checkpoint_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = AnalysisTaskQueue(database)
    task = _enqueue(queue, tmp_path)
    queue.prepare_segments(task.task_id, _segments())

    first = queue.claim_segment(task.task_id, worker_id="worker-a", lease_seconds=60)
    competing = queue.claim_segment(task.task_id, worker_id="worker-b", lease_seconds=60)

    assert first is not None and first.segment_index == 0
    assert competing is None

    result_path = Path(task.workspace_path) / "segments" / "segment-0000.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text('{"ok": true}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        first.segment_index,
        worker_id="worker-a",
        result_path=result_path,
        provider_request_id="resp_1",
    )

    reopened = AnalysisTaskQueue(database)
    persisted = reopened.list_segments(task.task_id)
    assert persisted[0].status == "succeeded"
    assert persisted[0].result_path == str(result_path.resolve())
    assert persisted[0].provider_request_id == "resp_1"
    assert reopened.claim_segment(task.task_id, worker_id="worker-b", lease_seconds=60).segment_index == 1  # type: ignore[union-attr]


def test_retryable_segment_failure_preserves_prior_success_and_retry_after(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _enqueue(queue, tmp_path)
    queue.prepare_segments(task.task_id, _segments())
    first = queue.claim_segment(task.task_id, worker_id="worker", lease_seconds=60)
    assert first is not None
    first_result = Path(task.workspace_path) / "segments" / "segment-0000.json"
    first_result.parent.mkdir(parents=True, exist_ok=True)
    first_result.write_text("{}", encoding="utf-8")
    queue.complete_segment(task.task_id, 0, worker_id="worker", result_path=first_result)
    second = queue.claim_segment(task.task_id, worker_id="worker", lease_seconds=60)
    assert second is not None and second.segment_index == 1

    queue.fail_segment(
        task.task_id,
        1,
        worker_id="worker",
        error=GatewayError(
            "rate limited",
            retryable=True,
            status_code=429,
            diagnostic_code="rate_limited",
            retry_after_seconds=9,
        ),
    )

    segments = queue.list_segments(task.task_id)
    assert segments[0].status == "succeeded"
    assert segments[1].status == "retry_wait"
    assert segments[1].attempt_count == 1
    assert segments[1].next_retry_at is not None
    assert queue.get(task.task_id).last_completed_segment == 0  # type: ignore[union-attr]


def test_cancelled_job_is_never_recovered_or_claimed(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    queue.cancel(task.task_id)

    assert queue.get(task.task_id).status == "cancelled"  # type: ignore[union-attr]
    assert queue.recover_interrupted() == 0
    assert queue.claim_next() is None


def test_recovery_does_not_steal_an_unexpired_live_task_lease(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    claimed = queue.claim_next()
    assert claimed is not None and claimed.worker_id is not None

    assert queue.recover_interrupted() == 0
    still_owned = queue.get(task.task_id)
    assert still_owned is not None and still_owned.status == "running"
    assert still_owned.worker_id == claimed.worker_id
    assert queue.claim_next() is None


def test_retry_delay_uses_jitter_honours_retry_after_and_stops_at_wait_budget(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(
        tmp_path / "queue.sqlite3",
        retry_base_seconds=2,
        retry_jitter_ratio=0.2,
        retry_random=lambda: 0.0,
    )

    assert queue._retry_delay(
        attempt_count=1,
        retry_after_seconds=None,
        accumulated_wait_seconds=0,
        wait_limit_seconds=10,
    ) == pytest.approx(1.6)
    assert queue._retry_delay(
        attempt_count=1,
        retry_after_seconds=9,
        accumulated_wait_seconds=0,
        wait_limit_seconds=10,
    ) == 9
    assert queue._retry_delay(
        attempt_count=1,
        retry_after_seconds=9,
        accumulated_wait_seconds=2,
        wait_limit_seconds=10,
    ) is None


def test_segment_retry_wait_budget_exhaustion_becomes_explicit_failure(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(
        tmp_path / "queue.sqlite3",
        retry_base_seconds=2,
        retry_jitter_ratio=0,
        segment_retry_wait_limit_seconds=1,
    )
    task = _enqueue(queue, tmp_path)
    queue.prepare_segments(task.task_id, _segments())
    claimed = queue.claim_segment(task.task_id, worker_id="worker", lease_seconds=60)
    assert claimed is not None

    queue.fail_segment(
        task.task_id,
        claimed.segment_index,
        worker_id="worker",
        error=GatewayError("temporary", retryable=True),
    )

    segment = queue.list_segments(task.task_id)[0]
    assert segment.status == "failed"
    assert segment.outcome == "permanent_failure"
    assert queue.get(task.task_id).diagnostic_code == "segment_retry_wait_budget_exhausted"  # type: ignore[union-attr]


def test_expired_job_is_failed_instead_of_remaining_pending_forever(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET deadline_at=? WHERE task_id=?",
            (expired, task.task_id),
        )

    assert queue.claim_next() is None
    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.diagnostic_code == "task_deadline_exceeded"


def test_reject_segment_checkpoint_updates_segment_and_task_atomically(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None and claimed_task.task_id == task.task_id
    queue.prepare_segments(task.task_id, _segments())
    segment = queue.claim_segment(task.task_id, worker_id="worker")
    assert segment is not None and segment.segment_index == 0
    checkpoint = Path(task.workspace_path) / "segments" / "segment-0000.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text('{"content": {}}', encoding="utf-8")
    queue.complete_segment(
        task.task_id, 0, worker_id="worker", result_path=checkpoint,
        provider_request_id="invalid-provider-response",
    )
    completed_segment = queue.list_segments(task.task_id)[0]

    rejected = queue.reject_segment_checkpoint(
        task.task_id,
        0,
        reason="timeline boundary is unsafe",
        expected_result_path=checkpoint,
        expected_provider_request_id="invalid-provider-response",
        expected_updated_at=completed_segment.updated_at,
        expected_checkpoint_identity=completed_segment.checkpoint_identity,
        diagnostic_code="model_timeline_invalid",
        task_worker_id=claimed_task.worker_id,
    )

    assert rejected.status == "retry_wait"
    assert rejected.result_path is None
    assert rejected.provider_request_id is None
    refreshed = queue.get(task.task_id)
    assert refreshed is not None
    assert refreshed.segment_completed == 0
    assert refreshed.last_completed_segment is None
    assert refreshed.last_checkpoint_at is None
    assert refreshed.current_step == "segment"
    recalled = queue.claim_segment(task.task_id, worker_id="worker-2")
    assert recalled is not None and recalled.segment_index == 0
    assert recalled.attempt_count == 2


def test_manual_retry_grants_an_exhausted_segment_exactly_one_new_attempt(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    queue.prepare_segments(task.task_id, _segments())
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed' WHERE task_id=?",
            (task.task_id,),
        )
        connection.execute(
            "UPDATE analysis_segments SET status='failed', attempt_count=4, max_attempts=4 WHERE task_id=? AND segment_index=0",
            (task.task_id,),
        )

    retried = queue.retry(task.task_id)
    segment = queue.list_segments(task.task_id)[0]

    assert retried.status == "pending"
    assert segment.status == "pending"
    assert segment.attempt_count == 4
    assert segment.max_attempts == 5
    recalled = queue.claim_segment(task.task_id, worker_id="manual-retry")
    assert recalled is not None and recalled.attempt_count == 5


def test_concurrent_manual_retry_grants_only_one_budget_increment(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed', max_attempts=4 WHERE task_id=?",
            (task.task_id,),
        )

    def retry_once() -> str:
        try:
            queue.retry(task.task_id)
            return "accepted"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: retry_once(), range(2)))

    assert sorted(results) == ["accepted", "rejected"]
    refreshed = queue.get(task.task_id)
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.max_attempts == 5


def test_concurrent_manual_retry_caps_legacy_segment_budget_at_five(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    queue.prepare_segments(task.task_id, _segments())
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed', max_attempts=4 WHERE task_id=?",
            (task.task_id,),
        )
        connection.execute(
            """UPDATE analysis_segments SET status='failed', attempt_count=4, max_attempts=6
               WHERE task_id=? AND segment_index=0""",
            (task.task_id,),
        )

    barrier = threading.Barrier(2)

    def retry_once() -> str:
        barrier.wait(timeout=5)
        try:
            queue.retry(task.task_id)
            return "accepted"
        except (RuntimeError, ValueError):
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: retry_once(), range(2)))

    assert sorted(results) == ["accepted", "rejected"]
    refreshed = queue.get(task.task_id)
    segment = queue.list_segments(task.task_id)[0]
    assert refreshed is not None and refreshed.status == "pending"
    assert refreshed.max_attempts == 5
    assert segment.status == "pending"
    assert segment.attempt_count == 4
    assert segment.max_attempts == 5


def test_manual_retry_allows_unused_existing_five_attempt_budget(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    media_result = tmp_path / "media-result.json"
    media_result.write_text(json.dumps({"source": {"sha256": "f" * 64}}), encoding="utf-8")
    task = queue.enqueue(
        media_task_id="media_ffffffffffffffffffffffffffffffff",
        media_result_path=media_result,
        workspace_path=tmp_path / "analysis-five",
        input_payload={"metric_snapshots": [], "comments": []},
        fixture_data=True,
        max_attempts=5,
    )
    first_attempt = queue.claim_next()
    assert first_attempt is not None and first_attempt.worker_id is not None
    assert first_attempt.attempt_count == 1
    queue.fail(
        task.task_id,
        GatewayError("configuration fixed after failure", retryable=False),
        worker_id=first_attempt.worker_id,
    )

    retried = queue.retry(task.task_id)

    assert retried.status == "pending"
    assert retried.max_attempts == 5
    second_attempt = queue.claim_next()
    assert second_attempt is not None and second_attempt.task_id == task.task_id
    assert second_attempt.attempt_count == 2
    assert second_attempt.max_attempts == 5


def test_manual_retry_rejects_five_consumed_task_attempts(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            """UPDATE analysis_tasks SET status='failed', current_step='failed',
               attempt_count=5, max_attempts=5 WHERE task_id=?""",
            (task.task_id,),
        )

    with pytest.raises(ValueError, match="5 次安全上限"):
        queue.retry(task.task_id)

    unchanged = queue.get(task.task_id)
    assert unchanged is not None and unchanged.status == "failed"
    assert unchanged.attempt_count == 5
    assert unchanged.max_attempts == 5


def test_manual_retry_rolls_back_task_budget_when_segment_reset_fails(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    queue.prepare_segments(task.task_id, _segments())
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed', max_attempts=4 WHERE task_id=?",
            (task.task_id,),
        )
        connection.execute(
            """UPDATE analysis_segments SET status='failed', attempt_count=4, max_attempts=4
               WHERE task_id=? AND segment_index=0""",
            (task.task_id,),
        )
        connection.execute(
            """CREATE TRIGGER abort_manual_segment_retry
               BEFORE UPDATE OF status ON analysis_segments
               WHEN OLD.status='failed' AND NEW.status='pending'
               BEGIN SELECT RAISE(ABORT, 'forced segment reset failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced segment reset failure"):
        queue.retry(task.task_id)

    refreshed = queue.get(task.task_id)
    segment = queue.list_segments(task.task_id)[0]
    assert refreshed is not None and refreshed.status == "failed"
    assert refreshed.max_attempts == 4
    assert segment.status == "failed"
    assert segment.max_attempts == 4


def test_manual_retry_clears_stale_task_and_segment_leases(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    old_task_lease = queue.claim_next()
    assert old_task_lease is not None and old_task_lease.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    old_segment_lease = queue.claim_segment(
        task.task_id,
        worker_id="old-segment-worker",
        task_worker_id=old_task_lease.worker_id,
    )
    assert old_segment_lease is not None
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed' WHERE task_id=?",
            (task.task_id,),
        )

    retried = queue.retry(task.task_id)
    assert retried.status == "pending"
    assert retried.worker_id is None
    assert retried.lease_expires_at is None
    reset_segment = queue.list_segments(task.task_id)[0]
    assert reset_segment.status == "pending"
    assert reset_segment.lease_owner is None
    assert reset_segment.lease_expires_at is None

    new_task_lease = queue.claim_next()
    assert new_task_lease is not None and new_task_lease.worker_id is not None
    new_segment_lease = queue.claim_segment(
        task.task_id,
        worker_id="new-segment-worker",
        task_worker_id=new_task_lease.worker_id,
    )
    assert new_segment_lease is not None
    assert new_segment_lease.segment_index == 0
    assert new_segment_lease.attempt_count == old_segment_lease.attempt_count + 1


def test_manual_retry_after_summary_exhaustion_grants_exactly_one_summary_attempt(
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _enqueue(queue, tmp_path)
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            """UPDATE analysis_tasks SET status='failed', current_step='failed',
               segment_total=1, segment_completed=1, attempt_count=2,
               max_attempts=2, summary_attempt_count=2 WHERE task_id=?""",
            (task.task_id,),
        )

    retried = queue.retry(task.task_id)
    assert retried.current_step == "summarizing"
    assert retried.attempt_count == 2
    assert retried.summary_attempt_count == 2
    assert retried.max_attempts == 3

    claimed = queue.claim_next()
    assert claimed is not None and claimed.task_id == task.task_id
    assert claimed.attempt_count == 3
    assert claimed.worker_id is not None
    queue.update_progress(task.task_id, "summarizing", 80, worker_id=claimed.worker_id)
    queue.fail(
        task.task_id,
        GatewayError("summary still invalid", retryable=True),
        worker_id=claimed.worker_id,
    )

    exhausted = queue.get(task.task_id)
    assert exhausted is not None
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 3
    assert exhausted.summary_attempt_count == 3
    assert exhausted.max_attempts == 3
    exhausted.to_dict()
    assert queue.claim_next() is None


def test_reject_checkpoint_uses_generation_cas_and_preserves_newer_result(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None and claimed_task.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    segment = queue.claim_segment(task.task_id, worker_id="worker")
    assert segment is not None and segment.segment_index == 0
    old_checkpoint = Path(task.workspace_path) / "segments" / "segment-0000-old.json"
    old_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    old_checkpoint.write_text('{"content": {}}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        0,
        worker_id="worker",
        result_path=old_checkpoint,
        provider_request_id="provider-old",
    )
    old_generation = queue.list_segments(task.task_id)[0]
    new_checkpoint = Path(task.workspace_path) / "segments" / "segment-0000-new.json"
    new_checkpoint.write_text('{"content": {}}', encoding="utf-8")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_segments SET result_path=?, provider_request_id=? WHERE task_id=? AND segment_index=0",
            (str(new_checkpoint.resolve()), "provider-new", task.task_id),
        )

    with pytest.raises(RuntimeError, match="版本已变化"):
        queue.reject_segment_checkpoint(
            task.task_id,
            0,
            reason="old validator result",
            expected_result_path=old_checkpoint,
            expected_provider_request_id="provider-old",
            expected_updated_at=old_generation.updated_at,
            expected_checkpoint_identity=old_generation.checkpoint_identity,
            diagnostic_code="model_contract_invalid",
            task_worker_id=claimed_task.worker_id,
        )

    refreshed = queue.list_segments(task.task_id)[0]
    assert refreshed.status == "succeeded"
    assert refreshed.result_path == str(new_checkpoint.resolve())
    assert refreshed.provider_request_id == "provider-new"


def test_stale_validator_cannot_revoke_same_path_aba_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None and claimed_task.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    fixed_now = "2099-09-07T01:02:03Z"
    monkeypatch.setattr(s3_queue_module, "_now_iso", lambda: fixed_now)

    first_lease = queue.claim_segment(task.task_id, worker_id="worker-old")
    assert first_lease is not None and first_lease.segment_index == 0
    checkpoint = Path(task.workspace_path) / "segments" / "segment-0000.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text('{"generation":"old"}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        0,
        worker_id="worker-old",
        result_path=checkpoint,
        provider_request_id="provider-same",
    )
    old_generation = queue.list_segments(task.task_id)[0]
    assert old_generation.checkpoint_identity

    queue.reject_segment_checkpoint(
        task.task_id,
        0,
        reason="first validator rejects old generation",
        expected_result_path=checkpoint,
        expected_provider_request_id="provider-same",
        expected_updated_at=old_generation.updated_at,
        expected_checkpoint_identity=old_generation.checkpoint_identity,
        diagnostic_code="model_contract_invalid",
        task_worker_id=claimed_task.worker_id,
    )
    second_lease = queue.claim_segment(task.task_id, worker_id="worker-new")
    assert second_lease is not None and second_lease.segment_index == 0
    checkpoint.write_text('{"generation":"new"}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        0,
        worker_id="worker-new",
        result_path=checkpoint,
        provider_request_id="provider-same",
    )
    new_generation = queue.list_segments(task.task_id)[0]
    assert new_generation.result_path == old_generation.result_path
    assert new_generation.provider_request_id == old_generation.provider_request_id
    assert new_generation.updated_at == old_generation.updated_at
    assert new_generation.checkpoint_identity != old_generation.checkpoint_identity

    with pytest.raises(RuntimeError, match="版本已变化"):
        queue.reject_segment_checkpoint(
            task.task_id,
            0,
            reason="late duplicate validation of old generation",
            expected_result_path=checkpoint,
            expected_provider_request_id="provider-same",
            expected_updated_at=old_generation.updated_at,
            expected_checkpoint_identity=old_generation.checkpoint_identity,
            diagnostic_code="model_contract_invalid",
            task_worker_id=claimed_task.worker_id,
        )

    winner = queue.list_segments(task.task_id)[0]
    assert winner.status == "succeeded"
    assert winner.checkpoint_identity == new_generation.checkpoint_identity
    assert checkpoint.read_text(encoding="utf-8") == '{"generation":"new"}'


@pytest.mark.parametrize(
    ("parent_status", "cancel_requested"),
    [("cancelled", 1), ("failed", 0), ("pending", 0)],
)
def test_checkpoint_rejection_refuses_nonrunning_parent_without_mutation(
    tmp_path: Path,
    parent_status: str,
    cancel_requested: int,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None and claimed_task.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    lease = queue.claim_segment(task.task_id, worker_id="worker")
    assert lease is not None and lease.segment_index == 0
    checkpoint = Path(task.workspace_path) / "segments" / "segment-0000.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text('{"generation":"protected"}', encoding="utf-8")
    queue.complete_segment(task.task_id, 0, worker_id="worker", result_path=checkpoint)
    generation = queue.list_segments(task.task_id)[0]
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status=?, cancel_requested=? WHERE task_id=?",
            (parent_status, cancel_requested, task.task_id),
        )

    with pytest.raises(RuntimeError, match="已取消或执行状态已变化"):
        queue.reject_segment_checkpoint(
            task.task_id,
            0,
            reason="stale validator",
            expected_result_path=checkpoint,
            expected_provider_request_id=None,
            expected_updated_at=generation.updated_at,
            expected_checkpoint_identity=generation.checkpoint_identity,
            diagnostic_code="model_contract_invalid",
            task_worker_id=claimed_task.worker_id,
        )

    preserved = queue.list_segments(task.task_id)[0]
    assert preserved.status == "succeeded"
    assert preserved.result_path == str(checkpoint.resolve())
    assert preserved.checkpoint_identity == generation.checkpoint_identity
    assert checkpoint.read_text(encoding="utf-8") == '{"generation":"protected"}'


def test_checkpoint_rejection_requires_task_owner_fence(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None and claimed_task.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    lease = queue.claim_segment(
        task.task_id,
        worker_id="worker",
        task_worker_id=claimed_task.worker_id,
    )
    assert lease is not None and lease.segment_index == 0
    checkpoint = Path(task.workspace_path) / "segments" / "segment-0000.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text('{"generation":"protected"}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        0,
        worker_id="worker",
        result_path=checkpoint,
        task_worker_id=claimed_task.worker_id,
    )
    generation = queue.list_segments(task.task_id)[0]

    with pytest.raises(RuntimeError, match="任务租约"):
        queue.reject_segment_checkpoint(
            task.task_id,
            0,
            reason="unfenced stale validator",
            expected_result_path=checkpoint,
            expected_provider_request_id=None,
            expected_updated_at=generation.updated_at,
            expected_checkpoint_identity=generation.checkpoint_identity,
            diagnostic_code="model_contract_invalid",
            task_worker_id=None,
        )

    preserved = queue.list_segments(task.task_id)[0]
    assert preserved.status == "succeeded"
    assert preserved.checkpoint_identity == generation.checkpoint_identity
    assert checkpoint.is_file()


def test_stale_task_owner_cannot_reject_checkpoint_after_lease_rollover(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    old_task_lease = queue.claim_next()
    assert old_task_lease is not None and old_task_lease.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    segment_lease = queue.claim_segment(
        task.task_id,
        worker_id="segment-worker",
        task_worker_id=old_task_lease.worker_id,
    )
    assert segment_lease is not None and segment_lease.segment_index == 0
    checkpoint = Path(task.workspace_path) / "segments" / "segment-0000.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text('{"generation":"committed"}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        0,
        worker_id="segment-worker",
        result_path=checkpoint,
        provider_request_id="provider-committed",
        task_worker_id=old_task_lease.worker_id,
    )
    generation = queue.list_segments(task.task_id)[0]
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET lease_expires_at=? WHERE task_id=?",
            (expired, task.task_id),
        )
    new_task_lease = queue.claim_next()
    assert new_task_lease is not None and new_task_lease.worker_id is not None
    assert new_task_lease.worker_id != old_task_lease.worker_id

    with pytest.raises(RuntimeError, match="任务租约已变化"):
        queue.reject_segment_checkpoint(
            task.task_id,
            0,
            reason="old task owner validated too late",
            expected_result_path=checkpoint,
            expected_provider_request_id="provider-committed",
            expected_updated_at=generation.updated_at,
            expected_checkpoint_identity=generation.checkpoint_identity,
            diagnostic_code="model_contract_invalid",
            task_worker_id=old_task_lease.worker_id,
        )

    preserved_task = queue.get(task.task_id)
    preserved_segment = queue.list_segments(task.task_id)[0]
    assert preserved_task is not None and preserved_task.worker_id == new_task_lease.worker_id
    assert preserved_segment.status == "succeeded"
    assert preserved_segment.checkpoint_identity == generation.checkpoint_identity
    assert preserved_segment.result_path == str(checkpoint.resolve())
    assert checkpoint.is_file()


def test_cancel_preserves_succeeded_checkpoint_generation_and_timestamp(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None and claimed_task.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    lease = queue.claim_segment(
        task.task_id,
        worker_id="worker",
        task_worker_id=claimed_task.worker_id,
    )
    assert lease is not None and lease.segment_index == 0
    checkpoint = Path(task.workspace_path) / "segments" / "segment-0000.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text('{"generation":"committed"}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        0,
        worker_id="worker",
        result_path=checkpoint,
        task_worker_id=claimed_task.worker_id,
    )
    before_task = queue.get(task.task_id)
    before_segment = queue.list_segments(task.task_id)[0]
    assert before_task is not None

    cancelled = queue.cancel(task.task_id)

    after_segment = queue.list_segments(task.task_id)[0]
    assert cancelled.status == "cancelled"
    assert cancelled.last_checkpoint_at == before_task.last_checkpoint_at
    assert after_segment.status == "succeeded"
    assert after_segment.updated_at == before_segment.updated_at
    assert after_segment.checkpoint_identity == before_segment.checkpoint_identity
    assert after_segment.result_path == before_segment.result_path
    assert checkpoint.is_file()


def test_legacy_succeeded_checkpoint_is_backfilled_with_generation_identity(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = AnalysisTaskQueue(database)
    task = _enqueue(queue, tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None and claimed_task.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    lease = queue.claim_segment(task.task_id, worker_id="worker")
    assert lease is not None and lease.segment_index == 0
    checkpoint = Path(task.workspace_path) / "segments" / "segment-0000.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("{}", encoding="utf-8")
    queue.complete_segment(task.task_id, 0, worker_id="worker", result_path=checkpoint)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE analysis_segments SET checkpoint_identity=NULL, max_attempts=7
               WHERE task_id=? AND segment_index=0""",
            (task.task_id,),
        )
        connection.execute(
            "UPDATE analysis_tasks SET max_attempts=7 WHERE task_id=?",
            (task.task_id,),
        )

    reopened = AnalysisTaskQueue(database)
    migrated = reopened.list_segments(task.task_id)[0]

    assert migrated.status == "succeeded"
    assert migrated.checkpoint_identity is not None
    assert migrated.checkpoint_identity.startswith("checkpoint_")
    assert migrated.result_path == str(checkpoint.resolve())
    assert migrated.max_attempts == 5
    assert reopened.get(task.task_id).max_attempts == 5  # type: ignore[union-attr]


def test_checkpoint_rejection_recalculates_last_checkpoint_from_commit_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None and claimed_task.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    clock = {"now": "2099-09-07T01:00:00Z"}
    monkeypatch.setattr(s3_queue_module, "_now_iso", lambda: clock["now"])

    first = queue.claim_segment(task.task_id, worker_id="worker-0")
    assert first is not None and first.segment_index == 0
    first_path = Path(task.workspace_path) / "segments" / "segment-0000.json"
    first_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_text("{}", encoding="utf-8")
    queue.complete_segment(task.task_id, 0, worker_id="worker-0", result_path=first_path)
    first_generation = queue.list_segments(task.task_id)[0]

    clock["now"] = "2099-09-07T02:00:00Z"
    second = queue.claim_segment(task.task_id, worker_id="worker-1")
    assert second is not None and second.segment_index == 1
    second_path = Path(task.workspace_path) / "segments" / "segment-0001.json"
    second_path.write_text("{}", encoding="utf-8")
    queue.complete_segment(task.task_id, 1, worker_id="worker-1", result_path=second_path)
    second_generation = queue.list_segments(task.task_id)[1]

    clock["now"] = "2099-09-07T03:00:00Z"
    queue.reject_segment_checkpoint(
        task.task_id,
        1,
        reason="reject latest checkpoint",
        expected_result_path=second_path,
        expected_provider_request_id=None,
        expected_updated_at=second_generation.updated_at,
        expected_checkpoint_identity=second_generation.checkpoint_identity,
        diagnostic_code="model_contract_invalid",
        task_worker_id=claimed_task.worker_id,
    )

    refreshed = queue.get(task.task_id)
    assert refreshed is not None
    assert refreshed.last_completed_segment == 0
    assert refreshed.last_checkpoint_at == first_generation.updated_at
    assert refreshed.last_checkpoint_at == "2099-09-07T01:00:00Z"


def test_recreated_task_can_reuse_segment_identity_after_previous_task_failed(
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    first = _enqueue(queue, tmp_path)
    queue.prepare_segments(first.task_id, _segments())
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed' WHERE task_id=?",
            (first.task_id,),
        )
    payload = json.loads(Path(first.input_path).read_text(encoding="utf-8"))
    second = queue.enqueue(
        media_task_id=first.media_task_id,
        media_result_path=first.media_result_path,
        workspace_path=tmp_path / "analysis-second",
        input_payload=payload,
        fixture_data=True,
    )

    queue.prepare_segments(second.task_id, _segments())

    assert second.task_id != first.task_id
    assert len(queue.list_segments(first.task_id)) == 2
    assert len(queue.list_segments(second.task_id)) == 2


def test_legacy_global_segment_key_unique_index_is_migrated_without_data_loss(
    tmp_path: Path,
) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = AnalysisTaskQueue(database)
    task = _enqueue(queue, tmp_path)
    queue.prepare_segments(task.task_id, _segments())
    before = [item.segment_key for item in queue.list_segments(task.task_id)]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE UNIQUE INDEX legacy_segment_key_unique ON analysis_segments(segment_key)"
        )

    reopened = AnalysisTaskQueue(database)

    after = [item.segment_key for item in reopened.list_segments(task.task_id)]
    assert after == before
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        unique_segment_indexes = []
        for index_row in connection.execute("PRAGMA index_list(analysis_segments)").fetchall():
            if not index_row["unique"]:
                continue
            index_name = str(index_row["name"]).replace('"', '""')
            columns = [
                row["name"]
                for row in connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
            ]
            if columns == ["segment_key"]:
                unique_segment_indexes.append(index_row["name"])
    assert unique_segment_indexes == []


def test_stale_segment_writer_cannot_overwrite_new_lease_winner(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    assert queue.claim_next() is not None
    queue.prepare_segments(task.task_id, _segments())
    lease_a = queue.claim_segment(task.task_id, worker_id="segment-worker-a", lease_seconds=10)
    assert lease_a is not None and lease_a.attempt_count == 1
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_segments SET lease_expires_at=? WHERE task_id=? AND segment_index=0",
            (expired, task.task_id),
        )
    lease_b = queue.claim_segment(task.task_id, worker_id="segment-worker-b", lease_seconds=10)
    assert lease_b is not None and lease_b.attempt_count == 2

    path_b = _segment_attempt_checkpoint_path(
        Path(task.workspace_path), 0, lease_b.attempt_count, "segment-worker-b"
    )
    path_b.parent.mkdir(parents=True, exist_ok=True)
    path_b.write_text('{"provider":"winner-b"}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        0,
        worker_id="segment-worker-b",
        result_path=path_b,
        provider_request_id="provider-b",
    )

    path_a = _segment_attempt_checkpoint_path(
        Path(task.workspace_path), 0, lease_a.attempt_count, "segment-worker-a"
    )
    path_a.write_text('{"provider":"stale-a"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="租约已变更"):
        queue.complete_segment(
            task.task_id,
            0,
            worker_id="segment-worker-a",
            result_path=path_a,
            provider_request_id="provider-a",
        )

    winner = queue.list_segments(task.task_id)[0]
    assert winner.status == "succeeded"
    assert winner.provider_request_id == "provider-b"
    assert winner.result_path == str(path_b.resolve())
    assert json.loads(Path(winner.result_path).read_text(encoding="utf-8")) == {"provider": "winner-b"}


def test_stale_task_worker_cannot_complete_or_fail_new_lease(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    lease_a = queue.claim_next()
    assert lease_a is not None and lease_a.worker_id is not None
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET lease_expires_at=? WHERE task_id=?",
            (expired, task.task_id),
        )
    lease_b = queue.claim_next()
    assert lease_b is not None and lease_b.worker_id is not None
    assert lease_b.worker_id != lease_a.worker_id

    task_dir = Path(task.workspace_path)
    task_dir.mkdir(parents=True, exist_ok=True)
    ocr = task_dir / "ocr.json"
    report_a = task_dir / "analysis-report-worker-a.json"
    report_b = task_dir / "analysis-report-worker-b.json"
    ocr.write_text("{}", encoding="utf-8")
    report_a.write_text('{"worker":"a"}', encoding="utf-8")
    report_b.write_text('{"worker":"b"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="任务租约已变化"):
        queue.complete(
            task.task_id,
            AnalysisArtifacts("analysis_a", report_a, ocr),
            worker_id=lease_a.worker_id,
        )
    queue.fail(
        task.task_id,
        GatewayError("stale failure", retryable=False),
        worker_id=lease_a.worker_id,
    )
    still_running = queue.get(task.task_id)
    assert still_running is not None and still_running.status == "running"
    assert still_running.worker_id == lease_b.worker_id

    queue.complete(
        task.task_id,
        AnalysisArtifacts("analysis_b", report_b, ocr),
        worker_id=lease_b.worker_id,
    )
    winner = queue.get(task.task_id)
    assert winner is not None and winner.status == "succeeded"
    assert winner.result_path == str(report_b)


def test_stale_task_worker_cannot_claim_a_new_segment(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = _enqueue(queue, tmp_path)
    lease_a = queue.claim_next()
    assert lease_a is not None and lease_a.worker_id is not None
    queue.prepare_segments(task.task_id, _segments())
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET lease_expires_at=? WHERE task_id=?",
            (expired, task.task_id),
        )
    lease_b = queue.claim_next()
    assert lease_b is not None and lease_b.worker_id is not None

    stale_claim = queue.claim_segment(
        task.task_id,
        worker_id="segment-worker-a",
        task_worker_id=lease_a.worker_id,
    )
    winner_claim = queue.claim_segment(
        task.task_id,
        worker_id="segment-worker-b",
        task_worker_id=lease_b.worker_id,
    )

    assert stale_claim is None
    assert winner_claim is not None and winner_claim.segment_index == 0


def test_completed_short_task_does_not_starve_long_task_lease_heartbeat(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    long_task = _enqueue(queue, first_root)
    second_media = second_root / "media-result.json"
    second_media.write_text(
        json.dumps({"source": {"sha256": "b" * 64}}),
        encoding="utf-8",
    )
    short_task = queue.enqueue(
        media_task_id="media_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        media_result_path=second_media,
        workspace_path=second_root / "analysis",
        input_payload={
            "metric_snapshots": [],
            "comments": [],
            "_gateway_purpose": "analysis",
            "_gateway_model_snapshot": {
                "model_id": "fixture",
                "model": "fixture-model",
                "provider": "openai_compatible",
                "api_mode": "chat_completions",
            },
        },
        fixture_data=True,
    )
    long_started = threading.Event()
    duplicate_started = threading.Event()
    call_lock = threading.Lock()
    long_calls = 0

    def artifacts(task, call_number: int) -> AnalysisArtifacts:
        workspace = Path(task.workspace_path)
        workspace.mkdir(parents=True, exist_ok=True)
        report = workspace / f"report-{call_number}-{task.worker_id}.json"
        ocr = workspace / "ocr.json"
        report.write_text("{}", encoding="utf-8")
        ocr.write_text("{}", encoding="utf-8")
        return AnalysisArtifacts(f"analysis-{task.task_id}", report, ocr)

    def processor(task, _callback) -> AnalysisArtifacts:
        nonlocal long_calls
        if task.task_id == long_task.task_id:
            with call_lock:
                long_calls += 1
                call_number = long_calls
            if call_number == 1:
                long_started.set()
                duplicate_started.wait(timeout=1.0)
            else:
                duplicate_started.set()
            return artifacts(task, call_number)

        assert task.task_id == short_task.task_id
        assert long_started.wait(timeout=1.0)
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        with sqlite3.connect(queue.database_path) as connection:
            connection.execute(
                "UPDATE analysis_tasks SET lease_expires_at=? WHERE task_id=?",
                (expired, long_task.task_id),
            )
        return artifacts(task, 1)

    queue.run_pending(gateway_config=None, max_workers=2, processor=processor)  # type: ignore[arg-type]

    assert long_calls == 1
    completed = queue.get(long_task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert completed.attempt_count == 1
