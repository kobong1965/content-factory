"""Footage-first editing batches: local artifacts, review, and durable provenance."""

from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from content_factory_api.main import app


@pytest.fixture(scope="module")
def actual_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("footage-media")
    video = root / "host.mp4"
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "本地实拍批次验收需要已安装的 FFmpeg"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=25:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
    ], check=True, timeout=60, capture_output=True)
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
        "-frames:v", "1", str(root / "cover.jpg"),
    ], check=True, timeout=60, capture_output=True)
    return video


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CONTENT_FACTORY_S7_DATA_DIR", str(tmp_path / "s7"))
    # No lifespan: avoid launching unrelated queues or touching the user's stores.
    return TestClient(app)


@pytest.fixture
def manifest(tmp_path: Path, actual_video: Path) -> Path:
    root = tmp_path / "review-batch"
    root.mkdir()
    shutil.copyfile(actual_video, root / "candidate.mp4")
    shutil.copyfile(actual_video.parent / "cover.jpg", root / "cover.jpg")
    (root / "candidate.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,800\n看一下这条裤子的腰头\n", encoding="utf-8"
    )
    payload = {
        "schema_version": 1,
        "id": "batch-live-20260915",
        "title": "千川对标 · 新实拍审核",
        "analysis_summary": "近景腰头展示对应原片动作；投放表现由用户提供，未获得量化数据。",
        "candidates": [{
            "id": "candidate-01", "title": "腰头细节版",
            "source_path": str(actual_video.resolve()),
            "source_start_ms": 0, "source_end_ms": 2000,
            "video_path": "candidate.mp4", "subtitle_path": "candidate.srt", "cover_path": "cover.jpg",
            "hook": "手部展示与口播同步", "benchmark_refs": ["4.20 J85", "5.9 J72"],
            "review_notes": ["ASR 字幕需人工听审", "款号待确认"],
        }],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _change_manifest(path: Path, change) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _import(client: TestClient, path: Path) -> dict:
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(path.resolve())})
    assert response.status_code in (200, 201), response.text
    return response.json()


def _review(client: TestClient, batch: dict, *, revision: int | None = None,
            status: str = "changes_requested", note: str = "字幕第 1 句需听审"):
    return client.patch(
        f"/s7/footage-batches/{batch['id']}/candidates/candidate-01/review",
        json={"revision": batch["revision"] if revision is None else revision,
              "status": status, "note": note, "reviewed_by": "审核甲"},
    )


def test_empty_batch_list_uses_isolated_store(client: TestClient) -> None:
    response = client.get("/s7/footage-batches")
    assert response.status_code == 200, response.text
    assert response.json() == {"batches": []}


def test_import_preserves_source_and_starts_pending(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    candidate = batch["candidates"][0]
    assert batch["id"] == "batch-live-20260915" and isinstance(batch["revision"], int)
    assert batch["analysis_summary"].startswith("近景腰头展示")
    assert candidate["review_status"] == "pending" and candidate["review_note"] == ""
    assert (candidate["source_start_ms"], candidate["source_end_ms"]) == (0, 2000)
    assert candidate["benchmark_refs"] == ["4.20 J85", "5.9 J72"]
    assert candidate["review_notes"] == ["ASR 字幕需人工听审", "款号待确认"]
    assert len(client.get("/s7/footage-batches").json()["batches"]) == 1


def test_review_survives_new_client_and_duplicate_import(client: TestClient, manifest: Path, tmp_path: Path) -> None:
    batch = _import(client, manifest)
    reviewed = _review(client, batch)
    assert reviewed.status_code == 200, reviewed.text
    updated = reviewed.json()
    assert updated["revision"] == batch["revision"] + 1
    assert updated["candidates"][0]["review_note"] == "字幕第 1 句需听审"
    assert updated["candidates"][0]["review_status"] == "changes_requested"
    reopened = TestClient(app).get("/s7/footage-batches").json()["batches"]
    assert reopened == [updated]
    duplicate = _import(TestClient(app), manifest)
    assert duplicate == updated
    assert (tmp_path / "s7" / "footage-batches.sqlite3").is_file()
    # A fresh interpreter cannot reuse an in-memory store from this test process.
    reopened_process = subprocess.run(
        [sys.executable, "-c",
         "import json; from fastapi.testclient import TestClient; "
         "from content_factory_api.main import app; "
         "response=TestClient(app).get('/s7/footage-batches'); "
         "assert response.status_code == 200, response.text; "
         "print(json.dumps(response.json(), ensure_ascii=True))"],
        check=True, capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert json.loads(reopened_process.stdout) == {"batches": [updated]}


def test_stale_review_does_not_overwrite_another_reviewer(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    accepted = _review(client, batch, status="approved", note="画面、原声和字幕已核对")
    assert accepted.status_code == 200, accepted.text
    stale = _review(client, batch, note="旧页面的修改")
    assert stale.status_code == 409, stale.text
    current = client.get("/s7/footage-batches").json()["batches"][0]
    assert current == accepted.json()


def test_changed_manifest_same_id_conflicts_without_replacing_review(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    accepted = _review(client, batch, status="approved", note="保留这条审核")
    assert accepted.status_code == 200, accepted.text
    _change_manifest(manifest, lambda payload: payload.update(title="被替换的标题"))
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code == 409, response.text
    assert client.get("/s7/footage-batches").json()["batches"] == [accepted.json()]


def test_all_candidates_are_validated_before_any_write(client: TestClient, manifest: Path) -> None:
    def add_invalid(payload):
        second = deepcopy(payload["candidates"][0])
        second.update(id="candidate-02", video_path="missing.mp4")
        payload["candidates"].append(second)
    _change_manifest(manifest, add_invalid)
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


@pytest.mark.parametrize("field,value", [
    ("video_path", "../outside.mp4"),
    ("subtitle_path", "../outside.srt"),
    ("cover_path", "../outside.jpg"),
])
def test_relative_artifacts_cannot_escape_batch_directory(client: TestClient, manifest: Path, field: str, value: str) -> None:
    original_name = {"video_path": "candidate.mp4", "subtitle_path": "candidate.srt", "cover_path": "cover.jpg"}[field]
    # The outside resource exists: rejection must enforce containment, not merely existence.
    shutil.copyfile(manifest.parent / original_name, manifest.parent.parent / Path(value).name)
    _change_manifest(manifest, lambda payload: payload["candidates"][0].update({field: value}))
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


@pytest.mark.parametrize("field", ["video_path", "subtitle_path", "cover_path"])
def test_artifact_paths_must_be_relative_even_when_inside_batch(client: TestClient, manifest: Path, field: str) -> None:
    def make_absolute(payload):
        candidate = payload["candidates"][0]
        candidate[field] = str((manifest.parent / candidate[field]).resolve())
    _change_manifest(manifest, make_absolute)
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


def test_source_must_be_absolute_readable_video(client: TestClient, manifest: Path) -> None:
    _change_manifest(manifest, lambda payload: payload["candidates"][0].update(source_path="candidate.mp4"))
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


def test_fake_video_is_not_registered_as_rendered_candidate(client: TestClient, manifest: Path) -> None:
    (manifest.parent / "candidate.mp4").write_bytes(b"not a video")
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


@pytest.mark.parametrize("start,end", [(1000, 500), (-1, 1000), (0, 3000)])
def test_source_range_must_be_valid_and_within_real_duration(client: TestClient, manifest: Path, start: int, end: int) -> None:
    _change_manifest(manifest, lambda payload: payload["candidates"][0].update(source_start_ms=start, source_end_ms=end))
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


def test_candidate_and_original_stream_real_media_with_range(client: TestClient, manifest: Path, actual_video: Path) -> None:
    batch = _import(client, manifest)
    base = f"/s7/footage-batches/{batch['id']}/candidates/candidate-01/media"
    for kind, expected_path in [("video", manifest.parent / "candidate.mp4"), ("source", actual_video)]:
        response = client.get(f"{base}/{kind}", headers={"Range": "bytes=0-31"})
        assert response.status_code == 206, response.text
        assert response.content == expected_path.read_bytes()[:32]
        assert response.headers["content-range"].startswith("bytes 0-31/")
        assert response.headers["content-type"].startswith("video/")
    subtitles = client.get(f"{base}/subtitle")
    assert subtitles.status_code == 200
    assert subtitles.content == (manifest.parent / "candidate.srt").read_bytes()
    cover = client.get(f"{base}/cover")
    assert cover.status_code == 200 and cover.content == (manifest.parent / "cover.jpg").read_bytes()
    for suffix in ["/other", "/unknown", "/%2e%2e"]:
        assert client.get(base + suffix).status_code == 404


def test_unknown_candidate_and_invalid_review_leave_saved_batch_unchanged(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    base = f"/s7/footage-batches/{batch['id']}/candidates"
    missing = client.patch(f"{base}/missing/review", json={"revision": batch["revision"], "status": "approved", "note": "", "reviewed_by": "审核甲"})
    assert missing.status_code == 404, missing.text
    invalid = _review(client, batch, status="published")
    assert invalid.status_code == 422, invalid.text
    assert client.get("/s7/footage-batches").json()["batches"] == [batch]


def test_duplicate_candidate_id_rejected_without_partial_batch(client: TestClient, manifest: Path) -> None:
    _change_manifest(manifest, lambda payload: payload["candidates"].append(deepcopy(payload["candidates"][0])))
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


def test_multiple_complete_source_segments_remain_ordered_and_bound_to_one_source(client: TestClient, manifest: Path) -> None:
    clips = [{"start_ms": 0, "end_ms": 600}, {"start_ms": 1000, "end_ms": 1900}]
    _change_manifest(manifest, lambda payload: payload["candidates"][0].update(clips=clips))
    batch = _import(client, manifest)
    candidate = batch["candidates"][0]
    assert candidate["clips"] == clips
    assert candidate["source_path"] == json.loads(manifest.read_text(encoding="utf-8"))["candidates"][0]["source_path"]
    assert candidate["review_status"] == "pending"


def test_out_of_range_clip_rejects_the_whole_batch(client: TestClient, manifest: Path) -> None:
    _change_manifest(manifest, lambda payload: payload["candidates"][0].update(
        clips=[{"start_ms": 0, "end_ms": 500}, {"start_ms": 1600, "end_ms": 2600}],
    ))
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


def test_malformed_manifest_is_reported_without_partial_write(client: TestClient, manifest: Path) -> None:
    manifest.write_text('{"schema_version":1,"candidates":[', encoding="utf-8")
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


def test_unsupported_manifest_version_is_not_silently_imported(client: TestClient, manifest: Path) -> None:
    _change_manifest(manifest, lambda payload: payload.update(schema_version=999))
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code in (400, 422), response.text
    assert client.get("/s7/footage-batches").json() == {"batches": []}


def _change_video_bytes(path: Path, *, preserve_stat: bool = False) -> None:
    original_stat = path.stat()
    content = bytearray(path.read_bytes())
    # Exact size is retained. This can occur when an editor replaces a resource
    # while preserving timestamps; mtime/size alone do not bind an approval.
    content[-16] ^= 1
    path.write_bytes(content)
    if preserve_stat:
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))


def test_changed_candidate_cannot_be_approved_under_old_revision(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    _change_video_bytes(manifest.parent / "candidate.mp4")
    response = _review(client, batch, status="approved", note="已审核")
    assert response.status_code == 409, response.text
    assert client.get("/s7/footage-batches").json()["batches"] == [batch]


def test_reimport_changed_bytes_does_not_silently_reuse_existing_approval(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    approved = _review(client, batch, status="approved", note="旧版本审核")
    assert approved.status_code == 200, approved.text
    _change_video_bytes(manifest.parent / "candidate.mp4")
    response = client.post("/s7/footage-batches/import", json={"manifest_path": str(manifest)})
    assert response.status_code == 409, response.text
    assert client.get("/s7/footage-batches").json()["batches"] == [approved.json()]


def test_media_hash_detects_changed_bytes_even_with_preserved_size_and_mtime(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    approved = _review(client, batch, status="approved", note="已审核原文件")
    assert approved.status_code == 200, approved.text
    _change_video_bytes(manifest.parent / "candidate.mp4", preserve_stat=True)
    response = client.get(f"/s7/footage-batches/{batch['id']}/candidates/candidate-01/media/video")
    assert response.status_code == 409, "修改后的文件不得继续以已审核原文件的身份播放"


def test_simultaneous_reviews_have_one_winner_without_lost_update(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    barrier = threading.Barrier(2)

    def review(note):
        barrier.wait(timeout=10)
        return _review(TestClient(app), batch, note=note)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(review, ["审核窗口甲", "审核窗口乙"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    accepted = next(response.json() for response in responses if response.status_code == 200)
    assert accepted["revision"] == batch["revision"] + 1
    assert client.get("/s7/footage-batches").json()["batches"] == [accepted]


def test_simultaneous_imports_commit_only_one_batch(client: TestClient, manifest: Path) -> None:
    barrier = threading.Barrier(3)

    def import_one(_):
        barrier.wait(timeout=10)
        return _import(TestClient(app), manifest)

    with ThreadPoolExecutor(max_workers=3) as pool:
        batches = list(pool.map(import_one, range(3)))
    assert batches[0] == batches[1] == batches[2]
    assert client.get("/s7/footage-batches").json()["batches"] == [batches[0]]


def test_database_connections_close_after_success_and_rejected_review(client: TestClient, manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connections = []
    connect = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        closed_after_request = False

        def close(self):
            super().close()
            self.closed_after_request = True

    def observe_connection(*args, **kwargs):
        # Permit cleanup in the test thread after the HTTP worker has returned;
        # database operations still run only in the request worker.
        kwargs["check_same_thread"] = False
        connection = connect(*args, **kwargs, factory=ObservedConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observe_connection)
    batch = _import(client, manifest)
    assert _review(client, batch).status_code == 200
    assert _review(client, batch).status_code == 409
    assert client.get("/s7/footage-batches").status_code == 200
    try:
        assert connections and all(connection.closed_after_request for connection in connections)
    finally:
        # Keep even a failed resource-lifecycle test from leaking open databases.
        for connection in connections:
            if not connection.closed_after_request:
                connection.close()


def _library(client: TestClient) -> dict:
    response = client.get("/s7/footage-batches/library")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == sum(len(group["items"]) for group in result["groups"])
    assert all(group["count"] == len(group["items"]) for group in result["groups"])
    identities = [(item["batch_id"], item["candidate"]["id"])
                  for group in result["groups"] for item in group["items"]]
    assert len(identities) == len(set(identities)), "成片入库不能重复"
    return result


def _set_product(client: TestClient, batch: dict, sku: str):
    return client.patch(f"/s7/footage-batches/{batch['id']}/product",
                        json={"revision": batch["revision"], "sku": sku})


def test_library_only_approved_and_retraction_removes_candidate(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    assert _library(client)["total"] == 0
    rejected = _review(client, batch)
    assert rejected.status_code == 200, rejected.text
    assert _library(client)["total"] == 0
    approved = _review(client, rejected.json(), status="approved", note="已核对")
    assert approved.status_code == 200, approved.text
    result = _library(client)
    assert result["total"] == 1
    assert len(result["groups"]) == 1 and result["groups"][0]["sku"] == ""
    item = result["groups"][0]["items"][0]
    assert item["batch_id"] == batch["id"] and item["batch_title"] == batch["title"]
    assert item["candidate"]["review_status"] == "approved"
    assert _library(TestClient(app)) == result
    assert _review(client, approved.json(), status="pending", note="撤回重新核对").status_code == 200
    assert _library(client)["total"] == 0


def test_library_review_needs_no_manual_reviewer_name(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    response = client.patch(
        f"/s7/footage-batches/{batch['id']}/candidates/candidate-01/review",
        json={"revision": batch["revision"], "status": "approved", "note": "已核对"},
    )
    assert response.status_code == 200, response.text
    candidate = response.json()["candidates"][0]
    assert candidate["reviewed_by"], "省略姓名仍须保留本机审核记录"
    assert _library(client)["total"] == 1


def test_library_product_reclassification_preserves_all_reviews(client: TestClient, manifest: Path) -> None:
    def add_pending(payload):
        second = deepcopy(payload["candidates"][0])
        second["id"] = "candidate-02"
        payload["candidates"].append(second)
    _change_manifest(manifest, add_pending)
    batch = _import(client, manifest)
    approved = _review(client, batch, status="approved", note="已核对").json()
    for sku in ["裤子-A", "裤子-B", ""]:
        response = _set_product(client, approved, sku)
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["sku"] == sku and updated["revision"] == approved["revision"] + 1
        assert updated["candidates"] == approved["candidates"]
        result = _library(client)
        assert result["total"] == 1
        assert [group["sku"] for group in result["groups"]] == [sku]
        assert TestClient(app).get("/s7/footage-batches").json()["batches"] == [updated]
        approved = updated


def test_library_different_skus_and_batches_never_mix(client: TestClient, manifest: Path) -> None:
    expected = {}
    for index, sku in enumerate(["裤子-A", "裤子-B", "裤子-A"]):
        batch_id = f"library-batch-{index}"
        _change_manifest(manifest, lambda payload: payload.update(id=batch_id))
        batch = _import(client, manifest)
        product = _set_product(client, batch, sku)
        assert product.status_code == 200, product.text
        response = _review(client, product.json(), status="approved", note="已核对")
        assert response.status_code == 200, response.text
        expected.setdefault(sku, set()).add(batch_id)
    result = _library(client)
    assert result["total"] == 3
    actual = {group["sku"]: {item["batch_id"] for item in group["items"]} for group in result["groups"]}
    assert actual == expected


def test_library_product_rejects_invalid_and_stale_without_write(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    approved_response = _review(client, batch, status="approved", note="已核对")
    assert approved_response.status_code == 200, approved_response.text
    approved = approved_response.json()
    invalid = _set_product(client, approved, "长" * 101)
    assert invalid.status_code == 422, invalid.text
    assert client.get("/s7/footage-batches").json()["batches"] == [approved]
    stale = _set_product(client, batch, "过期修改")
    assert stale.status_code == 409, stale.text
    assert client.get("/s7/footage-batches").json()["batches"] == [approved]


@pytest.mark.parametrize("artifact", ["candidate.mp4", "candidate.srt", "cover.jpg", "source"])
def test_library_changed_approved_files_are_not_valid_items(client: TestClient, manifest: Path, artifact: str) -> None:
    # Give this test a private original so corruption cannot leak to other tests.
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    source = manifest.parent / "private-source.mp4"
    shutil.copyfile(payload["candidates"][0]["source_path"], source)
    _change_manifest(manifest, lambda value: value["candidates"][0].update(source_path=str(source.resolve())))
    batch = _import(client, manifest)
    response = _review(client, batch, status="approved", note="已核对")
    assert response.status_code == 200, response.text
    assert _library(client)["total"] == 1
    target = source if artifact == "source" else manifest.parent / artifact
    _change_video_bytes(target, preserve_stat=True)
    assert _library(client)["total"] == 0, "文件变化后不能冒充仍有效的已批准成片"


def test_library_survives_fresh_process_without_duplicate_entries(client: TestClient, manifest: Path) -> None:
    batch = _import(client, manifest)
    response = _review(client, batch, status="approved", note="已核对")
    assert response.status_code == 200, response.text
    expected = _library(client)
    assert expected["total"] == 1
    reopened = subprocess.run(
        [sys.executable, "-c", "import json; from fastapi.testclient import TestClient; "
         "from content_factory_api.main import app; "
         "r=TestClient(app).get('/s7/footage-batches/library'); "
         "assert r.status_code == 200, r.text; print(json.dumps(r.json()))"],
        check=True, capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert json.loads(reopened.stdout) == expected
