from __future__ import annotations

import json

from content_factory_api.s3_observability import process_peak_memory_bytes, write_analysis_event


def test_structured_analysis_log_redacts_credentials_prompts_and_signed_queries(tmp_path) -> None:
    write_analysis_event(
        tmp_path,
        event="segment_failed",
        job_id="analysis_task_" + "a" * 32,
        trace_id="trace_" + "b" * 32,
        video_hash="c" * 64,
        phase="segment",
        segment_index=2,
        segment_total=8,
        provider="relay.example.com",
        model="qwen-vl-max",
        attempt=2,
        http_status=429,
        error_type="HTTPError",
        error_code="rate_limited",
        error_summary="Bearer sk-super-secret-token at https://host/video?Signature=private",
        retry_after=9,
        provider_request_id="req_safe_123",
        finish_reason=None,
        partial_response=True,
        last_completed_segment=1,
    )

    log_path = tmp_path / "diagnostics" / "analysis-events.jsonl"
    raw = log_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["job_id"].startswith("analysis_task_")
    assert payload["video_hash"] == "c" * 16
    assert payload["retry_after"] == 9
    assert "sk-super-secret-token" not in raw
    assert "Signature=private" not in raw
    assert "Bearer" not in raw
    assert "[redacted]" in payload["error_summary"]


def test_peak_memory_probe_is_bounded_and_never_raises() -> None:
    value = process_peak_memory_bytes()
    assert value is None or 0 < value < 2**63
