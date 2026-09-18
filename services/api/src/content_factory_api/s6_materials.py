"""Build privacy-safe S6 material profiles from the proven S2 media pipeline."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from content_factory_contracts import validate_or_raise

from .s6_capture import material_capture_role, normalize_material_capture_metadata
from .s6_store import MaterialNotFoundError, now_iso

_MIME_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo", ".webm": "video/webm", ".m4v": "video/x-m4v",
}
def material_upload_role(material_or_archive: Mapping[str, Any]) -> str:
    """Compatibility alias for callers and old stored note prefixes."""

    return material_capture_role(material_or_archive)


def _read_object(path: str | Path, message: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(message) from exc
    if not isinstance(payload, dict):
        raise ValueError(message)
    return payload


def _milliseconds(value: object) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    return round(value * 1000) if value < 10000 else round(value)


def _transcript_segments(media_result: Mapping[str, Any]) -> list[tuple[int, int, str]]:
    path = media_result.get("artifacts", {}).get("transcript_path")
    if not isinstance(path, str) or not path:
        return []
    payload = _read_object(path, "口播转写文件无法读取")
    raw = payload.get("segments", [])
    segments: list[tuple[int, int, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        start = _milliseconds(item.get("start", item.get("start_time")))
        end = _milliseconds(item.get("end", item.get("end_time")))
        text = item.get("text", item.get("transcription", ""))
        if isinstance(start, int) and isinstance(end, int) and isinstance(text, str) and text.strip():
            segments.append((start, end, text.strip()))
    return segments


def _overlapping_transcript(segments: list[tuple[int, int, str]], start_ms: int, end_ms: int) -> str:
    return " ".join(text for start, end, text in segments if start < end_ms and end > start_ms)[:5000]


def _quality(media: Mapping[str, Any], *, has_audio: bool) -> dict[str, int]:
    width = int(media["width"])
    height = int(media["height"])
    pixel_ratio = min(1.0, (width * height) / (720 * 1280))
    clarity = round(55 + pixel_ratio * 40)
    stability = 60
    audio = 70 if has_audio else 0
    exposure = 65
    values = (clarity, stability, audio, exposure)
    return {
        "clarity": clarity, "stability": stability, "audio": audio, "exposure": exposure,
        "overall": round(sum(values) / 4),
    }


def build_material_profile(
    media_result_path: str | Path,
    *,
    product: Mapping[str, Any],
    source_script_id: str | None,
    archive: Mapping[str, Any],
    recognition_configured: bool,
    capture_role: str | None = None,
) -> dict[str, Any]:
    """Convert a validated media result into an editable material asset contract."""

    result = _read_object(media_result_path, "本地媒体处理结果无法读取")
    validate_or_raise("media_result", result)
    if bool(result["fixture_data"]) != bool(product.get("fixture_data")):
        raise ValueError("工程样例和正式商品不能混用")
    material_id = f"material_{uuid4().hex}"
    suffix = Path(result["source"]["original_name"]).suffix.lower()
    if suffix not in _MIME_TYPES:
        raise ValueError("本地处理结果的视频格式不受支持")
    segments = _transcript_segments(result)
    quality = _quality(result["media"], has_audio=bool(result["media"]["has_audio"]))
    upload_role = capture_role or material_upload_role(archive)
    token = material_id.removeprefix("material_")[:24]
    clips: list[dict[str, Any]] = []
    for order, shot in enumerate(result["shots"], start=1):
        clips.append({
            "id": f"clip_{token}_{order:03d}", "source_shot_id": shot["id"], "order": order,
            "start_ms": shot["start_ms"], "end_ms": shot["end_ms"],
            "keyframe_ref": f"keyframe_{token}_{order:03d}",
            "transcript": _overlapping_transcript(segments, shot["start_ms"], shot["end_ms"]),
            "purpose_tags": ["detail"] if upload_role == "detail" else ["broll"],
            "visual_tags": [], "garment_views": ["detail"] if upload_role == "detail" else ["unknown"],
            "action_tags": [], "scene_tags": [archive["scene"]], "shot_size": "待识别",
            "people_count": 0, "standalone_usable": False, "reusable": True,
            "quality": dict(quality), "note": "",
        })
    created = now_iso()
    profile = {
        "schema_version": "1.0.0", "fixture_data": bool(result["fixture_data"]), "revision": 1,
        "material_id": material_id, "product_id": product["product_id"], "product_revision": product["revision"],
        "source_script_id": source_script_id,
        "file": {
            "original_name": result["source"]["original_name"], "mime_type": _MIME_TYPES[suffix],
            "sha256": result["source"]["sha256"], "size_bytes": result["source"]["size_bytes"],
            "duration_ms": result["media"]["duration_ms"], "width": result["media"]["width"],
            "height": result["media"]["height"], "fps": result["media"]["fps"],
            "has_audio": result["media"]["has_audio"],
            "original_ref": f"material_original_{token}", "proxy_ref": f"material_proxy_{token}",
        },
        "archive": {
            "model_name": archive["model_name"], "scene": archive["scene"], "shot_date": archive["shot_date"],
            "batch": archive["batch"], "imported_by": archive["imported_by"], "note": archive.get("note", ""),
        },
        "processing": {
            "media_task_id": result["task_id"], "local_status": "completed", "asr_status": result["asr"]["status"],
            "recognition_status": "pending" if recognition_configured else "not_configured",
            "provider": None, "model": None, "recognized_at": None, "original_video_uploaded": False,
        },
        "clips": clips, "created_at": created, "updated_at": created,
    }
    normalize_material_capture_metadata(profile, explicit_role=capture_role)
    validate_or_raise("material", profile, related={"product": product})
    return profile


def load_media_result_for_material(path: str | Path, *, allowed_root: str | Path) -> dict[str, Any]:
    root = Path(allowed_root).expanduser().resolve()
    result_path = Path(path).expanduser().resolve()
    if not result_path.is_relative_to(root):
        raise ValueError("素材处理结果超出受控目录")
    result = _read_object(result_path, "素材处理结果无法读取")
    validate_or_raise("media_result", result)
    for value in (
        result["source"]["managed_original_path"], result["artifacts"]["proxy_path"],
        *(shot["keyframe_path"] for shot in result["shots"]),
    ):
        candidate = Path(value).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ValueError("素材处理产物不完整或超出受控目录")
    return result


def material_resource(
    profile: Mapping[str, Any], media_result: Mapping[str, Any], *, clip_id: str | None = None,
) -> tuple[Path, str]:
    if clip_id is None:
        return Path(media_result["artifacts"]["proxy_path"]).resolve(), "video/mp4"
    clip = next((item for item in profile["clips"] if item["id"] == clip_id), None)
    if clip is None:
        raise MaterialNotFoundError("找不到这个素材片段")
    shot = next((item for item in media_result["shots"] if item["id"] == clip["source_shot_id"]), None)
    if shot is None:
        raise ValueError("素材片段的关键帧已损坏")
    return Path(shot["keyframe_path"]).resolve(), "image/jpeg"
