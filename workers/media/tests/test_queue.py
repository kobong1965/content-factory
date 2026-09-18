from __future__ import annotations

from copy import deepcopy
import json
import threading
import time
from pathlib import Path

from content_factory_media.queue import MediaTaskQueue


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"


def _persist_valid_media_result(task) -> Path:
    """Simulate the pipeline commit that happens just before queue.complete()."""
    payload = deepcopy(json.loads((FIXTURES / "media-result.valid.json").read_text(encoding="utf-8")))
    workspace = Path(task.workspace_path)
    task_directory = workspace / "tasks" / task.task_id
    task_directory.mkdir(parents=True, exist_ok=True)
    managed_original = workspace / "originals" / "aa" / "managed.mp4"
    managed_original.parent.mkdir(parents=True, exist_ok=True)
    managed_original.write_bytes(b"managed-video")
    artifact_paths = {
        "proxy_path": task_directory / "proxy.mp4",
        "audio_path": task_directory / "audio.wav",
        "transcript_path": task_directory / "transcript.json",
        "scene_manifest_path": task_directory / "scenes.json",
    }
    for path in artifact_paths.values():
        path.write_bytes(b"artifact")
    for index, shot in enumerate(payload["shots"], start=1):
        keyframe = task_directory / "keyframes" / f"shot-{index:03d}.jpg"
        keyframe.parent.mkdir(parents=True, exist_ok=True)
        keyframe.write_bytes(b"keyframe")
        shot["keyframe_path"] = str(keyframe)
    payload["task_id"] = task.task_id
    payload["fixture_data"] = task.fixture_data
    payload["source"]["original_name"] = task.source_name
    payload["source"]["managed_original_path"] = str(managed_original)
    payload["artifacts"] = {key: str(path) for key, path in artifact_paths.items()}
    result_path = task_directory / "result.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return result_path


def test_queue_runs_at_most_four_tasks_at_once(tmp_path: Path) -> None:
    queue = MediaTaskQueue(tmp_path / "queue.sqlite3")
    for index in range(6):
        queue.enqueue(tmp_path / f"source-{index}.mp4", tmp_path / "workspace")

    state_lock = threading.Lock()
    four_workers_started = threading.Event()
    active = 0
    peak = 0

    def processor(task, callback):
        nonlocal active, peak
        with state_lock:
            active += 1
            peak = max(peak, active)
            if active == 4:
                four_workers_started.set()
        assert four_workers_started.wait(timeout=2), "four queue workers did not start concurrently"
        callback("proxy", 50)
        time.sleep(0.01)
        result = tmp_path / f"{task.task_id}.json"
        result.touch()
        with state_lock:
            active -= 1
        return result

    completed = queue.run_pending(max_workers=9, processor=processor)

    assert len(completed) == 6
    assert peak == 4
    assert queue.counts()["completed"] == 6
    assert all(task.progress == 100 for task in completed)


def test_queue_retries_once_then_records_success(tmp_path: Path) -> None:
    queue = MediaTaskQueue(tmp_path / "queue.sqlite3")
    queued = queue.enqueue(tmp_path / "source.mp4", tmp_path / "workspace", max_attempts=2)
    calls = 0

    def flaky_processor(task, callback):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        result = tmp_path / "result.json"
        result.touch()
        return result

    queue.run_pending(processor=flaky_processor)
    finished = queue.get(queued.task_id)

    assert finished is not None
    assert finished.status == "completed"
    assert finished.attempt_count == 2
    assert finished.error is None


def test_running_task_is_recovered_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = MediaTaskQueue(database)
    queued = queue.enqueue(tmp_path / "source.mp4", tmp_path / "workspace")
    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.task_id == queued.task_id
    assert claimed.attempt_count == 1

    recovered_queue = MediaTaskQueue(database)
    still_running = recovered_queue.get(queued.task_id)
    assert still_running is not None
    assert still_running.status == "running"

    recovered_queue.recover_interrupted()
    recovered = recovered_queue.get(queued.task_id)

    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.current_step == "recovered"
    assert recovered.attempt_count == 1

    claimed_again = recovered_queue.claim_next()
    assert claimed_again is not None
    assert claimed_again.task_id == queued.task_id
    assert claimed_again.attempt_count == 2


def test_running_task_is_failed_after_restart_when_attempts_are_exhausted(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = MediaTaskQueue(database)
    queued = queue.enqueue(
        tmp_path / "source.mp4",
        tmp_path / "workspace",
        max_attempts=1,
    )
    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.task_id == queued.task_id
    assert claimed.attempt_count == claimed.max_attempts == 1

    recovered_queue = MediaTaskQueue(database)
    assert recovered_queue.recover_interrupted() == 1
    recovered = recovered_queue.get(queued.task_id)

    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.current_step == "failed"
    assert recovered.attempt_count == 1
    assert recovered.error == "任务在最后一次处理时被中断，已达到自动尝试上限，请重新导入视频"
    assert recovered_queue.claim_next() is None


def test_exhausted_running_task_reconciles_a_committed_valid_media_result(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = MediaTaskQueue(database)
    queued = queue.enqueue(
        tmp_path / "source.mp4",
        tmp_path / "workspace",
        max_attempts=1,
    )
    claimed = queue.claim_next()
    assert claimed is not None and claimed.task_id == queued.task_id
    result_path = _persist_valid_media_result(claimed)

    reopened = MediaTaskQueue(database)
    assert reopened.recover_interrupted() == 1
    recovered = reopened.get(queued.task_id)

    assert recovered is not None
    assert recovered.status == "completed"
    assert recovered.current_step == "completed"
    assert recovered.progress == 100
    assert recovered.error is None
    assert recovered.result_path == str(result_path.resolve())
    assert reopened.run_pending(processor=lambda *_args: (_ for _ in ()).throw(AssertionError("must not rerun"))) == []


def test_exhausted_running_task_rejects_an_invalid_committed_media_result(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = MediaTaskQueue(database)
    queued = queue.enqueue(
        tmp_path / "source.mp4",
        tmp_path / "workspace",
        max_attempts=1,
    )
    claimed = queue.claim_next()
    assert claimed is not None
    result_path = Path(claimed.workspace_path) / "tasks" / claimed.task_id / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text("{}", encoding="utf-8")

    reopened = MediaTaskQueue(database)
    assert reopened.recover_interrupted() == 1
    recovered = reopened.get(queued.task_id)

    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.result_path is None
