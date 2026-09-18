"""S3 analysis orchestration: local OCR, minimal relay input, validation and persistence."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from content_factory_contracts import ContractValidationError, validate_or_raise
from content_factory_contracts.schema_registry import schema_path
from content_factory_media.ocr import recognize_keyframes
from jsonschema import Draft202012Validator, FormatChecker

from .s3_gateway import GatewayError, GatewayResult, call_gateway
from .s3_analysis_method import AnalysisMethodError, validate_method_snapshot
from .s3_observability import process_peak_memory_bytes, write_analysis_event
from .s3_settings import GatewayConfig

if TYPE_CHECKING:
    from .s3_queue import AnalysisSegmentRecord

PROMPT_VERSION = "1.2.0"
_VIRAL_RESEARCH_INSTRUCTIONS = """你是抖音国内男装爆点研究智能体。只依据输入的镜头、关键帧、ASR、OCR、指标和评论，定位什么时候说了什么、做了什么，以及这些内容为什么可能形成停留、理解、互动或转化信号。
每个核心结论和爆点步骤都必须绑定输入中真实存在的证据 ID；步骤描述必须写清对应时间段内的原话或字幕、人物或商品动作及其可能机制。pattern_candidates 中每个候选都是“待人工确认的爆点 Skill 候选”，不能自动等同于可复用 Skill。名称和步骤应脱离具体款号仍能理解，但不得丢失原视频证据。
每个 pattern_candidate 都应填写 reuse_mode：可正向迁移时为 reuse，明确属于失效机制或反例时为 avoid，证据不足时为 uncertain；并从 result_then_visual_proof、question_then_demonstration、pain_point_then_solution、contrast_then_proof、identity_scenario、value_anchor、curiosity_gap、social_proof、direct_product_pitch、verbal_overload_failure、fit_reassurance、other 中选择最贴近的 mechanism_key。不得为了归类牺牲真实性，不确定时使用 other 或 uncertain。
只有输入包含对应经营指标时，才允许讨论内容节点与数据节点的关联；没有平台内部数据时，必须明确这是内容证据支持的推断，禁止声称某句话被算法抓取、一定导致放量或虚构平台规则。不得虚构商品参数、经营数据或评论。严格返回指定 JSON Schema。"""
_VIDEO_REVIEW_INSTRUCTIONS = """你是抖音国内男装短视频审核员。只依据输入的镜头、关键帧、ASR、OCR、指标和评论进行审核。
重点检查：画面与口播及 OCR 字幕是否一致；商品是否被清楚、真实地呈现；是否存在违规、夸大、无法由素材证明的功效或参数；字幕、音频、节奏和剪辑衔接是否影响理解。
每条结论、风险和修改建议都必须绑定输入中真实存在的证据 ID；证据不足时明确标记推断并降低置信度，不得补造商品信息、平台规则或经营数据。仍须严格返回指定 JSON Schema。"""
MAX_KEYFRAMES = 16
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 40 * 1024 * 1024
SEGMENT_MAX_DURATION_MS = 60_000
SEGMENT_MAX_SHOTS = 8
SEGMENT_MAX_KEYFRAMES = 6
SUMMARY_GROUP_SIZE = 6
TIMELINE_NORMALIZATION_POLICY_VERSION = "timeline-boundary-v1"
TIMELINE_MAX_BOUNDARY_DELTA_MS = 3_000
TIMELINE_MAX_TOTAL_DELTA_MS = 5_000
_SEMANTIC_FIELDS: tuple[str, ...] = (
    "summary",
    "shots",
    "timeline",
    "content_structure",
    "emotion_curve",
    "comment_insights",
    "evidence",
    "scores",
    "pattern_candidates",
)
_METRIC_FIELDS = (
    "play_count", "follower_gain", "three_second_retention", "completion_rate", "like_count",
    "comment_count", "favorite_count", "share_count", "product_click_rate", "transaction_count",
    "conversion_rate",
)


class AnalysisProcessingError(RuntimeError):
    """Raised when an S3 report cannot be created without weakening its evidence rules."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        diagnostic_code: str = "analysis_processing_failed",
    ) -> None:
        self.retryable = retryable
        self.diagnostic_code = diagnostic_code
        super().__init__(message)


class TimelineNormalizationError(AnalysisProcessingError):
    """Raised when model timing damage is too large to repair deterministically."""

    def __init__(self, message: str, audit: Mapping[str, Any]) -> None:
        self.audit = dict(audit)
        super().__init__(
            message,
            retryable=True,
            diagnostic_code="model_timeline_invalid",
        )


class AnalysisCancelledError(AnalysisProcessingError):
    def __init__(self) -> None:
        super().__init__("用户已取消分析任务", retryable=False, diagnostic_code="cancelled")


@dataclass(frozen=True)
class AnalysisSegmentSpec:
    segment_index: int
    start_ms: int
    end_ms: int
    shot_ids: tuple[str, ...]
    segment_key: str

    def checkpoint_dict(self) -> dict[str, int | str]:
        return {
            "segment_index": self.segment_index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "segment_key": self.segment_key,
        }


@dataclass(frozen=True)
class AnalysisArtifacts:
    analysis_id: str
    report_path: Path
    ocr_result_path: Path


ProgressCallback = Callable[[str, int], None]
GatewayCaller = Callable[..., GatewayResult]


class SegmentCheckpointStore(Protocol):
    def prepare_segments(self, task_id: str, segments: list[Mapping[str, Any]]) -> list["AnalysisSegmentRecord"]: ...
    def list_segments(self, task_id: str) -> list["AnalysisSegmentRecord"]: ...
    def claim_segment(
        self, task_id: str, *, worker_id: str, task_worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> "AnalysisSegmentRecord | None": ...
    def complete_segment(
        self,
        task_id: str,
        segment_index: int,
        *,
        worker_id: str,
        result_path: str | Path,
        provider_request_id: str | None = None,
        task_worker_id: str | None = None,
    ) -> None: ...
    def fail_segment(
        self,
        task_id: str,
        segment_index: int,
        *,
        worker_id: str,
        error: Exception,
        task_worker_id: str | None = None,
    ) -> None: ...
    def reject_segment_checkpoint(
        self,
        task_id: str,
        segment_index: int,
        *,
        reason: str,
        expected_result_path: str | Path,
        expected_provider_request_id: str | None,
        expected_updated_at: str,
        expected_checkpoint_identity: str,
        diagnostic_code: str,
        task_worker_id: str,
    ) -> "AnalysisSegmentRecord": ...
    def is_cancel_requested(self, task_id: str) -> bool: ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{uuid4().hex}.partial"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def plan_analysis_segments(
    media_result: Mapping[str, Any],
    config: GatewayConfig,
    *,
    purpose: Literal["analysis", "video_review"] = "analysis",
    analysis_method: Mapping[str, str] | None = None,
    max_duration_ms: int = SEGMENT_MAX_DURATION_MS,
    max_shots: int = SEGMENT_MAX_SHOTS,
) -> list[AnalysisSegmentSpec]:
    """Build deterministic, bounded segments without accumulating prior raw context."""

    if max_duration_ms < 1_000 or max_shots < 1:
        raise ValueError("分析片段上限无效")
    duration_ms = int(media_result["media"]["duration_ms"])
    source_hash = str(media_result["source"]["sha256"])
    raw_shots = [item for item in media_result["shots"] if isinstance(item, Mapping)]
    boundaries: list[tuple[int, int, tuple[str, ...]]] = []
    current: list[Mapping[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        boundaries.append((
            int(current[0]["start_ms"]),
            int(current[-1]["end_ms"]),
            tuple(str(item["id"]) for item in current),
        ))
        current.clear()

    for shot in raw_shots:
        start_ms = int(shot["start_ms"])
        end_ms = int(shot["end_ms"])
        if end_ms - start_ms > max_duration_ms:
            flush()
            cursor = start_ms
            while cursor < end_ms:
                upper = min(end_ms, cursor + max_duration_ms)
                boundaries.append((cursor, upper, (str(shot["id"]),)))
                cursor = upper
            continue
        proposed_start = int(current[0]["start_ms"]) if current else start_ms
        if current and (len(current) >= max_shots or end_ms - proposed_start > max_duration_ms):
            flush()
        current.append(shot)
    flush()
    if not boundaries:
        boundaries = [(0, max(1, duration_ms), tuple())]

    specs: list[AnalysisSegmentSpec] = []
    for index, (start_ms, end_ms, shot_ids) in enumerate(boundaries):
        identity = json.dumps(
            {
                "video_hash": source_hash,
                "segment_start": start_ms,
                "segment_end": end_ms,
                "provider": config.provider,
                "model": config.model,
                "api_mode": config.api_mode,
                "prompt_version": PROMPT_VERSION,
                "purpose": purpose,
                **({"analysis_method": analysis_method["instructions_sha256"],
                    "method_prompt_version": analysis_method["prompt_version"]}
                   if purpose == "analysis" and analysis_method else {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        specs.append(AnalysisSegmentSpec(
            segment_index=index,
            start_ms=start_ms,
            end_ms=end_ms,
            shot_ids=shot_ids,
            segment_key=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        ))
    return specs


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _repair_timeline_boundaries(
    candidate: Mapping[str, Any],
    *,
    expected_start_ms: int,
    expected_end_ms: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Snap small model boundary drift while rejecting semantically unsafe damage."""

    normalized = copy.deepcopy(dict(candidate))
    range_duration_ms = expected_end_ms - expected_start_ms
    proportional_limit = max(1, round(range_duration_ms * 0.10))
    max_boundary_delta_ms = min(TIMELINE_MAX_BOUNDARY_DELTA_MS, proportional_limit)
    max_total_delta_ms = min(TIMELINE_MAX_TOTAL_DELTA_MS, proportional_limit)
    audit: dict[str, Any] = {
        "schema_version": "1.0.0",
        "policy_version": TIMELINE_NORMALIZATION_POLICY_VERSION,
        "generated_at": _now_iso(),
        "raw_sha256": _canonical_json_sha256(candidate),
        "expected_start_ms": expected_start_ms,
        "expected_end_ms": expected_end_ms,
        "max_boundary_delta_ms": max_boundary_delta_ms,
        "max_total_delta_ms": max_total_delta_ms,
        "decision": "unchanged",
        "changes": [],
        "derived_changes": [],
        "evidence_changes": [],
        "total_adjusted_ms": 0,
        "reason": None,
    }

    def reject(reason: str) -> None:
        audit["decision"] = "rejected"
        audit["reason"] = reason
        raise TimelineNormalizationError(f"模型时间轴无法安全校正：{reason}", audit)

    if range_duration_ms <= 0:
        reject("预期时间范围无效")
    timeline = normalized.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        reject("timeline 为空或不是数组")

    ids: set[str] = set()
    starts: list[int] = []
    ends: list[int] = []
    for index, raw_item in enumerate(timeline):
        if not isinstance(raw_item, dict):
            reject(f"timeline[{index}] 不是对象")
        item_id = raw_item.get("id")
        if not isinstance(item_id, str) or not item_id or item_id in ids:
            reject(f"timeline[{index}].id 缺失或重复")
        ids.add(item_id)
        start_ms = raw_item.get("start_ms")
        end_ms = raw_item.get("end_ms")
        if type(start_ms) is not int or type(end_ms) is not int:
            reject(f"timeline[{index}] 的时间必须是整数")
        if start_ms < 0 or end_ms < 0 or start_ms >= end_ms:
            reject(f"timeline[{index}] 时间为负数或时长无效")
        if index < len(timeline) - 1 and end_ms > expected_end_ms:
            reject(f"timeline[{index}].end_ms 超出当前分析范围")
        starts.append(start_ms)
        ends.append(end_ms)
        if index and start_ms <= starts[index - 1]:
            reject(f"timeline[{index}].start_ms 未严格递增")
        if index and end_ms <= ends[index - 1]:
            reject(f"timeline[{index}].end_ms 未严格递增")

    proposed_changes: list[dict[str, Any]] = []

    def propose(path: str, before: int, after: int, kind: str) -> None:
        if before == after:
            return
        magnitude = abs(after - before)
        if magnitude > max_boundary_delta_ms:
            reject(f"{path} 偏差 {magnitude}ms 超过单边界安全阈值 {max_boundary_delta_ms}ms")
        proposed_changes.append({
            "path": path,
            "kind": kind,
            "before": before,
            "after": after,
            "delta_ms": after - before,
        })

    propose(
        "timeline[0].start_ms",
        starts[0],
        expected_start_ms,
        "gap" if starts[0] > expected_start_ms else "overlap",
    )
    for index in range(1, len(timeline)):
        expected = ends[index - 1]
        propose(
            f"timeline[{index}].start_ms",
            starts[index],
            expected,
            "gap" if starts[index] > expected else "overlap",
        )
    propose(
        f"timeline[{len(timeline) - 1}].end_ms",
        ends[-1],
        expected_end_ms,
        "tail_gap" if ends[-1] < expected_end_ms else "tail_overflow",
    )

    total_adjusted_ms = sum(abs(int(item["delta_ms"])) for item in proposed_changes)
    if total_adjusted_ms > max_total_delta_ms:
        reject(
            f"累计边界偏差 {total_adjusted_ms}ms 超过安全阈值 {max_total_delta_ms}ms"
        )

    for change in proposed_changes:
        path = str(change["path"])
        index_text, field = path.removeprefix("timeline[").split("]")
        index = int(index_text)
        field_name = field.removeprefix(".")
        timeline[index][field_name] = change["after"]

    for index, item in enumerate(timeline):
        if not expected_start_ms <= item["start_ms"] < item["end_ms"] <= expected_end_ms:
            reject(f"timeline[{index}] 校正后超出当前分析范围或成为空区间")
        expected_second = item["start_ms"] // 1000
        before_second = item.get("second_index")
        if before_second != expected_second:
            audit["derived_changes"].append({
                "path": f"timeline[{index}].second_index",
                "before": before_second,
                "after": expected_second,
            })
            item["second_index"] = expected_second

    repaired_bounds = {
        item["id"]: (item["start_ms"], item["end_ms"])
        for item in timeline
    }
    for index, evidence in enumerate(normalized.get("evidence", [])):
        if not isinstance(evidence, dict) or evidence.get("source_type") != "timeline":
            continue
        bounds = repaired_bounds.get(evidence.get("source_id"))
        evidence_start = evidence.get("start_ms")
        evidence_end = evidence.get("end_ms")
        if (
            bounds is not None
            and type(evidence_start) is int
            and type(evidence_end) is int
            and min(bounds[1], evidence_end) <= max(bounds[0], evidence_start)
        ):
            reject(f"evidence[{index}] 与校正后的 timeline 来源完全不相交")

    audit["changes"] = proposed_changes
    audit["total_adjusted_ms"] = total_adjusted_ms
    if proposed_changes or audit["derived_changes"]:
        audit["decision"] = "repaired"
    return normalized, audit


def _checkpoint_audit_path(checkpoint_path: Path) -> Path:
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.normalization-audit.json")


def _checkpoint_was_rejected(checkpoint_path: Path, content: Mapping[str, Any]) -> bool:
    audit = _load_json_object(_checkpoint_audit_path(checkpoint_path))
    return bool(
        audit
        and audit.get("decision") == "rejected"
        and audit.get("raw_sha256") == _canonical_json_sha256(content)
    )


def _remove_owned_checkpoint(task_dir: Path, checkpoint_path: Path) -> bool:
    """Remove only a checkpoint physically owned by this analysis workspace."""

    resolved_task_dir = task_dir.resolve()
    resolved_checkpoint = checkpoint_path.resolve()
    if not resolved_checkpoint.is_relative_to(resolved_task_dir) or not resolved_checkpoint.is_file():
        return False
    resolved_checkpoint.unlink()
    return True


def _quarantine_malformed_checkpoint(
    task_dir: Path,
    checkpoint_path: Path,
    *,
    phase: str,
    reason: str,
    move_source: bool = True,
) -> Path | None:
    """Atomically retain malformed bytes after the database stops referencing them."""

    resolved_task_dir = task_dir.resolve()
    resolved_checkpoint = checkpoint_path.resolve()
    if not resolved_checkpoint.is_relative_to(resolved_task_dir) or not resolved_checkpoint.is_file():
        return None
    raw = resolved_checkpoint.read_bytes()
    destination_dir = resolved_task_dir / "diagnostics" / "rejected-checkpoints"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"checkpoint-{uuid4().hex[:12]}.invalid"
    if move_source:
        os.replace(resolved_checkpoint, destination)
    else:
        destination.write_bytes(raw)
    _write_json_atomic(destination.with_suffix(".metadata.json"), {
        "schema_version": "1.0.0",
        "rejected_at": _now_iso(),
        "phase": phase,
        "reason": reason,
        "source_path": str(resolved_checkpoint),
        "size_bytes": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
    })
    return destination


def _archive_rejected_candidate(
    task_dir: Path,
    *,
    phase: str,
    content: Mapping[str, Any],
    provider_request_id: str | None,
    audit: Mapping[str, Any],
    source_path: Path | None = None,
) -> Path:
    destination = (
        task_dir
        / "diagnostics"
        / "rejected-checkpoints"
        / f"rejected-{uuid4().hex[:12]}.json"
    )
    _write_json_atomic(destination, {
        "schema_version": "1.0.0",
        "rejected_at": _now_iso(),
        "phase": phase,
        "provider_request_id": provider_request_id,
        "raw_sha256": _canonical_json_sha256(content),
        "source_path": str(source_path) if source_path is not None else None,
        "audit": dict(audit),
        "content": dict(content),
    })
    return destination


def _contract_rejection_audit(
    content: Mapping[str, Any],
    error: ContractValidationError,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "policy_version": "analysis-contract-v1",
        "generated_at": _now_iso(),
        "raw_sha256": _canonical_json_sha256(content),
        "decision": "rejected",
        "reason": "final_contract_invalid",
        "issues": list(error.issues),
    }


def model_output_schema() -> dict[str, Any]:
    schema = json.loads(schema_path("analysis").read_text(encoding="utf-8"))
    properties = {field: copy.deepcopy(schema["properties"][field]) for field in _SEMANTIC_FIELDS}
    pattern_required = properties["pattern_candidates"]["items"]["required"]
    for field in ("mechanism_key", "reuse_mode"):
        if field not in pattern_required:
            pattern_required.append(field)
    return {
        "$schema": schema["$schema"],
        "type": "object",
        "additionalProperties": False,
        "required": list(_SEMANTIC_FIELDS),
        "properties": properties,
        "$defs": copy.deepcopy(schema["$defs"]),
    }


def _load_media_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisProcessingError("无法读取 S2 媒体结果") from exc
    if not isinstance(payload, dict):
        raise AnalysisProcessingError("S2 媒体结果顶层不是对象")
    validate_or_raise("media_result", payload)
    task_directory = path.parent.resolve()
    referenced_paths = [shot["keyframe_path"] for shot in payload["shots"]]
    transcript_path = payload["artifacts"].get("transcript_path")
    if transcript_path:
        referenced_paths.append(transcript_path)
    for raw in referenced_paths:
        candidate = Path(raw).expanduser().resolve()
        if not candidate.is_relative_to(task_directory) or not candidate.is_file():
            raise AnalysisProcessingError("S2 结果引用了任务目录外或不存在的分析素材")
    return payload


def _load_transcript(media_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    path_value = media_result["artifacts"].get("transcript_path")
    if not path_value:
        return []
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisProcessingError("ASR 转写文件无法读取") from exc
    segments = payload.get("segments", []) if isinstance(payload, dict) else []
    duration_ms = int(media_result["media"]["duration_ms"])
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(segments if isinstance(segments, list) else [], start=1):
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        try:
            start_ms = max(0, int(item.get("start", 0)))
            if start_ms >= duration_ms:
                continue
            end_ms = min(duration_ms, max(start_ms + 1, int(item.get("end", start_ms + 1))))
        except (TypeError, ValueError):
            continue
        normalized.append(
            {"id": f"transcript_{index:03d}", "start_ms": start_ms, "end_ms": end_ms, "text": str(item["text"]).strip()}
        )
    return normalized


def _normalize_metrics(raw: Any, *, fallback_captured_at: str) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        captured_at = str(item.get("captured_at") or fallback_captured_at)
        try:
            parsed_captured_at = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"第 {index} 条经营数据的采集时间不是有效日期时间") from exc
        if parsed_captured_at.tzinfo is None:
            raise ValueError(f"第 {index} 条经营数据的采集时间必须包含时区")
        values = item.get("values", {})
        clean_values = {key: values.get(key) for key in _METRIC_FIELDS if isinstance(values, dict) and key in values}
        normalized.append(
            {
                "id": f"metric_input_{index:03d}",
                # The fallback must be stable across a retry of the same task;
                # otherwise the exact summary input hash changes every run.
                "captured_at": captured_at,
                "source_type": str(item.get("source_type") or "manual"),
                "confidence": float(item.get("confidence", 1.0)),
                "values": clean_values,
            }
        )
    return normalized


def _normalize_comments(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        like_count = item.get("like_count")
        normalized.append(
            {
                "id": f"comment_input_{index:03d}",
                "text": str(item["text"]).strip(),
                "source_label": str(item.get("source_label") or "人工录入"),
                "like_count": int(like_count) if isinstance(like_count, int) and like_count >= 0 else None,
                "is_author_reply": bool(item.get("is_author_reply", False)),
                "confidence": float(item.get("confidence", 1.0)),
            }
        )
    return normalized


def _sample_indices(count: int, maximum: int) -> list[int]:
    if count <= maximum:
        return list(range(count))
    return sorted({round(index * (count - 1) / (maximum - 1)) for index in range(maximum)})


def _encode_keyframes(
    ocr_result: Mapping[str, Any],
    *,
    shot_ids: set[str] | None = None,
    maximum: int = MAX_KEYFRAMES,
) -> tuple[list[str], list[str], int]:
    frames = [
        frame for frame in ocr_result["frames"]
        if shot_ids is None or frame["shot_id"] in shot_ids
    ]
    selected = _sample_indices(len(frames), maximum)
    urls: list[str] = []
    frame_ids: list[str] = []
    total = 0
    for index in selected:
        frame = frames[index]
        path = Path(frame["keyframe_path"])
        data = path.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            raise AnalysisProcessingError(f"关键帧 {path.name} 超过 5 MB，拒绝上传")
        total += len(data)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise AnalysisProcessingError("关键帧合计超过 40 MB，拒绝上传")
        urls.append(f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}")
        frame_ids.append(frame["keyframe_id"])
    return urls, frame_ids, total


def _build_context(
    media_result: Mapping[str, Any],
    ocr_result: Mapping[str, Any],
    metrics: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    image_frame_ids: list[str],
    segment: AnalysisSegmentSpec | None = None,
) -> str:
    transcript = _load_transcript(media_result)
    if segment is not None:
        transcript = [
            item for item in transcript
            if item["end_ms"] > segment.start_ms and item["start_ms"] < segment.end_ms
        ]
    ocr_by_shot = {frame["shot_id"]: frame for frame in ocr_result["frames"]}
    shots = []
    for shot in media_result["shots"]:
        if segment is not None and shot["id"] not in segment.shot_ids:
            continue
        frame = ocr_by_shot[shot["id"]]
        start_ms = max(int(shot["start_ms"]), segment.start_ms) if segment else int(shot["start_ms"])
        end_ms = min(int(shot["end_ms"]), segment.end_ms) if segment else int(shot["end_ms"])
        shots.append(
            {
                "id": shot["id"],
                "start_ms": start_ms,
                "end_ms": end_ms,
                "scene_score": shot["scene_score"],
                "keyframe_id": frame["keyframe_id"],
                "ocr_lines": [
                    {"id": line["id"], "text": line["text"], "confidence": line["confidence"], "bbox": line["bbox"]}
                    for line in frame["lines"]
                ],
            }
        )
    context = {
        "task": (
            "生成当前有界视频片段的完整、可追溯语义分析。shots 只覆盖当前片段输入，"
            "timeline 在 segment_start_ms 与 segment_end_ms 之间连续。不得推断未提供的其他片段。"
            if segment else
            "生成完整、可追溯的抖音男装短视频语义分析。shots 必须覆盖全部输入镜头，timeline 必须连续覆盖整个时长。"
        ),
        "fixed_ids": "只能引用输入中已有的 shot、keyframe、metric、comment ID；口播证据 source_id 使用它所在的 shot ID；新建的 evidence、timeline、structure、emotion、pattern ID 必须符合小写字母开头的格式。",
        "duration_ms": media_result["media"]["duration_ms"],
        "segment_index": segment.segment_index if segment else None,
        "segment_start_ms": segment.start_ms if segment else 0,
        "segment_end_ms": segment.end_ms if segment else media_result["media"]["duration_ms"],
        "shots": shots,
        "transcript_segments": transcript,
        "metric_snapshots": metrics if segment is None else [],
        "comments": comments if segment is None else [],
        "attached_image_frame_ids_in_order": image_frame_ids,
        "missing_data_rule": "指标或评论为空时不得虚构；相关评分必须降低置信度并在风险中说明。",
        "output_rules": [
            "所有时间必须在0到duration_ms之间，并且画面、口播、关键帧证据时间必须落在其来源镜头范围内。",
            "ASR 时间可能重叠或留有静音空档，只能作为证据时间，不得直接复制成 timeline 分段边界。",
            "timeline[0].start_ms必须等于segment_start_ms；后续每段start_ms必须严格等于前一段end_ms；最后一段end_ms必须等于segment_end_ms。",
            "每段timeline.second_index必须等于floor(start_ms/1000)。",
            "evidence_ids只能引用同一输出evidence数组中真实存在的ID，不得按ASR序号臆造证据ID。",
            "七个评分维度各出现一次，weight之和必须精确为1，total_score等于四舍五入后的加权分。",
            "关键帧证据必须填写其所在镜头的start_ms和end_ms。",
        ],
    }
    return json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_model_output(
    candidate: Mapping[str, Any],
    *,
    media_result: Mapping[str, Any],
    ocr_result: Mapping[str, Any],
    metrics: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    timeline_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Repair deterministic relay inconsistencies without inventing evidence."""

    duration_ms = int(media_result["media"]["duration_ms"])
    normalized, audit = _repair_timeline_boundaries(
        candidate,
        expected_start_ms=0,
        expected_end_ms=duration_ms,
    )
    if timeline_audit is not None:
        timeline_audit.clear()
        timeline_audit.update(audit)
    shot_bounds = {
        item["id"]: (int(item["start_ms"]), int(item["end_ms"]))
        for item in media_result["shots"]
    }
    frame_to_shot = {
        item["keyframe_id"]: item["shot_id"]
        for item in ocr_result["frames"]
        if item.get("shot_id") in shot_bounds
    }
    timeline_bounds = {
        item["id"]: (int(item["start_ms"]), int(item["end_ms"]))
        for item in normalized.get("timeline", [])
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("start_ms"), int)
        and isinstance(item.get("end_ms"), int)
    }
    untimed_sources = {
        "metric": {item["id"] for item in metrics},
        "comment": {item["id"] for item in comments},
    }

    def source_bounds(item: Mapping[str, Any]) -> tuple[int, int] | None:
        source_type = item.get("source_type")
        source_id = item.get("source_id")
        if not isinstance(source_id, str):
            return None
        if source_type in {"shot", "transcript"}:
            return shot_bounds.get(source_id)
        if source_type == "keyframe":
            shot_id = frame_to_shot.get(source_id)
            return shot_bounds.get(shot_id) if shot_id else None
        if source_type == "timeline":
            return timeline_bounds.get(source_id)
        return None

    evidence: list[dict[str, Any]] = []
    seen_evidence_ids: set[str] = set()
    for evidence_index, raw_item in enumerate(normalized.get("evidence", [])):
        if not isinstance(raw_item, dict) or not isinstance(raw_item.get("id"), str):
            continue
        item = raw_item
        evidence_id = item["id"]
        if evidence_id in seen_evidence_ids:
            continue
        source_type = item.get("source_type")
        bounds = source_bounds(item)
        if source_type in {"shot", "timeline", "transcript", "keyframe"}:
            if bounds is None:
                continue
            lower, upper = bounds
            raw_start = item.get("start_ms")
            raw_end = item.get("end_ms")
            start_ms = max(lower, min(int(raw_start), upper - 1)) if isinstance(raw_start, int) else lower
            end_ms = min(upper, max(int(raw_end), start_ms + 1)) if isinstance(raw_end, int) else upper
            item["start_ms"] = max(0, min(start_ms, duration_ms - 1))
            item["end_ms"] = max(item["start_ms"] + 1, min(end_ms, duration_ms))
            if source_type == "timeline" and (
                raw_start != item["start_ms"] or raw_end != item["end_ms"]
            ):
                audit["evidence_changes"].append({
                    "path": f"evidence[{evidence_index}]",
                    "source_id": item.get("source_id"),
                    "before": {"start_ms": raw_start, "end_ms": raw_end},
                    "after": {"start_ms": item["start_ms"], "end_ms": item["end_ms"]},
                })
        elif source_type in untimed_sources:
            if item.get("source_id") not in untimed_sources[source_type]:
                continue
            item["start_ms"] = None
            item["end_ms"] = None
        else:
            continue
        seen_evidence_ids.add(evidence_id)
        evidence.append(item)
    normalized["evidence"] = evidence

    known_ids = {item["id"] for item in evidence}
    fallback_id = evidence[0]["id"] if evidence else None

    def repaired_references(owner: Mapping[str, Any], values: Any) -> list[str]:
        repaired: list[str] = []
        for value in values if isinstance(values, list) else []:
            if isinstance(value, str) and value in known_ids and value not in repaired:
                repaired.append(value)
        if repaired or fallback_id is None:
            return repaired
        start_ms = owner.get("start_ms")
        end_ms = owner.get("end_ms")
        if isinstance(start_ms, int) and isinstance(end_ms, int):
            overlapping = []
            for item in evidence:
                evidence_start = item.get("start_ms")
                evidence_end = item.get("end_ms")
                if not isinstance(evidence_start, int) or not isinstance(evidence_end, int):
                    continue
                overlap = min(end_ms, evidence_end) - max(start_ms, evidence_start)
                if overlap > 0:
                    overlapping.append((overlap, item["id"]))
            if overlapping:
                overlapping.sort(reverse=True)
                return [evidence_id for _overlap, evidence_id in overlapping[:3]]
        return [fallback_id]

    def repair_nested(value: Any) -> None:
        if isinstance(value, dict):
            if "evidence_ids" in value:
                value["evidence_ids"] = repaired_references(value, value["evidence_ids"])
            for nested in value.values():
                repair_nested(nested)
        elif isinstance(value, list):
            for nested in value:
                repair_nested(nested)

    repair_nested(normalized)

    scores = normalized.get("scores")
    score_items = scores.get("items") if isinstance(scores, dict) else None
    if isinstance(score_items, list) and score_items:
        weights = [float(item.get("weight", 0)) for item in score_items if isinstance(item, dict)]
        if len(weights) == len(score_items):
            total_weight = sum(weights)
            if total_weight <= 0 or any(weight <= 0 for weight in weights):
                weights = [1.0] * len(score_items)
                total_weight = float(len(score_items))
            normalized_weights = [round(weight / total_weight, 6) for weight in weights]
            normalized_weights[-1] = round(1 - sum(normalized_weights[:-1]), 6)
            for item, weight in zip(score_items, normalized_weights, strict=True):
                item["weight"] = weight
            scores["total_score"] = round(
                sum(float(item["weight"]) * float(item["score"]) for item in score_items)
            )
    return normalized


def _controlled_keyframes(ocr_result: Mapping[str, Any], media_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    shot_map = {shot["id"]: shot for shot in media_result["shots"]}
    return [
        {
            "id": frame["keyframe_id"],
            "shot_id": frame["shot_id"],
            "timestamp_ms": (shot_map[frame["shot_id"]]["start_ms"] + shot_map[frame["shot_id"]]["end_ms"]) // 2,
            "local_path": frame["keyframe_path"],
        }
        for frame in ocr_result["frames"]
    ]


def _gateway_arguments(
    *,
    context_json: str,
    keyframe_data_urls: list[str],
    purpose: Literal["analysis", "video_review"],
    schema_name: str,
    analysis_method: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "context_json": context_json,
        "keyframe_data_urls": keyframe_data_urls,
        "output_schema": model_output_schema(),
    }
    if purpose == "video_review":
        arguments["developer_instructions"] = _VIDEO_REVIEW_INSTRUCTIONS
    else:
        arguments["developer_instructions"] = _VIRAL_RESEARCH_INSTRUCTIONS
        if analysis_method is not None:
            arguments["developer_instructions"] += (
                "\n\nHuashu 七维方法参考：\n" + analysis_method["upstream_prompt"]
                + "\n\n" + analysis_method["adapter_instructions"]
            )
    return arguments


def _require_semantic_fields(candidate: Mapping[str, Any]) -> None:
    missing = [field for field in _SEMANTIC_FIELDS if field not in candidate]
    if missing:
        raise AnalysisProcessingError(
            f"模型输出缺少字段：{', '.join(missing)}",
            retryable=True,
            diagnostic_code="model_output_invalid",
        )


def _validate_model_candidate_schema(candidate: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(model_output_schema()).iter_errors(candidate),
        key=lambda item: list(item.absolute_path),
    )
    if not errors:
        return
    issues: list[str] = []
    for error in errors[:5]:
        path = "$"
        for part in error.absolute_path:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        issues.append(f"{path}: {error.message}")
    if len(errors) > len(issues):
        issues.append(f"另有 {len(errors) - len(issues)} 项")
    raise AnalysisProcessingError(
        "模型输出结构无效：" + "；".join(issues),
        retryable=True,
        diagnostic_code="model_output_invalid",
    )


def _validate_controlled_analysis_inputs(
    *,
    metrics: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> None:
    schema = json.loads(schema_path("analysis").read_text(encoding="utf-8"))
    issues: list[str] = []
    for field, value in (("metric_snapshots", metrics), ("comments", comments)):
        field_schema = copy.deepcopy(schema["properties"][field])
        field_schema["$schema"] = schema["$schema"]
        field_schema["$defs"] = copy.deepcopy(schema["$defs"])
        validator = Draft202012Validator(field_schema, format_checker=FormatChecker())
        for error in sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path)):
            path = f"$.{field}"
            for part in error.absolute_path:
                path += f"[{part}]" if isinstance(part, int) else f".{part}"
            issues.append(f"{path}: {error.message}")
    if issues:
        raise AnalysisProcessingError(
            "分析输入无效：" + "；".join(issues[:8]),
            retryable=False,
            diagnostic_code="invalid_analysis_input",
        )


def _segment_checkpoint_path(task_dir: Path, segment_index: int) -> Path:
    """Legacy/non-queued checkpoint path retained for backward compatibility."""

    return task_dir / "segments" / f"segment-{segment_index:04d}.json"


def _segment_attempt_checkpoint_path(
    task_dir: Path,
    segment_index: int,
    attempt_count: int,
    worker_id: str,
) -> Path:
    """Return an immutable path owned by one lease generation."""

    worker_token = hashlib.sha256(worker_id.encode("utf-8")).hexdigest()[:12]
    return (
        task_dir
        / "segments"
        / f"segment-{segment_index:04d}-attempt-{attempt_count:04d}-{worker_token}.json"
    )


def _orphan_segment_checkpoint_paths(task_dir: Path, segment_index: int) -> list[Path]:
    segment_dir = task_dir / "segments"
    candidates = [
        item
        for item in segment_dir.glob(f"segment-{segment_index:04d}-attempt-*.json")
        if not item.name.endswith(".normalization-audit.json")
    ]
    legacy = _segment_checkpoint_path(task_dir, segment_index)
    if legacy.is_file():
        candidates.append(legacy)
    return sorted(
        candidates,
        key=lambda item: item.stat().st_mtime_ns if item.is_file() else 0,
        reverse=True,
    )


def _read_segment_checkpoint(path: Path, expected_key: str) -> tuple[dict[str, Any], str | None] | None:
    payload = _load_json_object(path)
    if not payload or payload.get("segment_key") != expected_key:
        return None
    content = payload.get("content")
    if not isinstance(content, dict):
        return None
    try:
        _require_semantic_fields(content)
        _validate_model_candidate_schema(content)
    except AnalysisProcessingError:
        return None
    response_id = payload.get("provider_request_id")
    return content, response_id if isinstance(response_id, str) else None


def _write_segment_checkpoint(
    path: Path,
    spec: AnalysisSegmentSpec,
    result: GatewayResult,
) -> None:
    _write_json_atomic(path, {
        "schema_version": "1.0.0",
        "segment_key": spec.segment_key,
        "segment_index": spec.segment_index,
        "start_ms": spec.start_ms,
        "end_ms": spec.end_ms,
        "provider_request_id": result.response_id,
        "provider_status": result.provider_status,
        "finish_reason": result.finish_reason,
        "completed_event_received": result.completed_event_received,
        "content": result.content,
        "completed_at": _now_iso(),
    })


def _summary_context(
    *,
    media_result: Mapping[str, Any],
    metrics: list[dict[str, Any]],
    comments: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    level: Literal["chapter", "final"],
    group_index: int = 0,
    expected_start_ms: int = 0,
    expected_end_ms: int | None = None,
) -> str:
    full_duration_ms = int(media_result["media"]["duration_ms"])
    range_end_ms = full_duration_ms if expected_end_ms is None else expected_end_ms
    shots = [
        {
            "id": item["id"],
            "start_ms": item["start_ms"],
            "end_ms": item["end_ms"],
        }
        for item in media_result["shots"]
        if int(item["end_ms"]) > expected_start_ms and int(item["start_ms"]) < range_end_ms
    ]
    context = {
        "task": (
            "合并这组已完成的片段结构化分析，形成一份章节级结构化分析。"
            if level == "chapter" else
            "只基于已完成的片段或章节结构化结果，生成全片最终可追溯分析。"
            "保留原始 shot/keyframe/metric/comment 证据 ID；合并重叠结论，不得创造原输入中没有的商品事实。"
        ),
        "summary_level": level,
        "group_index": group_index,
        "duration_ms": full_duration_ms,
        "summary_range": {
            "start_ms": expected_start_ms,
            "end_ms": range_end_ms,
        },
        "all_shots": shots,
        "metric_snapshots": metrics,
        "comments": comments,
        "structured_inputs": [
            {field: candidate[field] for field in _SEMANTIC_FIELDS}
            for candidate in candidates
        ],
        "output_rules": [
            "shots 必须按时间顺序去重，全片最终结果覆盖 all_shots。",
            "ASR 时间可能重叠或留有静音空档，只能作为证据时间，不得直接复制成 timeline 分段边界。",
            f"timeline[0].start_ms必须等于{expected_start_ms}；后续每段start_ms必须严格等于前一段end_ms；最后一段end_ms必须等于{range_end_ms}。",
            "每段timeline.second_index必须等于floor(start_ms/1000)。",
            "仅引用 structured_inputs 中能回溯到原始输入的证据。",
        ],
    }
    return _canonical_json(context)


def _summary_checkpoint_key(
    *,
    level: Literal["chapter", "final"],
    input_keys: list[str],
    context_json: str,
) -> str:
    normalized_context = _canonical_json(json.loads(context_json))
    identity = f"{level}:{':'.join(input_keys)}:{normalized_context}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _summarize_with_checkpoint(
    *,
    checkpoint_path: Path,
    checkpoint_key: str,
    context_json: str,
    expected_start_ms: int,
    expected_end_ms: int,
    config: GatewayConfig,
    purpose: Literal["analysis", "video_review"],
    gateway_caller: GatewayCaller,
    schema_name: str,
    analysis_method: Mapping[str, str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[GatewayResult, dict[str, Any]]:
    if cancel_check is not None and cancel_check():
        raise AnalysisCancelledError()
    cached = _read_segment_checkpoint(checkpoint_path, checkpoint_key)
    if cached is None and checkpoint_path.is_file():
        _quarantine_malformed_checkpoint(
            checkpoint_path.parent.parent,
            checkpoint_path,
            phase=schema_name,
            reason="汇总检查点已损坏、结构无效或输入身份不匹配",
        )
    if cached is not None and _checkpoint_was_rejected(checkpoint_path, cached[0]):
        _remove_owned_checkpoint(checkpoint_path.parent.parent, checkpoint_path)
        cached = None
    if cached is not None:
        raw_content, response_id = cached
        try:
            prepared_content, audit = _repair_timeline_boundaries(
                raw_content,
                expected_start_ms=expected_start_ms,
                expected_end_ms=expected_end_ms,
            )
        except TimelineNormalizationError as exc:
            _write_json_atomic(_checkpoint_audit_path(checkpoint_path), exc.audit)
            _archive_rejected_candidate(
                checkpoint_path.parent.parent,
                phase=schema_name,
                content=raw_content,
                provider_request_id=response_id,
                audit=exc.audit,
                source_path=checkpoint_path,
            )
            if checkpoint_path.is_file():
                checkpoint_path.unlink()
            raise
        _write_json_atomic(_checkpoint_audit_path(checkpoint_path), audit)
        cached_result = GatewayResult(raw_content, response_id, checkpoint_path.stat().st_size)
        return replace(cached_result, content=prepared_content), raw_content
    result = gateway_caller(config, **_gateway_arguments(
        context_json=context_json,
        keyframe_data_urls=[],
        purpose=purpose,
        schema_name=schema_name,
        analysis_method=analysis_method,
    ))
    if cancel_check is not None and cancel_check():
        raise AnalysisCancelledError()
    _require_semantic_fields(result.content)
    _validate_model_candidate_schema(result.content)
    try:
        prepared_content, audit = _repair_timeline_boundaries(
            result.content,
            expected_start_ms=expected_start_ms,
            expected_end_ms=expected_end_ms,
        )
    except TimelineNormalizationError as exc:
        _write_json_atomic(_checkpoint_audit_path(checkpoint_path), exc.audit)
        _archive_rejected_candidate(
            checkpoint_path.parent.parent,
            phase=schema_name,
            content=result.content,
            provider_request_id=result.response_id,
            audit=exc.audit,
        )
        raise
    _write_json_atomic(_checkpoint_audit_path(checkpoint_path), audit)
    synthetic = AnalysisSegmentSpec(
        0,
        expected_start_ms,
        expected_end_ms,
        tuple(),
        checkpoint_key,
    )
    _write_segment_checkpoint(checkpoint_path, synthetic, result)
    return replace(result, content=prepared_content), result.content


def process_analysis(
    *,
    media_result_path: str | Path,
    input_payload: Mapping[str, Any],
    task_directory: str | Path,
    config: GatewayConfig,
    purpose: Literal["analysis", "video_review"] = "analysis",
    progress: ProgressCallback | None = None,
    gateway_caller: GatewayCaller = call_gateway,
    ocr_engine: Callable[[str], Any] | None = None,
    task_id: str | None = None,
    task_worker_id: str | None = None,
    trace_id: str | None = None,
    checkpoint_store: SegmentCheckpointStore | None = None,
    segment_max_duration_ms: int = SEGMENT_MAX_DURATION_MS,
    segment_max_shots: int = SEGMENT_MAX_SHOTS,
) -> AnalysisArtifacts:
    if purpose not in {"analysis", "video_review"}:
        raise AnalysisProcessingError("不支持的分析任务用途")
    try:
        analysis_method = validate_method_snapshot(input_payload.get("_analysis_method_snapshot")) if purpose == "analysis" else None
    except AnalysisMethodError as exc:
        raise AnalysisProcessingError(str(exc), diagnostic_code="analysis_method_invalid") from exc
    report_started = time.perf_counter()
    task_dir = Path(task_directory).expanduser().resolve()
    if checkpoint_store is not None and (task_id is None or not task_worker_id):
        raise AnalysisProcessingError("持久化分析缺少任务或租约标识")
    task_dir.mkdir(parents=True, exist_ok=True)
    if analysis_method is not None:
        _write_json_atomic(task_dir / "analysis-method.json", {
            key: value for key, value in analysis_method.items()
            if key not in {"upstream_prompt", "adapter_instructions"}
        })
    notify = progress or (lambda _step, _value: None)

    media_path = Path(media_result_path).expanduser().resolve()
    media_result = _load_media_result(media_path)
    job_id = task_id or f"analysis_task_{uuid4().hex}"
    effective_trace_id = trace_id or f"trace_{uuid4().hex}"
    video_hash = str(media_result["source"]["sha256"])
    provider_label = urlparse(config.base_url).hostname or "configured-relay"

    def log_event(event: str, phase: str, **values: Any) -> None:
        try:
            disk_free = shutil.disk_usage(task_dir).free
        except OSError:
            disk_free = None
        write_analysis_event(
            task_dir,
            event=event,
            job_id=job_id,
            trace_id=effective_trace_id,
            video_hash=video_hash,
            phase=phase,
            provider=provider_label,
            model=config.model,
            process_peak_memory_bytes=process_peak_memory_bytes(),
            temporary_disk_free_bytes=disk_free,
            **values,
        )

    def ensure_active(phase: str) -> None:
        if checkpoint_store is not None and task_id is not None and checkpoint_store.is_cancel_requested(task_id):
            log_event("job_cancelled", phase, completed_at=_now_iso())
            raise AnalysisCancelledError()

    log_event("job_started", "prepare")
    notify("ocr", 12)
    ocr_path = task_dir / "ocr-result.json"
    ocr_result = _load_json_object(ocr_path)
    try:
        if ocr_result is None:
            raise ValueError("missing")
        validate_or_raise("ocr_result", ocr_result)
        if ocr_result.get("media_task_id") != media_result.get("task_id"):
            raise ValueError("mismatch")
    except (ValueError, TypeError, ContractValidationError):
        ocr_result = recognize_keyframes(media_result=media_result, destination=ocr_path, engine=ocr_engine)
    log_event("ocr_checkpoint_ready", "ocr")
    ensure_active("ocr")

    notify("prepare", 35)
    try:
        metrics = _normalize_metrics(
            input_payload.get("metric_snapshots"),
            fallback_captured_at=str(media_result["created_at"]),
        )
        comments = _normalize_comments(input_payload.get("comments"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnalysisProcessingError(
            f"分析输入无法规范化：{exc}",
            retryable=False,
            diagnostic_code="invalid_analysis_input",
        ) from exc
    _validate_controlled_analysis_inputs(metrics=metrics, comments=comments)
    segments = plan_analysis_segments(
        media_result,
        config,
        purpose=purpose,
        max_duration_ms=segment_max_duration_ms,
        max_shots=segment_max_shots,
        analysis_method=analysis_method,
    )
    if checkpoint_store is not None:
        if task_id is None:
            raise AnalysisProcessingError("持久化分段处理缺少 task_id")
        checkpoint_store.prepare_segments(task_id, [segment.checkpoint_dict() for segment in segments])

    segment_outputs: list[dict[str, Any]] = []
    segment_raw_outputs: list[dict[str, Any]] = []
    segment_response_ids: list[str] = []
    uploaded_keyframes = 0
    uploaded_image_bytes = 0
    uploaded_text_characters = 0
    uploaded_text_bytes = 0
    worker_id = f"segment-worker-{uuid4().hex}"

    for spec in segments:
        ensure_active("segment")
        checkpoint_path = _segment_checkpoint_path(task_dir, spec.segment_index)
        cached: tuple[dict[str, Any], str | None] | None = None
        raw_content: dict[str, Any] | None = None
        cached_source_path: Path | None = None
        adopt_orphan_checkpoint = False
        persisted = None
        claimed = None
        if checkpoint_store is not None and task_id is not None:
            records = {item.segment_index: item for item in checkpoint_store.list_segments(task_id)}
            persisted = records.get(spec.segment_index)
            if persisted is not None and persisted.status == "succeeded" and persisted.result_path:
                cached_source_path = Path(persisted.result_path)
                source_is_owned = cached_source_path.resolve().is_relative_to(task_dir.resolve())
                cached = (
                    _read_segment_checkpoint(cached_source_path, spec.segment_key)
                    if source_is_owned
                    else None
                )
                if cached is None:
                    reason = f"第 {spec.segment_index + 1} 段检查点文件丢失、已损坏或身份不匹配"
                    _quarantine_malformed_checkpoint(
                        task_dir,
                        cached_source_path,
                        phase=f"segment-{spec.segment_index:04d}",
                        reason=reason,
                        move_source=False,
                    )
                    rejected = checkpoint_store.reject_segment_checkpoint(
                        task_id,
                        spec.segment_index,
                        reason=reason,
                        expected_result_path=cached_source_path,
                        expected_provider_request_id=persisted.provider_request_id,
                        expected_updated_at=persisted.updated_at,
                        expected_checkpoint_identity=persisted.checkpoint_identity,
                        diagnostic_code="checkpoint_corrupt",
                        task_worker_id=task_worker_id,
                    )
                    raise AnalysisProcessingError(
                        reason,
                        retryable=rejected.status == "retry_wait",
                        diagnostic_code="checkpoint_corrupt",
                    )
                if _checkpoint_was_rejected(cached_source_path, cached[0]):
                    reason = f"第 {spec.segment_index + 1} 段检查点已被合同校验拒绝"
                    rejected = checkpoint_store.reject_segment_checkpoint(
                        task_id,
                        spec.segment_index,
                        reason=reason,
                        expected_result_path=cached_source_path,
                        expected_provider_request_id=persisted.provider_request_id,
                        expected_updated_at=persisted.updated_at,
                        expected_checkpoint_identity=persisted.checkpoint_identity,
                        diagnostic_code="model_contract_invalid",
                        task_worker_id=task_worker_id,
                    )
                    raise AnalysisProcessingError(
                        reason,
                        retryable=rejected.status == "retry_wait",
                        diagnostic_code="model_contract_invalid",
                    )
            else:
                claimed = checkpoint_store.claim_segment(
                    task_id,
                    worker_id=worker_id,
                    task_worker_id=task_worker_id,
                    lease_seconds=300,
                )
                if claimed is None or claimed.segment_index != spec.segment_index:
                    refreshed = {item.segment_index: item for item in checkpoint_store.list_segments(task_id)}.get(spec.segment_index)
                    if refreshed is not None and refreshed.status == "failed":
                        raise AnalysisProcessingError(
                            refreshed.error or f"第 {spec.segment_index + 1} 段已失败",
                            diagnostic_code="segment_failed",
                        )
                    delay = 1.0
                    if refreshed is not None and refreshed.next_retry_at:
                        try:
                            retry_at = datetime.fromisoformat(refreshed.next_retry_at.replace("Z", "+00:00"))
                            delay = max(0.05, (retry_at - datetime.now(UTC)).total_seconds())
                        except ValueError:
                            pass
                    raise GatewayError(
                        f"第 {spec.segment_index + 1} 段正在等待重试或租约释放",
                        retryable=True,
                        diagnostic_code="segment_retry_wait",
                        retry_after_seconds=delay,
                    )
                checkpoint_path = _segment_attempt_checkpoint_path(
                    task_dir,
                    spec.segment_index,
                    claimed.attempt_count,
                    worker_id,
                )
                # A crash may happen after the atomic file write but before the
                # SQLite commit. Scan immutable prior-attempt files and adopt a
                # complete valid one instead of paying the provider again.
                for orphan_path in _orphan_segment_checkpoint_paths(task_dir, spec.segment_index):
                    orphan = _read_segment_checkpoint(orphan_path, spec.segment_key)
                    if orphan is not None and _checkpoint_was_rejected(orphan_path, orphan[0]):
                        _remove_owned_checkpoint(task_dir, orphan_path)
                        continue
                    if orphan is not None:
                        cached = orphan
                        cached_source_path = orphan_path
                        checkpoint_path = orphan_path
                        adopt_orphan_checkpoint = True
                        break
                    if orphan_path.is_file():
                        _quarantine_malformed_checkpoint(
                            task_dir,
                            orphan_path,
                            phase=f"segment-{spec.segment_index:04d}-orphan",
                            reason="未提交的片段检查点已损坏或身份不匹配",
                        )
        else:
            cached = _read_segment_checkpoint(checkpoint_path, spec.segment_key)
            if cached is not None:
                cached_source_path = checkpoint_path

        if cached is not None:
            raw_content, response_id = cached
            audit_path = _checkpoint_audit_path(cached_source_path or checkpoint_path)
            try:
                prepared_content, audit = _repair_timeline_boundaries(
                    raw_content,
                    expected_start_ms=spec.start_ms,
                    expected_end_ms=spec.end_ms,
                )
            except TimelineNormalizationError as exc:
                _write_json_atomic(audit_path, exc.audit)
                _archive_rejected_candidate(
                    task_dir,
                    phase=f"segment-{spec.segment_index:04d}",
                    content=raw_content,
                    provider_request_id=response_id,
                    audit=exc.audit,
                    source_path=cached_source_path,
                )
                if checkpoint_store is not None and task_id is not None:
                    if persisted is not None and persisted.status == "succeeded":
                        checkpoint_store.reject_segment_checkpoint(
                            task_id,
                            spec.segment_index,
                            reason=str(exc),
                            expected_result_path=cached_source_path or checkpoint_path,
                            expected_provider_request_id=persisted.provider_request_id,
                            expected_updated_at=persisted.updated_at,
                            expected_checkpoint_identity=persisted.checkpoint_identity,
                            diagnostic_code=exc.diagnostic_code,
                            task_worker_id=task_worker_id,
                        )
                    elif claimed is not None:
                        if cached_source_path is not None:
                            _remove_owned_checkpoint(task_dir, cached_source_path)
                        checkpoint_store.fail_segment(
                            task_id,
                            spec.segment_index,
                            worker_id=worker_id,
                            error=exc,
                            task_worker_id=task_worker_id,
                        )
                elif cached_source_path is not None:
                    _remove_owned_checkpoint(task_dir, cached_source_path)
                log_event(
                    "timeline_boundary_rejected",
                    "segment",
                    segment_index=spec.segment_index,
                    segment_total=len(segments),
                    error_code=exc.diagnostic_code,
                    error_summary=str(exc),
                )
                raise
            _write_json_atomic(audit_path, audit)
            cached = (prepared_content, response_id)
            if audit["decision"] == "repaired":
                log_event(
                    "timeline_boundary_repaired",
                    "segment",
                    segment_index=spec.segment_index,
                    segment_total=len(segments),
                    incomplete_details=(
                        f"policy={audit['policy_version']};"
                        f"boundaries={len(audit['changes'])};"
                        f"total_ms={audit['total_adjusted_ms']}"
                    ),
                )
            if adopt_orphan_checkpoint and checkpoint_store is not None and task_id is not None:
                checkpoint_store.complete_segment(
                    task_id,
                    spec.segment_index,
                    worker_id=worker_id,
                    result_path=checkpoint_path,
                    provider_request_id=response_id,
                    task_worker_id=task_worker_id,
                )
                log_event(
                    "orphan_checkpoint_adopted",
                    "segment",
                    segment_index=spec.segment_index,
                    segment_total=len(segments),
                    segment_start_ms=spec.start_ms,
                    segment_end_ms=spec.end_ms,
                    attempt=claimed.attempt_count if claimed is not None else 1,
                    provider_request_id=response_id,
                    last_completed_segment=spec.segment_index,
                )
            elif persisted is not None and persisted.status == "succeeded":
                log_event(
                    "segment_checkpoint_reused",
                    "segment",
                    segment_index=spec.segment_index,
                    segment_total=len(segments),
                    segment_start_ms=spec.start_ms,
                    segment_end_ms=spec.end_ms,
                    attempt=persisted.attempt_count,
                    provider_request_id=response_id,
                    last_completed_segment=spec.segment_index,
                )

        if cached is None:
            ensure_active("segment")
            notify("segment", 35 + round(40 * spec.segment_index / max(1, len(segments))))
            image_urls, image_frame_ids, image_bytes = _encode_keyframes(
                ocr_result,
                shot_ids=set(spec.shot_ids),
                maximum=SEGMENT_MAX_KEYFRAMES,
            )
            context_json = _build_context(
                media_result,
                ocr_result,
                metrics,
                comments,
                image_frame_ids,
                segment=spec,
            )
            uploaded_keyframes += len(image_urls)
            uploaded_image_bytes += image_bytes
            uploaded_text_characters += len(context_json)
            uploaded_text_bytes += len(context_json.encode("utf-8"))
            try:
                log_event(
                    "provider_request_started",
                    "segment",
                    segment_index=spec.segment_index,
                    segment_total=len(segments),
                    segment_start_ms=spec.start_ms,
                    segment_end_ms=spec.end_ms,
                    attempt=claimed.attempt_count if claimed is not None else 1,
                )
                result = gateway_caller(config, **_gateway_arguments(
                    context_json=context_json,
                    keyframe_data_urls=image_urls,
                    purpose=purpose,
                    schema_name=f"content_factory_segment_{spec.segment_index:04d}",
                    analysis_method=analysis_method,
                ))
                ensure_active("segment")
                if len(segments) == 1:
                    _write_json_atomic(task_dir / "model-output.candidate.json", result.content)
                _require_semantic_fields(result.content)
                _validate_model_candidate_schema(result.content)
                raw_content = result.content
                try:
                    prepared_content, audit = _repair_timeline_boundaries(
                        result.content,
                        expected_start_ms=spec.start_ms,
                        expected_end_ms=spec.end_ms,
                    )
                except TimelineNormalizationError as exc:
                    _write_json_atomic(_checkpoint_audit_path(checkpoint_path), exc.audit)
                    _archive_rejected_candidate(
                        task_dir,
                        phase=f"segment-{spec.segment_index:04d}",
                        content=result.content,
                        provider_request_id=result.response_id,
                        audit=exc.audit,
                    )
                    raise
                _write_json_atomic(_checkpoint_audit_path(checkpoint_path), audit)
                _write_segment_checkpoint(checkpoint_path, spec, result)
                if checkpoint_store is not None and task_id is not None:
                    checkpoint_store.complete_segment(
                        task_id,
                        spec.segment_index,
                        worker_id=worker_id,
                        result_path=checkpoint_path,
                        provider_request_id=result.response_id,
                        task_worker_id=task_worker_id,
                    )
                cached = (prepared_content, result.response_id)
                if audit["decision"] == "repaired":
                    log_event(
                        "timeline_boundary_repaired",
                        "segment",
                        segment_index=spec.segment_index,
                        segment_total=len(segments),
                        incomplete_details=(
                            f"policy={audit['policy_version']};"
                            f"boundaries={len(audit['changes'])};"
                            f"total_ms={audit['total_adjusted_ms']}"
                        ),
                    )
                log_event(
                    "segment_completed",
                    "segment",
                    segment_index=spec.segment_index,
                    segment_total=len(segments),
                    segment_start_ms=spec.start_ms,
                    segment_end_ms=spec.end_ms,
                    attempt=claimed.attempt_count if claimed is not None else 1,
                    request_started_at=result.request_started_at,
                    response_headers_at=result.response_headers_at,
                    first_event_at=result.first_event_at,
                    last_event_at=result.last_event_at,
                    completed_at=result.completed_at or _now_iso(),
                    http_status=result.http_status,
                    provider_request_id=result.response_id,
                    provider_status=result.provider_status,
                    finish_reason=result.finish_reason,
                    partial_response=result.provider_partial,
                    last_completed_segment=spec.segment_index,
                )
            except Exception as exc:
                if checkpoint_store is not None and task_id is not None and claimed is not None:
                    checkpoint_store.fail_segment(
                        task_id,
                        spec.segment_index,
                        worker_id=worker_id,
                        error=exc,
                        task_worker_id=task_worker_id,
                    )
                log_event(
                    "segment_failed",
                    "segment",
                    **getattr(exc, "transport_diagnostics", {}),
                    segment_index=spec.segment_index,
                    segment_total=len(segments),
                    segment_start_ms=spec.start_ms,
                    segment_end_ms=spec.end_ms,
                    attempt=claimed.attempt_count if claimed is not None else 1,
                    completed_at=_now_iso(),
                    http_status=getattr(exc, "status_code", None),
                    error_type=type(exc).__name__,
                    error_code=getattr(exc, "diagnostic_code", None),
                    error_summary=str(exc),
                    retry_after=getattr(exc, "retry_after_seconds", None),
                    provider_request_id=getattr(exc, "provider_request_id", None),
                    last_completed_segment=spec.segment_index - 1 if spec.segment_index else None,
                )
                raise
        assert raw_content is not None
        segment_outputs.append(cached[0])
        segment_raw_outputs.append(raw_content)
        if cached[1]:
            segment_response_ids.append(cached[1])

    notify("summarizing", 80)
    ensure_active("summarizing")
    log_event(
        "summary_started",
        "summarizing",
        segment_total=len(segments),
        last_completed_segment=len(segments) - 1,
    )
    if len(segment_outputs) == 1:
        gateway_result = GatewayResult(segment_outputs[0], segment_response_ids[0] if segment_response_ids else None, 0)
        final_raw_output = segment_raw_outputs[0]
    else:
        summary_inputs = segment_outputs
        if len(segment_outputs) > SUMMARY_GROUP_SIZE:
            chapter_outputs: list[dict[str, Any]] = []
            for group_index in range(0, len(segment_outputs), SUMMARY_GROUP_SIZE):
                ensure_active("summarizing")
                group = segment_outputs[group_index:group_index + SUMMARY_GROUP_SIZE]
                keys = [item.segment_key for item in segments[group_index:group_index + SUMMARY_GROUP_SIZE]]
                chapter_context = _summary_context(
                    media_result=media_result,
                    metrics=[],
                    comments=[],
                    candidates=group,
                    level="chapter",
                    group_index=group_index // SUMMARY_GROUP_SIZE,
                    expected_start_ms=segments[group_index].start_ms,
                    expected_end_ms=segments[
                        min(group_index + SUMMARY_GROUP_SIZE, len(segments)) - 1
                    ].end_ms,
                )
                checkpoint_key = _summary_checkpoint_key(
                    level="chapter",
                    input_keys=keys,
                    context_json=chapter_context,
                )
                chapter_end_index = min(group_index + SUMMARY_GROUP_SIZE, len(segments)) - 1
                chapter, _chapter_raw = _summarize_with_checkpoint(
                    checkpoint_path=task_dir / "chapters" / f"chapter-{group_index // SUMMARY_GROUP_SIZE:03d}.json",
                    checkpoint_key=checkpoint_key,
                    context_json=chapter_context,
                    expected_start_ms=segments[group_index].start_ms,
                    expected_end_ms=segments[chapter_end_index].end_ms,
                    config=config,
                    purpose=purpose,
                    gateway_caller=gateway_caller,
                    schema_name=f"content_factory_chapter_{group_index // SUMMARY_GROUP_SIZE:03d}",
                    analysis_method=analysis_method,
                    cancel_check=lambda: checkpoint_store is not None
                    and task_id is not None
                    and checkpoint_store.is_cancel_requested(task_id),
                )
                chapter_outputs.append(chapter.content)
            summary_inputs = chapter_outputs
        final_summary_context = _summary_context(
            media_result=media_result,
            metrics=metrics,
            comments=comments,
            candidates=summary_inputs,
            level="final",
            expected_start_ms=0,
            expected_end_ms=int(media_result["media"]["duration_ms"]),
        )
        summary_key = _summary_checkpoint_key(
            level="final",
            input_keys=[item.segment_key for item in segments],
            context_json=final_summary_context,
        )
        gateway_result, final_raw_output = _summarize_with_checkpoint(
            checkpoint_path=task_dir / "summaries" / "final.json",
            checkpoint_key=summary_key,
            context_json=final_summary_context,
            expected_start_ms=0,
            expected_end_ms=int(media_result["media"]["duration_ms"]),
            config=config,
            purpose=purpose,
            gateway_caller=gateway_caller,
            schema_name="content_factory_final_analysis",
            analysis_method=analysis_method,
            cancel_check=lambda: checkpoint_store is not None
            and task_id is not None
            and checkpoint_store.is_cancel_requested(task_id),
        )
        uploaded_text_characters += len(final_summary_context)
        uploaded_text_bytes += len(final_summary_context.encode("utf-8"))

    ensure_active("summarizing")
    _write_json_atomic(task_dir / "model-output.candidate.json", final_raw_output)
    _require_semantic_fields(final_raw_output)
    log_event(
        "summary_completed",
        "summarizing",
        segment_total=len(segments),
        completed_at=gateway_result.completed_at or _now_iso(),
        http_status=gateway_result.http_status,
        provider_request_id=gateway_result.response_id,
        provider_status=gateway_result.provider_status,
        finish_reason=gateway_result.finish_reason,
        partial_response=gateway_result.provider_partial,
        last_completed_segment=len(segments) - 1,
    )
    timeline_audit: dict[str, Any] = {}
    try:
        model_output = _normalize_model_output(
            final_raw_output,
            media_result=media_result,
            ocr_result=ocr_result,
            metrics=metrics,
            comments=comments,
            timeline_audit=timeline_audit,
        )
    except TimelineNormalizationError as exc:
        _write_json_atomic(task_dir / "model-output.normalization-audit.json", exc.audit)
        raise
    _write_json_atomic(task_dir / "model-output.normalized.json", model_output)
    _write_json_atomic(task_dir / "model-output.normalization-audit.json", timeline_audit)
    if timeline_audit["decision"] == "repaired":
        log_event(
            "timeline_boundary_repaired",
            "normalizing",
            incomplete_details=(
                f"policy={timeline_audit['policy_version']};"
                f"boundaries={len(timeline_audit['changes'])};"
                f"total_ms={timeline_audit['total_adjusted_ms']}"
            ),
        )

    notify("validate", 86)
    ensure_active("validate")
    analysis_id = f"analysis_{uuid4().hex}"
    source_hash = media_result["source"]["sha256"]
    provider = urlparse(config.base_url).hostname or "configured-relay"
    elapsed_ms = round((time.perf_counter() - report_started) * 1000)
    report: dict[str, Any] = {
        "schema_version": "1.1.0",
        "fixture_data": bool(media_result["fixture_data"]),
        "status": "draft",
        "revision": 1,
        "analysis_id": analysis_id,
        "video_id": f"video_{source_hash[:24]}",
        "duration_ms": media_result["media"]["duration_ms"],
        "source": {
            "source_id": f"source_{source_hash[:24]}",
            "source_type": "local_file",
            "source_uri": media_result["source"]["original_name"],
            "imported_at": media_result["created_at"],
            "authorization_status": "internal",
        },
        "processing": {
            "provider": provider,
            "model_profile_id": config.model_id,
            "purpose": purpose,
            "model": config.model,
            "api_mode": config.api_mode,
            "response_id": gateway_result.response_id,
            "prompt_version": analysis_method["prompt_version"] if analysis_method else PROMPT_VERSION,
            "generated_at": _now_iso(),
            "duration_ms": max(0, elapsed_ms),
            "upload_summary": {
                "keyframe_count": uploaded_keyframes,
                "text_characters": uploaded_text_characters,
                "total_bytes": uploaded_image_bytes + uploaded_text_bytes,
                "original_video_uploaded": False,
            },
        },
        "keyframes": _controlled_keyframes(ocr_result, media_result),
        "metric_snapshots": metrics,
        "comments": comments,
        "review": {"reviewer": None, "reviewed_at": None, "note": None},
    }
    report.update({field: model_output[field] for field in _SEMANTIC_FIELDS})
    try:
        validate_or_raise("analysis", report)
    except ContractValidationError as exc:
        rejection_audit = _contract_rejection_audit(final_raw_output, exc)
        segment_record: AnalysisSegmentRecord | None = None
        if len(segments) == 1:
            if checkpoint_store is not None and task_id is not None:
                segment_record = {
                    item.segment_index: item for item in checkpoint_store.list_segments(task_id)
                }.get(0)
            rejected_checkpoint = (
                Path(segment_record.result_path)
                if segment_record is not None and segment_record.result_path
                else _segment_checkpoint_path(task_dir, 0)
            )
            phase = "segment-0000-final-contract"
        else:
            rejected_checkpoint = task_dir / "summaries" / "final.json"
            phase = "final-summary-contract"
        _write_json_atomic(_checkpoint_audit_path(rejected_checkpoint), rejection_audit)
        _archive_rejected_candidate(
            task_dir,
            phase=phase,
            content=final_raw_output,
            provider_request_id=gateway_result.response_id,
            audit=rejection_audit,
            source_path=rejected_checkpoint,
        )

        retryable = True
        if len(segments) == 1 and checkpoint_store is not None and task_id is not None:
            if segment_record is None or segment_record.status != "succeeded" or not segment_record.result_path:
                raise AnalysisProcessingError(
                    f"模型结果未通过最终合同校验，且片段状态已变化：{exc}",
                    retryable=True,
                    diagnostic_code="model_contract_invalid",
                ) from exc
            rejected = checkpoint_store.reject_segment_checkpoint(
                task_id,
                0,
                reason=str(exc),
                expected_result_path=segment_record.result_path,
                expected_provider_request_id=segment_record.provider_request_id,
                expected_updated_at=segment_record.updated_at,
                expected_checkpoint_identity=segment_record.checkpoint_identity,
                diagnostic_code="model_contract_invalid",
                task_worker_id=task_worker_id,
            )
            retryable = rejected.status == "retry_wait"
        # A persisted segment may already be re-claimed as soon as the DB
        # transition commits. Leave its rejected generation in place; the
        # next lease owner recognizes the audit hash and removes it safely.
        if len(segments) > 1 or checkpoint_store is None:
            _remove_owned_checkpoint(task_dir, rejected_checkpoint)
        raise AnalysisProcessingError(
            f"模型结果未通过最终合同校验，已作废错误检查点：{exc}",
            retryable=retryable,
            diagnostic_code="model_contract_invalid",
        ) from exc

    notify("persist", 94)
    ensure_active("persist")
    report_path = (
        task_dir
        / f"analysis-report-{hashlib.sha256(worker_id.encode('utf-8')).hexdigest()[:12]}.json"
        if checkpoint_store is not None
        else task_dir / "analysis-report.json"
    )
    _write_json_atomic(report_path, report)
    log_event(
        "job_completed",
        "completed",
        segment_total=len(segments),
        completed_at=_now_iso(),
        provider_request_id=gateway_result.response_id,
        last_completed_segment=len(segments) - 1,
    )
    notify("completed", 100)
    return AnalysisArtifacts(analysis_id=analysis_id, report_path=report_path, ocr_result_path=ocr_path)
