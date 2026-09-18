"""S8 CSV and local OCR parsing. Parsed values remain drafts until a human confirms them."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from content_factory_media.ocr import OcrEngineError, ocr_available


INTEGER_FIELDS = {
    "views", "followers_gained", "likes", "comments", "favorites", "shares",
    "product_clicks", "orders", "gmv_cents",
}
RATIO_FIELDS = {"retention_3s", "completion_rate", "product_ctr", "conversion_rate"}

HEADER_ALIASES = {
    "publication_id": {"publication_id", "发布记录id", "作品记录id"},
    "captured_at": {"captured_at", "采集时间", "截图时间"},
    "views": {"views", "播放量"},
    "followers_gained": {"followers_gained", "增粉", "新增粉丝"},
    "likes": {"likes", "点赞", "点赞量"},
    "comments": {"comments", "评论", "评论量"},
    "favorites": {"favorites", "收藏", "收藏量"},
    "shares": {"shares", "分享", "分享量"},
    "product_clicks": {"product_clicks", "商品点击", "商品点击量"},
    "orders": {"orders", "订单", "成交订单"},
    "gmv_cents": {"gmv_cents", "成交额", "gmv", "成交额(元)", "成交额（元）"},
    "retention_3s": {"retention_3s", "3秒留存", "3秒留存率"},
    "completion_rate": {"completion_rate", "完播率"},
    "product_ctr": {"product_ctr", "商品点击率"},
    "conversion_rate": {"conversion_rate", "转化率", "成交转化率"},
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_headers(fieldnames: list[str] | None) -> dict[str, str]:
    if not fieldnames:
        raise ValueError("CSV 没有表头")
    canonical: dict[str, str] = {}
    for original in fieldnames:
        normalized = original.strip().lower()
        matches = [field for field, aliases in HEADER_ALIASES.items() if normalized in aliases]
        if matches:
            if matches[0] in canonical:
                raise ValueError(f"CSV 表头重复映射到 {matches[0]}")
            canonical[matches[0]] = original
    for required in ("publication_id", "captured_at"):
        if required not in canonical:
            raise ValueError("CSV 必须包含发布记录ID和采集时间")
    if not any(field in canonical for field in INTEGER_FIELDS | RATIO_FIELDS):
        raise ValueError("CSV 至少需要一个指标列")
    return canonical


def _plain_number(value: str) -> float:
    cleaned = value.strip().replace(",", "").replace("，", "")
    multiplier = 1.0
    if cleaned.endswith("万"):
        multiplier, cleaned = 10000.0, cleaned[:-1]
    elif cleaned.endswith("千"):
        multiplier, cleaned = 1000.0, cleaned[:-1]
    if not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        raise ValueError(f"无法识别数字“{value}”")
    return float(cleaned) * multiplier


def parse_metric_value(field: str, value: str, *, header: str = "") -> int | float:
    cleaned = value.strip()
    if field in RATIO_FIELDS:
        if cleaned.endswith("%") or cleaned.endswith("％"):
            ratio = _plain_number(cleaned[:-1]) / 100
        else:
            ratio = _plain_number(cleaned)
            if ratio > 1:
                raise ValueError(f"{header or field} 大于 1 时必须带百分号")
        if not 0 <= ratio <= 1:
            raise ValueError(f"{header or field} 必须在 0%—100%")
        return round(ratio, 6)
    number = _plain_number(cleaned.removeprefix("¥").removeprefix("￥"))
    is_yuan = field == "gmv_cents" and header.strip().lower() != "gmv_cents"
    value_as_int = round(number * 100) if is_yuan else round(number)
    if abs(value_as_int - (number * 100 if is_yuan else number)) > 0.0001:
        raise ValueError(f"{header or field} 必须是整数")
    return value_as_int


def parse_csv_candidates(data: bytes) -> tuple[str, list[dict[str, Any]]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV 必须使用 UTF-8 编码") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = _canonical_headers(reader.fieldnames)
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    for row_number, row in enumerate(reader, start=2):
        try:
            publication_id = (row.get(headers["publication_id"]) or "").strip()
            captured_at = (row.get(headers["captured_at"]) or "").strip()
            if not publication_id or not captured_at:
                raise ValueError("发布记录ID和采集时间不能为空")
            metrics: dict[str, int | float] = {}
            for field in INTEGER_FIELDS | RATIO_FIELDS:
                source_header = headers.get(field)
                raw = (row.get(source_header) or "").strip() if source_header else ""
                if raw:
                    metrics[field] = parse_metric_value(field, raw, header=source_header or field)
            if not metrics:
                raise ValueError("至少填写一个指标")
            candidates.append({
                "candidate_id": f"candidate_{uuid4().hex}", "publication_id": publication_id,
                "captured_at": captured_at, "confidence": "unconfirmed", "metrics": metrics,
                "field_confidence": {field: 1.0 for field in metrics},
            })
        except ValueError as exc:
            errors.append(f"第 {row_number} 行：{exc}")
    if not candidates and not errors:
        errors.append("CSV 没有数据行")
    if errors:
        raise ValueError("CSV 未导入：" + "；".join(errors[:20]))
    return text, candidates


OCR_PATTERNS = {
    "views": r"播放量\s*[:：]?\s*([\d,.，]+(?:\.\d+)?[万千]?)",
    "followers_gained": r"(?:新增粉丝|增粉)\s*[:：]?\s*([\d,.，]+(?:\.\d+)?[万千]?)",
    "likes": r"点赞(?:量)?\s*[:：]?\s*([\d,.，]+(?:\.\d+)?[万千]?)",
    "comments": r"评论(?:量)?\s*[:：]?\s*([\d,.，]+(?:\.\d+)?[万千]?)",
    "favorites": r"收藏(?:量)?\s*[:：]?\s*([\d,.，]+(?:\.\d+)?[万千]?)",
    "shares": r"分享(?:量)?\s*[:：]?\s*([\d,.，]+(?:\.\d+)?[万千]?)",
    "product_clicks": r"商品点击(?:量)?\s*[:：]?\s*([\d,.，]+(?:\.\d+)?[万千]?)",
    "orders": r"(?:成交订单|订单)\s*[:：]?\s*([\d,.，]+(?:\.\d+)?[万千]?)",
    "gmv_cents": r"(?:成交额|GMV)\s*[:：]?\s*[¥￥]?([\d,.，]+(?:\.\d+)?[万千]?)",
    "retention_3s": r"3\s*秒留存(?:率)?\s*[:：]?\s*([\d.]+\s*[％%])",
    "completion_rate": r"完播率\s*[:：]?\s*([\d.]+\s*[％%])",
    "product_ctr": r"商品点击率\s*[:：]?\s*([\d.]+\s*[％%])",
    "conversion_rate": r"(?:成交)?转化率\s*[:：]?\s*([\d.]+\s*[％%])",
}


def metrics_from_ocr_text(text: str, *, confidence: float = 0.5) -> tuple[dict[str, int | float], dict[str, float]]:
    metrics: dict[str, int | float] = {}
    field_confidence: dict[str, float] = {}
    for field, pattern in OCR_PATTERNS.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        header = "成交额" if field == "gmv_cents" else field
        metrics[field] = parse_metric_value(field, match.group(1).replace(" ", ""), header=header)
        field_confidence[field] = round(max(0.0, min(1.0, confidence)), 4)
    if not metrics:
        raise ValueError("截图中没有识别出支持的指标，请人工填写")
    return metrics, field_confidence


def recognize_metric_image(
    image_path: str | Path, *, publication_id: str, captured_at: str,
    engine: Callable[[str], Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    if engine is None:
        if not ocr_available():
            raise OcrEngineError("本地 OCR 不可用，请改用 CSV 或人工填写")
        from rapidocr import RapidOCR
        engine = RapidOCR()
    try:
        result = engine(str(Path(image_path).resolve()))
    except Exception as exc:
        if isinstance(exc, OcrEngineError):
            raise
        raise OcrEngineError(f"截图 OCR 失败：{type(exc).__name__}") from exc
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if texts is None or scores is None or len(texts) != len(scores):
        raise OcrEngineError("OCR 返回的文字和置信度数量不一致")
    cleaned = [str(item).strip() for item in texts if str(item).strip()]
    if not cleaned:
        raise OcrEngineError("截图中没有识别到文字，请人工填写")
    raw_text = " ".join(cleaned)
    confidence = sum(float(item) for item in scores) / max(1, len(scores))
    metrics, field_confidence = metrics_from_ocr_text(raw_text, confidence=confidence)
    return raw_text, [{
        "candidate_id": f"candidate_{uuid4().hex}", "publication_id": publication_id,
        "captured_at": captured_at, "confidence": "unconfirmed", "metrics": metrics,
        "field_confidence": field_confidence,
    }]
