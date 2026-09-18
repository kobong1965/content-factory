"""Local keyframe OCR for S3. No remote service or API key is used here."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from content_factory_contracts import validate_or_raise


class OcrEngineError(RuntimeError):
    """Raised when local OCR cannot produce a trustworthy result."""


def ocr_available() -> bool:
    return importlib.util.find_spec("rapidocr") is not None and importlib.util.find_spec("onnxruntime") is not None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _rapidocr_engine() -> Callable[[str], Any]:
    if not ocr_available():
        raise OcrEngineError("本地 OCR 引擎未安装，请先完成 S3 OCR 依赖安装")
    from rapidocr import RapidOCR

    return RapidOCR()


def _box_to_xywh(raw_box: Any) -> list[float]:
    try:
        points = [[float(point[0]), float(point[1])] for point in raw_box]
    except (TypeError, ValueError, IndexError) as exc:
        raise OcrEngineError("OCR 返回了无法解析的文字位置") from exc
    if not points:
        raise OcrEngineError("OCR 返回了空文字位置")
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    left, top = max(0.0, min(x_values)), max(0.0, min(y_values))
    return [round(left, 2), round(top, 2), round(max(x_values) - left, 2), round(max(y_values) - top, 2)]


def _extract_lines(result: Any, frame_number: int) -> list[dict[str, Any]]:
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None and texts is None and scores is None:
        return []
    if boxes is None or texts is None or scores is None or not (len(boxes) == len(texts) == len(scores)):
        raise OcrEngineError("OCR 返回的文字、置信度和位置数量不一致")
    lines: list[dict[str, Any]] = []
    for line_number, (box, text, score) in enumerate(zip(boxes, texts, scores, strict=True), start=1):
        cleaned = str(text).strip()
        if not cleaned:
            continue
        confidence = max(0.0, min(1.0, float(score)))
        lines.append(
            {
                "id": f"ocr_line_f{frame_number:03d}_{line_number:03d}",
                "text": cleaned,
                "confidence": round(confidence, 4),
                "bbox": _box_to_xywh(box),
            }
        )
    return lines


def recognize_keyframes(
    *,
    media_result: Mapping[str, Any],
    destination: str | Path,
    engine: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Recognize every S2 keyframe and persist a contract-validated OCR result."""

    validate_or_raise("media_result", media_result)
    task_id = media_result["task_id"]
    shots = media_result["shots"]
    if not isinstance(shots, Sequence) or not shots:
        raise OcrEngineError("媒体结果没有可识别的关键帧")

    started = time.perf_counter()
    recognize = engine or _rapidocr_engine()
    frames: list[dict[str, Any]] = []
    for frame_number, shot in enumerate(shots, start=1):
        if not isinstance(shot, Mapping):
            raise OcrEngineError("媒体镜头数据格式无效")
        keyframe_path = Path(str(shot["keyframe_path"])).expanduser().resolve()
        if not keyframe_path.is_file():
            raise OcrEngineError(f"找不到关键帧：{keyframe_path.name}")
        try:
            result = recognize(str(keyframe_path))
        except Exception as exc:
            if isinstance(exc, OcrEngineError):
                raise
            raise OcrEngineError(f"关键帧 {frame_number} OCR 失败：{type(exc).__name__}") from exc
        frames.append(
            {
                "shot_id": str(shot["id"]),
                "keyframe_id": f"frame_{task_id.removeprefix('media_')[:12]}_{frame_number:03d}",
                "keyframe_path": str(keyframe_path),
                "lines": _extract_lines(result, frame_number),
            }
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    try:
        version = importlib.metadata.version("rapidocr")
    except importlib.metadata.PackageNotFoundError:
        version = "test-double"
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "fixture_data": bool(media_result["fixture_data"]),
        "ocr_id": f"ocr_{uuid4().hex}",
        "media_task_id": str(task_id),
        "status": "completed",
        "engine": {"name": "rapidocr", "version": version},
        "frames": frames,
        "timings_ms": {"total": max(0, elapsed_ms)},
        "created_at": _now_iso(),
    }
    validate_or_raise("ocr_result", payload)
    output_path = Path(destination).expanduser().resolve()
    _atomic_json(output_path, payload)
    return payload
