"""Interrupted registration must not trust changed files or discard human work."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from content_factory_api import auto_edit_worker as worker, edit_batches, speech_captions, subtitle_editor
from content_factory_api.auto_edit_store import AutoEditConflictError, AutoEditProjectStore
from test_auto_edit_projects import _settings, _skills


@pytest.fixture
def interrupted_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONTENT_FACTORY_S7_DATA_DIR", str(tmp_path / "s7"))
    source = tmp_path / "中文原声.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=96x160:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-c:v", "libx264",
        "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    project = store.create_project(
        title="登记恢复验收", source={"source_id": "source_01", "file_name": source.name,
            "path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "duration_ms": 6000},
        settings=_settings(target_count=1, duration_min_ms=5000, duration_max_ms=6000),
    )
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    words = [{"text": "裤子", "start_ms": 100, "end_ms": 500, "probability": .99}]
    monkeypatch.setattr(speech_captions, "recognize", lambda *args, **kwargs: {"words": words, "raw_segments": []})
    original_import = worker.import_batch

    def interrupt(*args, **kwargs):
        raise RuntimeError("simulated registration interruption")

    monkeypatch.setattr(worker, "import_batch", interrupt)
    project = worker.process_one(
        store, available_skills=_skills(), worker_id="first", root=tmp_path / "render",
        planner=lambda project: ("skill_a", "连续原声", "实际渲染后中断登记", [{
            "candidate_id": "candidate_01", "title": "裤腰展示", "clips": [{"start_ms": 0, "end_ms": 5000}],
        }]),
    )
    assert project["status"] == "failed" and project.get("registration_checkpoint")
    manifest_path = Path(project["registration_checkpoint"]["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(worker, "import_batch", original_import)
    return store, project, manifest_path, manifest, tmp_path / "render"


def forbid_expensive_retry(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Registration recovery must not run encoding, ASR, or a paid model")

    monkeypatch.setattr(worker, "_run", forbidden)
    monkeypatch.setattr(speech_captions, "recognize", forbidden)
    monkeypatch.setattr(worker, "analyze_and_plan_with_gateway", forbidden)
    return forbidden


def test_legacy_checkpoint_with_duration_drift_cannot_skip_final_acceptance(interrupted_registration, monkeypatch):
    from dataclasses import replace
    store, project, path, manifest, root = interrupted_registration
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.parent.iterdir() if p.is_file()}
    real_probe = worker.probe_media
    def legacy_drift(video):
        info = real_probe(video)
        return replace(info, duration_ms=info.duration_ms + 800)
    monkeypatch.setattr(worker, 'probe_media', legacy_drift)
    forbid_expensive_retry(monkeypatch)
    store.enqueue(project['project_id'], expected_revision=project['revision'])
    result = worker.process_one(store, available_skills=[], worker_id='resume', root=root)
    assert result['status'] == 'failed' and '成片实际时长' in result['error']
    assert result['output_batch_id'] is None
    assert result['registration_checkpoint'] == project['registration_checkpoint']
    assert result['plan'] == project['plan']
    assert edit_batches.list_batches() == {'batches': []}
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.parent.iterdir() if p.is_file()}


@pytest.mark.parametrize("changed", ["manifest.json", "candidate_01.captions.json", "candidate_01.mp4", "candidate_01.srt", "candidate_01.jpg"])
def test_changed_generated_artifact_rejects_registration_without_replanning(interrupted_registration, monkeypatch, changed):
    store, project, path, manifest, root = interrupted_registration
    target = path.parent / changed
    target.write_bytes(target.read_bytes() + b" ")
    changed_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    plan = deepcopy(project["plan"])
    checkpoint = deepcopy(project["registration_checkpoint"])
    forbid_expensive_retry(monkeypatch)
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    result = worker.process_one(store, available_skills=[], worker_id="resume", root=root)
    assert result["status"] == "failed" and result["output_batch_id"] is None
    assert "发生变化" in result["error"]
    assert result["plan"] == plan and result["registration_checkpoint"] == checkpoint
    assert hashlib.sha256(target.read_bytes()).hexdigest() == changed_sha
    assert edit_batches.list_batches() == {"batches": []}


@pytest.mark.parametrize("trash", [False, True])
def test_cancelled_or_trashed_project_rejects_stale_registration(interrupted_registration, monkeypatch, trash):
    store, project, path, manifest, root = interrupted_registration
    forbid_expensive_retry(monkeypatch)
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    stale = store.claim_next(available_skills=[], worker_id="resume")
    current = store.cancel(stale["project_id"], expected_revision=stale["revision"])
    if trash:
        current = store.set_deleted(current["project_id"], expected_revision=current["revision"], deleted=True)
    with pytest.raises(ValueError, match="状态已改变"):
        worker._register_checkpoint(store, stale, root=root)
    with pytest.raises(AutoEditConflictError):
        store.register_output_batch(stale["project_id"], batch_id=manifest["id"],
            expected_revision=stale["revision"], render_generation=stale["render_generation"])
    assert store.get_project(current["project_id"]) == current
    assert current["status"] == "cancelled" and current["output_batch_id"] is None
    assert edit_batches.list_batches() == {"batches": []}
    assert path.is_file() and (path.parent / "candidate_01.mp4").is_file()


def test_registration_resume_preserves_manual_subtitle_revision_and_words(interrupted_registration, monkeypatch):
    store, project, path, manifest, root = interrupted_registration
    batch = edit_batches.import_batch(edit_batches.ImportRequest(manifest_path=str(path)))
    document = json.loads((path.parent / "candidate_01.captions.json").read_text(encoding="utf-8"))
    subtitle_editor.inherit_draft(batch["id"], "candidate_01", document)
    first = subtitle_editor.get_draft(batch["id"], "candidate_01")
    edited = deepcopy(first["document"])
    edited.update(y=80, size=72)
    # A real human text correction invalidates the old acoustic word alignment.
    edited["cues"][0]["text"] = "裤腰"
    saved = subtitle_editor.save_draft(batch["id"], "candidate_01",
        subtitle_editor.DraftRequest(revision=first["revision"], document=edited))
    assert saved["revision"] == first["revision"] + 1
    assert "words" not in saved["document"]["cues"][0]
    before_history = subtitle_editor.history(batch["id"], "candidate_01")
    forbid_expensive_retry(monkeypatch)
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    done = worker.process_one(store, available_skills=[], worker_id="resume", root=root)
    assert done["status"] == "review" and done["output_batch_id"] == batch["id"]
    assert subtitle_editor.get_draft(batch["id"], "candidate_01") == saved
    assert subtitle_editor.history(batch["id"], "candidate_01") == before_history
    assert len(edit_batches.list_batches()["batches"]) == 1
