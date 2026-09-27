"""A failed local render must not discard an already-paid, validated plan."""

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from content_factory_api import auto_edit_worker as worker
from content_factory_api.auto_edit_store import AutoEditConflictError, AutoEditProjectStore
from test_auto_edit_projects import _settings, _skills, _source


def _failed_render(store, tmp_path):
    project = store.create_project(
        title="字幕处理失败的项目", source=_source(tmp_path), settings=_settings(target_count=1),
    )
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    store.claim_next(available_skills=_skills(), worker_id="original-worker")
    store.save_plan(
        project["project_id"], worker_id="original-worker", selected_skill_id="skill_a",
        reason="源素材中的真实版型展示", analysis_summary="已经完成的原片分析，不应再次请求模型",
        plan=[{"candidate_id": "candidate_01", "title": "展示版型",
               "clips": [{"start_ms": 1000, "end_ms": 21000}]}],
    )
    store.mark_rendering(project["project_id"], worker_id="original-worker")
    failed = store.fail(project["project_id"], message="字词单位过长，无法安全分句", worker_id="original-worker")
    assert failed["status"] == "failed" and failed["progress"] == 55
    assert failed["plan"] and failed["selected_skill"]
    assert not failed.get("registration_checkpoint")
    return failed


def test_failed_render_retry_keeps_frozen_plan_in_new_output_generation(tmp_path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)
    old_batch, old_root = worker._output_identity(failed, tmp_path / "render")
    old_root.mkdir(parents=True)
    partial = old_root / "candidate_01.clean.mp4"
    partial.write_bytes(b"preserve-the-original-partial-video")

    pending = store.enqueue(failed["project_id"], expected_revision=failed["revision"])

    assert pending["status"] == "render_pending", "本地失败不得重新排队付费规划"
    assert pending["plan"] == failed["plan"]
    assert pending["selected_skill"] == failed["selected_skill"]
    assert pending["eligible_skill_snapshots"] == failed["eligible_skill_snapshots"]
    assert pending["analysis_summary"] == failed["analysis_summary"]
    assert pending["revision"] == failed["revision"] + 1
    assert pending["error"] is None and pending["output_batch_id"] is None
    assert pending["render_generation"] != failed["render_generation"]
    new_batch, new_root = worker._output_identity(pending, tmp_path / "render")
    assert new_batch != old_batch and new_root != old_root
    assert partial.read_bytes() == b"preserve-the-original-partial-video"
    assert AutoEditProjectStore(store.database_path).get_project(failed["project_id"]) == pending


def test_render_retry_finishes_without_new_planner_or_current_skill_library(tmp_path, monkeypatch):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)
    store.enqueue(failed["project_id"], expected_revision=failed["revision"])

    def complete_local_render(current_store, project, *, root):
        assert project["plan"] == failed["plan"]
        assert project["selected_skill"] == failed["selected_skill"]
        batch_id, output = worker._output_identity(project, root)
        output.mkdir(parents=True)
        (output / "local-render-fixture.txt").write_text("local render completed", encoding="utf-8")
        return current_store.register_output_batch(
            project["project_id"], batch_id=batch_id, expected_revision=project["revision"],
            render_generation=project["render_generation"],
        )

    monkeypatch.setattr(worker, "render_and_register", complete_local_render)
    result = worker.process_one(
        store, available_skills=[], worker_id="retry-worker", root=tmp_path / "render",
        planner=lambda project: pytest.fail("重试本地渲染不得再次付费调用规划模型"),
    )

    assert result is not None, "冻结的已批准 Skill 足以恢复渲染，不应要求当前方法库"
    assert result["status"] == "review" and result["progress"] == 100
    assert result["plan"] == failed["plan"]
    assert result["output_batch_id"]
    assert store.get_project(failed["project_id"]) == result


def test_changed_settings_invalidate_saved_plan_before_requeue(tmp_path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)

    edited = store.update_project(
        failed["project_id"], expected_revision=failed["revision"],
        settings=_settings(target_count=1, duration_min_ms=25000),
    )

    assert edited["plan"] == [], "20 秒的旧计划不能用于新的 25 秒最短时长"
    assert edited["selected_skill"] is None
    assert not edited.get("registration_checkpoint")
    queued = store.enqueue(edited["project_id"], expected_revision=edited["revision"])
    assert queued["status"] == "queued" and queued["plan"] == []
    assert queued["render_generation"] != failed["render_generation"]
    claimed = store.claim_next(available_skills=_skills(), worker_id="new-planning-worker")
    assert claimed["status"] == "analyzing"
    assert claimed["settings"]["duration_min_ms"] == 25000


def test_render_retry_rejects_stale_revision_without_mutation(tmp_path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)

    with pytest.raises(AutoEditConflictError):
        store.enqueue(failed["project_id"], expected_revision=failed["revision"] - 1)

    assert store.get_project(failed["project_id"]) == failed


def test_render_retry_cannot_start_from_recycle_bin(tmp_path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)
    trashed = store.set_deleted(failed["project_id"], expected_revision=failed["revision"], deleted=True)

    with pytest.raises(AutoEditConflictError):
        store.enqueue(trashed["project_id"], expected_revision=trashed["revision"])

    assert store.get_project(failed["project_id"]) == trashed
    assert trashed["plan"] == failed["plan"]
    assert Path(failed["source"]["path"]).is_file()


def test_render_retry_interrupted_before_commit_keeps_failed_project(tmp_path, monkeypatch):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)

    def interrupt_before_commit(*args, **kwargs):
        raise KeyboardInterrupt("simulated termination after write, before commit")

    monkeypatch.setattr(store, "_event", interrupt_before_commit)
    with pytest.raises(KeyboardInterrupt):
        store.enqueue(failed["project_id"], expected_revision=failed["revision"])

    reopened = AutoEditProjectStore(store.database_path)
    reopened.recover_interrupted()
    assert reopened.get_project(failed["project_id"]) == failed


def test_committed_render_retry_survives_restart_without_replanning(tmp_path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)
    pending = store.enqueue(failed["project_id"], expected_revision=failed["revision"])
    # Termination occurs after enqueue commit but before a worker claims it.
    reopened = AutoEditProjectStore(store.database_path)
    reopened.recover_interrupted()

    assert reopened.get_project(failed["project_id"])["status"] == "render_pending"
    resumed = reopened.claim_next(available_skills=[], worker_id="resumed-worker")
    assert resumed is not None and resumed["status"] == "rendering"
    assert resumed["plan"] == failed["plan"]
    assert resumed["render_generation"] == pending["render_generation"]
    assert resumed["render_generation"] != failed["render_generation"]
    assert reopened.claim_next(available_skills=_skills(), worker_id="other-worker") is None


def test_old_render_worker_cannot_fail_or_register_retry_generation(tmp_path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)
    pending = store.enqueue(failed["project_id"], expected_revision=failed["revision"])
    assert pending["status"] == "render_pending"
    current = store.claim_next(available_skills=[], worker_id="retry-worker")
    assert current is not None and current["status"] == "rendering"

    assert store.fail(failed["project_id"], message="旧进程迟到错误", worker_id="original-worker") == current
    with pytest.raises(AutoEditConflictError):
        store.register_output_batch(
            failed["project_id"], batch_id="old_generation_output", expected_revision=current["revision"],
            render_generation=failed["render_generation"],
        )

    assert store.get_project(failed["project_id"]) == current


def _database_state(store):
    with sqlite3.connect(store.database_path) as connection:
        return {
            "projects": connection.execute("SELECT * FROM auto_edit_projects ORDER BY project_id").fetchall(),
            "events": connection.execute("SELECT * FROM auto_edit_events ORDER BY event_id").fetchall(),
        }


@pytest.mark.parametrize("damage", [
    "duration_over_maximum",
    "duration_under_minimum",
    "clip_outside_source",
    "unknown_source_id",
    "candidate_skill_snapshot_tampered",
    "selected_skill_snapshot_tampered",
    "unknown_candidate_skill_id",
    "missing_frozen_skill_library",
    "case_insensitive_candidate_collision",
    "unsafe_candidate_id",
    "candidate_count_mismatch",
    "zero_length_clip",
])
def test_damaged_saved_plan_is_atomically_rejected_before_render_retry(tmp_path, damage):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)
    damaged = copy.deepcopy(failed)
    candidate = damaged["plan"][0]
    if damage == "duration_over_maximum":
        candidate["clips"] = [{"start_ms": 1000, "end_ms": 41001}]
    elif damage == "duration_under_minimum":
        candidate["clips"] = [{"start_ms": 1000, "end_ms": 20999}]
    elif damage == "clip_outside_source":
        candidate["clips"] = [{"start_ms": 290000, "end_ms": 310000}]
    elif damage == "unknown_source_id":
        candidate["source_id"] = "source_from_a_different_project"
    elif damage == "candidate_skill_snapshot_tampered":
        candidate["skill_snapshot"]["mechanism"] = "unapproved change"
    elif damage == "selected_skill_snapshot_tampered":
        damaged["selected_skill"]["snapshot"]["revision"] += 1
    elif damage == "unknown_candidate_skill_id":
        candidate["skill_id"] = "not_in_frozen_library"
    elif damage == "missing_frozen_skill_library":
        damaged["eligible_skill_snapshots"] = []
    elif damage == "case_insensitive_candidate_collision":
        damaged["settings"]["target_count"] = 2
        duplicate = copy.deepcopy(candidate)
        duplicate["candidate_id"] = candidate["candidate_id"].upper()
        damaged["plan"].append(duplicate)
    elif damage == "unsafe_candidate_id":
        candidate["candidate_id"] = "../outside-project"
    elif damage == "candidate_count_mismatch":
        damaged["settings"]["target_count"] = 2
    elif damage == "zero_length_clip":
        candidate["clips"] = [{"start_ms": 1000, "end_ms": 21000}, {"start_ms": 15000, "end_ms": 15000}]
    else:
        raise AssertionError(f"Unknown fixture: {damage}")

    # Deliberately model damaged persisted history, bypassing the save-plan
    # boundary that correctly refuses to create these records in normal use.
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE auto_edit_projects SET payload_json=? WHERE project_id=?",
            (json.dumps(damaged, ensure_ascii=False), failed["project_id"]),
        )
    before = _database_state(store)
    _, old_output = worker._output_identity(failed, tmp_path / "render")
    old_output.mkdir(parents=True)
    partial = old_output / "candidate_01.clean.mp4"
    partial.write_bytes(b"do-not-rewrite-old-partial-result")

    with pytest.raises(ValueError):
        store.enqueue(failed["project_id"], expected_revision=failed["revision"])

    assert _database_state(store) == before, "拒绝必须同时保留 payload、revision、generation、租约和事件记录"
    assert AutoEditProjectStore(store.database_path).get_project(failed["project_id"]) == damaged
    assert partial.read_bytes() == b"do-not-rewrite-old-partial-result"


def test_title_only_edit_preserves_saved_plan_and_resumes_without_replanning(tmp_path):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)

    edited = store.update_project(
        failed["project_id"], expected_revision=failed["revision"],
        settings=failed["settings"], title="仅改项目显示名称",
    )

    assert edited["title"] == "仅改项目显示名称"
    assert edited["revision"] == failed["revision"] + 1
    for field in ("plan", "selected_skill", "eligible_skill_snapshots", "analysis_summary", "settings"):
        assert edited[field] == failed[field], f"仅改名称不得丢失 {field}"
    assert AutoEditProjectStore(store.database_path).get_project(failed["project_id"]) == edited
    pending = store.enqueue(edited["project_id"], expected_revision=edited["revision"])
    assert pending["status"] == "render_pending"
    assert pending["plan"] == failed["plan"]
    assert pending["render_generation"] != failed["render_generation"]
    resumed = store.claim_next(available_skills=[], worker_id="renamed-project-worker")
    assert resumed["status"] == "rendering"
    assert resumed["title"] == "仅改项目显示名称"


@pytest.mark.parametrize("changes", [
    {"target_count": 2},
    {"subtitle_font_size": 70},
    {"keyword_color": "#FF0000"},
])
def test_effective_settings_change_clears_all_frozen_planning_state(tmp_path, changes):
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    failed = _failed_render(store, tmp_path)

    edited = store.update_project(
        failed["project_id"], expected_revision=failed["revision"],
        settings={**failed["settings"], **changes},
    )

    assert edited["plan"] == []
    assert edited["eligible_skill_snapshots"] == []
    assert edited["selected_skill"] is None and edited["analysis_summary"] is None
    assert edited["settings"] == {**failed["settings"], **changes}
    assert not edited.get("registration_checkpoint")
    assert AutoEditProjectStore(store.database_path).get_project(failed["project_id"]) == edited
    queued = store.enqueue(edited["project_id"], expected_revision=edited["revision"])
    assert queued["status"] == "queued" and queued["plan"] == []
    assert queued["render_generation"] != failed["render_generation"]
