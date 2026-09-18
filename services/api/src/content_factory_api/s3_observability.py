"""Bounded, redacted JSONL diagnostics for durable video-analysis jobs."""

from __future__ import annotations

import json
import os
import re
import threading
import ctypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()
_BEARER = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b", re.IGNORECASE)
_SIGNED_URL = re.compile(r"(https?://[^\s?#]+)(?:\?[^\s]*)?", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    text = "".join(character if character.isprintable() else " " for character in str(value))
    text = _BEARER.sub("[redacted]", text)
    text = _SECRET.sub("[redacted]", text)
    text = _SIGNED_URL.sub(r"\1", text)
    return text[:limit]


def process_peak_memory_bytes() -> int | None:
    """Return this worker process' peak resident memory without adding a dependency."""

    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        try:
            current_process = ctypes.windll.kernel32.GetCurrentProcess()
            success = ctypes.windll.psapi.GetProcessMemoryInfo(
                current_process,
                ctypes.byref(counters),
                counters.cb,
            )
        except (AttributeError, OSError):
            return None
        return int(counters.PeakWorkingSetSize) if success else None
    try:
        import resource

        maximum_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return maximum_rss if os.uname().sysname == "Darwin" else maximum_rss * 1024
    except (AttributeError, ImportError, OSError, ValueError):
        return None


def write_analysis_event(
    task_directory: str | Path,
    *,
    event: str,
    job_id: str,
    trace_id: str,
    video_hash: str,
    phase: str,
    segment_index: int | None = None,
    segment_total: int | None = None,
    segment_start_ms: int | None = None,
    segment_end_ms: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    attempt: int | None = None,
    request_started_at: str | None = None,
    response_headers_at: str | None = None,
    first_event_at: str | None = None,
    last_event_at: str | None = None,
    transport_stage: str | None = None,
    transport_exception: str | None = None,
    received_bytes: int | None = None,
    elapsed_seconds: float | None = None,
    completed_at: str | None = None,
    http_status: int | None = None,
    error_type: str | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    retry_after: float | None = None,
    provider_request_id: str | None = None,
    provider_status: str | None = None,
    incomplete_details: str | None = None,
    finish_reason: str | None = None,
    partial_response: bool | None = None,
    ffmpeg_pid: int | None = None,
    ffmpeg_exit_code: int | None = None,
    ffmpeg_stderr_tail: str | None = None,
    process_peak_memory_bytes: int | None = None,
    temporary_disk_free_bytes: int | None = None,
    last_completed_segment: int | None = None,
) -> None:
    """Append one safe event; arbitrary prompts/transcripts are not accepted."""

    payload = {
        "timestamp": _now_iso(),
        "event": _clean_text(event, limit=80),
        "job_id": _clean_text(job_id, limit=80),
        "trace_id": _clean_text(trace_id, limit=80),
        "video_hash": _clean_text(video_hash, limit=16),
        "phase": _clean_text(phase, limit=80),
        "segment_index": segment_index,
        "segment_total": segment_total,
        "segment_start_ms": segment_start_ms,
        "segment_end_ms": segment_end_ms,
        "provider": _clean_text(provider, limit=120),
        "model": _clean_text(model, limit=120),
        "attempt": attempt,
        "request_started_at": request_started_at,
        "response_headers_at": response_headers_at,
        "first_event_at": first_event_at,
        "last_event_at": last_event_at,
        "transport_stage": _clean_text(transport_stage, limit=80),
        "transport_exception": _clean_text(transport_exception, limit=80),
        "received_bytes": received_bytes,
        "elapsed_seconds": elapsed_seconds,
        "completed_at": completed_at,
        "http_status": http_status,
        "error_type": _clean_text(error_type, limit=120),
        "error_code": _clean_text(error_code, limit=120),
        "error_summary": _clean_text(error_summary, limit=500),
        "retry_after": retry_after,
        "provider_request_id": _clean_text(provider_request_id, limit=160),
        "provider_status": _clean_text(provider_status, limit=80),
        "incomplete_details": _clean_text(incomplete_details, limit=240),
        "finish_reason": _clean_text(finish_reason, limit=80),
        "partial_response": partial_response,
        "ffmpeg_pid": ffmpeg_pid,
        "ffmpeg_exit_code": ffmpeg_exit_code,
        "ffmpeg_stderr_tail": _clean_text(ffmpeg_stderr_tail, limit=1000),
        "process_peak_memory_bytes": process_peak_memory_bytes,
        "temporary_disk_free_bytes": temporary_disk_free_bytes,
        "last_completed_segment": last_completed_segment,
    }
    path = Path(task_directory).expanduser().resolve() / "diagnostics" / "analysis-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
