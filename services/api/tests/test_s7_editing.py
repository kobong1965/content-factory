from __future__ import annotations

from copy import deepcopy
import io
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory_api import s7, s7_renderer
from content_factory_api.main import app
from content_factory_api.s6_matching import build_shooting_task, confirm_suggestion
from content_factory_api.s6_store import MaterialStore
from content_factory_api.s7_projects import build_edit_project
from content_factory_api.s7_queue import RenderQueue
from content_factory_api.s7_renderer import _render_source_path, _silence_bounds
from content_factory_api.s7_store import EditConflictError, EditStore
from content_factory_media.tools import write_json_atomic

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _three_clip_material() -> dict:
    material = _json("material.valid.json")
    template = material["clips"][0]
    clips = []
    for index in range(3):
        clip = deepcopy(template)
        clip.update({
            "id": f"clip_demo_00{index + 1}", "source_shot_id": f"shot_00{index + 1}", "order": index + 1,
            "start_ms": index * 2000, "end_ms": (index + 1) * 2000,
            "keyframe_ref": f"keyframe_demo_00{index + 1}", "purpose_tags": ["broll"],
        })
        clips.append(clip)
    material["clips"] = clips
    return material


def _material_with_detail_clips() -> dict:
    material = _three_clip_material()
    template = material["clips"][0]
    for index in range(2):
        clip = deepcopy(template)
        clip.update({
            "id": f"clip_detail_00{index + 1}",
            "source_shot_id": f"shot_detail_00{index + 1}",
            "order": len(material["clips"]) + 1,
            "start_ms": 6000 + index * 2000,
            "end_ms": 8000 + index * 2000,
            "keyframe_ref": f"keyframe_detail_00{index + 1}",
            "transcript": "同款面料纹理细节",
            "purpose_tags": ["detail"],
            "visual_tags": ["面料纹理", "同款细节"],
            "garment_views": ["detail"],
            "action_tags": ["手指轻压面料"],
            "shot_size": "特写",
            "people_count": 0,
        })
        material["clips"].append(clip)
    material["file"]["duration_ms"] = 10000
    return material


def _two_segment_fixed_livestream_script() -> dict:
    script = _json("script.valid.json")
    version = script["versions"][0]
    first = deepcopy(version["shots"][0])
    first.update({"end_ms": 3000, "detail_overlay": {"mode": "none", "detail_tag": None, "instruction": "保持主播画面"}})
    second = deepcopy(first)
    second.update({
        "id": "script_shot_demo_a2", "order": 2, "start_ms": 3000, "end_ms": 6000,
        "visual": "主播保持固定机位继续展示袖口", "voiceover": "接着看袖口，原声和动作都不要从头重来。",
        "subtitle": "接着看袖口", "transition": "无（连续长镜头）",
    })
    version["shots"] = [first, second]
    script["shooting_order"][0]["shot_ids"].insert(1, second["id"])
    script["material_checklist"][0]["notes"] = "同一条主播连续长镜头的前半段"
    script["material_checklist"].insert(1, {
        "shot_id": second["id"], "status": "required", "notes": "同一条主播连续长镜头的后半段",
    })
    return script


def _ready_project(tmp_path: Path) -> tuple[dict, dict, MaterialStore]:
    material_store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _three_clip_material()
    material_store.create(material, media_result_path=tmp_path / "result.json", actor="摄影甲")
    script = _json("script.valid.json")
    for index, version in enumerate(script["versions"]):
        shot = version["shots"][0]
        material_store.confirm_match(
            script_id=script["script_id"], script_shot_id=shot["id"],
            suggestion={
                "material_id": material["material_id"], "clip_id": material["clips"][index]["id"],
                "score": 90, "repeat_risk": "low", "reason": "工程验收匹配",
            }, actor="编导甲",
        )
    shooting = build_shooting_task(material_store, script)
    return build_edit_project(script, shooting, material_store), script, material_store


def test_build_edit_project_only_from_ready_s6_matches(tmp_path: Path) -> None:
    project, script, _ = _ready_project(tmp_path)

    assert project["status"] == "draft"
    assert project["script_revision"] == script["revision"]
    assert project["selected_version_id"] == "version_demo_a"
    assert [variant["script_version_id"] for variant in project["variants"]] == ["version_demo_a"]
    assert project["variants"][0]["clips"][0]["material_clip_id"] == "clip_demo_001"
    assert project["settings"]["width"] == 1080 and project["settings"]["height"] == 1920


def test_unconfirmed_optional_detail_does_not_block_edit_and_adds_warning(tmp_path: Path) -> None:
    project, _, _ = _ready_project(tmp_path)

    for variant in project["variants"]:
        assert "visual_overlay" not in variant["clips"][0]
        assert any("细节覆盖" in warning and "主播原声" in warning for warning in variant["warnings"])


def test_confirmed_detail_becomes_visual_overlay_while_primary_audio_remains_authoritative(tmp_path: Path) -> None:
    material_store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _material_with_detail_clips()
    material_store.create(material, media_result_path=tmp_path / "result.json", actor="摄影甲")
    script = _json("script.valid.json")
    for index, version in enumerate(script["versions"]):
        shot = version["shots"][0]
        material_store.confirm_match(
            script_id=script["script_id"], script_shot_id=shot["id"],
            suggestion={
                "material_id": material["material_id"], "clip_id": material["clips"][index]["id"],
                "score": 90, "repeat_risk": "low", "reason": "主播连续主素材",
            }, actor="编导甲",
        )
    shooting = build_shooting_task(material_store, script)
    detail = next(
        item for item in shooting["requirements"]
        if item.get("requirement_kind") == "detail_overlay"
        and item.get("source_script_shot_id") == "script_shot_demo_a1"
    )
    shooting = confirm_suggestion(
        material_store, script, script_shot_id=detail["script_shot_id"],
        material_id=material["material_id"], clip_id="clip_detail_001", actor="编导甲",
    )

    project = build_edit_project(script, shooting, material_store)
    clip = project["variants"][0]["clips"][0]
    overlay = clip["visual_overlay"]

    assert (clip["material_id"], clip["material_clip_id"]) == (material["material_id"], "clip_demo_001")
    assert clip["has_source_audio"] is True
    assert overlay == {
        "material_id": material["material_id"],
        "material_clip_id": "clip_detail_001",
        "source_start_ms": 6000,
        "source_end_ms": 8000,
        "speed": 0.5,
        "detail_tag": "面料纹理",
        "instruction": "主播原声连续播放时覆盖同款面料近景",
        "audio_mode": "retain_primary",
    }
    assert not any("细节覆盖" in warning for warning in project["variants"][0]["warnings"])


def test_reused_host_take_is_split_into_contiguous_source_ranges_in_one_version(tmp_path: Path) -> None:
    material_store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _three_clip_material()
    material["archive"]["note"] = "[主播连续长镜头] 固定直播间一镜到底"
    material["clips"][0].update({"start_ms": 0, "end_ms": 6000})
    material["clips"][1].update({"start_ms": 6000, "end_ms": 8000})
    material["clips"][2].update({"start_ms": 8000, "end_ms": 10000})
    material["file"]["duration_ms"] = 10000
    saved, _ = material_store.create(material, media_result_path=tmp_path / "result.json", actor="摄影甲")
    full_take = next(clip for clip in saved["clips"] if clip.get("capture_scope") == "full_take")
    script = _two_segment_fixed_livestream_script()

    shooting = build_shooting_task(material_store, script)
    first, second = [
        item for item in shooting["requirements"]
        if item.get("requirement_kind") == "primary" and item["version_id"] == "version_demo_a"
    ]
    shooting = confirm_suggestion(
        material_store, script, script_shot_id=first["script_shot_id"],
        material_id=saved["material_id"], clip_id=full_take["id"], actor="编导甲",
    )
    shooting = confirm_suggestion(
        material_store, script, script_shot_id=second["script_shot_id"],
        material_id=saved["material_id"], clip_id=full_take["id"], actor="编导甲",
    )
    for index, version in enumerate(script["versions"][1:], start=1):
        material_store.confirm_match(
            script_id=script["script_id"], script_shot_id=version["shots"][0]["id"],
            suggestion={
                "material_id": material["material_id"], "clip_id": material["clips"][index]["id"],
                "score": 90, "repeat_risk": "low", "reason": "其余版本主素材",
            }, actor="编导甲",
        )
    shooting = build_shooting_task(material_store, script)

    project = build_edit_project(script, shooting, material_store)
    first_variant = project["variants"][0]

    assert shooting["status"] == "ready_for_edit"
    assert [(clip["source_start_ms"], clip["source_end_ms"]) for clip in first_variant["clips"]] == [
        (0, 5000), (5000, 10000),
    ]
    assert [clip["speed"] for clip in first_variant["clips"]] == [1.667, 1.667]
    assert all(clip["continuous_take"] is True for clip in first_variant["clips"])


def test_build_edit_project_rejects_a_silent_host_take_even_if_a_match_was_persisted(tmp_path: Path) -> None:
    material_store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _three_clip_material()
    material["archive"]["note"] = "[主播连续长镜头] 静音误上传"
    material["file"]["has_audio"] = False
    for clip in material["clips"]:
        clip["quality"]["audio"] = 0
        clip["quality"]["overall"] = round(sum(
            clip["quality"][field] for field in ("clarity", "stability", "audio", "exposure")
        ) / 4)
    saved, _ = material_store.create(
        material, media_result_path=tmp_path / "silent-host.json", actor="摄影甲",
    )
    full_take = next(clip for clip in saved["clips"] if clip.get("capture_scope") == "full_take")
    script = _json("script.valid.json")
    selected_shot = script["versions"][0]["shots"][0]
    material_store.confirm_match(
        script_id=script["script_id"], script_shot_id=selected_shot["id"],
        suggestion={
            "material_id": saved["material_id"], "clip_id": full_take["id"],
            "score": 90, "repeat_risk": "low", "reason": "模拟旧数据已确认静音主素材",
        }, actor="编导甲",
    )
    shooting = build_shooting_task(material_store, script)

    with pytest.raises(ValueError, match="主播连续长镜头.*原声"):
        build_edit_project(script, shooting, material_store)


def test_build_edit_project_rejects_incomplete_shooting_task(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    script = _json("script.valid.json")
    task = build_shooting_task(store, script)

    with pytest.raises(ValueError, match="没有确认素材"):
        build_edit_project(script, task, store)


def test_edit_store_versions_conflicts_outputs_and_review(tmp_path: Path) -> None:
    project = _json("edit-project.valid.json")
    store = EditStore(tmp_path / "s7")
    saved, duplicate = store.create_project(project, actor="编辑甲")
    same, is_duplicate = store.create_project(project, actor="编辑乙")
    assert duplicate is False and is_duplicate is True and same["project_id"] == saved["project_id"]

    updated = store.save_project(
        project["project_id"], expected_revision=1, actor="编辑甲",
        settings=project["settings"], variants=project["variants"],
    )
    assert updated["revision"] == 2 and len(store.project_revisions(project["project_id"])) == 2
    with pytest.raises(EditConflictError):
        store.save_project(
            project["project_id"], expected_revision=1, actor="编辑乙",
            settings=project["settings"], variants=project["variants"],
        )

    output = _json("render-output.valid.json")
    output["project_revision"] = 2
    output_directory = store.output_root / output["output_id"]
    output_directory.mkdir()
    resources = {}
    for key, resource_ref in output["resources"].items():
        if key == "jianying_experimental":
            continue
        path = output_directory / f"{key}.bin"
        path.write_bytes(b"fixture-output")
        resources[resource_ref] = (path, "application/octet-stream", path.name)
    store.create_output(output, resources=resources)
    reviewed = store.review_output(output["output_id"], decision="approved", reviewer="成片审核甲", note="通过")

    assert reviewed["status"] == "approved"
    assert store.get_project(project["project_id"])["status"] == "approved"
    assert str(tmp_path) not in json.dumps(reviewed, ensure_ascii=False)


def test_put_project_accepts_the_single_selected_shooting_variant(monkeypatch, tmp_path: Path) -> None:
    project, script, material_store = _ready_project(tmp_path)
    edit_store = EditStore(tmp_path / "s7")
    edit_store.create_project(project, actor="编辑甲")
    monkeypatch.setattr(s7, "get_edit_store", lambda: edit_store)
    monkeypatch.setattr(s7, "get_material_store", lambda: material_store)
    monkeypatch.setattr(s7, "_fixtures_allowed", lambda: True)
    monkeypatch.setattr(
        s7,
        "_approved_script",
        lambda script_id, *, include_fixtures: script if script_id == script["script_id"] else None,
    )
    client = TestClient(app)

    response = client.put(
        f"/s7/projects/{project['project_id']}",
        json={
            "expected_revision": project["revision"],
            "actor": "编辑甲",
            "settings": project["settings"],
            "variants": project["variants"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert [item["script_version_id"] for item in response.json()["variants"]] == ["version_demo_a"]


@pytest.mark.parametrize("lineage_change", ["script_revision", "selected_version_id"])
def test_create_project_rebases_when_the_upstream_script_lineage_changes(
    tmp_path: Path, lineage_change: str,
) -> None:
    original = _json("edit-project.valid.json")
    original["selected_version_id"] = "version_demo_a"
    original["variants"] = [
        item for item in original["variants"] if item["script_version_id"] == "version_demo_a"
    ]
    store = EditStore(tmp_path / lineage_change / "s7")
    saved, duplicate = store.create_project(original, actor="编辑甲")
    assert duplicate is False

    rebuilt = deepcopy(original)
    rebuilt["project_id"] = f"edit_project_rebuilt_{lineage_change}"
    if lineage_change == "script_revision":
        rebuilt["script_revision"] += 1
    else:
        rebuilt["selected_version_id"] = "version_demo_b"
        source = _json("edit-project.valid.json")
        rebuilt["variants"] = [
            item for item in source["variants"] if item["script_version_id"] == "version_demo_b"
        ]

    refreshed, is_duplicate = store.create_project(rebuilt, actor="编辑乙")

    assert is_duplicate is False
    assert refreshed["project_id"] == saved["project_id"]
    assert refreshed["revision"] == saved["revision"] + 1
    assert refreshed["created_at"] == saved["created_at"]
    assert refreshed["script_revision"] == rebuilt["script_revision"]
    assert refreshed["selected_version_id"] == rebuilt["selected_version_id"]
    assert [item["script_version_id"] for item in refreshed["variants"]] == [
        item["script_version_id"] for item in rebuilt["variants"]
    ]
    assert len(store.list_projects(include_fixtures=True)) == 1
    assert [item["action"] for item in store.project_revisions(saved["project_id"])] == [
        "script_rebased", "created",
    ]


def test_old_output_cannot_be_approved_after_project_changes(tmp_path: Path) -> None:
    project = _json("edit-project.valid.json")
    store = EditStore(tmp_path / "s7")
    store.create_project(project, actor="编辑甲")

    output = _json("render-output.valid.json")
    output_directory = store.output_root / output["output_id"]
    output_directory.mkdir()
    resources = {}
    for key, resource_ref in output["resources"].items():
        if key == "jianying_experimental":
            continue
        path = output_directory / f"{key}.bin"
        path.write_bytes(b"fixture-output")
        resources[resource_ref] = (path, "application/octet-stream", path.name)
    store.create_output(output, resources=resources)

    store.save_project(
        project["project_id"], expected_revision=1, actor="编辑甲",
        settings=project["settings"], variants=project["variants"],
    )

    with pytest.raises(EditConflictError, match="旧工程修订"):
        store.review_output(output["output_id"], decision="approved", reviewer="成片审核甲", note="误点通过")


def test_render_queue_recovers_retries_and_hides_paths(tmp_path: Path) -> None:
    project = _json("edit-project.valid.json")
    snapshot = tmp_path / "private" / "project.json"
    write_json_atomic(snapshot, project)
    queue = RenderQueue(tmp_path / "render.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(project, project["variants"][0]["id"], snapshot_path=snapshot, workspace_path=tmp_path / "workspace", max_attempts=1)
    assert str(tmp_path) not in json.dumps(task.to_dict(), ensure_ascii=False)
    with pytest.raises(ValueError, match="正在渲染"):
        queue.enqueue(project, project["variants"][0]["id"], snapshot_path=snapshot, workspace_path=tmp_path / "workspace-2")

    def fail(record, _progress):
        raise ValueError(f"无法读取 {record.snapshot_path} 和 {record.workspace_path}")

    finished = queue.run_pending(fail)[0]
    assert finished.status == "failed"
    assert str(tmp_path) not in json.dumps(finished.to_dict(), ensure_ascii=False)
    assert "本机受控文件" in (finished.error or "")


def test_render_recovery_reuses_output_registered_before_queue_completion(monkeypatch, tmp_path: Path) -> None:
    project = _json("edit-project.valid.json")
    edit_store = EditStore(tmp_path / "s7")
    edit_store.create_project(project, actor="编辑甲")
    snapshot = tmp_path / "workspace" / "project.json"
    write_json_atomic(snapshot, project)
    queue = RenderQueue(tmp_path / "render.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        project, project["variants"][0]["id"], snapshot_path=snapshot,
        workspace_path=tmp_path / "workspace", max_attempts=1,
    )
    running = queue._claim()
    assert running is not None and running.task_id == task.task_id

    output = _json("render-output.valid.json")
    output["render"]["task_id"] = task.task_id
    output_directory = edit_store.output_root / output["output_id"]
    output_directory.mkdir()
    resources = {}
    for key, resource_ref in output["resources"].items():
        if key == "jianying_experimental":
            continue
        path = output_directory / f"{key}.bin"
        path.write_bytes(b"fixture-output")
        resources[resource_ref] = (path, "application/octet-stream", path.name)
    # Resource integrity now checks actual bytes; the fixture metadata must
    # describe the small fake file used by this recovery test.
    output['media']['sha256'] = hashlib.sha256(b'fixture-output').hexdigest()
    output['media']['size_bytes'] = len(b'fixture-output')
    edit_store.create_output(output, resources=resources)
    monkeypatch.setattr(s7, "get_edit_store", lambda: edit_store)
    assert queue.recover_interrupted(s7._reconcile_interrupted_render) == 1

    def unexpected_render_tool(_name: str) -> str:
        raise AssertionError("已登记的同一任务成片应被复用，不应再启动渲染")

    monkeypatch.setattr(s7_renderer, "find_tool", unexpected_render_tool)
    completed = queue.get(task.task_id)
    assert completed is not None

    assert completed.status == "completed"
    assert completed.output_id == output["output_id"]
    assert [item["output_id"] for item in edit_store.list_outputs(include_fixtures=True)] == [output["output_id"]]
    assert {path.name for path in edit_store.output_root.iterdir()} == {output["output_id"]}


def test_render_last_attempt_without_registered_output_is_failed(tmp_path: Path) -> None:
    project = _json("edit-project.valid.json")
    snapshot = tmp_path / "workspace" / "project.json"
    write_json_atomic(snapshot, project)
    queue = RenderQueue(tmp_path / "render.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        project, project["variants"][0]["id"], snapshot_path=snapshot,
        workspace_path=tmp_path / "workspace", max_attempts=1,
    )
    assert queue._claim() is not None

    assert queue.recover_interrupted(lambda _task: None) == 1

    recovered = queue.get(task.task_id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.output_id is None


def test_render_last_attempt_with_incomplete_registered_output_is_failed(
    monkeypatch, tmp_path: Path,
) -> None:
    project = _json("edit-project.valid.json")
    edit_store = EditStore(tmp_path / "s7")
    edit_store.create_project(project, actor="编辑甲")
    snapshot = tmp_path / "workspace" / "project.json"
    write_json_atomic(snapshot, project)
    queue = RenderQueue(tmp_path / "render.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        project, project["variants"][0]["id"], snapshot_path=snapshot,
        workspace_path=tmp_path / "workspace", max_attempts=1,
    )
    assert queue._claim() is not None

    output = _json("render-output.valid.json")
    output["render"]["task_id"] = task.task_id
    output_directory = edit_store.output_root / output["output_id"]
    output_directory.mkdir()
    resources = {}
    for key, resource_ref in output["resources"].items():
        if key == "jianying_experimental":
            continue
        path = output_directory / f"{key}.bin"
        path.write_bytes(b"fixture-output")
        resources[resource_ref] = (path, "application/octet-stream", path.name)
    edit_store.create_output(output, resources=resources)
    Path(resources[output["resources"]["video_ref"]][0]).unlink()
    monkeypatch.setattr(s7, "get_edit_store", lambda: edit_store)

    assert queue.recover_interrupted(s7._reconcile_interrupted_render) == 1

    recovered = queue.get(task.task_id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.output_id is None
    assert edit_store.list_outputs(include_fixtures=True) == []
    assert edit_store.get_project(project["project_id"])["variants"][0]["latest_output_id"] is None
    assert edit_store.recovery_audits()[0]["output_id"] == output["output_id"]

    replacement = deepcopy(output)
    replacement["output_id"] = "render_output_recovered_001"
    replacement["resources"] = {
        key: value if key == "jianying_experimental" else f"render_recovered_{key}"
        for key, value in replacement["resources"].items()
    }

    def render_replacement(record, _progress):
        replacement_directory = edit_store.output_root / replacement["output_id"]
        replacement_directory.mkdir()
        replacement_resources = {}
        for key, resource_ref in replacement["resources"].items():
            if key == "jianying_experimental":
                continue
            path = replacement_directory / f"{key}.bin"
            path.write_bytes(b"replacement-output")
            replacement_resources[resource_ref] = (path, "application/octet-stream", path.name)
        edit_store.create_output(replacement, resources=replacement_resources)
        return replacement["output_id"]

    queue.retry(task.task_id)
    retried = queue.run_pending(render_replacement, max_workers=1)[0]
    assert retried.status == "completed"
    assert retried.output_id == replacement["output_id"]
    assert [item["output_id"] for item in edit_store.list_outputs(include_fixtures=True)] == [replacement["output_id"]]
    assert edit_store.get_project(project["project_id"])["variants"][0]["latest_output_id"] == replacement["output_id"]


def test_renderer_archives_a_legacy_incomplete_output_before_manual_retry(
    monkeypatch, tmp_path: Path,
) -> None:
    project = _json("edit-project.valid.json")
    edit_store = EditStore(tmp_path / "s7")
    edit_store.create_project(project, actor="编辑甲")
    snapshot = tmp_path / "workspace" / "project.json"
    write_json_atomic(snapshot, project)
    queue = RenderQueue(tmp_path / "render.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        project, project["variants"][0]["id"], snapshot_path=snapshot,
        workspace_path=tmp_path / "workspace", max_attempts=1,
    )
    running = queue._claim()
    assert running is not None

    output = _json("render-output.valid.json")
    output["render"]["task_id"] = task.task_id
    output_directory = edit_store.output_root / output["output_id"]
    output_directory.mkdir()
    resources = {}
    for key, resource_ref in output["resources"].items():
        if key == "jianying_experimental":
            continue
        path = output_directory / f"{key}.bin"
        path.write_bytes(b"fixture-output")
        resources[resource_ref] = (path, "application/octet-stream", path.name)
    edit_store.create_output(output, resources=resources)
    Path(resources[output["resources"]["video_ref"]][0]).unlink()

    class RenderRestarted(BaseException):
        pass

    def renderer_started(_name: str) -> str:
        raise RenderRestarted

    monkeypatch.setattr(s7_renderer, "find_tool", renderer_started)
    with pytest.raises(RenderRestarted):
        s7_renderer.render_project(
            running, project, material_store=MaterialStore(tmp_path / "materials.sqlite3"),
            edit_store=edit_store, s6_media_root=tmp_path / "media", progress=lambda *_args: None,
        )

    assert edit_store.list_outputs(include_fixtures=True) == []
    assert edit_store.recovery_audits()[0]["output_id"] == output["output_id"]


def test_audio_import_deduplicates_and_rejects_invalid_file(monkeypatch, tmp_path: Path) -> None:
    store = EditStore(tmp_path / "s7")
    monkeypatch.setattr(store, "_probe_audio", lambda _path: (1000, 48000, 2))
    asset, duplicate = store.add_audio(
        kind="bgm", name="测试 BGM", original_name="bgm.mp3", license_note="自有授权",
        imported_by="编辑甲", fixture_data=True, stream=io.BytesIO(b"ID3-fixture-audio"),
    )
    same, is_duplicate = store.add_audio(
        kind="bgm", name="测试 BGM", original_name="bgm.mp3", license_note="自有授权",
        imported_by="编辑甲", fixture_data=True, stream=io.BytesIO(b"ID3-fixture-audio"),
    )
    assert duplicate is False and is_duplicate is True and same["asset_id"] == asset["asset_id"]
    with pytest.raises(ValueError, match="仅支持"):
        store.add_audio(
            kind="bgm", name="错误", original_name="bad.exe", license_note="无版权",
            imported_by="编辑甲", fixture_data=True, stream=io.BytesIO(b"bad"),
        )


def test_only_obvious_edge_silence_is_trimmed() -> None:
    log = "silence_start: 0\nsilence_end: 0.42\nsilence_start: 5.35\n"

    assert _silence_bounds(log, 6000) == (420, 650)
    assert _silence_bounds("silence_start: 2.1\nsilence_end: 2.8\n", 6000) == (0, 0)


def test_final_render_uses_the_managed_original_instead_of_the_analysis_proxy(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    original = media_root / "task" / "original.mp4"
    proxy = media_root / "task" / "proxy.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original-source")
    proxy.write_bytes(b"low-quality-proxy")
    result = {
        "source": {"managed_original_path": str(original)},
        "artifacts": {"proxy_path": str(proxy)},
    }

    assert _render_source_path(result, allowed_root=media_root) == original.resolve()


def test_duplicate_render_request_does_not_leave_an_orphan_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S7_DATA_DIR", str(tmp_path / "s7"))
    monkeypatch.setenv("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS", "1")
    s7._stores.clear()
    s7._queues.clear()
    project = _json("edit-project.valid.json")
    s7.get_edit_store().create_project(project, actor="工程验收")
    existing = tmp_path / "s7" / "tasks" / "existing"
    snapshot = existing / "project-snapshot.json"
    write_json_atomic(snapshot, project)
    s7.get_render_queue().enqueue(
        project,
        project["variants"][0]["id"],
        snapshot_path=snapshot,
        workspace_path=existing,
    )
    before = {item.name for item in (tmp_path / "s7" / "tasks").iterdir()}
    client = TestClient(app)

    response = client.post(f"/s7/projects/{project['project_id']}/render", json={
        "expected_revision": project["revision"],
        "variant_id": project["variants"][0]["id"],
    })

    after = {item.name for item in (tmp_path / "s7" / "tasks").iterdir()}
    assert response.status_code == 422
    assert after == before


def test_s7_routes_expose_no_private_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S7_DATA_DIR", str(tmp_path / "s7"))
    monkeypatch.setenv("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS", "1")
    s7._stores.clear()
    s7._queues.clear()
    project = _json("edit-project.valid.json")
    s7.get_edit_store().create_project(project, actor="工程验收")
    client = TestClient(app)

    response = client.get("/s7/projects")

    assert response.status_code == 200
    assert response.json()[0]["project_id"] == project["project_id"]
    assert str(tmp_path) not in response.text
    assert "/s7/outputs/{output_id}/resources/{resource_ref}" in app.openapi()["paths"]
