"""Create deterministic, editable S7 timelines from approved S6 matches."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from content_factory_contracts import validate_or_raise

from .s6_materials import material_upload_role
from .s6_store import MaterialStore, now_iso


def _default_settings() -> dict[str, Any]:
    return {
        "width": 1080, "height": 1920, "fps": 30,
        "video_codec": "h264", "audio_codec": "aac", "color_preset": "natural",
        "normalize_voice": True, "reduce_noise": True, "auto_sound_effects": True,
        "original_volume": 1.0, "bgm_asset_id": None, "bgm_volume": 0.12,
        "output_clean_copy": True,
        "subtitle_style": {
            "enabled": True, "font_family": "SimHei", "font_size": 68,
            "primary_color": "#FFFFFF", "outline_color": "#111827", "margin_v": 180,
        },
    }


def _transition(value: object) -> tuple[str, int]:
    text = str(value or "")
    if any(word in text for word in ("淡入", "淡出", "叠化", "溶解")):
        return "fade", 120
    return "cut", 0


def build_edit_project(
    script: Mapping[str, Any], shooting_task: Mapping[str, Any], material_store: MaterialStore,
) -> dict[str, Any]:
    """Build one editable variant per approved script version.

    The S6 confirmed source interval remains authoritative. A short source clip
    is slowed only to 0.5x; any remaining gap is held on its final frame by the
    renderer and is disclosed as a warning.
    """

    if script.get("review", {}).get("status") != "approved":
        raise ValueError("只有已批准脚本才能建立剪辑工程")
    if shooting_task.get("status") != "ready_for_edit":
        raise ValueError("还有脚本镜头没有确认素材，暂时不能剪辑")
    if (
        shooting_task.get("script_id") != script.get("script_id")
        or shooting_task.get("script_revision") != script.get("revision")
        or shooting_task.get("product_id") != script.get("product_id")
        or shooting_task.get("fixture_data") != script.get("fixture_data")
    ):
        raise ValueError("素材匹配任务与当前脚本版本不一致")
    versions = [item for item in script.get("versions", []) if isinstance(item, Mapping)]
    selected_version_id = script.get("selected_version_id") or (versions[0].get("id") if versions else None)
    selected_version = next((item for item in versions if item.get("id") == selected_version_id), None)
    if selected_version is None:
        raise ValueError("脚本选中的拍摄版本已经不存在")
    if shooting_task.get("selected_version_id") not in {None, selected_version_id}:
        raise ValueError("素材匹配任务与脚本当前选中的拍摄版本不一致")
    validate_or_raise("shooting_task", shooting_task, related={"script": script})

    confirmed: dict[str, Mapping[str, Any]] = {}
    detail_confirmed: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for item in shooting_task.get("requirements", []):
        if not isinstance(item, Mapping) or not isinstance(item.get("confirmed_match"), Mapping):
            continue
        match = item["confirmed_match"]
        if item.get("requirement_kind") == "detail_overlay":
            source_shot_id = item.get("source_script_shot_id")
            if isinstance(source_shot_id, str):
                detail_confirmed[source_shot_id] = (match, item)
        else:
            confirmed[str(item["script_shot_id"])] = match
    all_confirmed = [*confirmed.values(), *(item[0] for item in detail_confirmed.values())]
    material_ids = {str(item["material_id"]) for item in all_confirmed}
    materials = {material_id: material_store.get(material_id) for material_id in material_ids}
    for material in materials.values():
        if (
            material.get("product_id") != script.get("product_id")
            or bool(material.get("fixture_data")) != bool(script.get("fixture_data"))
        ):
            raise ValueError("已确认素材与当前脚本的商品或数据环境不一致")
    now = now_iso()
    token = uuid4().hex
    fixed_long_take = (
        isinstance(script.get("production_mode"), Mapping)
        and script["production_mode"].get("kind") == "fixed_livestream_long_take"
    )
    variants: list[dict[str, Any]] = []
    for variant_order, version in enumerate([selected_version], start=1):
        clips: list[dict[str, Any]] = []
        warnings: list[str] = []
        version_matches = {
            shot["id"]: confirmed.get(shot["id"])
            for shot in version["shots"]
        }
        pair_counts: dict[tuple[str, str], int] = {}
        for match in version_matches.values():
            if not isinstance(match, Mapping):
                continue
            pair = (str(match["material_id"]), str(match["clip_id"]))
            pair_counts[pair] = pair_counts.get(pair, 0) + 1
        for shot_order, shot in enumerate(version["shots"], start=1):
            match = version_matches[shot["id"]]
            if match is None:
                raise ValueError(f"分镜 {shot['id']} 缺少已确认素材")
            material = materials[str(match["material_id"])]
            source = next((item for item in material["clips"] if item["id"] == match["clip_id"]), None)
            if source is None:
                raise ValueError(f"分镜 {shot['id']} 的已确认素材片段不存在")
            primary_role = material_upload_role(material)
            if primary_role == "host_take":
                if not bool(material.get("file", {}).get("has_audio")):
                    raise ValueError("主播连续长镜头必须包含可用原声，请重新上传带声音的主素材")
                if source.get("capture_scope") != "full_take":
                    raise ValueError("当前主素材仍指向旧的场景切片，请撤销匹配后重新选择受控全片主素材")
            target_duration = int(shot["end_ms"]) - int(shot["start_ms"])
            source_start = int(source["start_ms"])
            source_end = int(source["end_ms"])
            pair = (str(match["material_id"]), str(match["clip_id"]))
            continuous_take = fixed_long_take and pair_counts.get(pair, 0) > 1
            if continuous_take:
                if primary_role != "host_take":
                    raise ValueError("只有标记为主播连续长镜头的素材，才能覆盖同一版本的多个口播段落")
                source_total = source_end - source_start
                version_duration = int(version["duration_ms"])
                source_end = int(source["start_ms"]) + round(source_total * int(shot["end_ms"]) / version_duration)
                source_start = int(source["start_ms"]) + round(source_total * int(shot["start_ms"]) / version_duration)
                if source_start >= source_end:
                    raise ValueError(f"分镜 {shot['id']} 对应的主播长镜头太短，无法连续切分")
            source_duration = source_end - source_start
            speed = round(max(0.5, min(2.0, source_duration / target_duration)), 3)
            available_output = source_duration / speed
            if available_output + 1 < target_duration:
                warnings.append(f"{shot['id']} 源片段偏短，成片会保守延长末帧")
            if match.get("repeat_risk") == "high":
                warnings.append(f"{shot['id']} 素材近期复用较多，发布前请检查重复感")
            transition, transition_ms = _transition(shot.get("transition"))
            edit_clip = {
                "id": f"edit_clip_{token[:18]}_{variant_order:02d}_{shot_order:03d}",
                "order": shot_order, "script_shot_id": shot["id"],
                "material_id": match["material_id"], "material_clip_id": match["clip_id"],
                "source_start_ms": source_start, "source_end_ms": source_end,
                "timeline_start_ms": shot["start_ms"], "timeline_end_ms": shot["end_ms"],
                "speed": speed, "crop_mode": "fill", "focus_x": 0.5, "focus_y": 0.5,
                "transition": transition, "transition_ms": transition_ms,
                "subtitle": shot.get("subtitle", ""), "voiceover": shot.get("voiceover", ""),
                "sound_effect": shot.get("sound_effect", ""),
                "has_source_audio": bool(material["file"]["has_audio"]), "original_volume": 1.0,
                "note": "", "continuous_take": continuous_take,
            }
            detail_policy = shot.get("detail_overlay")
            if isinstance(detail_policy, Mapping) and detail_policy.get("mode") == "optional_detail":
                detail_entry = detail_confirmed.get(shot["id"])
                if detail_entry is None:
                    warnings.append(
                        f"{shot['id']} 未确认同款细节覆盖素材，当前保留主播主画面并继续使用主播原声"
                    )
                else:
                    detail_match, detail_requirement = detail_entry
                    detail_material = materials[str(detail_match["material_id"])]
                    detail_source = next(
                        (
                            item for item in detail_material["clips"]
                            if item["id"] == detail_match["clip_id"]
                        ),
                        None,
                    )
                    if detail_source is None:
                        raise ValueError(f"分镜 {shot['id']} 的已确认细节素材片段不存在")
                    if (
                        "detail" not in detail_source.get("purpose_tags", [])
                        and material_upload_role(detail_material) != "detail"
                    ):
                        raise ValueError(f"分镜 {shot['id']} 的覆盖素材不是已标记的细节片段")
                    detail_duration = int(detail_source["end_ms"]) - int(detail_source["start_ms"])
                    detail_speed = round(max(0.5, min(2.0, detail_duration / target_duration)), 3)
                    if detail_duration / detail_speed + 1 < target_duration:
                        warnings.append(f"{shot['id']} 细节素材偏短，覆盖画面会保守延长末帧")
                    if detail_match.get("repeat_risk") == "high":
                        warnings.append(f"{shot['id']} 细节素材近期复用较多，发布前请检查重复感")
                    edit_clip["visual_overlay"] = {
                        "material_id": detail_match["material_id"],
                        "material_clip_id": detail_match["clip_id"],
                        "source_start_ms": detail_source["start_ms"],
                        "source_end_ms": detail_source["end_ms"],
                        "speed": detail_speed,
                        "detail_tag": detail_requirement.get("detail_tag") or detail_policy.get("detail_tag"),
                        "instruction": detail_policy.get("instruction") or "覆盖同款商品细节画面",
                        "audio_mode": "retain_primary",
                    }
            clips.append(edit_clip)
        variants.append({
            "id": f"edit_variant_{token[:20]}_{variant_order:02d}",
            "script_version_id": version["id"], "name": version["name"],
            "duration_ms": version["duration_ms"], "clips": clips,
            "warnings": list(dict.fromkeys(warnings)), "latest_output_id": None,
        })
    project = {
        "schema_version": "1.0.0", "fixture_data": bool(script.get("fixture_data")), "revision": 1,
        "project_id": f"edit_project_{token}", "script_id": script["script_id"],
        "script_revision": script["revision"], "selected_version_id": selected_version_id,
        "product_id": script["product_id"], "status": "draft",
        "settings": _default_settings(), "variants": variants, "created_at": now, "updated_at": now,
    }
    validate_or_raise("edit_project", project, related={"script": script, "materials": materials})
    return project


def apply_project_changes(
    current: Mapping[str, Any], *, settings: Mapping[str, Any], variants: list[Mapping[str, Any]],
) -> dict[str, Any]:
    updated = copy.deepcopy(dict(current))
    updated["settings"] = copy.deepcopy(dict(settings))
    updated["variants"] = [copy.deepcopy(dict(item)) for item in variants]
    updated["status"] = "draft"
    for variant in updated["variants"]:
        variant["latest_output_id"] = None
    return updated
