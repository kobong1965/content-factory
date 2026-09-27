"""Semantic caption grouping built on top of real speech word timings.

The speech model is authoritative for *what was said* and for every word's
time range.  The text model is only allowed to choose boundaries between those
already-recognised words.  It cannot rewrite, invent, reorder, or drop spoken
text.  This separation prevents subtitles such as randomly recombined product
claims while still producing natural, meaning-complete Chinese lines.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .s3_gateway import GatewayError, call_gateway


class SemanticCaptionError(ValueError):
    """The semantic boundary model returned no safe, complete segmentation."""


def _schema(word_count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["cues"],
        "properties": {
            "cues": {
                "type": "array",
                "minItems": 1,
                "maxItems": max(1, min(word_count, 200)),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["start_word_index", "end_word_index", "reason", "confidence"],
                    "properties": {
                        "start_word_index": {"type": "integer", "minimum": 0, "maximum": max(0, word_count - 1)},
                        "end_word_index": {"type": "integer", "minimum": 1, "maximum": word_count},
                        "reason": {"type": "string", "minLength": 1, "maxLength": 80},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
        },
    }


def _validate_boundaries(raw: object, word_count: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise SemanticCaptionError("字幕语义模型没有返回分段边界")
    cursor = 0
    boundaries: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise SemanticCaptionError("字幕语义模型返回了无效分段")
        try:
            start = int(item["start_word_index"])
            end = int(item["end_word_index"])
            confidence = float(item.get("confidence", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticCaptionError("字幕语义模型缺少合法字词边界") from exc
        if start != cursor or not (start < end <= word_count):
            raise SemanticCaptionError("字幕语义模型跳过、重复或打乱了口播字词")
        if not 0 <= confidence <= 1:
            raise SemanticCaptionError("字幕语义模型置信度无效")
        boundaries.append({
            "start_word_index": start,
            "end_word_index": end,
            "reason": str(item.get("reason") or "语义完整短句"),
            "confidence": confidence,
        })
        cursor = end
    if cursor != word_count:
        raise SemanticCaptionError("字幕语义模型没有覆盖完整段口播")
    return boundaries


def _cues_from_boundaries(words: list[dict[str, Any]], boundaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    for boundary in boundaries:
        units = [dict(word) for word in words[boundary["start_word_index"]:boundary["end_word_index"]]]
        text = "".join(str(word.get("text") or "") for word in units).strip()
        if not text or not units:
            raise SemanticCaptionError("字幕语义模型产生了空字幕")
        if len(text) > 28:
            raise SemanticCaptionError("字幕语义模型产生了过长短句，不能安全显示")
        start_ms = int(units[0]["start_ms"])
        end_ms = int(units[-1]["end_ms"])
        if not (0 <= start_ms < end_ms):
            raise SemanticCaptionError("字幕字词时间轴无效")
        cues.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "words": units,
            "semantic_reason": boundary["reason"],
            "semantic_confidence": boundary["confidence"],
            "alignment_source": "api_word_timestamps+semantic_boundaries",
        })
    return cues


def group_words_semantically(
    words: list[dict[str, Any]],
    *,
    gateway_config: Any,
    max_chars: int = 14,
) -> list[dict[str, Any]]:
    """Group already timed words into complete meaning units.

    A configured text-capable gateway is required.  When it is missing or
    fails, this function raises instead of silently falling back to arbitrary
    fixed-size grouping.  Callers may explicitly choose the legacy acoustic
    splitter when semantic subtitles are not requested.
    """

    normalized = [dict(word) for word in words if str(word.get("text") or "").strip()]
    if not normalized:
        raise SemanticCaptionError("语音转写没有返回可生成字幕的字词")
    if gateway_config is None:
        raise SemanticCaptionError("未配置字幕语义分析模型，请配置文字模型后再生成字幕")
    indexed = [
        {
            "index": index,
            "text": str(word["text"]),
            "start_ms": int(word["start_ms"]),
            "end_ms": int(word["end_ms"]),
        }
        for index, word in enumerate(normalized)
    ]
    context = {
        "task": "为主播原声字幕选择自然、语义完整的短句边界",
        "rules": [
            "只能输出字词索引边界，不得改写、增补、删减、重排任何口播文字",
            "所有字词必须按原顺序恰好覆盖一次，不能跳词、重复或跨句拼接",
            f"每条字幕尽量 {max_chars} 字以内，绝不超过 28 字；优先在停顿、标点和意思完成处断句",
            "价格、尺码、面料、款号等专有信息必须和相邻解释保持语义完整",
        ],
        "words": indexed,
    }
    instructions = (
        "你是中文口播字幕断句器。你只负责理解语义并选择断句边界，不能修改主播说过的字。"
        "必须返回指定 JSON：cues 中每项是半开区间 [start_word_index, end_word_index)，"
        "按顺序覆盖全部字词。优先在自然停顿、标点和完整意思结束处断句。"
        "如果 ASR 文字听起来像错词，也只能原样保留，不能猜测替换。"
    )
    try:
        result = call_gateway(
            gateway_config,
            context_json=json.dumps(context, ensure_ascii=False),
            keyframe_data_urls=[],
            output_schema=_schema(len(normalized)),
            timeout_seconds=180,
            developer_instructions=instructions,
            schema_name="semantic_caption_boundaries",
        )
    except GatewayError as exc:
        raise SemanticCaptionError(f"字幕语义分析模型不可用：{exc}") from exc
    boundaries = _validate_boundaries(result.content.get("cues"), len(normalized))
    return _cues_from_boundaries(normalized, boundaries)

