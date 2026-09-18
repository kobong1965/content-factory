from __future__ import annotations

from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient

from content_factory_api import s2
from content_factory_api.main import app
from content_factory_media.models import MediaInfo
from content_factory_media.queue import MediaTaskQueue


def test_s2_routes_are_exposed() -> None:
    paths = app.openapi()["paths"]

    assert "/s2/readiness" in paths
    assert "/s2/import/file" in paths
    assert "/s2/import/link" in paths
    assert "/s2/tasks" in paths


def test_douyin_link_returns_manual_import_boundary() -> None:
    response = TestClient(app).post("/s2/import/link", json={"url": "https://v.douyin.com/example/"})

    assert response.status_code == 200
    assert response.json()["status"] == "manual_file_required"
    assert "不抓取" in response.json()["message"]


def test_non_douyin_link_is_rejected() -> None:
    response = TestClient(app).post("/s2/import/link", json={"url": "https://example.com/video"})

    assert response.status_code == 400


def test_file_upload_is_validated_and_queued(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS", "1")
    monkeypatch.setattr(
        s2,
        "probe_media",
        lambda _path: MediaInfo(6000, 360, 640, 25, "h264", "aac", True, "mp4"),
    )
    monkeypatch.setattr(s2, "_run_queue_safely", lambda: None)

    response = TestClient(app).post(
        "/s2/import/file",
        files={"video": ("男装样片.mp4", b"fixture-video-bytes", "video/mp4")},
        headers={"X-Content-Factory-Fixture": "true"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.json()["fixture_data"] is True
    assert response.json()["source_name"].endswith(".mp4")
    assert len(s2._queue().list()) == 1


def test_wrong_upload_extension_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    response = TestClient(app).post(
        "/s2/import/file",
        files={"video": ("notes.txt", b"not-a-video", "text/plain")},
    )

    assert response.status_code == 400


def test_fixture_result_cannot_count_as_real_acceptance() -> None:
    fixture_result = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures" / "media-result.valid.json"

    assert s2._accepted_real_case({
        "status": "accepted",
        "source": "a" * 64,
        "result": str(fixture_result),
    }) is False


def test_queue_run_requests_are_serialized_without_dropping_a_wakeup(monkeypatch) -> None:
    class FakeQueue:
        def __init__(self) -> None:
            self.calls = 0
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def run_pending(self, **_kwargs) -> None:
            with self.lock:
                self.calls += 1
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.03)
            with self.lock:
                self.active -= 1

        def list(self, **_kwargs):
            return []

    fake_queue = FakeQueue()
    monkeypatch.setattr(s2, "_queue", lambda: fake_queue)
    threads = [threading.Thread(target=s2._run_queue_safely) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert fake_queue.calls == 2
    assert fake_queue.peak == 1


def test_completed_staging_upload_is_removed_after_managed_copy(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    upload_directory = runtime_root / "uploads"
    upload_directory.mkdir(parents=True)
    source = upload_directory / "uploaded.mp4"
    source.touch()
    result = tmp_path / "media" / "tasks" / "task" / "result.json"
    result.parent.mkdir(parents=True)
    result.touch()
    queue = MediaTaskQueue(runtime_root / "queue.sqlite3")
    queued = queue.enqueue(source, tmp_path / "media")
    claimed = queue.claim_next()
    assert claimed is not None
    queue.complete(queued.task_id, result)
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(runtime_root))

    s2._cleanup_completed_uploads(queue)

    assert source.exists() is False
