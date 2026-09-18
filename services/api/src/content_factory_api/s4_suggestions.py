"""Conservative multimodal suggestions for visible product facts.

The model is only allowed to propose what can be seen in the uploaded images.
Nothing returned here is written into the product profile until a human accepts
the suggestion in S4.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .s3_gateway import GatewayResult, call_gateway
from .s3_settings import GatewayConfig

VISIBLE_FACT_FIELDS = ("color", "fit", "feature", "other")
_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_INSTRUCTIONS = """你是男装商品图事实提取助手。只根据上传的当前商品图片提出肉眼可见、可复核的卖点候选，供人工确认后写脚本。

硬规则：
1. 只能描述图片中能直接看见的颜色、廓形/版型表现和设计细节；不推断面料成分、弹力、透气、耐磨、尺码、价格、活动、功效或穿着体感。
2. 不把背景、模特身材或拍摄效果当成商品事实。
3. 每条候选必须引用输入中已有的 source_id，并说明具体可见证据。
4. 不确定就降低 confidence 或不输出，禁止用营销话术替代事实。
5. 严格返回指定 JSON Schema，不输出额外字段。"""


def visible_fact_schema(source_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["suggestions"],
        "properties": {
            "suggestions": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source_id", "field", "label", "value", "evidence", "confidence"],
                    "properties": {
                        "source_id": {"type": "string", "enum": list(source_ids)},
                        "field": {"enum": list(VISIBLE_FACT_FIELDS)},
                        "label": {"type": "string", "minLength": 1, "maxLength": 40},
                        "value": {"type": "string", "minLength": 1, "maxLength": 240},
                        "evidence": {"type": "string", "minLength": 1, "maxLength": 300},
                        "confidence": {"enum": ["high", "medium", "low"]},
                    },
                },
            },
        },
    }


def _image_data_url(path: Path) -> str:
    mime_type = _IMAGE_MIME_TYPES.get(path.suffix.lower())
    if mime_type is None or not path.is_file():
        raise ValueError("商品图片不存在或格式不支持")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("单张商品图片不能超过 20 MB")
    return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


GatewayCaller = Callable[..., GatewayResult]


def suggest_visible_facts(
    profile: Mapping[str, Any],
    image_sources: Sequence[tuple[Mapping[str, Any], Path]],
    config: GatewayConfig,
    *,
    gateway_caller: GatewayCaller = call_gateway,
) -> dict[str, Any]:
    """Return proposals only; callers must never persist them automatically."""

    selected = list(image_sources[:8])
    if not selected:
        raise ValueError("请先上传至少一张商品图片")
    source_ids = [str(source["id"]) for source, _ in selected]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("商品图片来源编号重复")
    context = {
        "product": {
            "product_id": profile.get("product_id"),
            "name": profile.get("name"),
            "sku": profile.get("sku"),
        },
        "images": [
            {"source_id": source["id"], "label": source["label"], "kind": source["kind"]}
            for source, _ in selected
        ],
        "allowed_fields": list(VISIBLE_FACT_FIELDS),
        "unknown_and_forbidden": ["面料成分", "弹力", "透气", "耐磨", "尺码", "价格", "活动", "功效", "体感"],
        "result_is_proposal_only": True,
    }
    result = gateway_caller(
        config,
        context_json=json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        keyframe_data_urls=[_image_data_url(path) for _, path in selected],
        output_schema=visible_fact_schema(source_ids),
        developer_instructions=_INSTRUCTIONS,
        schema_name="content_factory_visible_product_facts",
    )
    returned = result.content.get("suggestions")
    if not isinstance(returned, list):
        raise ValueError("中转站没有返回可见卖点候选")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in returned:
        if not isinstance(raw, Mapping):
            raise ValueError("中转站返回的卖点候选格式不正确")
        source_id = str(raw.get("source_id") or "")
        field = str(raw.get("field") or "")
        label = str(raw.get("label") or "").strip()
        value = str(raw.get("value") or "").strip()
        evidence = str(raw.get("evidence") or "").strip()
        confidence = str(raw.get("confidence") or "")
        if source_id not in source_ids or field not in VISIBLE_FACT_FIELDS:
            raise ValueError("中转站引用了未上传图片或不可由图片确认的字段")
        if confidence not in {"high", "medium", "low"} or not label or not value or not evidence:
            raise ValueError("中转站返回了不完整的卖点候选")
        key = (source_id, field, value.casefold())
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "source_id": source_id,
            "field": field,
            "label": label,
            "value": value,
            "evidence": evidence,
            "confidence": confidence,
            "source_type": "media_evidence",
        })
    return {
        "product_id": profile["product_id"],
        "product_revision": profile["revision"],
        "model_profile_id": config.model_id,
        "model": config.model,
        "suggestions": normalized,
        "saved": False,
        "notice": "AI 结果只是候选；勾选并填写确认人后，才会成为脚本可引用事实。",
    }
