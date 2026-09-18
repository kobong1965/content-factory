from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from content_factory_api.s3_queue import AnalysisTaskQueue
from content_factory_api.s3_skills import ViralSkillStore, refresh_skill_candidates
from content_factory_api.s5_sources import list_templates, resolve_template
from content_factory_contracts import validate_or_raise

ROOT = Path(__file__).resolve().parents[3]


def case_files(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    original = root / "original.mp4"
    original.write_bytes(b"local import test media; not a real video")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    report = json.loads((ROOT / "packages/contracts/fixtures/analysis.valid.json").read_text(encoding="utf-8"))
    report.update(fixture_data=False, status="accepted", video_id="video_" + digest[:24])
    report["processing"].update(api_mode="external_review", provider="Codex external review", response_id=None)
    report["source"]["source_uri"] = str(original)
    report["review"] = {"reviewer": "test import authorization", "reviewed_at": "2026-09-13T07:00:00Z", "note": "structure only"}
    for i, frame in enumerate(report["keyframes"]):
        frame_path = root / f"frame-{i}.jpg"
        frame_path.write_bytes(b"test keyframe")
        frame["local_path"] = str(frame_path)
    report_path = root / "analysis-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    media = {"task_id": "media_" + "1" * 32, "fixture_data": False, "source": {"sha256": digest, "managed_original_path": str(original)}}
    media_path = root / "media.json"
    media_path.write_text(json.dumps(media), encoding="utf-8")
    return report_path, media_path


def test_external_review_is_explicit_not_a_faked_gateway_protocol(tmp_path):
    report_path, _ = case_files(tmp_path / "case")
    validate_or_raise("analysis", json.loads(report_path.read_text(encoding="utf-8")))


def test_external_import_reopen_idempotency_and_s5_resolution(tmp_path):
    from content_factory_api.s3_external_import import import_reviewed_case

    report, media = case_files(tmp_path / "case")
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    store = ViralSkillStore(tmp_path / "skills.sqlite3")
    task = import_reviewed_case(queue, report_path=report, media_result_path=media, workspace=tmp_path / "managed")
    assert task.status == "succeeded" and task.progress == 100
    assert queue.claim_next() is None, "An imported result must never be sent to a paid gateway"
    assert import_reviewed_case(queue, report_path=report, media_result_path=media, workspace=tmp_path / "managed").task_id == task.task_id
    assert len(queue.list()) == 1
    reopened = AnalysisTaskQueue(queue.database_path)
    assert reopened.recover_interrupted() == 0
    refresh_skill_candidates(store, reopened)
    candidate = store.list_candidates()[0]
    skill = store.approve_candidate(candidate["candidate_id"], expected_candidate_revision=candidate["revision"], expected_skill_revision=None, reviewer="test", reuse_mode="reuse", name="4.20 J85", mechanism=candidate["suggested_mechanism"], note="structure only")
    refresh_skill_candidates(store, reopened)
    assert [item["name"] for item in list_templates(reopened, store)] == ["4.20 J85"]
    _, resolved, _, _ = resolve_template(reopened, store, skill["skill_id"])
    assert resolved["processing"]["api_mode"] == "external_review"
    for frame in resolved["keyframes"]:
        assert Path(frame["local_path"]).is_relative_to(tmp_path / "managed")
        assert Path(frame["local_path"]).read_bytes() == b"test keyframe"
    assert report.exists(), "Source package is preserved"


@pytest.mark.parametrize("failure", ["hash", "frame_escape", "draft", "wrong_video", "fixture"])
def test_rejects_bad_import_before_adding_task(tmp_path, failure):
    from content_factory_api.s3_external_import import import_reviewed_case

    report_path, media_path = case_files(tmp_path / "case")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if failure == "hash":
        (tmp_path / "case/original.mp4").write_bytes(b"changed")
    elif failure == "frame_escape":
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"not part of package")
        report["keyframes"][0]["local_path"] = str(outside)
    elif failure == "draft":
        report["status"] = "draft"
    elif failure == "wrong_video":
        report["video_id"] = "video_wrong"
    else:
        report["fixture_data"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    with pytest.raises(ValueError):
        import_reviewed_case(queue, report_path=report_path, media_result_path=media_path, workspace=tmp_path / "managed")
    assert queue.list() == []


def test_committed_import_survives_reopen_and_refuses_changed_managed_result(tmp_path):
    from content_factory_api.s3_external_import import import_reviewed_case

    report, media = case_files(tmp_path / "case")
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    task = import_reviewed_case(queue, report_path=report, media_result_path=media, workspace=tmp_path / "managed")
    saved = Path(task.result_path)
    saved.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed|变化"):
        import_reviewed_case(AnalysisTaskQueue(queue.database_path), report_path=report, media_result_path=media, workspace=tmp_path / "managed")
    assert saved.read_text(encoding="utf-8") == "{}", "Never overwrite later user edits"
    assert len(queue.list()) == 1


def test_interruption_before_commit_leaves_no_runnable_task_and_retry_recovers(tmp_path, monkeypatch):
    from content_factory_api import s3_external_import as importer

    report, media = case_files(tmp_path / "case")
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3")
    persist = importer._persist_immutable
    calls = 0

    def interrupt_after_file(path, content):
        nonlocal calls
        persist(path, content)
        calls += 1
        if calls == 2:
            raise InterruptedError("simulated interruption before database commit")

    monkeypatch.setattr(importer, "_persist_immutable", interrupt_after_file)
    with pytest.raises(InterruptedError):
        importer.import_reviewed_case(queue, report_path=report, media_result_path=media, workspace=tmp_path / "managed")
    assert AnalysisTaskQueue(queue.database_path).list() == []
    monkeypatch.setattr(importer, "_persist_immutable", persist)
    task = importer.import_reviewed_case(queue, report_path=report, media_result_path=media, workspace=tmp_path / "managed")
    assert task.status == "succeeded"
    assert queue.claim_next() is None
