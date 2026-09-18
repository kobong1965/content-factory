"""Server-owned S5 production policy for new script generations.

The script contract keeps the enriched fields optional so historical 1.1 files
remain readable.  This module is the stricter service boundary: every newly
generated script must use the fixed-livestream policy and provide complete,
traceable speaking-segment directions.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

PRODUCTION_POLICY_VERSION = "1.0.0"
LOCKED_SCENE = "固定直播间"
LOCKED_CAMERA = "固定直播间竖屏机位（不移动）"
LOCKED_LIGHTING = "固定直播间灯光"
LOCKED_EQUIPMENT = "固定竖屏机位、三脚架、固定直播间灯光"
LOCKED_TRANSITION = "无（连续长镜头）"

LOCKED_PRODUCTION_MODE: dict[str, Any] = {
    "kind": "fixed_livestream_long_take",
    "scene": LOCKED_SCENE,
    "camera": LOCKED_CAMERA,
    "lighting": LOCKED_LIGHTING,
    "performer_count": 1,
    "primary_take": "continuous_long_take",
    "detail_overlays": "optional_reusable_same_product",
}

_NEW_SHOT_OBJECT_FIELDS = {
    "delivery": ("tone", "pacing", "emphasis", "pause"),
    "performance": ("expression", "eye_line", "body_action", "product_action"),
    "detail_overlay": ("mode", "detail_tag", "instruction"),
}
_MOVING_CAMERA_MARKERS = ("手持", "推进", "环绕", "跟拍", "摇镜", "移动机位", "运镜", "镜内变焦")
_SCENE_CHANGE_MARKERS = ("户外", "外景", "换场", "转场到", "移动灯光")
_MULTI_PRESENTER_MARKERS = ("两名主播", "两位主播", "双主播", "双人出镜", "摄影师入镜")


class ProductionPolicyError(ValueError):
    """Raised when a new model result attempts to escape the locked policy."""


def _clean_ids(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in result:
            result.append(item)
    return result


def build_production_policy(
    product: Mapping[str, Any], eligibility: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze S4 eligibility and the server-owned studio policy for one task."""

    if eligibility.get("eligible") is not True:
        blockers = eligibility.get("blockers", eligibility.get("reasons", []))
        suffix = f"：{'；'.join(str(item) for item in blockers)}" if blockers else ""
        raise ProductionPolicyError(f"商品暂不具备脚本生成资格{suffix}")
    usable_fact_ids = _clean_ids(eligibility.get("usable_fact_ids"))
    if not usable_fact_ids:
        raise ProductionPolicyError("商品没有已确认且可引用的卖点事实")
    product_fact_ids = {
        item.get("id") for item in product.get("facts", []) if isinstance(item, Mapping)
    }
    missing = set(usable_fact_ids) - product_fact_ids
    if missing:
        raise ProductionPolicyError(f"脚本资格引用了不存在的商品事实：{sorted(missing)}")
    return {
        "policy_version": PRODUCTION_POLICY_VERSION,
        "owner": "server",
        "product_id": product.get("product_id"),
        "product_revision": product.get("revision"),
        "production_mode": copy.deepcopy(LOCKED_PRODUCTION_MODE),
        "shooting_order": {"scene": LOCKED_SCENE, "equipment": LOCKED_EQUIPMENT},
        "usable_fact_ids": usable_fact_ids,
        "unknown_fields": [str(item) for item in eligibility.get("unknown_fields", []) if str(item).strip()],
    }


def require_locked_policy(input_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    policy = input_payload.get("production_policy")
    if not isinstance(policy, Mapping):
        raise ProductionPolicyError("旧脚本任务没有锁定生产策略，请用当前商品与爆点重新生成")
    product = input_payload.get("product")
    if not isinstance(product, Mapping):
        raise ProductionPolicyError("脚本任务缺少冻结商品")
    if policy.get("policy_version") != PRODUCTION_POLICY_VERSION or policy.get("owner") != "server":
        raise ProductionPolicyError("脚本生产策略版本无效，请重新生成")
    production_mode = policy.get("production_mode")
    if not isinstance(production_mode, Mapping) or dict(production_mode) != LOCKED_PRODUCTION_MODE:
        raise ProductionPolicyError("生产策略已被改写：必须使用固定直播间单主播连续长镜头")
    expected_order = {"scene": LOCKED_SCENE, "equipment": LOCKED_EQUIPMENT}
    shooting_order = policy.get("shooting_order")
    if not isinstance(shooting_order, Mapping) or dict(shooting_order) != expected_order:
        raise ProductionPolicyError("生产策略已被改写：拍摄场地、机位与灯光必须固定")
    if policy.get("product_id") != product.get("product_id") or policy.get("product_revision") != product.get("revision"):
        raise ProductionPolicyError("生产策略与冻结商品版本不一致，请重新生成")
    return policy


def resolve_viral_evidence(
    analysis: Mapping[str, Any], pattern: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve every selected pattern-step evidence ID to its evidence object."""

    evidence_by_id = {
        item.get("id"): copy.deepcopy(dict(item))
        for item in analysis.get("evidence", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    resolved_steps: list[dict[str, Any]] = []
    unresolved: list[str] = []
    used_ids: list[str] = []
    for raw_step in pattern.get("steps", []):
        if not isinstance(raw_step, Mapping):
            continue
        evidence_ids = _clean_ids(raw_step.get("evidence_ids"))
        step_evidence: list[dict[str, Any]] = []
        for evidence_id in evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                unresolved.append(evidence_id)
                continue
            step_evidence.append(copy.deepcopy(evidence))
            if evidence_id not in used_ids:
                used_ids.append(evidence_id)
        resolved_steps.append({
            "step_id": raw_step.get("id"),
            "order": raw_step.get("order"),
            "description": raw_step.get("description"),
            "evidence_ids": evidence_ids,
            "evidence": step_evidence,
        })
    return {
        "steps": resolved_steps,
        "by_id": {evidence_id: evidence_by_id[evidence_id] for evidence_id in used_ids},
        "unresolved_evidence_ids": list(dict.fromkeys(unresolved)),
    }


def _non_empty_object_fields(shot: Mapping[str, Any], field: str, required: Sequence[str]) -> bool:
    value = shot.get(field)
    return isinstance(value, Mapping) and all(
        isinstance(value.get(name), str) and bool(value.get(name, "").strip())
        for name in required if not (field == "detail_overlay" and name == "detail_tag")
    )


def validate_candidate_against_policy(
    candidate: Mapping[str, Any], input_payload: Mapping[str, Any],
) -> None:
    """Reject model output that contradicts the frozen production policy."""

    policy = require_locked_policy(input_payload)
    allowed_fact_ids = set(_clean_ids(policy.get("usable_fact_ids")))
    viral_evidence = resolve_viral_evidence(input_payload["analysis"], input_payload["pattern"])
    if viral_evidence["unresolved_evidence_ids"]:
        raise ProductionPolicyError(
            f"爆点步骤存在无法解析的证据：{viral_evidence['unresolved_evidence_ids']}"
        )
    evidence_by_step = {
        item["step_id"]: set(item["evidence_ids"]) for item in viral_evidence["steps"]
    }
    versions = candidate.get("versions")
    if not isinstance(versions, list):
        raise ProductionPolicyError("模型输出缺少脚本版本")
    all_shot_ids: list[str] = []
    for version_index, version in enumerate(versions, start=1):
        if not isinstance(version, Mapping):
            raise ProductionPolicyError(f"第 {version_index} 版脚本格式错误")
        unconfirmed = set(_clean_ids(version.get("fact_ids"))) - allowed_fact_ids
        if unconfirmed:
            raise ProductionPolicyError(f"第 {version_index} 版引用了未确认商品事实：{sorted(unconfirmed)}")
        shots = version.get("shots")
        if not isinstance(shots, list) or not shots:
            raise ProductionPolicyError(f"第 {version_index} 版至少需要一个长镜头口播段落")
        for shot_index, shot in enumerate(shots, start=1):
            label = f"第 {version_index} 版第 {shot_index} 段"
            if not isinstance(shot, Mapping):
                raise ProductionPolicyError(f"{label}格式错误")
            shot_id = shot.get("id")
            if isinstance(shot_id, str):
                all_shot_ids.append(shot_id)
            if shot.get("camera") != LOCKED_CAMERA:
                raise ProductionPolicyError(f"{label}必须使用固定机位，模型不能改写摄像方式")
            if shot.get("transition") != LOCKED_TRANSITION:
                raise ProductionPolicyError(f"{label}不能要求切镜或运镜，它是连续长镜头内的口播段落")
            if shot.get("source_shot_id") is not None:
                raise ProductionPolicyError(f"{label}是新拍口播段落，source_shot_id 必须为 null")
            if not isinstance(shot.get("subtitle"), str) or not shot.get("subtitle", "").strip():
                raise ProductionPolicyError(f"{label}必须给出完整字幕")
            for field, required in _NEW_SHOT_OBJECT_FIELDS.items():
                if not _non_empty_object_fields(shot, field, required):
                    raise ProductionPolicyError(f"{label}缺少可执行的 {field} 指令")
            detail = shot["detail_overlay"]
            if detail.get("mode") not in {"none", "optional_detail"}:
                raise ProductionPolicyError(f"{label}细节插片模式无效")
            if detail.get("mode") == "optional_detail":
                tag = detail.get("detail_tag")
                instruction = str(detail.get("instruction", ""))
                if not isinstance(tag, str) or not tag.strip():
                    raise ProductionPolicyError(f"{label}的可选细节插片必须标明细节类型")
                if not any(marker in instruction for marker in ("同款", "同一商品", "本商品")):
                    raise ProductionPolicyError(f"{label}只能覆盖同款商品细节素材")
            shot_unconfirmed = set(_clean_ids(shot.get("fact_ids"))) - allowed_fact_ids
            if shot_unconfirmed:
                raise ProductionPolicyError(f"{label}引用了未确认商品事实：{sorted(shot_unconfirmed)}")
            step_id = shot.get("pattern_step_id")
            cited_evidence = set(_clean_ids(shot.get("evidence_ids")))
            if not cited_evidence:
                raise ProductionPolicyError(f"{label}必须引用对应爆点步骤的证据")
            if step_id not in evidence_by_step or not cited_evidence.issubset(evidence_by_step[step_id]):
                raise ProductionPolicyError(f"{label}的爆点证据与 pattern_step_id 无法解析")
            descriptive_text = " ".join(
                str(shot.get(field, "")) for field in ("visual", "action", "shot_size", "transition")
            )
            forbidden = _MOVING_CAMERA_MARKERS + _SCENE_CHANGE_MARKERS + _MULTI_PRESENTER_MARKERS
            marker = next((item for item in forbidden if item in descriptive_text), None)
            if marker:
                raise ProductionPolicyError(f"{label}出现与固定直播间单主播策略冲突的指令“{marker}”")

    shooting_order = candidate.get("shooting_order")
    if not isinstance(shooting_order, list) or len(shooting_order) != 1:
        raise ProductionPolicyError("必须生成单一拍摄顺序，不能拆成多场地或多机位")
    group = shooting_order[0]
    expected = policy["shooting_order"]
    if not isinstance(group, Mapping) or group.get("scene") != expected["scene"] or group.get("equipment") != expected["equipment"]:
        raise ProductionPolicyError("单一拍摄顺序必须使用服务器锁定的直播间、机位和灯光")
    if _clean_ids(group.get("shot_ids")) != all_shot_ids:
        raise ProductionPolicyError("单一拍摄顺序必须按脚本顺序且只覆盖全部口播段落")


def validate_script_for_approval(
    script: Mapping[str, Any], frozen: Mapping[str, Any],
) -> None:
    """Apply new-generation requirements without breaking historical reads."""

    require_locked_policy(frozen)
    production_mode = script.get("production_mode")
    if not isinstance(production_mode, Mapping) or dict(production_mode) != LOCKED_PRODUCTION_MODE:
        raise ProductionPolicyError("旧脚本或未锁定生产模式的脚本不能批准，请重新生成")
    version_ids = {
        item.get("id") for item in script.get("versions", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    if script.get("selected_version_id") not in version_ids:
        raise ProductionPolicyError("批准前必须选择一个实际拍摄版本")
    validate_candidate_against_policy(script, frozen)


def validate_policy_eligibility_snapshot(
    frozen: Mapping[str, Any], current_product: Mapping[str, Any], eligibility: Mapping[str, Any],
) -> None:
    """Ensure approval still uses the current S4 script eligibility result."""

    policy = require_locked_policy(frozen)
    if eligibility.get("eligible") is not True:
        blockers = eligibility.get("blockers", eligibility.get("reasons", []))
        suffix = f"：{'；'.join(str(item) for item in blockers)}" if blockers else ""
        raise ProductionPolicyError(f"当前商品已不具备脚本使用资格{suffix}")
    if current_product.get("product_id") != policy.get("product_id"):
        raise ProductionPolicyError("当前商品与脚本生产策略不一致")
    if _clean_ids(eligibility.get("usable_fact_ids")) != _clean_ids(policy.get("usable_fact_ids")):
        raise ProductionPolicyError("商品可用事实在生成后发生变化，请用最新商品重新生成脚本")
