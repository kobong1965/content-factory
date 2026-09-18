"""Canonical product-profile construction and S4 business rules."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from content_factory_contracts import compute_product_completeness, product_script_eligibility, validate_or_raise

PRODUCT_VERSION = "1.1.0"
EDITABLE_FIELDS = {
    "sku", "name", "sources", "facts", "selling_point_fact_ids", "forbidden_expressions",
    "unprovable_claims", "brand_boundary_confirmed_by", "brand_boundary_confirmed_at", "shooting_constraints",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def clean_unique(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def blank_shooting_constraints() -> dict[str, Any]:
    """Return the shared fixed-livestream physical defaults for a new product.

    Brand tone and required disclosures remain intentionally empty because they
    are product/brand facts, not global studio capabilities.
    """

    return {
        "models": ["主播 1 人"],
        "locations": ["固定直播间"],
        "equipment": ["固定直播间竖屏机位（不移动）"],
        "lights": ["固定直播间灯光"],
        "daily_available_minutes": 0, "budget_cny": 0, "reusable_assets_allowed": True,
        "brand_tone": "", "required_disclosures": [], "max_duration_ms": 60_000,
        "confirmed_by": None, "confirmed_at": None,
    }


def temporary_sku() -> str:
    return f"TEMP-{datetime.now(UTC):%Y%m%d}-{uuid4().hex[:8].upper()}"


def script_product_eligibility(profile: Mapping[str, Any]) -> dict[str, Any]:
    """API-domain alias for the canonical contract eligibility calculation."""

    return product_script_eligibility(profile)


def new_product(*, sku: str | None = None, name: str) -> dict[str, Any]:
    clean_sku = sku.strip() if isinstance(sku, str) else ""
    clean_sku = clean_sku or temporary_sku()
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("请填写商品名称")
    timestamp = now_iso()
    profile: dict[str, Any] = {
        "schema_version": PRODUCT_VERSION,
        "fixture_data": False,
        "revision": 1,
        "product_id": f"product_{uuid4().hex}",
        "sku": clean_sku,
        "name": clean_name,
        "category": "mens_clothing",
        "status": "draft",
        "sources": [],
        "facts": [],
        "selling_point_fact_ids": [],
        "forbidden_expressions": [],
        "unprovable_claims": [],
        "brand_boundary_confirmed_by": None,
        "brand_boundary_confirmed_at": None,
        "completeness": {},
        "shooting_constraints": blank_shooting_constraints(),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    profile["completeness"] = compute_product_completeness(profile)
    validate_or_raise("product", profile)
    return profile


def _normalize_unmanaged_source(source: Mapping[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(source))
    normalized["label"] = str(normalized.get("label", "")).strip()
    normalized["source_ref"] = str(normalized.get("source_ref", "")).strip()
    if normalized.get("managed") is not False or normalized.get("kind") != "detail_page":
        raise ValueError("新增外部资料只能是详情页链接；本地文件请使用上传按钮")
    parsed = urlparse(normalized["source_ref"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("详情页链接必须是完整的 http 或 https 地址")
    for field in ("asset_id", "mime_type", "sha256", "size_bytes"):
        normalized[field] = None
    return normalized


def apply_product_changes(
    current: Mapping[str, Any], changes: Mapping[str, Any], *, managed_sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"这些商品字段不能修改：{', '.join(sorted(unknown))}")
    updated = copy.deepcopy(dict(current))
    for field, value in changes.items():
        updated[field] = copy.deepcopy(value)

    updated["sku"] = str(updated.get("sku", "")).strip()
    updated["name"] = str(updated.get("name", "")).strip()
    if not updated["sku"] or not updated["name"]:
        raise ValueError("款号和商品名称不能为空")
    updated["forbidden_expressions"] = clean_unique(updated.get("forbidden_expressions"))
    updated["unprovable_claims"] = clean_unique(updated.get("unprovable_claims"))

    raw_sources = updated.get("sources", [])
    if not isinstance(raw_sources, list):
        raise ValueError("资料来源格式不正确")
    normalized_sources: list[dict[str, Any]] = []
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            raise ValueError("资料来源格式不正确")
        source_id = raw.get("id")
        if source_id in managed_sources:
            if dict(raw) != dict(managed_sources[str(source_id)]):
                raise ValueError("已托管文件不能在表单里改写，请重新上传正确文件")
            normalized_sources.append(copy.deepcopy(dict(raw)))
        else:
            normalized_sources.append(_normalize_unmanaged_source(raw))
    retained_managed = {str(item.get("id")) for item in normalized_sources if item.get("managed") is True}
    missing_managed = set(managed_sources) - retained_managed
    if missing_managed:
        raise ValueError("已托管文件不能从表单移除；S4 会保留它和历史版本")
    updated["sources"] = normalized_sources

    raw_facts = updated.get("facts", [])
    if not isinstance(raw_facts, list):
        raise ValueError("商品事实格式不正确")
    normalized_facts: list[dict[str, Any]] = []
    for raw in raw_facts:
        if not isinstance(raw, Mapping):
            raise ValueError("商品事实格式不正确")
        fact = copy.deepcopy(dict(raw))
        for field in ("label", "value", "source_ref", "confirmed_by"):
            fact[field] = str(fact.get(field, "")).strip()
            if not fact[field]:
                raise ValueError(f"商品事实的{field}不能为空")
        unit = fact.get("unit")
        fact["unit"] = unit.strip() if isinstance(unit, str) and unit.strip() else None
        normalized_facts.append(fact)
    updated["facts"] = normalized_facts
    updated["selling_point_fact_ids"] = clean_unique(updated.get("selling_point_fact_ids"))

    shooting = updated.get("shooting_constraints")
    if not isinstance(shooting, dict):
        raise ValueError("拍摄条件格式不正确")
    for field in ("models", "locations", "equipment", "lights", "required_disclosures"):
        shooting[field] = clean_unique(shooting.get(field))
    # Physical production is a workspace-level iron rule.  Preserve product-
    # specific brand/disclosure fields, but never persist ad-hoc camera, crew,
    # location or lighting instructions from an older client.
    shooting["models"] = ["主播 1 人"]
    shooting["locations"] = ["固定直播间"]
    shooting["equipment"] = ["固定直播间竖屏机位（不移动）"]
    shooting["lights"] = ["固定直播间灯光"]
    shooting["reusable_assets_allowed"] = True
    if not isinstance(shooting.get("max_duration_ms"), int) or shooting["max_duration_ms"] < 1_000:
        shooting["max_duration_ms"] = 60_000
    shooting["brand_tone"] = str(shooting.get("brand_tone", "")).strip()
    for field in ("confirmed_by",):
        value = shooting.get(field)
        shooting[field] = value.strip() if isinstance(value, str) and value.strip() else None

    brand_by = updated.get("brand_boundary_confirmed_by")
    updated["brand_boundary_confirmed_by"] = brand_by.strip() if isinstance(brand_by, str) and brand_by.strip() else None
    updated["completeness"] = compute_product_completeness(updated)
    validate_or_raise("product", updated)
    return updated


def activate_product(profile: Mapping[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(dict(profile))
    updated["status"] = "active"
    updated["completeness"] = compute_product_completeness(updated)
    validate_or_raise("product", updated)
    return updated
