"""Read-only S5 adapters over accepted S3 analyses and script-eligible S4 products."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .s3_queue import AnalysisTaskQueue
from .s3_reports import load_report
from .s3_skills import SkillNotFoundError, ViralSkillStore
from .s4_products import script_product_eligibility
from .s4_store import ProductNotFoundError, ProductStore


def _accepted_reports(queue: AnalysisTaskQueue, *, include_fixtures: bool) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for task in queue.completed_reports():
        try:
            report = load_report(task.result_path)
        except (OSError, ValueError):
            continue
        if report.get("status") != "accepted":
            continue
        if report.get("fixture_data") and not include_fixtures:
            continue
        if report.get("processing", {}).get("purpose") == "video_review":
            continue
        reports.append(report)
    return reports


def _occurrence_evidence(occurrence: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for step in occurrence.get("steps", [])
        for item in step.get("evidence", [])
        if isinstance(item, dict)
    ]


def _evidence_quality(evidence: dict[str, Any]) -> tuple[Any, ...]:
    start = evidence.get("start_ms")
    end = evidence.get("end_ms")
    timed = isinstance(start, int) and isinstance(end, int) and end >= start
    dialogue = bool(evidence.get("exact_dialogue"))
    action = bool(evidence.get("action"))
    return (
        timed and (dialogue or action),
        dialogue and action,
        not bool(evidence.get("is_inference")),
        float(evidence.get("confidence") or 0),
        sum(bool(evidence.get(key)) for key in ("subtitle", "visual_event", "claim")),
        str(evidence.get("qualified_evidence_id") or evidence.get("evidence_id") or ""),
    )


def _occurrence_quality(occurrence: dict[str, Any]) -> tuple[Any, ...]:
    evidence = _occurrence_evidence(occurrence)
    best = max((_evidence_quality(item) for item in evidence), default=(False, False, False, 0.0, 0, ""))
    primary_count = sum(
        bool(item.get("exact_dialogue") or item.get("action"))
        and isinstance(item.get("start_ms"), int)
        and isinstance(item.get("end_ms"), int)
        for item in evidence
    )
    return (
        bool(occurrence.get("has_primary_evidence")),
        primary_count,
        best,
        int(occurrence.get("analysis_revision") or 0),
        str(occurrence.get("accepted_at") or ""),
        str(occurrence.get("occurrence_id") or ""),
    )


def _representative_sources(skill: dict[str, Any]) -> list[dict[str, Any]]:
    # A repeatedly analysed source video is still one source. Keep the best
    # evidence-bearing occurrence for each video, then cap the compact summary.
    by_video: dict[str, dict[str, Any]] = {}
    for occurrence in skill.get("occurrences", []):
        video_id = str(occurrence.get("video_id") or "")
        current = by_video.get(video_id)
        if current is None or _occurrence_quality(occurrence) > _occurrence_quality(current):
            by_video[video_id] = occurrence

    sources: list[dict[str, Any]] = []
    for occurrence in sorted(by_video.values(), key=_occurrence_quality, reverse=True)[:3]:
        evidence_items = _occurrence_evidence(occurrence)
        evidence = max(evidence_items, key=_evidence_quality) if evidence_items else None
        sources.append({
            "source_name": occurrence["source_name"],
            "video_id": occurrence["video_id"],
            # These four fields form one quote. Never combine an occurrence-wide
            # range with dialogue/action copied from only one evidence item.
            "start_ms": evidence.get("start_ms") if evidence else None,
            "end_ms": evidence.get("end_ms") if evidence else None,
            "dialogue": evidence.get("exact_dialogue") if evidence else None,
            "action": evidence.get("action") if evidence else None,
        })
    return sources


def _template_summary(
    report: dict[str, Any],
    pattern: dict[str, Any],
    skill: dict[str, Any],
    *,
    usage_count: int = 0,
) -> dict[str, Any]:
    summary = report.get("summary", {}).get("overall_conclusion", {}).get("text", "")
    return {
        "template_id": skill["skill_id"],
        "skill_id": skill["skill_id"],
        "skill_revision": skill["revision"],
        "status": skill["status"],
        "reuse_mode": skill["reuse_mode"],
        "source_analysis_id": report["analysis_id"],
        "source_analysis_revision": report["revision"],
        "pattern_id": pattern["id"],
        "name": skill["name"],
        "mechanism": skill["mechanism"],
        "steps": [
            {"id": step["id"], "order": step["order"], "description": step["description"]}
            for step in pattern["steps"]
        ],
        "necessary_conditions": skill["necessary_conditions"],
        "failure_signals": skill["failure_signals"],
        "analysis_summary": summary,
        "fixture_data": bool(report["fixture_data"]),
        "distinct_video_count": skill["distinct_video_count"],
        "occurrence_count": skill["occurrence_count"],
        "script_usage_count": max(0, int(usage_count)),
        "classification": skill["classification"],
        "evidence_level": skill["evidence_level"],
        "causality_status": skill["causality_status"],
        "source_status": skill.get("source_status", "current"),
        "update_available": bool(skill.get("update_available", False)),
        "eligibility": skill.get("eligibility", {
            "s5_eligible": True,
            "reason_code": "eligible",
            "reason": None,
        }),
        "representative_sources": _representative_sources(skill),
    }


def _report_and_pattern_for_skill(
    queue: AnalysisTaskQueue,
    skill: dict[str, Any],
    *,
    include_fixtures: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    representative_id = skill["representative_occurrence_id"]
    occurrence = next(
        (item for item in skill["occurrences"] if item["occurrence_id"] == representative_id),
        None,
    )
    if occurrence is None:
        raise ValueError("正式 Skill 缺少代表来源，请重新确认")
    task = queue.get(occurrence["analysis_task_id"])
    if task is None or task.model_purpose != "analysis" or not task.result_path:
        raise ValueError("Skill 的来源分析已不可用，请重新确认候选")
    try:
        report = load_report(task.result_path)
    except (OSError, ValueError) as exc:
        raise ValueError("Skill 的来源分析已不可用，请重新确认候选") from exc
    if report.get("analysis_id") != occurrence["analysis_id"]:
        raise ValueError("Skill 的来源分析身份已变化，请重新确认候选")
    if report.get("revision") != occurrence["analysis_revision"] or report.get("status") != "accepted":
        raise ValueError("Skill 的来源分析已变化，请重新确认候选后再用于脚本")
    if report.get("processing", {}).get("purpose") == "video_review":
        raise ValueError("视频审核报告不能作为脚本爆点来源")
    if report.get("fixture_data") and not include_fixtures:
        raise ValueError("工程样例 Skill 不能用于真实脚本")
    current_hash = hashlib.sha256(Path(task.result_path).read_bytes()).hexdigest()
    if current_hash != occurrence["report_sha256"]:
        raise ValueError("Skill 的来源分析文件已变化，请刷新候选后重新确认")
    pattern = next(
        (item for item in report.get("pattern_candidates", []) if item.get("id") == occurrence["pattern_id"]),
        None,
    )
    if pattern is None:
        raise ValueError("Skill 的来源爆点步骤已变化，请重新确认候选")
    return report, pattern


def _require_eligible_skill(skill: dict[str, Any]) -> None:
    eligibility = skill["eligibility"]
    if not eligibility["s5_eligible"]:
        raise ValueError(eligibility["reason"] or "这个爆点 Skill 暂不能用于 S5")


def inspect_templates(
    queue: AnalysisTaskQueue,
    store: ViralSkillStore,
    *,
    include_fixtures: bool = False,
    usage_counts: dict[str, int] | None = None,
) -> dict[str, list[Any]]:
    templates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for skill in store.list_skill_views(status="approved", reuse_mode="reuse"):
        try:
            _require_eligible_skill(skill)
            report, pattern = _report_and_pattern_for_skill(
                queue, skill, include_fixtures=include_fixtures,
            )
        except ValueError as exc:
            blocked.append({
                "skill_id": skill["skill_id"],
                "name": skill["name"],
                "source_status": skill["source_status"],
                "reason_code": skill["eligibility"]["reason_code"],
                "reason": str(exc),
            })
            continue
        templates.append(_template_summary(
            report,
            pattern,
            skill,
            usage_count=(usage_counts or {}).get(skill["skill_id"], 0),
        ))
    return {
        "templates": sorted(templates, key=lambda item: (item["name"], item["template_id"])),
        "blocked": blocked,
    }


def list_templates(
    queue: AnalysisTaskQueue,
    store: ViralSkillStore,
    *,
    include_fixtures: bool = False,
    usage_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    return inspect_templates(
        queue,
        store,
        include_fixtures=include_fixtures,
        usage_counts=usage_counts,
    )["templates"]


def resolve_template(
    queue: AnalysisTaskQueue,
    store: ViralSkillStore,
    template_id: str,
    *,
    include_fixtures: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        skill_view = store.get_skill_view(template_id)
    except SkillNotFoundError as exc:
        raise ValueError("这个爆点 Skill 已不可用，请重新选择") from exc
    _require_eligible_skill(skill_view)
    skill = store.get_skill(template_id)
    report, pattern = _report_and_pattern_for_skill(queue, skill, include_fixtures=include_fixtures)
    return _template_summary(report, pattern, skill_view), report, pattern, skill


def resolve_analysis(queue: AnalysisTaskQueue, analysis_id: str) -> dict[str, Any] | None:
    for task in queue.completed_reports():
        try:
            report = load_report(task.result_path)
        except (OSError, ValueError):
            continue
        if report.get("analysis_id") == analysis_id:
            return report
    return None


def list_script_products(store: ProductStore, *, include_fixtures: bool = False) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for summary in store.list():
        try:
            profile = store.get(summary["product_id"])
        except ProductNotFoundError:
            continue
        if profile.get("fixture_data") and not include_fixtures:
            continue
        eligibility = script_product_eligibility(profile)
        if eligibility.get("eligible") is not True:
            continue
        usable = set(eligibility.get("usable_fact_ids", []))
        fact_values = {
            item.get("id"): item.get("value")
            for item in profile.get("facts", [])
            if isinstance(item, dict) and item.get("id") in usable
        }
        products.append({
            "product_id": profile["product_id"],
            "revision": profile["revision"],
            "sku": profile["sku"],
            "name": profile["name"],
            "status": profile["status"],
            "script_eligible": True,
            "selling_points": [fact_values[item] for item in eligibility["usable_fact_ids"] if fact_values.get(item)],
            "max_duration_ms": profile["shooting_constraints"]["max_duration_ms"],
            "usable_fact_ids": eligibility["usable_fact_ids"],
            "unknown_fields": eligibility["unknown_fields"],
            "eligibility_warnings": eligibility.get("warnings", []),
        })
    return products


def resolve_product(store: ProductStore, product_id: str, *, include_fixtures: bool = False) -> dict[str, Any]:
    try:
        profile = store.get(product_id)
    except ProductNotFoundError as exc:
        raise ValueError("找不到这个商品，请重新选择") from exc
    if profile.get("fixture_data") and not include_fixtures:
        raise ValueError("工程样例不能用于真实脚本")
    eligibility = script_product_eligibility(profile)
    if eligibility.get("eligible") is not True:
        blockers = [str(item) for item in eligibility.get("blockers", []) if str(item).strip()]
        detail = "：" + "；".join(blockers) if blockers else ""
        raise ValueError(f"商品暂不具备脚本生成资格{detail}")
    return profile
