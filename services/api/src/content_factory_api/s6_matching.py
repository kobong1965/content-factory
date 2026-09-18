"""Deterministic same-product material suggestions and shooting-gap state."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from content_factory_contracts import validate_or_raise

from .s6_materials import material_upload_role
from .s6_store import MaterialStore, now_iso

_PURPOSE_TERMS: list[tuple[str, tuple[str, ...]]] = [
    ("detail", ("面料", "纹理", "细节", "近看", "特写", "微距")),
    ("comfort", ("舒适", "久坐", "弹力", "透气", "轻松")),
    ("cta", ("购买", "下单", "点击", "行动", "到手")),
    ("transition", ("转场", "过渡", "切换")),
    ("proof", ("证明", "展示", "上身", "版型", "效果", "轮廓", "穿好")),
]


def infer_purpose(shot: Mapping[str, Any]) -> str:
    text = " ".join(str(shot.get(field, "")) for field in ("visual", "voiceover", "action", "shot_size", "subtitle"))
    for purpose, terms in _PURPOSE_TERMS:
        if any(term in text for term in terms):
            return purpose
    return "hook" if shot.get("order") == 1 else "broll"


def _grams(text: str) -> set[str]:
    clean = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).casefold()
    return {clean[index:index + 2] for index in range(max(0, len(clean) - 1))}


def repeat_risk(store: MaterialStore, material_id: str, clip_id: str, current_script_id: str) -> str:
    scripts: list[str] = []
    for event in store.usage(material_id, clip_id):
        if event["action"] != "confirmed" or event["script_id"] == current_script_id or event["script_id"] in scripts:
            continue
        scripts.append(event["script_id"])
        if len(scripts) == 3:
            break
    return "high" if len(scripts) >= 3 else "medium" if len(scripts) >= 2 else "low"


def _suggestions(
    store: MaterialStore, script_id: str, product_id: str, shot: Mapping[str, Any], purpose: str,
    *, fixture_data: bool, strict_purpose: bool = False,
) -> list[dict[str, Any]]:
    shot_text = " ".join(str(shot.get(field, "")) for field in ("visual", "voiceover", "action", "subtitle"))
    shot_grams = _grams(shot_text)
    suggestions: list[dict[str, Any]] = []
    for material in store.list(product_id=product_id):
        if bool(material.get("fixture_data")) != fixture_data:
            continue
        upload_role = material_upload_role(material)
        if upload_role == "host_take" and not bool(material.get("file", {}).get("has_audio")):
            # A silent host take cannot preserve the authoritative continuous
            # voice track, so it must never enter the suggestion list.
            continue
        if strict_purpose and upload_role == "host_take":
            continue
        for clip in material["clips"]:
            if not clip["reusable"]:
                continue
            if upload_role == "host_take" and clip.get("capture_scope") != "full_take":
                # Scene cuts remain browsable, but only the controlled full
                # interval is eligible as the continuous primary source.
                continue
            if strict_purpose and upload_role != "detail" and purpose not in clip.get("purpose_tags", []):
                continue
            clip_text = " ".join([
                clip["transcript"], *clip["visual_tags"], *clip["action_tags"], *clip["scene_tags"], clip["shot_size"],
            ])
            overlap = len(shot_grams & _grams(clip_text))
            risk = repeat_risk(store, material["material_id"], clip["id"], script_id)
            score = 20
            reasons = ["同商品"]
            if purpose in clip["purpose_tags"]:
                score += 45
                reasons.append("用途一致")
            if overlap:
                score += min(20, overlap * 2)
                reasons.append("画面或口播相近")
            score += round(clip["quality"]["overall"] / 10)
            if clip["standalone_usable"]:
                score += 5
            if material["processing"]["recognition_status"] == "completed":
                score += 5
            score -= 25 if risk == "high" else 10 if risk == "medium" else 0
            suggestions.append({
                "material_id": material["material_id"], "clip_id": clip["id"],
                "score": max(0, min(100, score)), "repeat_risk": risk, "reason": "、".join(reasons),
                "continuous_take_reusable": upload_role == "host_take" and not strict_purpose,
            })
    return sorted(suggestions, key=lambda item: (-item["score"], item["material_id"], item["clip_id"]))[:3]


def _detail_requirement_id(script_id: str, script_shot_id: str) -> str:
    digest = hashlib.sha256(f"{script_id}:{script_shot_id}:detail_overlay".encode()).hexdigest()[:32]
    return f"detail_overlay_{digest}"


def build_shooting_task(store: MaterialStore, script: Mapping[str, Any]) -> dict[str, Any]:
    if script.get("review", {}).get("status") != "approved":
        raise ValueError("只有已批准脚本才能建立拍摄任务")
    versions = [item for item in script.get("versions", []) if isinstance(item, Mapping)]
    if not versions:
        raise ValueError("脚本没有可拍摄版本")
    selected_version_id = script.get("selected_version_id") or versions[0].get("id")
    selected_version = next((item for item in versions if item.get("id") == selected_version_id), None)
    if selected_version is None:
        raise ValueError("脚本选中的拍摄版本已经不存在，请回到脚本编导重新选择")
    checklist = {
        item["shot_id"]: item["status"] for item in script.get("material_checklist", []) if isinstance(item, Mapping)
    }
    confirmed = store.matches(script["script_id"])
    requirements: list[dict[str, Any]] = []
    matched_count = 0
    missing_count = 0
    for version in [selected_version]:
        for shot in version["shots"]:
            requirement_status = checklist.get(shot["id"], shot.get("material_status", "required"))
            if requirement_status not in {"required", "reusable", "reshoot", "optional"}:
                requirement_status = "required"
            purpose = infer_purpose(shot)
            suggestions = _suggestions(
                store, script["script_id"], script["product_id"], shot, purpose,
                fixture_data=bool(script.get("fixture_data")),
            )
            match = confirmed.get(shot["id"])
            confirmed_match = None
            if match:
                confirmed_match = {
                    "material_id": match["material_id"], "clip_id": match["clip_id"], "score": match["score"],
                    "repeat_risk": repeat_risk(store, match["material_id"], match["clip_id"], script["script_id"]),
                    "reason": match["reason"],
                }
                match_status = "confirmed"
                matched_count += 1
            elif requirement_status == "optional":
                match_status = "optional"
            elif suggestions:
                match_status = "suggested"
                missing_count += 1
            else:
                match_status = "missing"
                missing_count += 1
            requirements.append({
                "script_shot_id": shot["id"], "version_id": version["id"], "order": shot["order"],
                "requirement_kind": "primary", "source_script_shot_id": shot["id"], "detail_tag": None,
                "requirement_status": requirement_status, "purpose": purpose, "visual": shot["visual"],
                "voiceover": shot["voiceover"], "match_status": match_status, "suggestions": suggestions,
                "confirmed_match": confirmed_match,
            })

            detail_overlay = shot.get("detail_overlay")
            if not isinstance(detail_overlay, Mapping) or detail_overlay.get("mode") != "optional_detail":
                continue
            detail_tag = str(detail_overlay.get("detail_tag") or "").strip()
            detail_instruction = str(detail_overlay.get("instruction") or "").strip()
            detail_requirement_id = _detail_requirement_id(script["script_id"], shot["id"])
            detail_suggestions = _suggestions(
                store, script["script_id"], script["product_id"], shot, "detail",
                fixture_data=bool(script.get("fixture_data")), strict_purpose=True,
            )
            detail_match = confirmed.get(detail_requirement_id)
            detail_confirmed = None
            if detail_match:
                detail_confirmed = {
                    "material_id": detail_match["material_id"], "clip_id": detail_match["clip_id"],
                    "score": detail_match["score"],
                    "repeat_risk": repeat_risk(
                        store, detail_match["material_id"], detail_match["clip_id"], script["script_id"],
                    ),
                    "reason": detail_match["reason"],
                }
                detail_match_status = "confirmed"
                matched_count += 1
            elif detail_suggestions:
                detail_match_status = "suggested"
            else:
                detail_match_status = "optional"
            requirements.append({
                "script_shot_id": detail_requirement_id, "version_id": version["id"], "order": shot["order"],
                "requirement_kind": "detail_overlay", "source_script_shot_id": shot["id"],
                "detail_tag": detail_tag, "requirement_status": "optional", "purpose": "detail",
                "visual": detail_instruction or f"可选覆盖同款{detail_tag}细节画面",
                "voiceover": shot["voiceover"], "match_status": detail_match_status,
                "suggestions": detail_suggestions, "confirmed_match": detail_confirmed,
            })
    status = "ready_for_edit" if missing_count == 0 else "materials_uploaded" if matched_count else "pending_shoot"
    digest = hashlib.sha256(script["script_id"].encode()).hexdigest()[:32]
    task = {
        "schema_version": "1.0.0", "fixture_data": bool(script.get("fixture_data")),
        "task_id": f"shooting_task_{digest}", "script_id": script["script_id"], "script_revision": script["revision"],
        "selected_version_id": selected_version_id,
        "product_id": script["product_id"], "status": status, "requirements": requirements,
        "missing_count": missing_count, "matched_count": matched_count,
        "created_at": script.get("created_at", script["updated_at"]), "updated_at": now_iso(),
    }
    validate_or_raise("shooting_task", task, related={"script": script})
    return task


def confirm_suggestion(
    store: MaterialStore, script: Mapping[str, Any], *, script_shot_id: str,
    material_id: str, clip_id: str, actor: str,
) -> dict[str, Any]:
    current = build_shooting_task(store, script)
    requirement = next((item for item in current["requirements"] if item["script_shot_id"] == script_shot_id), None)
    if requirement is None:
        raise ValueError("这个镜头不属于当前脚本")
    selected_material: Mapping[str, Any] | None = None
    if requirement.get("requirement_kind", "primary") == "primary":
        selected_material = store.get(material_id)
        selected_clip = next(
            (item for item in selected_material.get("clips", []) if item.get("id") == clip_id),
            None,
        )
        if material_upload_role(selected_material) == "host_take":
            if not bool(selected_material.get("file", {}).get("has_audio")):
                raise ValueError("主播连续长镜头必须包含可用原声，请重新上传带声音的主素材")
            if not isinstance(selected_clip, Mapping) or selected_clip.get("capture_scope") != "full_take":
                raise ValueError("主播连续长镜头必须选择系统生成的受控全片主素材片段")
    suggestion = next(
        (item for item in requirement["suggestions"] if item["material_id"] == material_id and item["clip_id"] == clip_id), None,
    )
    if suggestion is None:
        raise ValueError("这个片段不是当前镜头的同商品可用建议，请刷新后重选")
    existing_uses = [
        item for item in current["requirements"]
        if isinstance(item.get("confirmed_match"), Mapping)
        and item["confirmed_match"].get("material_id") == material_id
        and item["confirmed_match"].get("clip_id") == clip_id
    ]
    allow_reuse = False
    if existing_uses:
        production_mode = script.get("production_mode")
        material = selected_material or store.get(material_id)
        allow_reuse = (
            isinstance(production_mode, Mapping)
            and production_mode.get("kind") == "fixed_livestream_long_take"
            and material_upload_role(material) == "host_take"
            and requirement.get("requirement_kind") == "primary"
            and all(
                item.get("requirement_kind") == "primary"
                and item.get("version_id") == requirement.get("version_id")
                for item in existing_uses
            )
        )
        if not allow_reuse:
            raise ValueError("只有同一脚本版本的主播连续长镜头，才允许复用同一个主素材片段")
    store.confirm_match(
        script_id=script["script_id"], script_shot_id=script_shot_id,
        suggestion=suggestion, actor=actor, allow_reuse=allow_reuse,
    )
    return build_shooting_task(store, script)
