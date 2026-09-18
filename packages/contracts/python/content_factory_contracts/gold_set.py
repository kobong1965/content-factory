"""Honest readiness calculation for the real S1 gold set."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .validation import load_json, validate_document, validate_or_raise


@dataclass(frozen=True)
class GoldSetReadiness:
    stage: str
    schema_version: str
    schema_count: int
    engineering_ready: bool
    business_ready: bool
    accepted_videos: int
    required_videos: int
    accepted_products: int
    required_products: int
    pending_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_document(root: Path, relative_path: Any) -> dict[str, Any] | None:
    if not isinstance(relative_path, str) or not relative_path:
        return None
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        return None
    try:
        return load_json(candidate)
    except (OSError, ValueError):
        return None


def _accepted_products(manifest: dict[str, Any], root: Path) -> dict[str, dict[str, Any]]:
    accepted: dict[str, dict[str, Any]] = {}
    for slot in manifest["product_slots"]:
        if slot["status"] != "accepted":
            continue
        profile = _safe_document(root, slot["profile_file"])
        if profile is None:
            continue
        if profile.get("fixture_data") or profile.get("status") != "active":
            continue
        if profile.get("product_id") != slot["slot_id"] or validate_document("product", profile):
            continue
        accepted[slot["slot_id"]] = profile
    return accepted


def _case_is_complete(
    slot: dict[str, Any],
    root: Path,
    accepted_products: dict[str, dict[str, Any]],
) -> bool:
    case = _safe_document(root, slot["case_file"])
    if case is None or validate_document("gold_case", case):
        return False
    if case.get("video_slot_id") != slot["slot_id"]:
        return False
    product_slot_ids = case.get("product_slot_ids", [])
    if not set(product_slot_ids).issubset(accepted_products):
        return False

    analysis = _safe_document(root, case.get("analysis_file"))
    script = _safe_document(root, case.get("script_file"))
    product_files = case.get("product_files", [])
    products = [_safe_document(root, path) for path in product_files]
    if analysis is None or script is None or not products or any(product is None for product in products):
        return False
    if analysis.get("fixture_data") or script.get("fixture_data"):
        return False
    if validate_document("analysis", analysis):
        return False

    product_by_id: dict[str, dict[str, Any]] = {}
    for product in products:
        assert product is not None
        if product.get("fixture_data") or validate_document("product", product):
            return False
        product_id = product.get("product_id")
        if isinstance(product_id, str):
            product_by_id[product_id] = product
    if set(product_slot_ids) != set(product_by_id):
        return False
    related_product = product_by_id.get(script.get("product_id"))
    if related_product is None:
        return False
    return not validate_document("script", script, related={"analysis": analysis, "product": related_product})


def read_gold_set_readiness(manifest_path: str | Path) -> GoldSetReadiness:
    manifest_file = Path(manifest_path).resolve()
    manifest = load_json(manifest_file)
    validate_or_raise("gold_manifest", manifest)
    root = manifest_file.parent
    accepted_products_by_id = _accepted_products(manifest, root)
    accepted_videos = sum(
        slot["status"] == "accepted" and _case_is_complete(slot, root, accepted_products_by_id)
        for slot in manifest["video_slots"]
    )
    accepted_products = len(accepted_products_by_id)
    required_videos = manifest["required_video_count"]
    required_products = manifest["required_product_count"]
    business_ready = (
        not manifest["fixture_data"]
        and accepted_videos == required_videos
        and accepted_products == required_products
    )
    if business_ready:
        pending_reason = None
    elif manifest["fixture_data"]:
        pending_reason = "工程样例不能计入真实金标准集"
    else:
        pending_reason = (
            f"待补 {required_videos - accepted_videos} 条真实视频和 "
            f"{required_products - accepted_products} 款真实商品"
        )
    return GoldSetReadiness(
        stage="S1",
        schema_version=manifest["schema_version"],
        schema_count=5,
        engineering_ready=True,
        business_ready=business_ready,
        accepted_videos=accepted_videos,
        required_videos=required_videos,
        accepted_products=accepted_products,
        required_products=required_products,
        pending_reason=pending_reason,
    )
