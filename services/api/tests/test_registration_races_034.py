"""Deterministic worker interleavings must preserve the current project owner."""
from pathlib import Path

import pytest

from content_factory_api import auto_edit_worker as worker
from content_factory_api.auto_edit_store import AutoEditConflictError, AutoEditProjectStore
from test_auto_edit_projects import _settings, _skills, _source


def _planned_project(store, tmp_path, *, worker_id="old-worker"):
    project = store.create_project(
        title="登记并发回归", source=_source(tmp_path), settings=_settings(target_count=1),
    )
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    claimed = store.claim_next(available_skills=_skills(), worker_id=worker_id)
    assert claimed["project_id"] == project["project_id"]
    return store.save_plan(
        project["project_id"], worker_id=worker_id, selected_skill_id="skill_a", reason="真实选段证据",
        plan=[{"candidate_id": "candidate_01", "title": "版型展示", "clips": [{"start_ms": 0, "end_ms": 20000}]}],
    )


def _checkpoint_project(store, tmp_path):
    project = _planned_project(store, tmp_path)
    rendering = store.mark_rendering(project["project_id"], worker_id="old-worker")
    # These tests exercise the store's lifecycle, not media validation. Real
    # artifact validation and acoustic inheritance are covered by its sibling.
    return store.save_registration_checkpoint(
        project["project_id"], expected_revision=rendering["revision"],
        checkpoint={"manifest_path": str(tmp_path / "manifest.json"), "manifest_sha256": "a" * 64, "artifacts": {}},
    )


def _register_as_worker(store, project, batch_id="test_batch"):
    return store.register_output_batch(
        project["project_id"], batch_id=batch_id, expected_revision=project["revision"],
        render_generation=project["render_generation"],
    )


def test_cancelled_checkpoint_rejects_late_registration(tmp_path: Path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    checkpoint = _checkpoint_project(store, tmp_path)
    cancelled = store.cancel(checkpoint["project_id"], expected_revision=checkpoint["revision"])

    with pytest.raises(AutoEditConflictError):
        _register_as_worker(store, checkpoint)

    assert store.get_project(checkpoint["project_id"]) == cancelled
    assert cancelled["status"] == "cancelled"
    assert cancelled["output_batch_id"] is None


def test_new_generation_rejects_old_batch_even_with_current_revision(tmp_path: Path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    old = _checkpoint_project(store, tmp_path)
    failed = store.fail(old["project_id"], message="登记中断", worker_id="old-worker")
    edited = store.update_project(
        old["project_id"], expected_revision=failed["revision"],
        settings=_settings(target_count=1, subtitle_font_size=70),
    )
    queued = store.enqueue(old["project_id"], expected_revision=edited["revision"])
    assert queued["render_generation"] != old["render_generation"]
    store.claim_next(available_skills=_skills(), worker_id="new-worker")
    store.save_plan(
        old["project_id"], worker_id="new-worker", selected_skill_id="skill_a", reason="新的选段",
        plan=[{"candidate_id": "candidate_02", "title": "新方案", "clips": [{"start_ms": 20000, "end_ms": 40000}]}],
    )
    current = store.mark_rendering(old["project_id"], worker_id="new-worker")

    with pytest.raises(AutoEditConflictError):
        store.register_output_batch(
            old["project_id"], batch_id="old_batch", expected_revision=current["revision"],
            render_generation=old["render_generation"],
        )

    assert store.get_project(old["project_id"]) == current
    assert current["output_batch_id"] is None


@pytest.mark.parametrize("claim_new_generation", [False, True])
def test_late_failure_cannot_fail_a_new_generation(tmp_path: Path, claim_new_generation: bool):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    old = _planned_project(store, tmp_path)
    failed = store.fail(old["project_id"], message="第一次失败", worker_id="old-worker")
    current = store.enqueue(old["project_id"], expected_revision=failed["revision"])
    if claim_new_generation:
        current = store.claim_next(available_skills=_skills(), worker_id="new-worker")

    returned = store.fail(old["project_id"], message="迟到的旧任务异常", worker_id="old-worker")

    assert returned == current
    assert store.get_project(old["project_id"]) == current
    assert current["error"] is None


def test_saved_plan_cannot_be_stolen_until_recovery_releases_lease(tmp_path: Path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    planned = _planned_project(store, tmp_path)

    assert store.claim_next(available_skills=_skills(), worker_id="other-worker") is None
    with pytest.raises(AutoEditConflictError):
        store.mark_rendering(planned["project_id"], worker_id="other-worker")
    assert store.get_project(planned["project_id"]) == planned

    reopened = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    reopened.recover_interrupted()
    resumed = reopened.claim_next(available_skills=[], worker_id="new-worker")
    assert resumed["project_id"] == planned["project_id"]
    assert resumed["status"] == "rendering"
    assert resumed["plan"] == planned["plan"]
    assert resumed["render_generation"] == planned["render_generation"]
    assert reopened.fail(planned["project_id"], message="过期异常", worker_id="old-worker") == resumed


def test_recovered_render_ignores_old_worker_failure(tmp_path: Path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    old = _checkpoint_project(store, tmp_path)
    assert old["project_id"] in store.recover_interrupted()
    resumed = store.claim_next(available_skills=[], worker_id="new-worker")

    assert store.fail(old["project_id"], message="旧进程异常", worker_id="old-worker") == resumed
    assert store.get_project(old["project_id"]) == resumed
    # The check must not swallow a failure reported by the current owner.
    failed = store.fail(old["project_id"], message="当前登记异常", worker_id="new-worker")
    assert failed["status"] == "failed"
    assert failed["error"] == "当前登记异常"
    assert failed["registration_checkpoint"] == old["registration_checkpoint"]


def test_cancelled_render_rejects_late_checkpoint(tmp_path: Path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    planned = _planned_project(store, tmp_path)
    rendering = store.mark_rendering(planned["project_id"], worker_id="old-worker")
    cancelled = store.cancel(planned["project_id"], expected_revision=rendering["revision"])

    with pytest.raises(AutoEditConflictError):
        store.save_registration_checkpoint(
            planned["project_id"], expected_revision=rendering["revision"], checkpoint={"manifest_path": "late.json"},
        )

    assert store.get_project(planned["project_id"]) == cancelled
    assert not cancelled.get("registration_checkpoint")


def test_registration_resume_precedes_unplannable_queued_project(tmp_path: Path, monkeypatch):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    checkpoint = _checkpoint_project(store, tmp_path)
    failed = store.fail(checkpoint["project_id"], message="登记中断", worker_id="old-worker")
    pending = store.enqueue(checkpoint["project_id"], expected_revision=failed["revision"])
    fresh = store.create_project(
        title="等待新方案", source=_source(tmp_path, "new.mp4"), settings=_settings(target_count=1),
    )
    queued = store.enqueue(fresh["project_id"], expected_revision=fresh["revision"])
    calls = []

    def register_existing(current_store, project, *, root):
        calls.append(project["project_id"])
        assert project["registration_checkpoint"] == pending["registration_checkpoint"]
        assert project["plan"] == pending["plan"]
        return _register_as_worker(current_store, project)

    monkeypatch.setattr(worker, "render_and_register", register_existing)
    result = worker.process_one(
        store, available_skills=[], worker_id="resume-worker", root=tmp_path / "render",
        planner=lambda project: pytest.fail("登记恢复不得调用模型或重新规划"),
    )

    assert result is not None, "队首无可用 Skill 的新任务不能阻塞已生成成片的登记"
    assert result["project_id"] == pending["project_id"]
    assert result["status"] == "review" and result["progress"] == 100
    assert calls == [pending["project_id"]]
    assert store.get_project(fresh["project_id"]) == queued
