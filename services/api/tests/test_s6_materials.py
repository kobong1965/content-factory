from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from content_factory_api import s6
from content_factory_api.main import app
from content_factory_api.s3_gateway import GatewayError, GatewayResult
from content_factory_api.s3_settings import GatewayConfig
from content_factory_api.s6_matching import build_shooting_task, confirm_suggestion
from content_factory_api.s6_materials import build_material_profile
from content_factory_api.s6_queue import MaterialImportQueue
from content_factory_api.s6_recognition import recognize_material
from content_factory_api.s6_store import MaterialConflictError, MaterialStore
from content_factory_media.models import MediaInfo

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"
CONFIG = GatewayConfig(
    "http://127.0.0.1:9999/v1", "fixture-model", "responses", "fixture-secret", "2026-08-29T06:00:00Z",
)


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


def test_build_material_profile_from_s2_result(tmp_path: Path) -> None:
    result = _json("media-result.valid.json")
    result["source"]["original_name"] = "中文长文件名.mp4"
    result["artifacts"]["transcript_path"] = None
    result["artifacts"]["audio_path"] = None
    result["media"]["has_audio"] = False
    result["media"]["audio_codec"] = None
    result["asr"] = {"status": "no_audio", "model_name": None, "language": None, "segment_count": 0}
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    profile = build_material_profile(
        result_path, product=_json("product.valid.json"), source_script_id="script_demo_001",
        archive={"model_name": "模特甲", "scene": "白墙", "shot_date": "2026-08-29", "batch": "B01", "imported_by": "摄影甲", "note": ""},
        recognition_configured=False,
    )

    assert profile["file"]["original_name"] == "中文长文件名.mp4"
    assert profile["processing"]["recognition_status"] == "not_configured"
    assert profile["processing"]["original_video_uploaded"] is False
    assert len(profile["clips"]) == len(result["shots"])


def test_detail_upload_role_sets_deterministic_tags_without_ai(tmp_path: Path) -> None:
    result = _json("media-result.valid.json")
    result["artifacts"]["transcript_path"] = None
    result["artifacts"]["audio_path"] = None
    result["media"]["has_audio"] = False
    result["media"]["audio_codec"] = None
    result["asr"] = {"status": "no_audio", "model_name": None, "language": None, "segment_count": 0}
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    profile = build_material_profile(
        result_path, product=_json("product.valid.json"), source_script_id="script_demo_001",
        archive={
            "model_name": "模特甲", "scene": "直播间", "shot_date": "2026-08-29", "batch": "B01",
            "imported_by": "摄影甲", "note": "[同款商品细节镜头] 用于面料和裤脚覆盖",
        },
        recognition_configured=False,
    )

    assert profile["processing"]["recognition_status"] == "not_configured"
    assert all(clip["purpose_tags"] == ["detail"] and clip["reusable"] for clip in profile["clips"])


def test_host_take_import_uses_structured_role_user_note_and_a_controlled_full_clip(tmp_path: Path) -> None:
    result = _json("media-result.valid.json")
    result["artifacts"]["transcript_path"] = None
    result["artifacts"]["audio_path"] = None
    result["media"]["has_audio"] = False
    result["media"]["audio_codec"] = None
    result["asr"] = {"status": "no_audio", "model_name": None, "language": None, "segment_count": 0}
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    profile = build_material_profile(
        result_path, product=_json("product.valid.json"), source_script_id="script_demo_001",
        archive={
            "model_name": "主播 1 人", "scene": "固定直播间", "shot_date": "2026-08-29", "batch": "B01",
            "imported_by": "摄影甲", "note": "脚本 A 连续录制",
        },
        capture_role="host_take",
        recognition_configured=False,
    )

    full_take = next(clip for clip in profile["clips"] if clip.get("capture_scope") == "full_take")
    scene_clips = [clip for clip in profile["clips"] if clip.get("capture_scope") == "scene"]
    assert profile["capture_role"] == "host_take"
    assert profile["archive"]["note"] == "脚本 A 连续录制"
    assert (full_take["start_ms"], full_take["end_ms"]) == (0, profile["file"]["duration_ms"])
    assert len(scene_clips) == len(result["shots"])


def test_legacy_note_role_is_migrated_once_and_later_note_edits_do_not_change_role(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    legacy = _three_clip_material()
    legacy["archive"]["note"] = "[主播连续长镜头] 原始用户备注"
    encoded = json.dumps(legacy, ensure_ascii=False, separators=(",", ":"))
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                legacy["material_id"], legacy["product_id"], legacy["file"]["sha256"], encoded,
                str(tmp_path / "result.json"), legacy["created_at"], legacy["updated_at"],
            ),
        )

    saved = store.get(legacy["material_id"])
    assert saved["capture_role"] == "host_take"
    assert saved["archive"]["note"] == "原始用户备注"
    assert any(clip.get("capture_scope") == "full_take" for clip in saved["clips"])

    changed = store.update(
        saved["material_id"], expected_revision=saved["revision"],
        archive={**saved["archive"], "note": "[同款商品细节镜头] 这只是用户新备注"},
        clips=saved["clips"], actor="编导甲",
    )

    assert changed["capture_role"] == "host_take"
    assert changed["archive"]["note"] == "[同款商品细节镜头] 这只是用户新备注"


def test_build_material_profile_rejects_fixture_product_mixing(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_json("media-result.valid.json"), ensure_ascii=False), encoding="utf-8")
    product = _json("product.valid.json")
    product["fixture_data"] = False

    with pytest.raises(ValueError, match="不能混用"):
        build_material_profile(
            result_path, product=product, source_script_id=None,
            archive={"model_name": "模特甲", "scene": "白墙", "shot_date": "2026-08-29", "batch": "B01", "imported_by": "摄影甲"},
            recognition_configured=False,
        )


def test_material_store_deduplicates_versions_and_detects_conflict(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _json("material.valid.json")
    saved, duplicate = store.create(material, media_result_path=tmp_path / "result.json", actor="摄影甲")
    duplicate_saved, is_duplicate = store.create(material, media_result_path=tmp_path / "other.json", actor="摄影乙")

    assert duplicate is False and is_duplicate is True
    assert duplicate_saved["material_id"] == saved["material_id"]
    updated = store.update(
        saved["material_id"], expected_revision=1, archive={**saved["archive"], "scene": "办公室"},
        clips=saved["clips"], actor="编导甲",
    )
    assert updated["revision"] == 2
    assert len(store.revisions(saved["material_id"])) == 2
    with pytest.raises(MaterialConflictError):
        store.update(
            saved["material_id"], expected_revision=1, archive=updated["archive"], clips=updated["clips"], actor="编导乙",
        )


def test_recognition_uses_keyframes_only_and_batches_at_sixteen(tmp_path: Path) -> None:
    material = _json("material.valid.json")
    template = material["clips"][0]
    clips = []
    shots = []
    calls: list[dict] = []
    for index in range(17):
        frame = tmp_path / f"frame-{index}.jpg"
        frame.write_bytes(b"jpeg-fixture")
        clip = deepcopy(template)
        clip.update({
            "id": f"clip_batch_{index:03d}", "source_shot_id": f"shot_batch_{index:03d}", "order": index + 1,
            "start_ms": index * 100, "end_ms": (index + 1) * 100, "keyframe_ref": f"keyframe_batch_{index:03d}",
        })
        clips.append(clip)
        shots.append({"id": clip["source_shot_id"], "keyframe_path": str(frame)})
    material["clips"] = clips
    material["file"]["duration_ms"] = 1700

    def gateway(_config, **kwargs):
        calls.append(kwargs)
        context = json.loads(kwargs["context_json"])
        return GatewayResult(content={"clips": [{
            "clip_id": item["clip_id"], "purpose_tags": ["proof"], "visual_tags": ["正面展示"],
            "garment_views": ["front"], "action_tags": ["站立"], "scene_tags": ["白墙"], "shot_size": "中景",
            "people_count": 1, "standalone_usable": True,
            "quality": {"clarity": 80, "stability": 80, "audio": 80, "exposure": 80},
        } for item in context["clips"]]}, response_id="fixture", raw_bytes=100)

    updated = recognize_material(material, {"shots": shots}, CONFIG, gateway_caller=gateway)

    assert [len(call["keyframe_data_urls"]) for call in calls] == [16, 1]
    assert all(value.startswith("data:image/jpeg;base64,") for call in calls for value in call["keyframe_data_urls"])
    assert all("original" not in call["context_json"].casefold() or '"original_video_uploaded":false' in call["context_json"] for call in calls)
    assert str(tmp_path) not in "".join(call["context_json"] for call in calls)
    assert updated["processing"]["recognition_status"] == "completed"
    assert updated["clips"][0]["quality"]["overall"] == 80


def test_shooting_task_advances_and_rolls_back_with_audit(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _three_clip_material()
    store.create(material, media_result_path=tmp_path / "result.json", actor="摄影甲")
    script = _json("script.valid.json")

    task = build_shooting_task(store, script)
    assert task["status"] == "pending_shoot" and task["missing_count"] == 1
    used: set[str] = set()
    for requirement in (
        item for item in task["requirements"]
        if item.get("requirement_kind", "primary") == "primary"
    ):
        suggestion = next(item for item in requirement["suggestions"] if item["clip_id"] not in used)
        used.add(suggestion["clip_id"])
        task = confirm_suggestion(
            store, script, script_shot_id=requirement["script_shot_id"], material_id=suggestion["material_id"],
            clip_id=suggestion["clip_id"], actor="编导甲",
        )
    assert task["status"] == "ready_for_edit" and task["missing_count"] == 0
    first_shot = script["versions"][0]["shots"][0]["id"]
    store.release_match(script_id=script["script_id"], script_shot_id=first_shot, actor="编导乙")
    rolled_back = build_shooting_task(store, script)
    assert rolled_back["status"] == "pending_shoot" and rolled_back["missing_count"] == 1
    events = store.usage(material["material_id"])
    assert {event["action"] for event in events} == {"confirmed", "released"}


def test_shooting_task_only_requires_the_selected_script_version(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    script = _json("script.valid.json")

    task = build_shooting_task(store, script)

    assert task["selected_version_id"] == "version_demo_a"
    assert {item["version_id"] for item in task["requirements"]} == {"version_demo_a"}
    assert [
        item["script_shot_id"] for item in task["requirements"]
        if item.get("requirement_kind", "primary") == "primary"
    ] == ["script_shot_demo_a1"]
    assert task["missing_count"] == 1


def test_optional_detail_requirements_are_independent_non_blocking_and_same_product(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _material_with_detail_clips()
    store.create(material, media_result_path=tmp_path / "same-product.json", actor="摄影甲")

    other_product = deepcopy(material)
    other_product.update({"material_id": "material_other_001", "product_id": "product_other_001"})
    other_product["file"]["sha256"] = "c" * 64
    other_product["clips"][3]["id"] = "clip_other_detail_001"
    other_product["clips"][3]["quality"] = {
        "clarity": 100, "stability": 100, "audio": 100, "exposure": 100, "overall": 100,
    }
    store.create(other_product, media_result_path=tmp_path / "other-product.json", actor="摄影乙")

    script = _json("script.valid.json")
    selected_shot = script["versions"][0]["shots"][0]
    store.confirm_match(
        script_id=script["script_id"], script_shot_id=selected_shot["id"],
        suggestion={
            "material_id": material["material_id"], "clip_id": material["clips"][0]["id"],
            "score": 90, "repeat_risk": "low", "reason": "主播连续主素材",
        }, actor="编导甲",
    )

    task = build_shooting_task(store, script)
    primary = [item for item in task["requirements"] if item.get("requirement_kind", "primary") == "primary"]
    details = [item for item in task["requirements"] if item.get("requirement_kind") == "detail_overlay"]

    assert len(primary) == 1
    assert len(details) == 1
    assert task["status"] == "ready_for_edit" and task["missing_count"] == 0 and task["matched_count"] == 1
    assert all(item["requirement_status"] == "optional" for item in details)
    assert all(item["source_script_shot_id"] == "script_shot_demo_a1" for item in details)
    assert all(item["script_shot_id"].startswith("detail_overlay_") for item in details)
    assert all(item["detail_tag"] == "面料纹理" for item in details)
    assert all(item["match_status"] == "suggested" for item in details)
    assert all(
        suggestion["material_id"] == material["material_id"]
        for item in details for suggestion in item["suggestions"]
    )

    first_detail = details[0]
    with pytest.raises(ValueError, match="同商品"):
        confirm_suggestion(
            store, script, script_shot_id=first_detail["script_shot_id"],
            material_id=other_product["material_id"], clip_id="clip_other_detail_001", actor="编导甲",
        )
    confirmed = confirm_suggestion(
        store, script, script_shot_id=first_detail["script_shot_id"],
        material_id=material["material_id"], clip_id="clip_detail_001", actor="编导甲",
    )
    confirmed_detail = next(
        item for item in confirmed["requirements"] if item["script_shot_id"] == first_detail["script_shot_id"]
    )
    assert confirmed_detail["match_status"] == "confirmed"
    assert confirmed["status"] == "ready_for_edit" and confirmed["missing_count"] == 0

def test_host_take_can_match_primary_but_is_never_a_generic_detail_overlay(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _three_clip_material()
    material["archive"]["note"] = "[主播连续长镜头] 当前脚本主素材"
    for clip in material["clips"]:
        clip["purpose_tags"] = ["detail", "broll"]
    store.create(material, media_result_path=tmp_path / "host-take.json", actor="摄影甲")

    task = build_shooting_task(store, _json("script.valid.json"))
    primary = [item for item in task["requirements"] if item.get("requirement_kind") == "primary"]
    details = [item for item in task["requirements"] if item.get("requirement_kind") == "detail_overlay"]

    assert all(item["suggestions"] for item in primary)
    assert all(item["suggestions"] == [] and item["match_status"] == "optional" for item in details)


def test_silent_host_take_is_not_suggested_and_cannot_be_confirmed(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _three_clip_material()
    material["archive"]["note"] = "[主播连续长镜头] 静音误上传"
    material["file"]["has_audio"] = False
    for clip in material["clips"]:
        clip["quality"]["audio"] = 0
        clip["quality"]["overall"] = round(sum(
            clip["quality"][field] for field in ("clarity", "stability", "audio", "exposure")
        ) / 4)
    saved, _ = store.create(material, media_result_path=tmp_path / "silent-host.json", actor="摄影甲")
    full_take = next(clip for clip in saved["clips"] if clip.get("capture_scope") == "full_take")

    task = build_shooting_task(store, _json("script.valid.json"))
    primary = next(item for item in task["requirements"] if item.get("requirement_kind") == "primary")

    assert all(item["material_id"] != saved["material_id"] for item in primary["suggestions"])
    with pytest.raises(ValueError, match="主播连续长镜头.*原声"):
        confirm_suggestion(
            store, _json("script.valid.json"), script_shot_id=primary["script_shot_id"],
            material_id=saved["material_id"], clip_id=full_take["id"], actor="编导甲",
        )


def test_matching_never_crosses_products(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials.sqlite3")
    material = _three_clip_material()
    material["product_id"] = "product_other_001"
    store.create(material, media_result_path=tmp_path / "result.json", actor="摄影甲")

    task = build_shooting_task(store, _json("script.valid.json"))

    assert all(requirement["suggestions"] == [] for requirement in task["requirements"])


def test_material_queue_recovers_and_hides_internal_paths(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mp4"
    source.write_bytes(b"fixture")
    queue = MaterialImportQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        source, tmp_path / "workspace", fixture_data=True, source_name="fixture.mp4",
        product_id="product_demo_001", source_script_id=None, input_payload={"product": {}, "archive": {}},
    )
    public = task.to_dict()
    assert "source_path" not in public and str(tmp_path) not in json.dumps(public)

    queue.run_pending(lambda _task, callback: (callback("archive", 90), "material_demo_001")[1])
    assert queue.get(task.task_id).status == "completed"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "recognition_outcome", ["completed", "failed", "configuration_unavailable", "corrupt_media"],
)
def test_material_import_recovery_finishes_a_previously_stored_pending_recognition(
    monkeypatch, tmp_path: Path, recognition_outcome: str,
) -> None:
    source = tmp_path / "fixture.mp4"
    source.write_bytes(b"fixture-video")
    workspace = tmp_path / "workspace"
    media_root = workspace / "media"
    media_root.mkdir(parents=True)
    media_result = _json("media-result.valid.json")
    managed_original = media_root / "original.mp4"
    proxy = media_root / "proxy.mp4"
    audio = media_root / "audio.wav"
    transcript = media_root / "transcript.json"
    managed_original.write_bytes(b"managed-original")
    proxy.write_bytes(b"proxy")
    audio.write_bytes(b"audio-fixture")
    transcript.write_text('{"segments":[]}', encoding="utf-8")
    media_result["source"]["managed_original_path"] = str(managed_original)
    media_result["artifacts"].update({
        "proxy_path": str(proxy), "audio_path": str(audio), "transcript_path": str(transcript),
        "scene_manifest_path": str(media_root / "scenes.json"),
    })
    for shot in media_result["shots"]:
        keyframe = media_root / f"{shot['id']}.jpg"
        keyframe.write_bytes(b"jpeg-fixture")
        shot["keyframe_path"] = str(keyframe)
    result_path = media_root / "result.json"
    result_path.write_text(json.dumps(media_result, ensure_ascii=False), encoding="utf-8")

    store = MaterialStore(tmp_path / "materials.sqlite3")
    queue = MaterialImportQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        source, workspace, fixture_data=True, source_name="fixture.mp4",
        product_id="product_demo_001", source_script_id=None,
        input_payload={
            "product": _json("product.valid.json"),
            "archive": {
                "model_name": "模特甲", "scene": "白墙", "shot_date": "2026-08-29",
                "batch": "B01", "imported_by": "摄影甲", "note": "",
            },
            "_gateway_model_snapshot": CONFIG.execution_snapshot(),
        },
        max_attempts=1,
    )
    media_result["task_id"] = f"media_{task.task_id.removeprefix('material_task_')}"
    result_path.write_text(json.dumps(media_result, ensure_ascii=False), encoding="utf-8")
    running = queue._claim()
    assert running is not None and running.task_id == task.task_id

    class FakeMediaPipeline:
        def __init__(self, **_kwargs) -> None:
            pass

        def process(self, *_args, **_kwargs) -> Path:
            return result_path

    recognition_calls: list[str] = []

    def recognize(profile, result, config):
        recognition_calls.append(profile["material_id"])
        if recognition_outcome == "failed":
            raise RuntimeError("模拟识别失败")

        def gateway(_config, **kwargs):
            context = json.loads(kwargs["context_json"])
            return GatewayResult(content={"clips": [{
                "clip_id": item["clip_id"], "purpose_tags": ["proof"], "visual_tags": ["正面展示"],
                "garment_views": ["front"], "action_tags": ["站立"], "scene_tags": ["白墙"],
                "shot_size": "中景", "people_count": 1, "standalone_usable": True,
                "quality": {"clarity": 80, "stability": 80, "audio": 80, "exposure": 80},
            } for item in context["clips"]]}, response_id="fixture", raw_bytes=100)

        return recognize_material(profile, result, config, gateway_caller=gateway)

    original_create = store.create
    create_calls = 0
    media_pipeline_calls = 0
    gateway_config_calls = 0

    class SimulatedProcessCrash(BaseException):
        pass

    def create_then_crash(*args, **kwargs):
        nonlocal create_calls
        create_calls += 1
        saved = original_create(*args, **kwargs)
        if create_calls == 1:
            raise SimulatedProcessCrash
        return saved

    def process_once(self, *_args, **_kwargs) -> Path:
        nonlocal media_pipeline_calls
        media_pipeline_calls += 1
        if media_pipeline_calls > 1:
            raise AssertionError("已落库的本地媒体结果应直接对账，不应重复跑 FFmpeg")
        return result_path

    FakeMediaPipeline.process = process_once

    def material_gateway(_model_id=None):
        nonlocal gateway_config_calls
        gateway_config_calls += 1
        if recognition_outcome == "configuration_unavailable" and gateway_config_calls > 1:
            return None
        return CONFIG

    monkeypatch.setattr(s6, "MediaPipeline", FakeMediaPipeline)
    monkeypatch.setattr(s6, "_model_path", lambda: tmp_path / "unused-model.bin")
    monkeypatch.setattr(s6, "_material_gateway", material_gateway)
    monkeypatch.setattr(s6, "get_material_store", lambda: store)
    monkeypatch.setattr(s6, "recognize_material", recognize)
    monkeypatch.setattr(store, "create", create_then_crash)

    with pytest.raises(SimulatedProcessCrash):
        s6._process_import(running, lambda _step, _value: None)
    pending_material = store.list()[0]
    assert pending_material["processing"]["recognition_status"] == "pending"
    if recognition_outcome == "corrupt_media":
        proxy.unlink()
    assert queue.recover_interrupted(s6._reconcile_interrupted_import) == 1

    recovered = queue.get(task.task_id)
    assert recovered is not None
    stored = store.get(pending_material["material_id"])
    assert recovered.status == ("failed" if recognition_outcome == "corrupt_media" else "completed")
    assert recovered.material_id == (None if recognition_outcome == "corrupt_media" else pending_material["material_id"])
    expected_status = "completed" if recognition_outcome == "completed" else "failed"
    assert stored["processing"]["recognition_status"] == expected_status
    assert recognition_calls == (
        [] if recognition_outcome in {"configuration_unavailable", "corrupt_media"}
        else [pending_material["material_id"]]
    )
    assert media_pipeline_calls == 1


def test_material_last_attempt_without_persisted_side_effect_is_failed(tmp_path: Path) -> None:
    source = tmp_path / "fixture.mp4"
    source.write_bytes(b"fixture")
    queue = MaterialImportQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        source, tmp_path / "workspace", fixture_data=True, source_name="fixture.mp4",
        product_id="product_demo_001", source_script_id=None,
        input_payload={"product": {}, "archive": {}}, max_attempts=1,
    )
    assert queue._claim() is not None

    assert queue.recover_interrupted(lambda _task: None) == 1

    recovered = queue.get(task.task_id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.material_id is None


def test_material_queue_hides_private_paths_in_failure_message(tmp_path: Path) -> None:
    source = tmp_path / "private" / "fixture.mp4"
    source.parent.mkdir()
    source.write_bytes(b"fixture")
    workspace = tmp_path / "controlled-workspace"
    queue = MaterialImportQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        source, workspace, fixture_data=True, source_name="fixture.mp4",
        product_id="product_demo_001", source_script_id=None,
        input_payload={"product": {}, "archive": {}}, max_attempts=1,
    )

    def fail(record, _callback):
        raise ValueError(f"cannot read {record.source_path} under {record.workspace_path}")

    queue.run_pending(fail)
    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    public_text = json.dumps(failed.to_dict(), ensure_ascii=False)
    assert str(source) not in public_text and str(workspace) not in public_text
    assert "本机受控文件" in failed.error


def test_gateway_failures_are_reported_as_upstream_errors() -> None:
    with pytest.raises(HTTPException) as caught:
        s6._raise_domain(GatewayError("中转站暂时不可用", retryable=True, status_code=503))
    assert caught.value.status_code == 502


def test_s6_routes_and_upload_contract_hide_local_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S6_DATA_DIR", str(tmp_path / "s6"))
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway-config.json"))
    monkeypatch.setenv("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS", "1")
    s6._stores.clear()
    s6._queues.clear()
    product = _json("product.valid.json")
    product["status"] = "active"
    monkeypatch.setattr(s6, "_resolve_material_product", lambda *_args, **_kwargs: product)
    monkeypatch.setattr(s6, "probe_media", lambda _path: MediaInfo(6000, 360, 640, 25, "h264", "aac", True, "mp4"))
    monkeypatch.setattr(s6, "_run_queue", lambda: None)
    client = TestClient(app)

    assert "/s6/materials/{material_id}/recognize" in app.openapi()["paths"]
    response = client.post(
        "/s6/imports",
        data={
            "product_id": product["product_id"], "model_name": "模特甲", "scene": "室内白墙",
            "shot_date": "2026-08-29", "batch": "B01", "imported_by": "摄影甲", "note": "工程验收",
            "capture_role": "host_take",
        },
        files={"video": ("男装样片.mp4", b"fixture-video-bytes", "video/mp4")},
        headers={"X-Content-Factory-Fixture": "true"},
    )

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["fixture_data"] is True and payload["status"] == "pending"
    assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)
    assert "source_path" not in payload and "workspace_path" not in payload
    queued_input = s6.get_material_queue().list()[0].input()
    assert queued_input["capture_role"] == "host_take"
    assert queued_input["archive"]["note"] == "工程验收"

    invalid_date = client.post(
        "/s6/imports",
        data={
            "product_id": product["product_id"], "model_name": "模特甲", "scene": "室内白墙",
            "shot_date": "2026-02-30", "batch": "B01", "imported_by": "摄影甲", "note": "工程验收",
        },
        files={"video": ("男装样片.mp4", b"fixture-video-bytes", "video/mp4")},
        headers={"X-Content-Factory-Fixture": "true"},
    )
    assert invalid_date.status_code == 422
