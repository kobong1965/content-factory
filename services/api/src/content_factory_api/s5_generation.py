"""S5 relay orchestration with frozen inputs and local contract enforcement."""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from content_factory_contracts import ContractValidationError, validate_or_raise
from content_factory_contracts.schema_registry import schema_path

from .s3_gateway import GatewayResult, call_gateway
from .s3_settings import GatewayConfig
from .s5_production_policy import (
    LOCKED_CAMERA,
    LOCKED_EQUIPMENT,
    LOCKED_PRODUCTION_MODE,
    LOCKED_SCENE,
    LOCKED_TRANSITION,
    ProductionPolicyError,
    require_locked_policy,
    resolve_viral_evidence,
    validate_candidate_against_policy,
)

PROMPT_VERSION = "1.2.0"
_OUTPUT_FIELDS = ("versions", "shooting_order", "material_checklist")
_MAX_SKILL_CONTEXT_OCCURRENCES = 5
_MAX_SKILL_CONTEXT_EVIDENCE_PER_OCCURRENCE = 4
_SCRIPT_INSTRUCTIONS = """你是抖音国内男装直播间长镜头口播编导。根据已人工接受的爆点机制和已确认商品事实，编写 3—5 个差异明显、可由一名主播在同一固定直播间一次连续录完的版本。

必须遵守服务器给出的 production_policy，不得改成外景、多主播、手持、移动机位、运镜或多套灯光。shots 不是切镜分镜表，而是同一条连续长镜头中按时间连续的口播段落；段落之间不切镜，不变机位，不换场地。shooting_order 只能有一组，且必须按版本和段落顺序覆盖全部 shot_id。

每段 voiceover 写主播可逐字照读的精确台词；delivery 写语气、语速与节奏、重音、停顿；performance 写表情、目光、身体动作、商品动作；subtitle 写实际上屏文字。detail_overlay 只可为 none，或可选覆盖同款商品细节素材，必须保留主播连续原声。每段必须引用对应 pattern_step_id 的 evidence_id。

不得照抄原视频，不得添加输入中不存在的商品参数、功效、价格或活动；unknown_fields 中的信息必须省略。商品表述只能引用 usable_fact_ids。新拍段落的 source_shot_id 必须为 null。时间必须从 0 连续覆盖版本总时长。严格返回指定 JSON Schema。"""


class ScriptGenerationError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class ScriptArtifacts:
    script_id: str
    result_path: Path


ProgressCallback = Callable[[str, int], None]
GatewayCaller = Callable[..., GatewayResult]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def model_output_schema(
    version_count: int, production_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if version_count not in {3, 4, 5}:
        raise ValueError("脚本版本数量必须是 3、4 或 5")
    schema = json.loads(schema_path("script").read_text(encoding="utf-8"))
    properties = {field: copy.deepcopy(schema["properties"][field]) for field in _OUTPUT_FIELDS}
    properties["versions"]["minItems"] = version_count
    properties["versions"]["maxItems"] = version_count
    properties["shooting_order"]["minItems"] = 1
    properties["shooting_order"]["maxItems"] = 1
    order_properties = properties["shooting_order"]["items"]["properties"]
    order_properties["scene"] = {"const": LOCKED_SCENE}
    order_properties["equipment"] = {"const": LOCKED_EQUIPMENT}
    shot_schema = copy.deepcopy(schema["$defs"]["storyboardShot"])
    for field in ("delivery", "performance", "detail_overlay", "evidence_ids"):
        if field not in shot_schema["required"]:
            shot_schema["required"].append(field)
    shot_schema["properties"]["camera"] = {"const": LOCKED_CAMERA}
    shot_schema["properties"]["subtitle"] = copy.deepcopy(schema["$defs"]["nonEmptyText"])
    shot_schema["properties"]["transition"] = {"const": LOCKED_TRANSITION}
    shot_schema["properties"]["source_shot_id"] = {"const": None}
    definitions = copy.deepcopy(schema["$defs"])
    definitions["storyboardShot"] = shot_schema
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(_OUTPUT_FIELDS),
        "properties": properties,
        "$defs": definitions,
    }


def _compact_shots(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        "id", "start_ms", "end_ms", "shot_size", "camera_movement", "composition",
        "performer_action", "product_exposure", "transcript", "subtitle",
    )
    return [{field: shot.get(field) for field in fields} for shot in analysis.get("shots", [])]


def _flatten_occurrence_evidence(occurrence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        item
        for step in occurrence.get("steps", [])
        if isinstance(step, Mapping)
        for item in step.get("evidence", [])
        if isinstance(item, Mapping)
    ]


def _occurrence_quality(occurrence: Mapping[str, Any]) -> tuple[int, int, int, float, int, str, str]:
    evidence = _flatten_occurrence_evidence(occurrence)
    primary = sum(
        bool(item.get("exact_dialogue") or item.get("action"))
        for item in evidence
    )
    non_inference = sum(item.get("is_inference") is False for item in evidence)
    confidences = [
        float(item["confidence"])
        for item in evidence
        if isinstance(item.get("confidence"), (int, float))
    ]
    revision = occurrence.get("analysis_revision")
    return (
        int(bool(occurrence.get("has_primary_evidence"))),
        primary,
        non_inference,
        max(confidences, default=0.0),
        revision if isinstance(revision, int) else 0,
        str(occurrence.get("accepted_at") or ""),
        str(occurrence.get("occurrence_id") or ""),
    )


def _evidence_quality(item: Mapping[str, Any]) -> tuple[int, int, float, int]:
    confidence = item.get("confidence")
    return (
        int(bool(item.get("exact_dialogue") or item.get("action"))),
        int(item.get("is_inference") is False),
        float(confidence) if isinstance(confidence, (int, float)) else 0.0,
        -int(item.get("start_ms") or 0),
    )


def _compact_occurrence(occurrence: Mapping[str, Any]) -> dict[str, Any]:
    all_evidence = _flatten_occurrence_evidence(occurrence)
    selected_evidence = sorted(all_evidence, key=_evidence_quality, reverse=True)[
        :_MAX_SKILL_CONTEXT_EVIDENCE_PER_OCCURRENCE
    ]
    selected_evidence.sort(key=lambda item: (int(item.get("start_ms") or 0), str(item.get("qualified_evidence_id") or "")))
    evidence = [
        {
            "qualified_evidence_id": item.get("qualified_evidence_id"),
            "start_ms": item.get("start_ms"),
            "end_ms": item.get("end_ms"),
            "exact_dialogue": item.get("exact_dialogue"),
            "subtitle": item.get("subtitle"),
            "action": item.get("action"),
            "visual_event": item.get("visual_event"),
            "is_inference": item.get("is_inference"),
        }
        for item in selected_evidence
    ]
    return {
        "occurrence_id": occurrence.get("occurrence_id"),
        "video_id": occurrence.get("video_id"),
        "source_name": occurrence.get("source_name"),
        "start_ms": occurrence.get("start_ms"),
        "end_ms": occurrence.get("end_ms"),
        "evidence_total_count": len(all_evidence),
        "evidence_included_count": len(evidence),
        "evidence_truncated": len(evidence) < len(all_evidence),
        "evidence": evidence,
    }


def _compact_skill(skill: Mapping[str, Any]) -> dict[str, Any]:
    occurrences = [
        item for item in skill.get("occurrences", []) if isinstance(item, Mapping)
    ]
    representative_id = skill.get("representative_occurrence_id")
    representative = next(
        (item for item in occurrences if item.get("occurrence_id") == representative_id),
        None,
    )
    selected: list[Mapping[str, Any]] = []
    seen_videos: set[str] = set()

    def add_unique(occurrence: Mapping[str, Any]) -> None:
        if len(selected) >= _MAX_SKILL_CONTEXT_OCCURRENCES:
            return
        video_key = str(occurrence.get("video_id") or occurrence.get("occurrence_id") or "")
        if video_key in seen_videos:
            return
        seen_videos.add(video_key)
        selected.append(occurrence)

    if representative is not None:
        add_unique(representative)
    for occurrence in sorted(occurrences, key=_occurrence_quality, reverse=True):
        add_unique(occurrence)
        if len(selected) >= _MAX_SKILL_CONTEXT_OCCURRENCES:
            break

    supporting_occurrences = [_compact_occurrence(item) for item in selected]
    total_video_count = len({
        str(item.get("video_id") or item.get("occurrence_id") or "")
        for item in occurrences
    })
    return {
        "skill_id": skill.get("skill_id"),
        "revision": skill.get("revision"),
        "name": skill.get("name"),
        "mechanism": skill.get("mechanism"),
        "classification": skill.get("classification"),
        "evidence_level": skill.get("evidence_level"),
        "causality_status": skill.get("causality_status"),
        "distinct_video_count": skill.get("distinct_video_count"),
        "occurrence_count": skill.get("occurrence_count"),
        "total_occurrence_count": len(occurrences),
        "total_distinct_video_count": total_video_count,
        "included_occurrence_count": len(supporting_occurrences),
        "included_distinct_video_count": len(seen_videos),
        "supporting_occurrences_truncated": len(supporting_occurrences) < len(occurrences),
        "steps": copy.deepcopy(skill.get("steps", [])),
        "necessary_conditions": copy.deepcopy(skill.get("necessary_conditions", [])),
        "failure_signals": copy.deepcopy(skill.get("failure_signals", [])),
        "supporting_occurrences": supporting_occurrences,
    }


def build_generation_context(input_payload: Mapping[str, Any]) -> str:
    product = input_payload["product"]
    analysis = input_payload["analysis"]
    pattern = input_payload["pattern"]
    request = input_payload["request"]
    policy = require_locked_policy(input_payload)
    usable_fact_ids = set(policy["usable_fact_ids"])
    facts = [
        {
            "id": fact["id"], "field": fact["field"], "label": fact["label"], "value": fact["value"],
            "unit": fact.get("unit"), "source_type": fact["source_type"], "source_id": fact["source_id"],
        }
        for fact in product["facts"] if fact.get("id") in usable_fact_ids
    ]
    viral_evidence = resolve_viral_evidence(analysis, pattern)
    if viral_evidence["unresolved_evidence_ids"]:
        raise ProductionPolicyError(
            f"选中爆点存在无法解析的证据：{viral_evidence['unresolved_evidence_ids']}"
        )
    context = {
        "task": "用爆点机制重新编导抖音国内男装固定直播间长镜头口播脚本，不是替换原视频商品名。",
        "requested_version_count": request["version_count"],
        "content_goal": request["content_goal"],
        "target_audience": request["target_audience"],
        "production_policy": copy.deepcopy(dict(policy)),
        "product": {
            "product_id": product["product_id"], "revision": product["revision"], "sku": product["sku"],
            "name": product["name"], "confirmed_facts": facts,
            "usable_fact_ids": list(policy["usable_fact_ids"]),
            "unknown_fields": list(policy["unknown_fields"]),
            "forbidden_expressions": product["forbidden_expressions"],
            "unprovable_claims": product["unprovable_claims"],
        },
        "accepted_pattern": pattern,
        "source_analysis": {
            "analysis_id": analysis["analysis_id"], "revision": analysis["revision"],
            "summary": analysis["summary"], "source_shots": _compact_shots(analysis),
            "viral_evidence": viral_evidence,
        },
        "hard_rules": {
            "only_use_supplied_fact_ids": True,
            "only_use_selected_pattern_step_ids": True,
            "each_segment_must_cite_resolved_evidence": True,
            "timeline_must_be_continuous": True,
            "production_mode": "fixed_livestream_long_take",
            "performer_count": 1,
            "camera_must_remain_fixed": True,
            "lighting_and_scene_must_remain_fixed": True,
            "shots_are_speaking_segments_not_cuts": True,
            "single_shooting_order": True,
            "detail_overlays_are_optional": True,
            "detail_overlays_must_use_same_product": True,
            "original_video_or_local_files_included": False,
        },
    }
    skill = input_payload.get("skill")
    selected = input_payload.get("selected_product_assets", [])
    owned_ids = {s.get("asset_id") for s in product.get("sources", []) if s.get("asset_id")}
    context["available_same_product_materials"] = [
        {"asset_id": s["asset_id"], "label": s.get("label", ""), "kind": s.get("kind")}
        for s in selected if isinstance(s, Mapping) and s.get("asset_id") in owned_ids
    ]
    context["hard_rules"]["material_names_are_not_confirmed_product_facts"] = True
    context["hard_rules"]["material_list_is_not_a_claim_that_files_were_analyzed"] = True
    if isinstance(skill, Mapping):
        context["approved_skill"] = _compact_skill(skill)
        context["hard_rules"]["selected_skill_is_human_approved"] = True
        context["hard_rules"]["cross_video_occurrences_are_supporting_context_not_new_product_facts"] = True
    return json.dumps(context, ensure_ascii=False, separators=(",", ":"))


def machine_review_issues(script: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    versions = [item for item in script.get("versions", []) if isinstance(item, Mapping)]
    for index, version in enumerate(versions, start=1):
        if version.get("similarity_risk") == "high":
            issues.append({
                "code": f"similarity_warning_{index}", "severity": "warning",
                "message": f"第 {index} 版由模型标记为高相似风险，请重点核对是否像原视频。", "shot_id": None,
            })
    selected_version_id = script.get("selected_version_id") or (versions[0].get("id") if versions else None)
    selected_version = next(
        (item for item in versions if item.get("id") == selected_version_id),
        None,
    )
    selected_shot_ids = {
        shot.get("id")
        for shot in selected_version.get("shots", [])
        if isinstance(shot, Mapping) and isinstance(shot.get("id"), str)
    } if selected_version is not None else set()
    reshoot_count = sum(
        1
        for item in script.get("material_checklist", [])
        if isinstance(item, Mapping)
        and item.get("status") == "reshoot"
        and item.get("shot_id") in selected_shot_ids
    )
    if reshoot_count:
        issues.append({
            "code": "reshoot_materials", "severity": "info",
            "message": f"有 {reshoot_count} 个镜头需要补拍，批准前请确认拍摄安排。", "shot_id": None,
        })
    return issues


def _assemble_script(
    *, task_id: str, input_payload: Mapping[str, Any], candidate: Mapping[str, Any],
    config: GatewayConfig, gateway_result: GatewayResult, elapsed_ms: int,
) -> dict[str, Any]:
    missing = [field for field in _OUTPUT_FIELDS if field not in candidate]
    if missing:
        raise ScriptGenerationError(f"模型输出缺少字段：{', '.join(missing)}")
    product = input_payload["product"]
    analysis = input_payload["analysis"]
    pattern = input_payload["pattern"]
    request = input_payload["request"]
    if len(candidate["versions"]) != request["version_count"]:
        raise ScriptGenerationError("模型返回的脚本版本数量与请求不一致")
    try:
        validate_candidate_against_policy(candidate, input_payload)
    except ProductionPolicyError as exc:
        raise ScriptGenerationError(f"模型输出违反生产策略：{exc}") from exc
    timestamp = _now_iso()
    provider = urlparse(config.base_url).hostname or "configured-relay"
    script: dict[str, Any] = {
        "schema_version": "1.1.0",
        "fixture_data": bool(product.get("fixture_data") or analysis.get("fixture_data")),
        "revision": 1,
        "script_id": f"script_{uuid4().hex}",
        "product_id": product["product_id"],
        "product_revision": product["revision"],
        "pattern_id": pattern["id"],
        "source_analysis_id": analysis["analysis_id"],
        "source_analysis_revision": analysis["revision"],
        "content_goal": request["content_goal"],
        "target_audience": request["target_audience"],
        "generation": {
            "task_id": task_id, "provider": provider, "model_profile_id": config.model_id,
            "model": config.model, "api_mode": config.api_mode,
            "response_id": gateway_result.response_id, "prompt_version": PROMPT_VERSION,
            "generated_at": timestamp, "duration_ms": max(0, elapsed_ms), "original_video_uploaded": False,
        },
        "created_at": timestamp,
        "updated_at": timestamp,
        "production_mode": copy.deepcopy(LOCKED_PRODUCTION_MODE),
        # 第一版默认选用模型排序最靠前的推荐版本，编导可在批准前一键改选。
        "selected_version_id": candidate["versions"][0]["id"],
        "versions": copy.deepcopy(candidate["versions"]),
        "shooting_order": copy.deepcopy(candidate["shooting_order"]),
        "material_checklist": copy.deepcopy(candidate["material_checklist"]),
        "review": {"status": "pending", "checked_by": None, "checked_at": None, "note": None, "issues": []},
    }
    skill = input_payload.get("skill")
    if isinstance(skill, Mapping):
        script["skill_id"] = skill["skill_id"]
        script["skill_revision"] = skill["revision"]
    script["review"]["issues"] = machine_review_issues(script)
    try:
        validate_or_raise("script", script, related={"product": product, "analysis": analysis})
    except ContractValidationError as exc:
        raise ScriptGenerationError(str(exc)) from exc
    return script


def process_script_generation(
    *, task_id: str, input_payload: Mapping[str, Any], task_directory: str | Path,
    config: GatewayConfig, progress: ProgressCallback | None = None,
    gateway_caller: GatewayCaller = call_gateway,
) -> ScriptArtifacts:
    started = time.perf_counter()
    task_dir = Path(task_directory).expanduser().resolve()
    task_dir.mkdir(parents=True, exist_ok=True)
    notify = progress or (lambda _step, _value: None)
    request = input_payload["request"]
    try:
        policy = require_locked_policy(input_payload)
    except ProductionPolicyError as exc:
        raise ScriptGenerationError(str(exc)) from exc

    notify("prepare", 20)
    context_json = build_generation_context(input_payload)
    notify("gateway", 50)
    gateway_result = gateway_caller(
        config,
        context_json=context_json,
        keyframe_data_urls=[],
        output_schema=model_output_schema(request["version_count"], policy),
        developer_instructions=_SCRIPT_INSTRUCTIONS,
        schema_name="content_factory_script_package",
    )
    _atomic_json(task_dir / "model-output.candidate.json", gateway_result.content)

    notify("validate", 78)
    script = _assemble_script(
        task_id=task_id, input_payload=input_payload, candidate=gateway_result.content,
        config=config, gateway_result=gateway_result,
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    notify("persist", 92)
    result_path = task_dir / "script-package.json"
    _atomic_json(result_path, script)
    _atomic_json(task_dir / "revisions" / "script-r0001.json", script)
    _atomic_json(task_dir / "revision-log.json", [{
        "revision": 1, "action": "generated", "actor": f"AI · {config.model}",
        "created_at": script["created_at"], "review_status": "pending",
    }])
    notify("completed", 100)
    return ScriptArtifacts(script_id=script["script_id"], result_path=result_path)
