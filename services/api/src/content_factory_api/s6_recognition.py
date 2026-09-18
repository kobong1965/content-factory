"""Optional relay recognition for S6 keyframes; original videos never leave the PC."""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from content_factory_contracts import validate_or_raise

from .s3_gateway import GatewayResult, call_gateway
from .s3_settings import GatewayConfig
from .s6_store import now_iso

_PURPOSES = ["hook", "proof", "detail", "comfort", "cta", "transition", "broll"]
_VIEWS = ["front", "side", "back", "detail", "full_body", "unknown"]
_INSTRUCTIONS = """你是男装短视频素材标注助手。只观察给定低清关键帧和片段口播，为已有 clip_id 返回结构化标签。不得创建、删除、合并片段，不得猜测商品参数，不得输出输入之外的 clip_id。原视频未上传。严格返回指定 JSON Schema。"""


def recognition_schema(clip_ids: list[str]) -> dict[str, Any]:
    score = {"type": "integer", "minimum": 0, "maximum": 100}
    tags = {"type": "array", "maxItems": 20, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 80}}
    return {
        "type": "object", "additionalProperties": False, "required": ["clips"],
        "properties": {"clips": {"type": "array", "minItems": len(clip_ids), "maxItems": len(clip_ids), "items": {
            "type": "object", "additionalProperties": False,
            "required": ["clip_id", "purpose_tags", "visual_tags", "garment_views", "action_tags", "scene_tags",
                         "shot_size", "people_count", "standalone_usable", "quality"],
            "properties": {
                "clip_id": {"type": "string", "enum": clip_ids},
                "purpose_tags": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": _PURPOSES}},
                "visual_tags": tags, "garment_views": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": _VIEWS}},
                "action_tags": tags, "scene_tags": tags, "shot_size": {"type": "string", "minLength": 1, "maxLength": 100},
                "people_count": {"type": "integer", "minimum": 0, "maximum": 20}, "standalone_usable": {"type": "boolean"},
                "quality": {"type": "object", "additionalProperties": False,
                            "required": ["clarity", "stability", "audio", "exposure"],
                            "properties": {"clarity": score, "stability": score, "audio": score, "exposure": score}},
            },
        }}},
    }


def _image_data_url(path: Path) -> str:
    if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("素材关键帧不存在或格式错误")
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


GatewayCaller = Callable[..., GatewayResult]


def recognize_material(
    profile: Mapping[str, Any], media_result: Mapping[str, Any], config: GatewayConfig,
    *, gateway_caller: GatewayCaller = call_gateway,
) -> dict[str, Any]:
    """Recognize keyframes in batches of 16 and merge only existing clip IDs."""

    updated = copy.deepcopy(dict(profile))
    clips = updated["clips"]
    shots = {item["id"]: item for item in media_result["shots"]}
    by_id = {item["id"]: item for item in clips}
    for offset in range(0, len(clips), 16):
        batch = clips[offset:offset + 16]
        clip_ids = [item["id"] for item in batch]
        context = {
            "privacy": {"original_video_uploaded": False, "input_kind": "low_resolution_keyframes"},
            "material_id": updated["material_id"], "archive": updated["archive"],
            "clips": [{"clip_id": item["id"], "start_ms": item["start_ms"], "end_ms": item["end_ms"],
                       "transcript": item["transcript"]} for item in batch],
        }
        images = [_image_data_url(Path(shots[item["source_shot_id"]]["keyframe_path"])) for item in batch]
        result = gateway_caller(
            config, context_json=json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            keyframe_data_urls=images, output_schema=recognition_schema(clip_ids),
            developer_instructions=_INSTRUCTIONS, schema_name="content_factory_material_tags",
        )
        returned = result.content.get("clips")
        if not isinstance(returned, list):
            raise ValueError("中转站没有返回素材片段标签")
        returned_ids = [item.get("clip_id") for item in returned if isinstance(item, Mapping)]
        if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(clip_ids):
            raise ValueError("中转站返回的片段编号与本批素材不一致")
        for item in returned:
            target = by_id[item["clip_id"]]
            for field in ("purpose_tags", "visual_tags", "garment_views", "action_tags", "scene_tags", "shot_size",
                          "people_count", "standalone_usable"):
                target[field] = copy.deepcopy(item[field])
            quality = dict(item["quality"])
            quality["overall"] = round(sum(quality.values()) / 4)
            target["quality"] = quality
    updated["revision"] += 1
    updated["updated_at"] = now_iso()
    provider = urlparse(config.base_url).hostname or "configured-relay"
    updated["processing"] = {
        **updated["processing"], "recognition_status": "completed", "provider": provider,
        "model_profile_id": config.model_id, "model": config.model,
        "recognized_at": updated["updated_at"], "original_video_uploaded": False,
    }
    validate_or_raise("material", updated)
    return updated


def mark_recognition_failed(profile: Mapping[str, Any], config: GatewayConfig) -> dict[str, Any]:
    updated = copy.deepcopy(dict(profile))
    updated["revision"] += 1
    updated["updated_at"] = now_iso()
    provider = urlparse(config.base_url).hostname or "configured-relay"
    updated["processing"] = {
        **updated["processing"], "recognition_status": "failed", "provider": provider,
        "model_profile_id": config.model_id, "model": config.model,
        "recognized_at": None, "original_video_uploaded": False,
    }
    validate_or_raise("material", updated)
    return updated
