"""JSON Schema and cross-reference validation for S1 business documents."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

from .schema_registry import schema_path


class ContractValidationError(ValueError):
    """Raised when a document violates its schema or S1 domain rules."""

    def __init__(self, kind: str, issues: list[str]) -> None:
        self.kind = kind
        self.issues = issues
        super().__init__(f"{kind} 合同校验失败：" + "；".join(issues))


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _json_path(parts: Iterable[Any]) -> str:
    rendered = "$"
    for part in parts:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


def _schema_issues(kind: str, document: Mapping[str, Any]) -> list[str]:
    with schema_path(kind).open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{_json_path(error.absolute_path)}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    ]


def _duplicate_ids(items: Iterable[Mapping[str, Any]], field: str = "id") -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        value = item.get(field)
        if isinstance(value, str):
            if value in seen:
                duplicates.add(value)
            seen.add(value)
    return duplicates


def _check_range(issues: list[str], label: str, item: Mapping[str, Any], duration_ms: int) -> None:
    start = item.get("start_ms")
    end = item.get("end_ms")
    if isinstance(start, int) and isinstance(end, int):
        if start >= end:
            issues.append(f"{label}: start_ms 必须小于 end_ms")
        if end > duration_ms:
            issues.append(f"{label}: end_ms={end} 超出总时长 {duration_ms}")


def _check_continuous_ranges(
    issues: list[str], label: str, items: list[Any], duration_ms: int,
) -> None:
    previous_end = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        start = item.get("start_ms")
        end = item.get("end_ms")
        if isinstance(start, int) and start != previous_end:
            issues.append(f"{label}[{index}]: 必须从上一个时间边界 {previous_end} 连续开始")
        if isinstance(end, int):
            previous_end = end
    if items and previous_end != duration_ms:
        issues.append(f"{label}: 最后一段必须结束于视频总时长 {duration_ms}")


def _analysis_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    duration = document.get("duration_ms")
    if not isinstance(duration, int):
        return issues

    collections = {
        "shots": document.get("shots", []),
        "timeline": document.get("timeline", []),
        "metric_snapshots": document.get("metric_snapshots", []),
        "keyframes": document.get("keyframes", []),
        "evidence": document.get("evidence", []),
        "comment_insights": document.get("comment_insights", []),
        "comments": document.get("comments", []),
    }
    ids: dict[str, set[str]] = {}
    for name, raw_items in collections.items():
        items = raw_items if isinstance(raw_items, list) else []
        duplicates = _duplicate_ids(items)
        if duplicates:
            issues.append(f"{name}: ID 重复 {sorted(duplicates)}")
        ids[name] = {item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)}

    shots = document.get("shots", [])
    shot_items = shots if isinstance(shots, list) else []
    _check_continuous_ranges(issues, "shots", shot_items, duration)
    for index, shot in enumerate(shot_items):
        if not isinstance(shot, dict):
            continue
        _check_range(issues, f"shots[{index}]", shot, duration)
        missing = set(shot.get("keyframe_refs", [])) - ids["keyframes"]
        if missing:
            issues.append(f"shots[{index}].keyframe_refs 引用了不存在的关键帧 {sorted(missing)}")

    keyframes = document.get("keyframes", [])
    keyframe_items = keyframes if isinstance(keyframes, list) else []
    for index, frame in enumerate(keyframe_items):
        if not isinstance(frame, dict):
            continue
        if frame.get("shot_id") not in ids["shots"]:
            issues.append(f"keyframes[{index}].shot_id 引用了不存在的镜头")
        timestamp = frame.get("timestamp_ms")
        if isinstance(timestamp, int) and timestamp > duration:
            issues.append(f"keyframes[{index}].timestamp_ms 超出总时长")
    keyframed_shots = {
        frame["shot_id"] for frame in keyframe_items
        if isinstance(frame, dict) and isinstance(frame.get("shot_id"), str)
    }
    missing_keyframes = ids["shots"] - keyframed_shots
    if missing_keyframes:
        issues.append(f"keyframes: 每个镜头都必须有关键帧，当前缺少 {sorted(missing_keyframes)}")

    timeline = document.get("timeline", [])
    timeline_items = timeline if isinstance(timeline, list) else []
    _check_continuous_ranges(issues, "timeline", timeline_items, duration)
    for index, event in enumerate(timeline_items):
        if not isinstance(event, dict):
            continue
        _check_range(issues, f"timeline[{index}]", event, duration)
        start_ms = event.get("start_ms")
        second_index = event.get("second_index")
        if isinstance(start_ms, int) and isinstance(second_index, int) and second_index != start_ms // 1000:
            issues.append(
                f"timeline[{index}].second_index 必须等于 start_ms//1000（应为 {start_ms // 1000}）"
            )
        missing = set(event.get("metric_refs", [])) - ids["metric_snapshots"]
        if missing:
            issues.append(f"timeline[{index}].metric_refs 引用了不存在的数据快照 {sorted(missing)}")

    source_map = {
        "shot": ids["shots"],
        "timeline": ids["timeline"],
        "metric": ids["metric_snapshots"],
        "keyframe": ids["keyframes"],
        "transcript": ids["shots"],
        "comment": ids["comments"],
    }
    evidence = document.get("evidence", [])
    for index, item in enumerate(evidence if isinstance(evidence, list) else []):
        if not isinstance(item, dict):
            continue
        _check_range(issues, f"evidence[{index}]", item, duration)
        candidates = source_map.get(item.get("source_type"))
        if candidates is not None and item.get("source_id") not in candidates:
            issues.append(f"evidence[{index}].source_id 无法解析")
        source_type = item.get("source_type")
        has_timing = isinstance(item.get("start_ms"), int) and isinstance(item.get("end_ms"), int)
        if source_type in {"shot", "timeline", "transcript", "keyframe"} and not has_timing:
            issues.append(f"evidence[{index}]: 画面或口播证据必须带时间范围")
        if source_type in {"metric", "comment"} and (item.get("start_ms") is not None or item.get("end_ms") is not None):
            issues.append(f"evidence[{index}]: 指标或评论证据不能伪造视频时间范围")

    score_items = document.get("scores", {}).get("items", []) if isinstance(document.get("scores"), dict) else []
    if isinstance(score_items, list):
        dimensions = [item.get("dimension") for item in score_items if isinstance(item, dict)]
        if len(set(dimensions)) != len(dimensions):
            issues.append("scores.items: 评分维度不能重复")
        weights = [item.get("weight") for item in score_items if isinstance(item, dict)]
        if all(isinstance(weight, (int, float)) for weight in weights) and abs(sum(weights) - 1) > 0.0001:
            issues.append("scores.items: 权重之和必须等于 1")
        scores = [item.get("score") for item in score_items if isinstance(item, dict)]
        reported_total = document.get("scores", {}).get("total_score")
        if (
            all(isinstance(weight, (int, float)) for weight in weights)
            and all(isinstance(score, (int, float)) for score in scores)
            and isinstance(reported_total, (int, float))
        ):
            calculated_total = round(sum(weight * score for weight, score in zip(weights, scores, strict=True)))
            if abs(reported_total - calculated_total) > 0.0001:
                issues.append(f"scores.total_score 应为加权结果 {calculated_total}")
        for index, item in enumerate(score_items):
            if isinstance(item, dict):
                missing = set(item.get("evidence_ids", [])) - ids["evidence"]
                if missing:
                    issues.append(f"scores.items[{index}].evidence_ids 无法解析 {sorted(missing)}")

    patterns = document.get("pattern_candidates", [])
    for pattern_index, pattern in enumerate(patterns if isinstance(patterns, list) else []):
        if not isinstance(pattern, dict):
            continue
        steps = pattern.get("steps", [])
        if not isinstance(steps, list):
            continue
        if _duplicate_ids(steps):
            issues.append(f"pattern_candidates[{pattern_index}].steps: ID 不能重复")
        orders = [step.get("order") for step in steps if isinstance(step, dict)]
        if orders != list(range(1, len(orders) + 1)):
            issues.append(f"pattern_candidates[{pattern_index}].steps: order 必须从 1 连续递增")
        for step_index, step in enumerate(steps):
            if isinstance(step, dict):
                missing = set(step.get("evidence_ids", [])) - ids["evidence"]
                if missing:
                    issues.append(
                        f"pattern_candidates[{pattern_index}].steps[{step_index}].evidence_ids 无法解析 {sorted(missing)}"
                    )

    summary = document.get("summary", {})
    if isinstance(summary, dict):
        claims = [summary.get(field) for field in (
            "target_audience", "main_promise", "overall_conclusion", "hook_analysis", "consumer_psychology"
        )]
        risks = summary.get("risks", [])
        if isinstance(risks, list):
            claims.extend(risks)
        for index, claim in enumerate(claims):
            if isinstance(claim, dict):
                missing = set(claim.get("evidence_ids", [])) - ids["evidence"]
                if missing:
                    issues.append(f"summary.claims[{index}].evidence_ids 无法解析 {sorted(missing)}")

    for collection_name in ("content_structure", "emotion_curve"):
        collection = document.get(collection_name, [])
        items = collection if isinstance(collection, list) else []
        if _duplicate_ids(items):
            issues.append(f"{collection_name}: ID 不能重复")
        if collection_name == "content_structure":
            orders = [item.get("order") for item in items if isinstance(item, dict)]
            if orders != list(range(1, len(orders) + 1)):
                issues.append("content_structure.order 必须从 1 连续递增")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            _check_range(issues, f"{collection_name}[{index}]", item, duration)
            missing = set(item.get("evidence_ids", [])) - ids["evidence"]
            if missing:
                issues.append(f"{collection_name}[{index}].evidence_ids 无法解析 {sorted(missing)}")

    for index, insight in enumerate(document.get("comment_insights", [])):
        if isinstance(insight, dict):
            missing = set(insight.get("evidence_ids", [])) - ids["evidence"]
            if missing:
                issues.append(f"comment_insights[{index}].evidence_ids 无法解析 {sorted(missing)}")

    processing = document.get("processing", {})
    if isinstance(processing, dict):
        upload_summary = processing.get("upload_summary", {})
        if isinstance(upload_summary, dict) and upload_summary.get("original_video_uploaded") is not False:
            issues.append("processing.upload_summary.original_video_uploaded 必须为 false")

    status = document.get("status")
    review = document.get("review", {})
    if isinstance(review, dict):
        if status in {"reviewed", "accepted"} and (not review.get("reviewer") or not review.get("reviewed_at")):
            issues.append("已复核或已接受的报告必须记录审核人与审核时间")
        if status == "draft" and (review.get("reviewer") is not None or review.get("reviewed_at") is not None):
            issues.append("draft 报告不能提前写入审核人与审核时间")
    return issues


PRODUCT_COMPLETENESS_LABELS = (
    "商品名称", "资料来源", "面料", "版型", "尺码", "颜色", "价格", "核心卖点", "品牌边界", "拍摄条件",
)

SCRIPT_FACT_FIELD_LABELS = (
    ("fabric", "面料"),
    ("fit", "版型"),
    ("size", "尺码"),
    ("color", "颜色"),
    ("price", "价格"),
    ("activity", "活动"),
    ("feature", "功能卖点"),
    ("care", "护理信息"),
)


def compute_product_completeness(document: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the single canonical S4 product completeness result."""

    facts = document.get("facts", [])
    fact_items = facts if isinstance(facts, list) else []
    fact_fields = {
        item.get("field") for item in fact_items
        if isinstance(item, Mapping) and isinstance(item.get("value"), str) and item.get("value", "").strip()
    }
    shooting = document.get("shooting_constraints", {})
    shooting_ready = isinstance(shooting, Mapping) and all((
        bool(shooting.get("models")), bool(shooting.get("locations")), bool(shooting.get("equipment")),
        bool(shooting.get("lights")), isinstance(shooting.get("daily_available_minutes"), int)
        and shooting.get("daily_available_minutes", 0) > 0,
        isinstance(shooting.get("budget_cny"), (int, float)) and shooting.get("budget_cny", -1) >= 0,
        isinstance(shooting.get("brand_tone"), str) and bool(shooting.get("brand_tone", "").strip()),
        isinstance(shooting.get("max_duration_ms"), int) and shooting.get("max_duration_ms", 0) >= 1000,
        bool(shooting.get("confirmed_by")), bool(shooting.get("confirmed_at")),
    ))
    checks = {
        "商品名称": isinstance(document.get("name"), str) and bool(document.get("name", "").strip()),
        "资料来源": bool(document.get("sources")),
        "面料": "fabric" in fact_fields,
        "版型": "fit" in fact_fields,
        "尺码": "size" in fact_fields,
        "颜色": "color" in fact_fields,
        "价格": "price" in fact_fields,
        "核心卖点": bool(document.get("selling_point_fact_ids")),
        "品牌边界": bool(document.get("brand_boundary_confirmed_by"))
        and bool(document.get("brand_boundary_confirmed_at")),
        "拍摄条件": shooting_ready,
    }
    missing = [label for label in PRODUCT_COMPLETENESS_LABELS if not checks[label]]
    completed = len(PRODUCT_COMPLETENESS_LABELS) - len(missing)
    return {
        "required_fields": len(PRODUCT_COMPLETENESS_LABELS),
        "completed_fields": completed,
        "ratio": completed / len(PRODUCT_COMPLETENESS_LABELS),
        "missing_fields": missing,
    }


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _material_capture_role(document: Mapping[str, Any]) -> str:
    role = document.get("capture_role")
    if role in {"host_take", "detail", "standard"}:
        return str(role)
    archive = document.get("archive")
    note = str(archive.get("note") or "").strip() if isinstance(archive, Mapping) else ""
    if note.startswith("[主播连续长镜头]"):
        return "host_take"
    if note.startswith("[同款商品细节镜头]"):
        return "detail"
    return "standard"


def product_script_eligibility(document: Mapping[str, Any]) -> dict[str, Any]:
    """Describe whether a product can safely supply facts to script generation.

    Script eligibility is deliberately narrower than the full S4 completeness
    score and independent from ``active``.  A draft may be used progressively,
    but only explicitly selected facts with confirmation and a valid provenance
    are exposed to the script stage.
    """

    blockers: list[str] = []
    warnings: list[str] = []

    if document.get("fixture_data") is True:
        blockers.append("工程样例不能用于真实脚本")
    if document.get("status") == "archived":
        blockers.append("已归档商品不能用于新脚本")
    if not _has_text(document.get("name")):
        blockers.append("请先填写商品名称")

    raw_sources = document.get("sources", [])
    source_items = raw_sources if isinstance(raw_sources, list) else []
    source_ids = {
        item.get("id")
        for item in source_items
        if isinstance(item, Mapping)
        and _has_text(item.get("id"))
        and _has_text(item.get("source_ref"))
    }
    if not source_ids:
        blockers.append("请先上传或登记至少一项商品资料来源")

    raw_facts = document.get("facts", [])
    fact_items = raw_facts if isinstance(raw_facts, list) else []
    facts_by_id = {
        item.get("id"): item
        for item in fact_items
        if isinstance(item, Mapping) and _has_text(item.get("id"))
    }
    raw_selected = document.get("selling_point_fact_ids", [])
    selected_ids: list[str] = []
    if isinstance(raw_selected, list):
        for fact_id in raw_selected:
            if isinstance(fact_id, str) and fact_id not in selected_ids:
                selected_ids.append(fact_id)

    usable_fact_ids: list[str] = []
    usable_fields: set[str] = set()
    for fact_id in selected_ids:
        fact = facts_by_id.get(fact_id)
        if not isinstance(fact, Mapping):
            continue
        has_confirmation = _has_text(fact.get("confirmed_by")) and _has_text(fact.get("confirmed_at"))
        has_content = all(_has_text(fact.get(field)) for field in ("label", "value", "source_ref"))
        source_type = fact.get("source_type")
        has_provenance = source_type == "manual_confirmed" or fact.get("source_id") in source_ids
        if has_confirmation and has_content and has_provenance:
            usable_fact_ids.append(fact_id)
            field = fact.get("field")
            if isinstance(field, str):
                usable_fields.add(field)

    if not usable_fact_ids:
        blockers.append("请至少选择并保存一条有来源、有确认人的已确认商品事实")
    elif len(usable_fact_ids) != len(selected_ids):
        warnings.append("部分已选卖点缺少来源或人工确认，脚本不会引用这些内容")

    unknown_fields = [label for field, label in SCRIPT_FACT_FIELD_LABELS if field not in usable_fields]
    if unknown_fields:
        warnings.append(f"未确认信息必须从脚本中省略：{'、'.join(unknown_fields)}")
    if not (document.get("brand_boundary_confirmed_by") and document.get("brand_boundary_confirmed_at")):
        warnings.append("品牌边界尚未确认，脚本不得自行补充品牌承诺或宣传口径")
    if document.get("status") == "draft" and not blockers:
        warnings.append("当前为临时商品，脚本只能使用上述已确认事实")

    return {
        "eligible": not blockers,
        "usable_fact_ids": usable_fact_ids,
        "blockers": blockers,
        "warnings": warnings,
        "unknown_fields": unknown_fields,
    }


def _product_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    sources = document.get("sources", [])
    source_items = sources if isinstance(sources, list) else []
    source_duplicates = _duplicate_ids(source_items)
    if source_duplicates:
        issues.append(f"sources: ID 重复 {sorted(source_duplicates)}")
    source_ids = {
        item["id"] for item in source_items if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for index, source in enumerate(source_items):
        if not isinstance(source, dict):
            continue
        managed_fields = (source.get("asset_id"), source.get("mime_type"), source.get("sha256"), source.get("size_bytes"))
        if source.get("managed") and any(value is None for value in managed_fields):
            issues.append(f"sources[{index}]: 托管资料必须包含 asset_id、mime_type、sha256 和 size_bytes")
        if not source.get("managed") and any(value is not None for value in managed_fields):
            issues.append(f"sources[{index}]: 外部来源不能伪装成本地托管资料")

    facts = document.get("facts", [])
    fact_items = facts if isinstance(facts, list) else []
    duplicates = _duplicate_ids(fact_items)
    if duplicates:
        issues.append(f"facts: ID 重复 {sorted(duplicates)}")
    fact_ids = {item["id"] for item in fact_items if isinstance(item, dict) and isinstance(item.get("id"), str)}
    missing = set(document.get("selling_point_fact_ids", [])) - fact_ids
    if missing:
        issues.append(f"selling_point_fact_ids 引用了不存在的商品事实 {sorted(missing)}")

    for index, fact in enumerate(fact_items):
        if not isinstance(fact, dict):
            continue
        source_id = fact.get("source_id")
        if source_id is not None and source_id not in source_ids:
            issues.append(f"facts[{index}].source_id 引用了不存在的资料来源")
        if fact.get("source_type") != "manual_confirmed" and source_id is None:
            issues.append(f"facts[{index}]: 非人工确认事实必须引用资料来源")

    completeness = document.get("completeness")
    if isinstance(completeness, dict):
        expected = compute_product_completeness(document)
        if dict(completeness) != expected:
            issues.append(f"completeness 必须由固定规则计算，预期为 {expected}")

    brand_by = document.get("brand_boundary_confirmed_by")
    brand_at = document.get("brand_boundary_confirmed_at")
    if bool(brand_by) != bool(brand_at):
        issues.append("品牌边界确认人和确认时间必须同时填写或同时留空")
    shooting = document.get("shooting_constraints", {})
    if isinstance(shooting, dict) and bool(shooting.get("confirmed_by")) != bool(shooting.get("confirmed_at")):
        issues.append("拍摄条件确认人和确认时间必须同时填写或同时留空")
    if document.get("status") == "active":
        if document.get("fixture_data"):
            issues.append("工程样例不能启用为真实脚本商品")
        if compute_product_completeness(document)["ratio"] != 1:
            issues.append("商品资料未达到 100%，不能启用")
    return issues


def _script_texts(document: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    versions = document.get("versions", [])
    for version_index, version in enumerate(versions if isinstance(versions, list) else []):
        if not isinstance(version, dict):
            continue
        for field in ("primary_hook", "differentiation"):
            value = version.get(field)
            if isinstance(value, str):
                yield f"versions[{version_index}].{field}", value
        shots = version.get("shots", [])
        for shot_index, shot in enumerate(shots if isinstance(shots, list) else []):
            if not isinstance(shot, dict):
                continue
            for field in ("visual", "voiceover", "action", "subtitle"):
                value = shot.get(field)
                if isinstance(value, str):
                    yield f"versions[{version_index}].shots[{shot_index}].{field}", value


def _script_issues(
    document: Mapping[str, Any],
    related: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    issues: list[str] = []
    versions = document.get("versions", [])
    version_items = versions if isinstance(versions, list) else []
    production_mode = document.get("production_mode")
    fixed_livestream = (
        isinstance(production_mode, Mapping)
        and production_mode.get("kind") == "fixed_livestream_long_take"
    )
    fixed_camera = "固定直播间竖屏机位（不移动）"
    fixed_scene = "固定直播间"
    fixed_equipment = "固定竖屏机位、三脚架、固定直播间灯光"
    moving_camera_terms = ("手持", "推进", "推近", "拉远", "环绕", "跟拍", "摇摄", "平移", "升降", "运镜")
    forbidden_production_terms = (
        "助播", "第二位主播", "两位主播", "双人出镜", "户外", "外拍", "室外", "街道", "公园",
        "摄影师跟拍", "换机位", "切换机位",
    )
    if _duplicate_ids(version_items):
        issues.append("versions: ID 不能重复")
    version_ids = {
        item.get("id") for item in version_items
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    selected_version_id = document.get("selected_version_id")
    if selected_version_id is not None and selected_version_id not in version_ids:
        issues.append("selected_version_id 必须引用当前脚本包中的一个版本")
    hooks = ["".join(str(item.get("primary_hook", "")).split()).casefold() for item in version_items if isinstance(item, dict)]
    differentiations = ["".join(str(item.get("differentiation", "")).split()).casefold() for item in version_items if isinstance(item, dict)]
    if len(hooks) != len(set(hooks)):
        issues.append("versions.primary_hook: 不同版本的主钩子不能重复")
    if len(differentiations) != len(set(differentiations)):
        issues.append("versions.differentiation: 不同版本的差异说明不能重复")

    all_shot_ids: set[str] = set()
    all_shot_id_list: list[str] = []
    shot_copy_signatures: list[tuple[str, str]] = []
    for version_index, version in enumerate(version_items):
        if not isinstance(version, dict):
            continue
        shots = version.get("shots", [])
        shot_items = shots if isinstance(shots, list) else []
        duplicates = _duplicate_ids(shot_items)
        if duplicates:
            issues.append(f"versions[{version_index}].shots: ID 重复 {sorted(duplicates)}")
        orders = [shot.get("order") for shot in shot_items if isinstance(shot, dict)]
        if orders != list(range(1, len(orders) + 1)):
            issues.append(f"versions[{version_index}].shots: order 必须从 1 连续递增")
        duration = version.get("duration_ms")
        if isinstance(duration, int):
            previous_end = 0
            for shot_index, shot in enumerate(shot_items):
                if isinstance(shot, dict):
                    _check_range(issues, f"versions[{version_index}].shots[{shot_index}]", shot, duration)
                    camera = shot.get("camera")
                    if fixed_livestream:
                        if camera != fixed_camera or (
                            isinstance(camera, str) and any(term in camera for term in moving_camera_terms)
                        ):
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].camera: "
                                "固定直播间长镜头必须使用固定机位，不能安排手持或移动运镜"
                            )
                        delivery = shot.get("delivery")
                        if not isinstance(delivery, Mapping) or any(
                            not _has_text(delivery.get(field)) for field in ("tone", "pacing", "emphasis", "pause")
                        ):
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].delivery: "
                                "固定直播间脚本必须逐段写清语气、节奏、重读和停顿"
                            )
                        performance = shot.get("performance")
                        if not isinstance(performance, Mapping) or any(
                            not _has_text(performance.get(field))
                            for field in ("expression", "eye_line", "body_action", "product_action")
                        ):
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].performance: "
                                "固定直播间脚本必须逐段写清表情、目光、身体动作和商品动作"
                            )
                        if not _has_text(shot.get("subtitle")):
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].subtitle: "
                                "每个口播段落都必须提供可直接使用的字幕"
                            )
                        evidence_ids = shot.get("evidence_ids")
                        if not isinstance(evidence_ids, list) or not evidence_ids:
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].evidence_ids: "
                                "每个口播段落至少引用一条爆点证据"
                            )
                        detail_overlay = shot.get("detail_overlay")
                        if not isinstance(detail_overlay, Mapping):
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].detail_overlay: "
                                "必须明确本段是否需要同款细节覆盖画面"
                            )
                        elif detail_overlay.get("mode") == "optional_detail" and not _has_text(detail_overlay.get("detail_tag")):
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].detail_overlay.detail_tag: "
                                "细节覆盖必须写明裤腰、口袋、走线等具体标签"
                            )
                        production_texts: list[tuple[str, str]] = []
                        for field in ("visual", "action", "camera"):
                            value = shot.get(field)
                            if isinstance(value, str):
                                production_texts.append((field, value))
                        if isinstance(performance, Mapping):
                            for field in ("expression", "eye_line", "body_action", "product_action"):
                                value = performance.get(field)
                                if isinstance(value, str):
                                    production_texts.append((f"performance.{field}", value))
                        for field, value in production_texts:
                            hits = [term for term in forbidden_production_terms if term in value]
                            if hits:
                                issues.append(
                                    f"versions[{version_index}].shots[{shot_index}].{field}: "
                                    f"固定直播间单主播流程不能出现“{'、'.join(hits)}”"
                                )
                    if shot.get("start_ms") != previous_end:
                        issues.append(f"versions[{version_index}].shots[{shot_index}]: 镜头必须从上一个边界连续开始")
                    end = shot.get("end_ms")
                    if isinstance(end, int):
                        previous_end = end
                    if shot.get("pattern_step_id") not in set(version.get("pattern_step_ids", [])):
                        issues.append(f"versions[{version_index}].shots[{shot_index}].pattern_step_id 不在本版本声明中")
                    if not set(shot.get("fact_ids", [])).issubset(set(version.get("fact_ids", []))):
                        issues.append(f"versions[{version_index}].shots[{shot_index}].fact_ids 不在本版本声明中")
                    shot_copy_signatures.append((
                        "".join(str(shot.get("visual", "")).split()).casefold(),
                        "".join(str(shot.get("voiceover", "")).split()).casefold(),
                    ))
            if shot_items and previous_end != duration:
                issues.append(f"versions[{version_index}].shots: 最后一个镜头必须结束于版本总时长")
        version_shot_ids = [
            shot["id"] for shot in shot_items if isinstance(shot, dict) and isinstance(shot.get("id"), str)
        ]
        all_shot_id_list.extend(version_shot_ids)
        all_shot_ids.update(version_shot_ids)

    if len(all_shot_ids) != len(all_shot_id_list):
        issues.append("versions.shots: 不同版本的镜头 ID 也必须全局唯一")
    if len(shot_copy_signatures) != len(set(shot_copy_signatures)):
        issues.append("versions.shots: 不同版本不能出现完全相同的画面和口播组合")

    ordered_shot_refs: list[str] = []
    shooting_groups = document.get("shooting_order", [])
    for group_index, group in enumerate(shooting_groups):
        if isinstance(group, dict):
            ordered_shot_refs.extend(group.get("shot_ids", []))
            missing = set(group.get("shot_ids", [])) - all_shot_ids
            if missing:
                issues.append(f"shooting_order[{group_index}].shot_ids 无法解析 {sorted(missing)}")
    if len(ordered_shot_refs) != len(set(ordered_shot_refs)):
        issues.append("shooting_order.shot_ids: 镜头不能重复安排")
    if set(ordered_shot_refs) != all_shot_ids:
        issues.append("shooting_order.shot_ids: 必须覆盖全部脚本镜头")
    if fixed_livestream:
        if not isinstance(shooting_groups, list) or len(shooting_groups) != 1:
            issues.append("shooting_order: 固定直播间连续长镜头只能有一个主拍摄组")
        elif isinstance(shooting_groups[0], Mapping):
            if shooting_groups[0].get("scene") != fixed_scene:
                issues.append("shooting_order[0].scene: 固定直播间脚本不能切换到其他场景")
            if shooting_groups[0].get("equipment") != fixed_equipment:
                issues.append("shooting_order[0].equipment: 必须使用固定竖屏机位、三脚架和固定直播间灯光")

    material_shot_refs: list[str] = []
    for item_index, item in enumerate(document.get("material_checklist", [])):
        if isinstance(item, dict):
            material_shot_refs.append(item.get("shot_id"))
            if item.get("shot_id") not in all_shot_ids:
                issues.append(f"material_checklist[{item_index}].shot_id 无法解析")
    if len(material_shot_refs) != len(set(material_shot_refs)):
        issues.append("material_checklist.shot_id: 同一镜头只能出现一次")
    if set(material_shot_refs) != all_shot_ids:
        issues.append("material_checklist.shot_id: 必须覆盖全部脚本镜头")

    product = related.get("product")
    if product is not None:
        if document.get("product_id") != product.get("product_id"):
            issues.append("script.product_id 与关联商品不一致")
        if document.get("product_revision") != product.get("revision"):
            issues.append("script.product_revision 与关联商品修订版不一致")
        product_fact_ids = {
            item["id"] for item in product.get("facts", []) if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        for version_index, version in enumerate(version_items):
            if isinstance(version, dict):
                missing = set(version.get("fact_ids", [])) - product_fact_ids
                if missing:
                    issues.append(f"versions[{version_index}].fact_ids 无法解析 {sorted(missing)}")
        forbidden = [term for term in product.get("forbidden_expressions", []) if isinstance(term, str)]
        forbidden.extend(term for term in product.get("unprovable_claims", []) if isinstance(term, str))
        for path, value in _script_texts(document):
            for term in forbidden:
                if term and term in value:
                    issues.append(f"{path}: 包含禁用或无法证明的表达“{term}”")

    analysis = related.get("analysis")
    if analysis is not None:
        if document.get("source_analysis_id") != analysis.get("analysis_id"):
            issues.append("script.source_analysis_id 与关联分析不一致")
        if document.get("source_analysis_revision") != analysis.get("revision"):
            issues.append("script.source_analysis_revision 与关联分析修订版不一致")
        pattern_ids = {
            item["id"] for item in analysis.get("pattern_candidates", []) if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if document.get("pattern_id") not in pattern_ids:
            issues.append("script.pattern_id 无法在关联分析中解析")
        selected_pattern = next((
            item for item in analysis.get("pattern_candidates", [])
            if isinstance(item, dict) and item.get("id") == document.get("pattern_id")
        ), None)
        pattern_steps = {
            step["id"]
            for step in (selected_pattern or {}).get("steps", [])
            if isinstance(step, dict) and isinstance(step.get("id"), str)
        }
        pattern_step_evidence = {
            step["id"]: {
                evidence_id for evidence_id in step.get("evidence_ids", []) if isinstance(evidence_id, str)
            }
            for step in (selected_pattern or {}).get("steps", [])
            if isinstance(step, dict) and isinstance(step.get("id"), str)
        }
        analysis_evidence_ids = {
            item["id"] for item in analysis.get("evidence", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        for version_index, version in enumerate(version_items):
            if isinstance(version, dict):
                missing = set(version.get("pattern_step_ids", [])) - pattern_steps
                if missing:
                    issues.append(f"versions[{version_index}].pattern_step_ids 无法解析 {sorted(missing)}")
                analysis_shot_ids = {
                    item["id"] for item in analysis.get("shots", [])
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                }
                for shot_index, shot in enumerate(version.get("shots", [])):
                    if not isinstance(shot, dict):
                        continue
                    if shot.get("source_shot_id") is not None and shot.get("source_shot_id") not in analysis_shot_ids:
                        issues.append(f"versions[{version_index}].shots[{shot_index}].source_shot_id 无法在关联分析中解析")
                    if fixed_livestream:
                        evidence_ids = {
                            evidence_id for evidence_id in shot.get("evidence_ids", []) if isinstance(evidence_id, str)
                        }
                        unknown_evidence = evidence_ids - analysis_evidence_ids
                        if unknown_evidence:
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].evidence_ids 无法在关联分析中解析 "
                                f"{sorted(unknown_evidence)}"
                            )
                        allowed_evidence = pattern_step_evidence.get(str(shot.get("pattern_step_id")), set())
                        outside_step = evidence_ids - allowed_evidence
                        if outside_step:
                            issues.append(
                                f"versions[{version_index}].shots[{shot_index}].evidence_ids "
                                f"不属于当前爆点步骤 {sorted(outside_step)}"
                            )

    review = document.get("review", {})
    if isinstance(review, Mapping):
        status = review.get("status")
        checked_by = review.get("checked_by")
        checked_at = review.get("checked_at")
        if status in {"approved", "rejected"} and (not checked_by or not checked_at):
            issues.append("已批准或已驳回的脚本必须记录审核人与审核时间")
        if status in {"draft", "pending"} and (checked_by is not None or checked_at is not None):
            issues.append("待审核脚本不能提前写入审核人与审核时间")
        if status == "approved" and any(
            isinstance(item, Mapping) and item.get("severity") == "blocking" for item in review.get("issues", [])
        ):
            issues.append("含阻塞问题的脚本不能批准为可拍摄")
    return issues


def _script_task_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    status = document.get("status")
    if document.get("attempt_count", 0) > document.get("max_attempts", 0):
        issues.append("attempt_count 不能大于 max_attempts")
    if status == "completed":
        if document.get("progress") != 100 or document.get("current_step") != "completed":
            issues.append("completed 脚本任务必须是 100% 且 current_step=completed")
        if not document.get("result_ref") or not document.get("script_id") or document.get("error") is not None:
            issues.append("completed 脚本任务必须有脚本结果且不能有错误")
    if status == "failed" and (document.get("current_step") != "failed" or not document.get("error")):
        issues.append("failed 脚本任务必须有错误并停在 failed 步骤")
    if status in {"pending", "running", "retry_wait"} and (
        document.get("result_ref") is not None or document.get("script_id") is not None
    ):
        issues.append("未完成脚本任务不能提前填写结果")
    return issues


def _material_issues(
    document: Mapping[str, Any],
    related: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    issues: list[str] = []
    clips = document.get("clips", [])
    clip_items = clips if isinstance(clips, list) else []
    if _duplicate_ids(clip_items):
        issues.append("clips: ID 不能重复")
    scene_clip_items = [
        item for item in clip_items
        if isinstance(item, Mapping) and item.get("capture_scope") != "full_take"
    ]
    full_take_items = [
        item for item in clip_items
        if isinstance(item, Mapping) and item.get("capture_scope") == "full_take"
    ]
    source_shot_ids = [item.get("source_shot_id") for item in scene_clip_items]
    if len(source_shot_ids) != len(set(source_shot_ids)):
        issues.append("clips.source_shot_id 不能重复")
    orders = [item.get("order") for item in clip_items if isinstance(item, Mapping)]
    if orders != list(range(1, len(orders) + 1)):
        issues.append("clips.order 必须从 1 连续递增")

    file_info = document.get("file", {})
    duration = file_info.get("duration_ms") if isinstance(file_info, Mapping) else None
    if isinstance(duration, int):
        _check_continuous_ranges(issues, "clips", scene_clip_items, duration)
        for index, clip in enumerate(clip_items):
            if isinstance(clip, Mapping):
                _check_range(issues, f"clips[{index}]", clip, duration)
                quality = clip.get("quality", {})
                if isinstance(quality, Mapping):
                    scores = [quality.get(field) for field in ("clarity", "stability", "audio", "exposure")]
                    if all(isinstance(score, int) for score in scores):
                        expected = round(sum(scores) / len(scores))
                        if quality.get("overall") != expected:
                            issues.append(f"clips[{index}].quality.overall 应为四项质量均值 {expected}")

        if document.get("capture_role") == "host_take":
            if len(full_take_items) != 1:
                issues.append("主播连续长镜头必须有且只有一个受控全片主素材片段")
            elif full_take_items[0].get("start_ms") != 0 or full_take_items[0].get("end_ms") != duration:
                issues.append("主播连续长镜头的受控全片片段必须覆盖 0 到视频总时长")
        elif full_take_items:
            issues.append("只有主播连续长镜头可以声明受控全片片段")

    product = related.get("product")
    if product is not None:
        if document.get("product_id") != product.get("product_id"):
            issues.append("material.product_id 与关联商品不一致")
        if document.get("product_revision") != product.get("revision"):
            issues.append("material.product_revision 与关联商品修订不一致")
    return issues


def _shooting_task_issues(
    document: Mapping[str, Any],
    related: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    issues: list[str] = []
    requirements = document.get("requirements", [])
    items = requirements if isinstance(requirements, list) else []
    shot_ids = [item.get("script_shot_id") for item in items if isinstance(item, Mapping)]
    if len(shot_ids) != len(set(shot_ids)):
        issues.append("requirements.script_shot_id 不能重复")

    confirmed_pairs: list[tuple[Any, Any]] = []
    missing_count = 0
    matched_count = 0
    for index, requirement in enumerate(items):
        if not isinstance(requirement, Mapping):
            continue
        match_status = requirement.get("match_status")
        confirmed = requirement.get("confirmed_match")
        suggestions = requirement.get("suggestions", [])
        optional = requirement.get("requirement_status") == "optional"
        requirement_kind = requirement.get("requirement_kind", "primary")
        if requirement_kind == "detail_overlay":
            if not optional or requirement.get("purpose") != "detail":
                issues.append(f"requirements[{index}]: 细节覆盖必须是 optional/detail")
            if not _has_text(requirement.get("source_script_shot_id")):
                issues.append(f"requirements[{index}]: 细节覆盖必须关联原脚本镜头")
            if not _has_text(requirement.get("detail_tag")):
                issues.append(f"requirements[{index}]: 细节覆盖必须填写 detail_tag")
        elif requirement.get("source_script_shot_id") not in {None, requirement.get("script_shot_id")}:
            issues.append(f"requirements[{index}]: 主素材要求的源镜头必须与 script_shot_id 一致")
        if match_status == "confirmed":
            matched_count += 1
            if not isinstance(confirmed, Mapping):
                issues.append(f"requirements[{index}]: confirmed 状态必须有 confirmed_match")
            else:
                confirmed_pairs.append((confirmed.get("material_id"), confirmed.get("clip_id")))
        elif confirmed is not None:
            issues.append(f"requirements[{index}]: 未确认状态不能填写 confirmed_match")
        if match_status == "suggested" and not suggestions:
            issues.append(f"requirements[{index}]: suggested 状态必须有建议素材")
        if match_status == "missing" and suggestions:
            issues.append(f"requirements[{index}]: missing 状态不能同时存在建议素材")
        if not optional and match_status != "confirmed":
            missing_count += 1

    duplicated_confirmed_pairs = {
        pair for pair in confirmed_pairs if confirmed_pairs.count(pair) > 1
    }
    if document.get("matched_count") != matched_count:
        issues.append(f"matched_count 应为 {matched_count}")
    if document.get("missing_count") != missing_count:
        issues.append(f"missing_count 应为 {missing_count}")
    expected_status = "ready_for_edit" if missing_count == 0 else "materials_uploaded" if matched_count else "pending_shoot"
    if document.get("status") != expected_status:
        issues.append(f"status 应为 {expected_status}")

    script = related.get("script")
    if duplicated_confirmed_pairs and script is None:
        issues.append("同一拍摄任务不能重复确认同一个素材片段")
    if script is not None:
        if script.get("review", {}).get("status") != "approved":
            issues.append("拍摄任务只能关联已批准脚本")
        if document.get("script_id") != script.get("script_id"):
            issues.append("shooting_task.script_id 与关联脚本不一致")
        if document.get("script_revision") != script.get("revision"):
            issues.append("shooting_task.script_revision 与关联脚本修订不一致")
        selected_version_id = document.get("selected_version_id")
        script_selected_version_id = script.get("selected_version_id")
        if selected_version_id is not None and selected_version_id != (script_selected_version_id or selected_version_id):
            issues.append("shooting_task.selected_version_id 与脚本选中的拍摄版本不一致")
        if document.get("product_id") != script.get("product_id"):
            issues.append("shooting_task.product_id 与关联脚本商品不一致")
        fixed_long_take = (
            isinstance(script.get("production_mode"), Mapping)
            and script["production_mode"].get("kind") == "fixed_livestream_long_take"
        )
        for pair in duplicated_confirmed_pairs:
            reused_by = [
                item for item in items
                if isinstance(item, Mapping)
                and isinstance(item.get("confirmed_match"), Mapping)
                and (item["confirmed_match"].get("material_id"), item["confirmed_match"].get("clip_id")) == pair
            ]
            if not (
                fixed_long_take
                and all(item.get("requirement_kind") == "primary" for item in reused_by)
                and len({item.get("version_id") for item in reused_by}) == 1
            ):
                issues.append("只有同一脚本版本的主播连续长镜头才能重复确认同一个素材片段")
        selected_versions = [
            version for version in script.get("versions", [])
            if isinstance(version, Mapping)
            and (selected_version_id is None or version.get("id") == selected_version_id)
        ]
        script_shots = {
            shot.get("id"): (version.get("id"), shot)
            for version in selected_versions
            if isinstance(version, Mapping)
            for shot in version.get("shots", [])
            if isinstance(shot, Mapping)
        }
        primary_shot_ids = {
            item.get("script_shot_id") for item in items
            if isinstance(item, Mapping) and item.get("requirement_kind", "primary") == "primary"
        }
        if primary_shot_ids != set(script_shots):
            issues.append("requirements 必须完整覆盖选中拍摄版本的全部镜头")
        if any(isinstance(item, Mapping) and "requirement_kind" in item for item in items):
            expected_detail_sources = {
                shot_id for shot_id, (_, shot) in script_shots.items()
                if isinstance(shot.get("detail_overlay"), Mapping)
                and shot["detail_overlay"].get("mode") == "optional_detail"
            }
            detail_items = [
                (index, item) for index, item in enumerate(items)
                if isinstance(item, Mapping) and item.get("requirement_kind") == "detail_overlay"
            ]
            actual_detail_sources = [item.get("source_script_shot_id") for _, item in detail_items]
            if set(actual_detail_sources) != expected_detail_sources or len(actual_detail_sources) != len(set(actual_detail_sources)):
                issues.append("detail_overlay 要求必须逐一对应脚本中的可选细节镜头")
            for index, requirement in detail_items:
                source_shot_id = requirement.get("source_script_shot_id")
                source_entry = script_shots.get(source_shot_id)
                if source_entry is None:
                    continue
                version_id, source_shot = source_entry
                detail_overlay = source_shot.get("detail_overlay")
                if requirement.get("version_id") != version_id:
                    issues.append(f"requirements[{index}]: 细节覆盖的脚本版本不一致")
                if requirement.get("script_shot_id") == source_shot_id:
                    issues.append(f"requirements[{index}]: 细节覆盖必须使用独立的匹配 ID")
                if (
                    isinstance(detail_overlay, Mapping)
                    and requirement.get("detail_tag") != detail_overlay.get("detail_tag")
                ):
                    issues.append(f"requirements[{index}]: 细节标签必须与脚本一致")
    return issues


def _material_import_task_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    status = document.get("status")
    if document.get("attempt_count", 0) > document.get("max_attempts", 0):
        issues.append("attempt_count 不能大于 max_attempts")
    if status == "completed":
        if document.get("progress") != 100 or document.get("current_step") != "completed":
            issues.append("completed 素材任务必须是 100% 且 current_step=completed")
        if not document.get("material_id") or document.get("error") is not None:
            issues.append("completed 素材任务必须有素材结果且不能有错误")
    if status == "failed" and (document.get("current_step") != "failed" or not document.get("error")):
        issues.append("failed 素材任务必须有错误并停在 failed 步骤")
    if status in {"pending", "running", "retry_wait"} and document.get("material_id") is not None:
        issues.append("未完成素材任务不能提前填写素材结果")
    return issues


def _material_usage_issues(document: Mapping[str, Any]) -> list[str]:
    return []


def _gold_case_issues(document: Mapping[str, Any]) -> list[str]:
    if document.get("annotation_status") != "accepted":
        return []
    issues: list[str] = []
    if document.get("fixture_data"):
        issues.append("accepted 金标准案例不能是 fixture_data")
    for field in ("analysis_file", "script_file", "annotator", "reviewer", "accepted_at"):
        if not document.get(field):
            issues.append(f"accepted 金标准案例缺少 {field}")
    if not document.get("product_files"):
        issues.append("accepted 金标准案例至少需要一个 product_file")
    annotations = document.get("required_annotations", {})
    for field in ("transcript_complete", "shot_boundaries_complete", "evidence_complete", "script_complete"):
        if not annotations.get(field):
            issues.append(f"accepted 金标准案例尚未完成 {field}")
    return issues


def _gold_manifest_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    video_slots = document.get("video_slots", [])
    product_slots = document.get("product_slots", [])
    for name, items in (("video_slots", video_slots), ("product_slots", product_slots)):
        entries = items if isinstance(items, list) else []
        duplicates = _duplicate_ids(entries, "slot_id")
        if duplicates:
            issues.append(f"{name}: slot_id 重复 {sorted(duplicates)}")
        path_field = "case_file" if name == "video_slots" else "profile_file"
        for index, item in enumerate(entries):
            if isinstance(item, dict) and item.get("status") == "accepted" and not item.get(path_field):
                issues.append(f"{name}[{index}]: accepted 状态必须填写 {path_field}")
    if isinstance(product_slots, list) and document.get("required_product_count") != len(product_slots):
        issues.append("required_product_count 必须与 product_slots 数量一致")
    return issues


def _media_task_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    status = document.get("status")
    progress = document.get("progress")
    if document.get("attempt_count", 0) > document.get("max_attempts", 0):
        issues.append("attempt_count 不能大于 max_attempts")
    if status == "completed":
        if progress != 100 or document.get("current_step") != "completed":
            issues.append("completed 任务必须是 100% 且 current_step=completed")
        if not document.get("result_path") or document.get("error") is not None:
            issues.append("completed 任务必须有 result_path 且不能有 error")
    if status == "failed":
        if document.get("current_step") != "failed" or not document.get("error"):
            issues.append("failed 任务必须有错误并停在 failed 步骤")
    if status in {"pending", "retry_wait"} and document.get("result_path") is not None:
        issues.append("未完成任务不能提前填写 result_path")
    return issues


def _media_result_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    media = document.get("media", {})
    duration = media.get("duration_ms") if isinstance(media, dict) else None
    shots = document.get("shots", [])
    shot_items = shots if isinstance(shots, list) else []
    if _duplicate_ids(shot_items):
        issues.append("shots: ID 不能重复")
    if isinstance(duration, int):
        previous_end = 0
        for index, shot in enumerate(shot_items):
            if not isinstance(shot, dict):
                continue
            _check_range(issues, f"shots[{index}]", shot, duration)
            if shot.get("start_ms") != previous_end:
                issues.append(f"shots[{index}]: 镜头必须从上一个边界连续开始")
            end = shot.get("end_ms")
            if isinstance(end, int):
                previous_end = end
        if shot_items and previous_end != duration:
            issues.append("shots: 最后一个镜头必须结束于视频总时长")

    artifacts = document.get("artifacts", {})
    asr = document.get("asr", {})
    has_audio = media.get("has_audio") if isinstance(media, dict) else None
    if isinstance(artifacts, dict) and isinstance(asr, dict):
        status = asr.get("status")
        if status == "completed" and (not artifacts.get("audio_path") or not artifacts.get("transcript_path")):
            issues.append("ASR completed 必须同时有 audio_path 和 transcript_path")
        if status == "no_audio" and (has_audio is not False or artifacts.get("audio_path") or artifacts.get("transcript_path")):
            issues.append("ASR no_audio 必须与无音轨媒体和空音频产物一致")
        if status == "model_missing" and (has_audio is not True or not artifacts.get("audio_path") or artifacts.get("transcript_path")):
            issues.append("ASR model_missing 必须保留音频但不能伪造转写")
    return issues


def _analysis_task_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    status = document.get("status")
    if document.get("attempt_count", 0) > document.get("max_attempts", 0):
        issues.append("attempt_count 不能大于 max_attempts")
    if status == "completed":
        if document.get("progress") != 100 or document.get("current_step") != "completed":
            issues.append("completed 分析任务必须是 100% 且 current_step=completed")
        if not document.get("ocr_result_path") or not document.get("result_path") or document.get("error") is not None:
            issues.append("completed 分析任务必须有 OCR、报告路径且不能有错误")
    if status == "failed" and (document.get("current_step") != "failed" or not document.get("error")):
        issues.append("failed 分析任务必须有错误并停在 failed 步骤")
    if status == "cancelled" and document.get("current_step") != "cancelled":
        issues.append("cancelled 分析任务必须停在 cancelled 步骤")
    if status in {"pending", "retry_wait"} and document.get("result_path") is not None:
        issues.append("未完成分析任务不能提前填写 result_path")
    return issues


def _ocr_result_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    frames = document.get("frames", [])
    frame_items = frames if isinstance(frames, list) else []
    shot_ids = [frame.get("shot_id") for frame in frame_items if isinstance(frame, dict)]
    if len(shot_ids) != len(set(shot_ids)):
        issues.append("frames.shot_id 不能重复")
    line_ids: list[str] = []
    for frame in frame_items:
        if isinstance(frame, dict):
            line_ids.extend(
                line["id"] for line in frame.get("lines", [])
                if isinstance(line, dict) and isinstance(line.get("id"), str)
            )
    if len(line_ids) != len(set(line_ids)):
        issues.append("OCR 文本行 ID 必须全局唯一")
    return issues


def _edit_project_issues(
    document: Mapping[str, Any], related: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    issues: list[str] = []
    variants = document.get("variants", [])
    variant_items = variants if isinstance(variants, list) else []
    if _duplicate_ids(variant_items):
        issues.append("variants: ID 不能重复")
    if _duplicate_ids(variant_items, "script_version_id"):
        issues.append("variants.script_version_id 不能重复")
    all_clips = [clip for variant in variant_items if isinstance(variant, dict) for clip in variant.get("clips", []) if isinstance(clip, dict)]
    if _duplicate_ids(all_clips):
        issues.append("所有剪辑版本中的片段 ID 必须全局唯一")

    script = related.get("script")
    script_shot_map: dict[Any, Mapping[str, Any]] = {}
    if isinstance(script, Mapping):
        if script.get("review", {}).get("status") != "approved":
            issues.append("剪辑工程只能来自已通过脚本审核的脚本")
        checks = (
            ("script_id", "script_id", "脚本 ID"),
            ("script_revision", "revision", "脚本修订"),
            ("product_id", "product_id", "商品 ID"),
            ("fixture_data", "fixture_data", "样例标记"),
        )
        for project_field, script_field, label in checks:
            if document.get(project_field) != script.get(script_field):
                issues.append(f"剪辑工程的{label}必须与脚本一致")
        script_versions = script.get("versions", [])
        script_version_items = script_versions if isinstance(script_versions, list) else []
        selected_version_id = document.get("selected_version_id")
        expected_version_items = [
            item for item in script_version_items
            if isinstance(item, dict) and (selected_version_id is None or item.get("id") == selected_version_id)
        ]
        if selected_version_id is not None and selected_version_id != script.get("selected_version_id"):
            issues.append("剪辑工程的选中拍摄版本必须与脚本一致")
        script_shot_map = {
            shot.get("id"): shot
            for version in expected_version_items
            for shot in version.get("shots", []) if isinstance(shot, Mapping)
        }
        if {item.get("script_version_id") for item in variant_items if isinstance(item, dict)} != {
            item.get("id") for item in expected_version_items if isinstance(item, dict)
        }:
            issues.append("剪辑版本必须完整对应选中的脚本版本")
        shots_by_version = {
            item.get("id"): {
                shot.get("id") for shot in item.get("shots", []) if isinstance(shot, dict)
            }
            for item in expected_version_items if isinstance(item, dict)
        }
        for index, variant in enumerate(variant_items):
            if not isinstance(variant, dict):
                continue
            actual = {
                clip.get("script_shot_id") for clip in variant.get("clips", []) if isinstance(clip, dict)
            }
            expected = shots_by_version.get(variant.get("script_version_id"), set())
            if actual != expected:
                issues.append(f"variants[{index}]: 剪辑片段必须完整对应该脚本版本的分镜")

    materials = related.get("materials")
    material_map = materials if isinstance(materials, Mapping) else {}
    for variant_index, variant in enumerate(variant_items):
        if not isinstance(variant, dict):
            continue
        clips = variant.get("clips", [])
        clip_items = clips if isinstance(clips, list) else []
        if _duplicate_ids(clip_items):
            issues.append(f"variants[{variant_index}].clips: ID 不能重复")
        if _duplicate_ids(clip_items, "script_shot_id"):
            issues.append(f"variants[{variant_index}].clips: script_shot_id 不能重复")
        previous_end = 0
        for clip_index, clip in enumerate(clip_items):
            if not isinstance(clip, dict):
                continue
            label = f"variants[{variant_index}].clips[{clip_index}]"
            if clip.get("order") != clip_index + 1:
                issues.append(f"{label}: order 必须从 1 连续递增")
            start = clip.get("timeline_start_ms")
            end = clip.get("timeline_end_ms")
            if start != previous_end:
                issues.append(f"{label}: 时间线必须从上一个边界 {previous_end} 连续开始")
            if isinstance(start, int) and isinstance(end, int) and start >= end:
                issues.append(f"{label}: timeline_start_ms 必须小于 timeline_end_ms")
            if isinstance(end, int):
                previous_end = end
            source_start = clip.get("source_start_ms")
            source_end = clip.get("source_end_ms")
            if isinstance(source_start, int) and isinstance(source_end, int) and source_start >= source_end:
                issues.append(f"{label}: source_start_ms 必须小于 source_end_ms")
            if clip.get("transition") == "cut" and clip.get("transition_ms") != 0:
                issues.append(f"{label}: 硬切的 transition_ms 必须是 0")
            if clip.get("transition") == "fade" and clip.get("transition_ms") == 0:
                issues.append(f"{label}: 淡入淡出必须有正数 transition_ms")

            material = material_map.get(clip.get("material_id"))
            if isinstance(material, Mapping):
                if material.get("product_id") != document.get("product_id"):
                    issues.append(f"{label}: 素材商品与剪辑工程商品不一致")
                if material.get("fixture_data") != document.get("fixture_data"):
                    issues.append(f"{label}: 素材样例标记与剪辑工程不一致")
                if clip.get("has_source_audio") != material.get("file", {}).get("has_audio"):
                    issues.append(f"{label}: 源音轨标记必须与素材文件一致")
                material_clip = next((item for item in material.get("clips", []) if item.get("id") == clip.get("material_clip_id")), None)
                if not isinstance(material_clip, Mapping):
                    issues.append(f"{label}: 找不到对应的素材片段")
                else:
                    if _material_capture_role(material) == "host_take":
                        if material.get("file", {}).get("has_audio") is not True:
                            issues.append(f"{label}: 主播连续长镜头主素材必须包含可用原声")
                        if material_clip.get("capture_scope") != "full_take":
                            issues.append(f"{label}: 主播连续长镜头必须使用受控全片主素材片段")
                    if not (
                        isinstance(source_start, int) and isinstance(source_end, int)
                        and material_clip.get("start_ms", -1) <= source_start < source_end <= material_clip.get("end_ms", -1)
                    ):
                        issues.append(f"{label}: 源片段范围超出已确认素材片段")

            overlay = clip.get("visual_overlay")
            if isinstance(overlay, Mapping):
                source_shot = script_shot_map.get(clip.get("script_shot_id"))
                if isinstance(source_shot, Mapping):
                    detail_overlay = source_shot.get("detail_overlay")
                    if not isinstance(detail_overlay, Mapping) or detail_overlay.get("mode") != "optional_detail":
                        issues.append(f"{label}.visual_overlay: 脚本镜头没有声明可选细节覆盖")
                    elif overlay.get("detail_tag") != detail_overlay.get("detail_tag"):
                        issues.append(f"{label}.visual_overlay: 细节标签必须与脚本一致")
                overlay_material = material_map.get(overlay.get("material_id"))
                if isinstance(overlay_material, Mapping):
                    if overlay_material.get("product_id") != document.get("product_id"):
                        issues.append(f"{label}.visual_overlay: 细节素材商品与剪辑工程商品不一致")
                    if overlay_material.get("fixture_data") != document.get("fixture_data"):
                        issues.append(f"{label}.visual_overlay: 细节素材样例标记与剪辑工程不一致")
                    overlay_clip = next(
                        (
                            item for item in overlay_material.get("clips", [])
                            if item.get("id") == overlay.get("material_clip_id")
                        ),
                        None,
                    )
                    overlay_start = overlay.get("source_start_ms")
                    overlay_end = overlay.get("source_end_ms")
                    if not isinstance(overlay_clip, Mapping):
                        issues.append(f"{label}.visual_overlay: 找不到对应的细节素材片段")
                    else:
                        if (
                            "detail" not in overlay_clip.get("purpose_tags", [])
                            and _material_capture_role(overlay_material) != "detail"
                        ):
                            issues.append(f"{label}.visual_overlay: 覆盖片段必须标记为 detail")
                        if not (
                            isinstance(overlay_start, int) and isinstance(overlay_end, int)
                            and overlay_clip.get("start_ms", -1) <= overlay_start < overlay_end <= overlay_clip.get("end_ms", -1)
                        ):
                            issues.append(f"{label}.visual_overlay: 源范围超出已确认细节片段")
        if previous_end != variant.get("duration_ms"):
            issues.append(f"variants[{variant_index}]: 最后一段必须结束于版本总时长")

    if document.get("status") in {"video_review", "approved", "rejected"} and not any(
        isinstance(item, dict) and item.get("latest_output_id") for item in variant_items
    ):
        issues.append("进入成片审核后至少需要一个渲染产物")
    audio_asset = related.get("audio_asset")
    bgm_asset_id = document.get("settings", {}).get("bgm_asset_id")
    if bgm_asset_id and isinstance(audio_asset, Mapping):
        if audio_asset.get("asset_id") != bgm_asset_id or audio_asset.get("kind") != "bgm":
            issues.append("剪辑工程所选音频必须是对应的 BGM 资产")
        if audio_asset.get("fixture_data") != document.get("fixture_data"):
            issues.append("工程样例音频和正式剪辑工程不能混用")
    return issues


def _render_task_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    status = document.get("status")
    if document.get("attempt_count", 0) > document.get("max_attempts", 0):
        issues.append("attempt_count 不能大于 max_attempts")
    if status == "completed":
        if document.get("progress") != 100 or document.get("current_step") != "completed":
            issues.append("completed 渲染任务必须是 100% 且 current_step=completed")
        if not document.get("output_id") or document.get("error") is not None:
            issues.append("completed 渲染任务必须有成片产物且不能有错误")
    if status == "failed" and (document.get("current_step") != "failed" or not document.get("error")):
        issues.append("failed 渲染任务必须有错误并停在 failed 步骤")
    if status in {"pending", "running", "retry_wait"} and document.get("output_id") is not None:
        issues.append("未完成渲染任务不能提前填写成片产物")
    return issues


def _render_output_issues(
    document: Mapping[str, Any], related: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    issues: list[str] = []
    review = document.get("review", {})
    review_status = review.get("status") if isinstance(review, Mapping) else None
    expected_status = {"pending": "video_review", "approved": "approved", "rejected": "rejected"}.get(review_status)
    if expected_status != document.get("status"):
        issues.append("成片 status 必须与 review.status 一致")
    if isinstance(review, Mapping):
        if review_status == "pending" and (review.get("reviewed_by") or review.get("reviewed_at")):
            issues.append("待审成片不能提前填写审核人和时间")
        if review_status in {"approved", "rejected"} and (not review.get("reviewed_by") or not review.get("reviewed_at")):
            issues.append("已审成片必须填写审核人和审核时间")
        if review_status == "rejected" and not review.get("note"):
            issues.append("驳回成片必须填写原因")

    task = related.get("render_task")
    if isinstance(task, Mapping):
        checks = (
            (document.get("render", {}).get("task_id"), task.get("task_id"), "渲染任务 ID"),
            (document.get("project_id"), task.get("project_id"), "剪辑工程 ID"),
            (document.get("project_revision"), task.get("project_revision"), "剪辑工程修订"),
            (document.get("variant_id"), task.get("variant_id"), "剪辑版本 ID"),
            (document.get("fixture_data"), task.get("fixture_data"), "样例标记"),
        )
        for actual, expected, label in checks:
            if actual != expected:
                issues.append(f"成片的{label}必须与渲染任务一致")

    project = related.get("edit_project")
    if isinstance(project, Mapping):
        if document.get("project_id") != project.get("project_id") or document.get("project_revision") != project.get("revision"):
            issues.append("成片必须来自对应的剪辑工程修订")
        if document.get("fixture_data") != project.get("fixture_data"):
            issues.append("成片样例标记必须与剪辑工程一致")
        variants = project.get("variants", [])
        if not any(isinstance(item, dict) and item.get("id") == document.get("variant_id") for item in variants):
            issues.append("成片对应的剪辑版本不存在")
    return issues


def _edit_audio_asset_issues(document: Mapping[str, Any]) -> list[str]:
    return []


def _publication_issues(
    document: Mapping[str, Any], related: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    issues: list[str] = []
    parsed = urlparse(str(document.get("work_url", "")))
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (hostname == "douyin.com" or hostname.endswith(".douyin.com")):
        issues.append("抖音作品链接必须使用官方 douyin.com HTTPS 域名")
    history = document.get("history", [])
    if isinstance(history, list):
        revisions = [item.get("revision") for item in history if isinstance(item, Mapping)]
        if revisions != list(range(1, len(revisions) + 1)) or (revisions and revisions[-1] != document.get("revision")):
            issues.append("发布记录历史修订必须从 1 连续递增并等于当前修订")
        if history and isinstance(history[0], Mapping) and history[0].get("action") != "registered":
            issues.append("发布记录第一条历史必须是 registered")
    business_review = document.get("business_review", {})
    if isinstance(business_review, Mapping):
        if business_review.get("status") == "confirmed":
            if not business_review.get("reviewed_by") or not business_review.get("reviewed_at"):
                issues.append("业务闭环确认必须记录确认人和确认时间")
            if not business_review.get("note"):
                issues.append("业务闭环确认必须填写复盘说明")
        elif business_review.get("reviewed_by") is not None or business_review.get("reviewed_at") is not None:
            issues.append("待确认的业务闭环不能提前填写确认人或确认时间")
    output = related.get("render_output")
    if isinstance(output, Mapping):
        review = output.get("review", {})
        checks = (
            (document.get("output_id"), output.get("output_id"), "成片 ID"),
            (document.get("project_id"), output.get("project_id"), "剪辑工程 ID"),
            (document.get("project_revision"), output.get("project_revision"), "剪辑工程修订"),
            (document.get("variant_id"), output.get("variant_id"), "剪辑版本 ID"),
            (document.get("fixture_data"), output.get("fixture_data"), "样例标记"),
            (document.get("output_sha256"), output.get("media", {}).get("sha256"), "成片指纹"),
        )
        for actual, expected, label in checks:
            if actual != expected:
                issues.append(f"发布记录的{label}必须与成片一致")
        if output.get("status") != "approved" or not isinstance(review, Mapping) or review.get("status") != "approved":
            issues.append("只有人工审核通过的成片才能登记发布")
    project = related.get("edit_project")
    if isinstance(project, Mapping):
        if project.get("project_id") != document.get("project_id") or project.get("revision") != document.get("project_revision"):
            issues.append("发布记录必须来自当前剪辑工程修订")
        if project.get("script_id") != document.get("script_id") or project.get("product_id") != document.get("product_id"):
            issues.append("发布记录的脚本和商品必须与剪辑工程一致")
        variant = next((item for item in project.get("variants", []) if isinstance(item, Mapping) and item.get("id") == document.get("variant_id")), None)
        if not isinstance(variant, Mapping):
            issues.append("发布记录对应的剪辑版本不存在")
        else:
            if variant.get("script_version_id") != document.get("script_version_id"):
                issues.append("发布记录的脚本版本必须与剪辑版本一致")
            if variant.get("latest_output_id") != document.get("output_id"):
                issues.append("旧修订或非最新成片不能登记发布")
    return issues


def _metric_snapshot_issues(
    document: Mapping[str, Any], related: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    issues: list[str] = []
    correction = bool(document.get("is_correction"))
    reason = document.get("correction_reason")
    if correction and not reason:
        issues.append("修正快照必须填写修正原因")
    if not correction and reason:
        issues.append("普通快照不能填写修正原因")
    if document.get("confidence") == "unconfirmed":
        issues.append("未确认数据只能保存在导入草稿，不能成为正式快照")
    publication = related.get("publication")
    if isinstance(publication, Mapping):
        if publication.get("publication_id") != document.get("publication_id"):
            issues.append("指标快照必须对应同一发布记录")
        if publication.get("fixture_data") != document.get("fixture_data"):
            issues.append("指标快照与发布记录不能混用样例和正式数据")
        try:
            published_at = datetime.fromisoformat(str(publication.get("published_at")).replace("Z", "+00:00"))
            captured_at = datetime.fromisoformat(str(document.get("captured_at")).replace("Z", "+00:00"))
            expected = max(0, round((captured_at - published_at).total_seconds() / 60))
            if captured_at < published_at:
                issues.append("指标采集时间不能早于作品发布时间")
            elif abs(expected - int(document.get("observation_minutes", -1))) > 1:
                issues.append("观察时长必须由作品发布时间和采集时间计算")
        except (TypeError, ValueError):
            pass
    previous = related.get("previous_snapshot")
    if isinstance(previous, Mapping) and not correction:
        cumulative = ("views", "followers_gained", "likes", "comments", "favorites", "shares", "product_clicks", "orders", "gmv_cents")
        current_metrics = document.get("metrics", {})
        previous_metrics = previous.get("metrics", {})
        if isinstance(current_metrics, Mapping) and isinstance(previous_metrics, Mapping):
            for field in cumulative:
                current = current_metrics.get(field)
                prior = previous_metrics.get(field)
                if isinstance(current, (int, float)) and isinstance(prior, (int, float)) and current < prior:
                    issues.append(f"累计指标 {field} 不能低于上一条快照；如需更正请标记修正快照")
    return issues


def _metric_import_draft_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    candidates = document.get("candidates", [])
    candidate_items = candidates if isinstance(candidates, list) else []
    if _duplicate_ids(candidate_items, "candidate_id"):
        issues.append("导入候选 ID 不能重复")
    status = document.get("status")
    snapshot_ids = document.get("confirmed_snapshot_ids", [])
    if status == "pending" and snapshot_ids:
        issues.append("待确认导入不能提前绑定正式快照")
    if status == "confirmed" and (not snapshot_ids or len(snapshot_ids) != len(candidate_items)):
        issues.append("已确认导入必须为每条候选生成一条正式快照")
    if status == "rejected" and snapshot_ids:
        issues.append("已拒绝导入不能绑定正式快照")
    return issues


def _learning_report_issues(
    document: Mapping[str, Any], related: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    issues: list[str] = []
    publications = document.get("publication_ids", [])
    comparison = document.get("comparison", [])
    comparison_items = comparison if isinstance(comparison, list) else []
    comparison_ids = {item.get("publication_id") for item in comparison_items if isinstance(item, Mapping)}
    winner = document.get("winner_publication_id")
    if document.get("status") == "ready":
        if len(publications) < 2 or len(comparison_items) < 2:
            issues.append("可用复盘至少需要两条可比作品")
        if winner not in comparison_ids:
            issues.append("复盘赢家必须来自对比作品")
        if not document.get("evidence") or not document.get("observations"):
            issues.append("可用复盘必须包含观察和证据")
    elif winner is not None:
        issues.append("数据不足时不能宣布赢家")
    if not comparison_ids.issubset(set(publications)):
        issues.append("对比项必须全部出现在 publication_ids")
    publication_map = related.get("publications")
    if isinstance(publication_map, Mapping):
        for publication_id in publications:
            item = publication_map.get(publication_id)
            if isinstance(item, Mapping):
                if item.get("product_id") != document.get("product_id"):
                    issues.append("复盘只能比较同一商品的作品")
                if item.get("fixture_data") != document.get("fixture_data"):
                    issues.append("复盘不能混用样例和正式作品")
    return issues


def _viral_skill_issues(document: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    occurrences = [item for item in document.get("occurrences", []) if isinstance(item, Mapping)]
    occurrence_ids = [item.get("occurrence_id") for item in occurrences]
    if len(occurrence_ids) != len(set(occurrence_ids)):
        issues.append("occurrences.occurrence_id 不能重复")

    video_ids = {item.get("video_id") for item in occurrences if isinstance(item.get("video_id"), str)}
    if document.get("occurrence_count") != len(occurrences):
        issues.append("occurrence_count 必须等于实际来源 occurrence 数量")
    if document.get("distinct_video_count") != len(video_ids):
        issues.append("distinct_video_count 必须按 occurrences 中不同 video_id 计算")
    expected_classification = "common_candidate" if len(video_ids) >= 2 else "single_video"
    if document.get("classification") != expected_classification:
        issues.append(f"classification 必须按不同视频数标记为 {expected_classification}")
    if document.get("representative_occurrence_id") not in set(occurrence_ids):
        issues.append("representative_occurrence_id 无法在 occurrences 中解析")

    canonical_steps = [item for item in document.get("steps", []) if isinstance(item, Mapping)]
    if [item.get("order") for item in canonical_steps] != list(range(1, len(canonical_steps) + 1)):
        issues.append("steps.order 必须从 1 连续递增")

    has_timed_primary_evidence = False
    primary_video_ids: set[str] = set()
    has_metric_evidence = False
    for occurrence_index, occurrence in enumerate(occurrences):
        steps = [item for item in occurrence.get("steps", []) if isinstance(item, Mapping)]
        if [item.get("order") for item in steps] != list(range(1, len(steps) + 1)):
            issues.append(f"occurrences[{occurrence_index}].steps.order 必须从 1 连续递增")
        occurrence_has_primary = False
        for step in steps:
            for evidence in step.get("evidence", []):
                if not isinstance(evidence, Mapping):
                    continue
                expected_qualified_id = f"{occurrence.get('analysis_id')}:{evidence.get('evidence_id')}"
                if evidence.get("qualified_evidence_id") != expected_qualified_id:
                    issues.append(
                        f"occurrences[{occurrence_index}] 的 qualified_evidence_id 必须包含 analysis_id 命名空间"
                    )
                has_time = isinstance(evidence.get("start_ms"), int) and isinstance(evidence.get("end_ms"), int)
                has_content = bool(evidence.get("exact_dialogue") or evidence.get("action"))
                if has_time and has_content:
                    occurrence_has_primary = True
                    has_timed_primary_evidence = True
                if evidence.get("source_type") == "metric" and isinstance(evidence.get("metric_values"), Mapping):
                    if any(value is not None for value in evidence["metric_values"].values()):
                        has_metric_evidence = True
        if occurrence.get("has_primary_evidence") is not occurrence_has_primary:
            issues.append(f"occurrences[{occurrence_index}].has_primary_evidence 与实际证据不一致")
        if occurrence_has_primary and isinstance(occurrence.get("video_id"), str):
            primary_video_ids.add(occurrence["video_id"])

    if document.get("reuse_mode") == "reuse" and not has_timed_primary_evidence:
        issues.append("正向复用 Skill 至少需要一条带时间码的原话或动作证据")
    if document.get("reuse_mode") == "reuse":
        missing_primary_videos = sorted(video_ids - primary_video_ids)
        if missing_primary_videos:
            issues.append(
                "正向复用 Skill 的每条计入统计的视频都必须有时间码及原话或动作证据："
                + ", ".join(missing_primary_videos)
            )
    if document.get("evidence_level") == "metric_correlation" and not has_metric_evidence:
        issues.append("metric_correlation 必须有真实指标证据；相关性不能由内容推断代替")
    return issues


def _gateway_settings_issues(document: Mapping[str, Any]) -> list[str]:
    """Validate model capability declarations and task-to-model references."""

    issues: list[str] = []
    raw_models = document.get("models", [])
    models = [item for item in raw_models if isinstance(item, Mapping)] if isinstance(raw_models, list) else []
    if _duplicate_ids(models, "model_id"):
        issues.append("模型配置 ID 不能重复")

    models_by_id = {
        item["model_id"]: item
        for item in models
        if isinstance(item.get("model_id"), str)
    }
    requirements = {
        "analysis": {"text", "image"},
        "script": {"text"},
        "material": {"text", "image"},
        "video_review": {"text", "image"},
    }
    labels = {
        "analysis": "深度分析",
        "script": "脚本生成",
        "material": "素材识别",
        "video_review": "视频审核",
    }

    for model in models:
        modalities = set(model.get("modalities", []))
        for purpose in model.get("purposes", []):
            missing = requirements.get(purpose, set()) - modalities
            if missing:
                issues.append(
                    f"{model.get('display_name', model.get('model_id'))}声明支持{labels.get(purpose, purpose)}"
                    f"但缺少输入能力 {sorted(missing)}"
                )

    default_model_id = document.get("default_model_id")
    if isinstance(default_model_id, str):
        default_model = models_by_id.get(default_model_id)
        if default_model is None:
            issues.append("默认模型引用了不存在的模型配置")
        elif default_model.get("enabled") is not True:
            issues.append("默认模型必须是已启用的模型配置")

    routing = document.get("routing", {})
    if isinstance(routing, Mapping):
        for purpose, model_id in routing.items():
            if model_id is None:
                continue
            model = models_by_id.get(model_id)
            if model is None:
                issues.append(f"{labels.get(purpose, purpose)}路由引用了不存在的模型配置")
                continue
            if model.get("enabled") is not True:
                issues.append(f"{labels.get(purpose, purpose)}路由必须使用已启用的模型")
            if purpose not in model.get("purposes", []):
                issues.append(f"{labels.get(purpose, purpose)}路由的模型未声明对应用途")
            missing = requirements.get(purpose, set()) - set(model.get("modalities", []))
            if missing:
                issues.append(f"{labels.get(purpose, purpose)}路由模型缺少输入能力 {sorted(missing)}")

    return issues


def validate_document(
    kind: str,
    document: Mapping[str, Any],
    *,
    related: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return all schema and domain issues. An empty list means valid."""

    issues = _schema_issues(kind, document)
    if issues:
        return issues
    related_documents = related or {}
    domain_validators = {
        "analysis": lambda: _analysis_issues(document),
        "product": lambda: _product_issues(document),
        "script": lambda: _script_issues(document, related_documents),
        "gold_case": lambda: _gold_case_issues(document),
        "gold_manifest": lambda: _gold_manifest_issues(document),
        "media_task": lambda: _media_task_issues(document),
        "media_result": lambda: _media_result_issues(document),
        "ocr_result": lambda: _ocr_result_issues(document),
        "analysis_task": lambda: _analysis_task_issues(document),
        "gateway_settings": lambda: _gateway_settings_issues(document),
        "script_task": lambda: _script_task_issues(document),
        "material": lambda: _material_issues(document, related_documents),
        "shooting_task": lambda: _shooting_task_issues(document, related_documents),
        "material_import_task": lambda: _material_import_task_issues(document),
        "material_usage": lambda: _material_usage_issues(document),
        "edit_project": lambda: _edit_project_issues(document, related_documents),
        "render_task": lambda: _render_task_issues(document),
        "render_output": lambda: _render_output_issues(document, related_documents),
        "edit_audio_asset": lambda: _edit_audio_asset_issues(document),
        "publication": lambda: _publication_issues(document, related_documents),
        "metric_snapshot": lambda: _metric_snapshot_issues(document, related_documents),
        "metric_import_draft": lambda: _metric_import_draft_issues(document),
        "learning_report": lambda: _learning_report_issues(document, related_documents),
        "viral_skill": lambda: _viral_skill_issues(document),
    }
    try:
        issues.extend(domain_validators[kind]())
    except KeyError as exc:
        raise ValueError(f"未知合同类型 {kind!r}") from exc
    return issues


def validate_or_raise(
    kind: str,
    document: Mapping[str, Any],
    *,
    related: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    issues = validate_document(kind, document, related=related)
    if issues:
        raise ContractValidationError(kind, issues)
