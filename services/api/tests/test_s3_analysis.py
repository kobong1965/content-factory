from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory_api.s3_analysis import (
    AnalysisProcessingError,
    model_output_schema,
    plan_analysis_segments,
    process_analysis,
)
from content_factory_api.s3_gateway import GatewayResult
from content_factory_api.s3_settings import GatewayConfig
from content_factory_contracts import validate_or_raise


def _media_result(tmp_path: Path, *, duration_ms: int = 1000) -> Path:
    task_dir = tmp_path / "media-task"
    keyframes = task_dir / "keyframes"
    keyframes.mkdir(parents=True)
    frame = keyframes / "shot-001.jpg"
    frame.write_bytes(b"fixture-jpeg")
    result = {
        "schema_version": "1.0.0", "fixture_data": True,
        "task_id": "media_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "source": {"original_name": "男装样片.mp4", "sha256": "a" * 64, "size_bytes": 100, "managed_original_path": str(task_dir / "original.mp4")},
        "media": {"duration_ms": duration_ms, "width": 360, "height": 640, "fps": 25, "video_codec": "h264", "audio_codec": None, "has_audio": False, "format_name": "mp4"},
        "artifacts": {"proxy_path": str(task_dir / "proxy.mp4"), "audio_path": None, "transcript_path": None, "scene_manifest_path": str(task_dir / "scenes.json")},
        "asr": {"status": "no_audio", "model_name": None, "language": None, "segment_count": 0},
        "shots": [{"id": "shot_001", "start_ms": 0, "end_ms": duration_ms, "scene_score": 0, "keyframe_path": str(frame)}],
        "timings_ms": {"total": 1}, "created_at": "2026-08-29T06:00:00Z",
    }
    path = task_dir / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return path


def _claim(text: str) -> dict:
    return {"text": text, "evidence_ids": ["evidence_fixture_001"], "confidence": 0.7, "is_inference": True}


def _semantic() -> dict:
    score_dimensions = [
        ("hook_strength", 0.2), ("proof_strength", 0.2), ("rhythm_efficiency", 0.15),
        ("product_fit", 0.15), ("consumer_psychology", 0.1), ("interaction_design", 0.1),
        ("data_result", 0.1),
    ]
    return {
        "summary": {
            "video_type": "product_seeding", "target_audience": _claim("关注裤型的用户"),
            "main_promise": _claim("展示裤型"), "overall_conclusion": _claim("样例只有一个镜头"),
            "hook_analysis": _claim("开头直接展示"), "consumer_psychology": _claim("降低判断成本"),
            "risks": [_claim("缺少真实指标和评论")],
        },
        "shots": [{
            "id": "shot_001", "start_ms": 0, "end_ms": 1000, "shot_size": "full",
            "camera_movement": "static", "composition": "人物居中", "performer_action": "站立展示",
            "product_exposure": "裤型正面", "transcript": "", "subtitle": "直筒西裤",
            "keyframe_refs": ["frame_aaaaaaaaaaaa_001"],
        }],
        "timeline": [{
            "id": "timeline_fixture_001", "second_index": 0, "start_ms": 0, "end_ms": 1000,
            "visual_event": "人物展示裤型", "transcript": "", "subtitle": "直筒西裤", "rhythm": "medium", "metric_refs": [],
        }],
        "content_structure": [{
            "id": "structure_fixture_001", "order": 1, "label": "展示", "description": "直接展示裤型",
            "start_ms": 0, "end_ms": 1000, "evidence_ids": ["evidence_fixture_001"],
        }],
        "emotion_curve": [{
            "id": "emotion_fixture_001", "start_ms": 0, "end_ms": 1000, "intensity": 50,
            "label": "平稳", "reason": "单镜头展示", "evidence_ids": ["evidence_fixture_001"],
        }],
        "comment_insights": [],
        "evidence": [{
            "id": "evidence_fixture_001", "claim": "画面展示裤型", "source_type": "keyframe",
            "source_id": "frame_aaaaaaaaaaaa_001", "start_ms": 0, "end_ms": 1000,
            "confidence": 0.9, "is_inference": False,
        }],
        "scores": {
            "score_version": "1.0.0", "total_score": 50,
            "items": [
                {"dimension": dimension, "weight": weight, "score": 50, "evidence_ids": ["evidence_fixture_001"]}
                for dimension, weight in score_dimensions
            ],
        },
        "pattern_candidates": [{
            "id": "pattern_fixture_001", "name": "直接展示", "mechanism": "用完整裤型快速给结果",
            "mechanism_key": "direct_product_pitch", "reuse_mode": "reuse",
            "steps": [
                {"id": "pattern_step_fixture_001", "order": 1, "description": "展示裤型", "evidence_ids": ["evidence_fixture_001"]},
                {"id": "pattern_step_fixture_002", "order": 2, "description": "补充文字", "evidence_ids": ["evidence_fixture_001"]},
            ],
            "necessary_conditions": ["裤型完整可见"], "failure_signals": ["画面遮挡"],
        }],
    }


def test_s3_processing_keeps_local_paths_and_original_video_out_of_gateway(tmp_path: Path) -> None:
    media_result_path = _media_result(tmp_path)
    fake_ocr = SimpleNamespace(
        boxes=[[[0, 0], [100, 0], [100, 30], [0, 30]]], txts=["直筒西裤"], scores=[0.99],
    )

    def gateway(_config, *, context_json, keyframe_data_urls, output_schema, developer_instructions):
        assert str(tmp_path) not in context_json
        assert "original.mp4" not in context_json
        assert len(keyframe_data_urls) == 1
        assert output_schema == model_output_schema()
        assert all(phrase in developer_instructions for phrase in (
            "爆点研究智能体", "什么时候", "说了什么", "做了什么", "爆点 Skill", "平台内部数据",
        ))
        return GatewayResult(_semantic(), "fixture_response", 1024)

    artifacts = process_analysis(
        media_result_path=media_result_path,
        input_payload={"metric_snapshots": [], "comments": []},
        task_directory=tmp_path / "analysis-task",
        config=GatewayConfig("http://127.0.0.1:9876/v1", "fixture-model", "responses", "fixture-secret", "2026-08-29T06:00:00Z"),
        gateway_caller=gateway,
        ocr_engine=lambda _path: fake_ocr,
    )

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    validate_or_raise("analysis", report)
    assert report["fixture_data"] is True
    assert report["processing"]["purpose"] == "analysis"
    assert report["processing"]["upload_summary"]["original_video_uploaded"] is False
    assert report["status"] == "draft"
    assert (tmp_path / "analysis-task" / "model-output.candidate.json").is_file()


def test_model_output_schema_requires_skill_classification_fields() -> None:
    schema = model_output_schema()
    pattern_required = schema["properties"]["pattern_candidates"]["items"]["required"]

    assert "mechanism_key" in pattern_required
    assert "reuse_mode" in pattern_required


def test_video_review_uses_dedicated_evidence_bound_prompt_and_persists_purpose(tmp_path: Path) -> None:
    media_result_path = _media_result(tmp_path)
    fake_ocr = SimpleNamespace(
        boxes=[[[0, 0], [100, 0], [100, 30], [0, 30]]],
        txts=["直筒西裤"],
        scores=[0.99],
    )
    seen_instructions: list[str] = []

    def gateway(
        _config,
        *,
        context_json,
        keyframe_data_urls,
        output_schema,
        developer_instructions,
    ):
        assert context_json
        assert keyframe_data_urls
        assert output_schema == model_output_schema()
        seen_instructions.append(developer_instructions)
        return GatewayResult(_semantic(), "fixture_video_review", 1024)

    artifacts = process_analysis(
        media_result_path=media_result_path,
        input_payload={"metric_snapshots": [], "comments": []},
        task_directory=tmp_path / "video-review-task",
        config=GatewayConfig(
            "http://127.0.0.1:9876/v1",
            "fixture-model",
            "responses",
            "fixture-secret",
            "2026-09-05T00:00:00Z",
        ),
        purpose="video_review",
        gateway_caller=gateway,
        ocr_engine=lambda _path: fake_ocr,
    )

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    validate_or_raise("analysis", report)
    assert report["processing"]["purpose"] == "video_review"
    assert report["processing"]["response_id"] == "fixture_video_review"
    assert len(seen_instructions) == 1
    assert all(
        phrase in seen_instructions[0]
        for phrase in ("画面与口播", "OCR", "商品", "违规", "夸大", "字幕", "音频", "剪辑", "证据 ID")
    )


def test_process_analysis_rejects_non_analysis_purpose_before_processing(tmp_path: Path) -> None:
    with pytest.raises(AnalysisProcessingError, match="不支持的分析任务用途"):
        process_analysis(
            media_result_path=tmp_path / "missing.json",
            input_payload={},
            task_directory=tmp_path / "invalid-purpose",
            config=GatewayConfig(
                "http://127.0.0.1:9876/v1",
                "fixture-model",
                "responses",
                "fixture-secret",
                "2026-09-05T00:00:00Z",
            ),
            purpose="script",  # type: ignore[arg-type]
        )


def test_invalid_model_candidate_is_quarantined_for_local_diagnosis(tmp_path: Path) -> None:
    media_result_path = _media_result(tmp_path)
    fake_ocr = SimpleNamespace(
        boxes=[[[0, 0], [100, 0], [100, 30], [0, 30]]], txts=["直筒西裤"], scores=[0.99],
    )
    task_directory = tmp_path / "failed-analysis"

    with pytest.raises(AnalysisProcessingError, match="缺少字段"):
        process_analysis(
            media_result_path=media_result_path,
            input_payload={"metric_snapshots": [], "comments": []},
            task_directory=task_directory,
            config=GatewayConfig(
                "http://127.0.0.1:9876/v1", "fixture-model", "responses",
                "fixture-secret", "2026-08-29T06:00:00Z",
            ),
            gateway_caller=lambda *_args, **_kwargs: GatewayResult({"summary": {}}, "bad_fixture", 24),
            ocr_engine=lambda _path: fake_ocr,
        )

    candidate = json.loads((task_directory / "model-output.candidate.json").read_text(encoding="utf-8"))
    assert candidate == {"summary": {}}
    assert not (task_directory / "analysis-report.json").exists()


def test_schema_invalid_model_candidate_never_becomes_a_successful_checkpoint(
    tmp_path: Path,
) -> None:
    media_result_path = _media_result(tmp_path)
    candidate = _semantic()
    candidate["timeline"][0]["rhythm"] = "impossible"
    task_directory = tmp_path / "schema-invalid-analysis"

    with pytest.raises(AnalysisProcessingError) as captured:
        process_analysis(
            media_result_path=media_result_path,
            input_payload={"metric_snapshots": [], "comments": []},
            task_directory=task_directory,
            config=GatewayConfig(
                "http://127.0.0.1:9876/v1", "fixture-model", "responses",
                "fixture-secret", "2026-08-29T06:00:00Z",
            ),
            gateway_caller=lambda *_args, **_kwargs: GatewayResult(candidate, "bad_schema", 24),
            ocr_engine=lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        )

    assert captured.value.retryable is True
    assert captured.value.diagnostic_code == "model_output_invalid"
    assert (task_directory / "model-output.candidate.json").is_file()
    assert not (task_directory / "segments" / "segment-0000.json").exists()
    assert not (task_directory / "analysis-report.json").exists()


def test_invalid_controlled_metric_is_rejected_before_calling_model(tmp_path: Path) -> None:
    media_result_path = _media_result(tmp_path)
    gateway_calls = 0

    def gateway(*_args, **_kwargs):
        nonlocal gateway_calls
        gateway_calls += 1
        return GatewayResult(_semantic(), "must_not_run", 24)

    with pytest.raises(AnalysisProcessingError) as captured:
        process_analysis(
            media_result_path=media_result_path,
            input_payload={
                "metric_snapshots": [{
                    "captured_at": "not-a-date",
                    "source_type": "manual",
                    "confidence": 1,
                    "values": {"play_count": 10},
                }],
                "comments": [],
            },
            task_directory=tmp_path / "invalid-controlled-input",
            config=GatewayConfig(
                "http://127.0.0.1:9876/v1", "fixture-model", "responses",
                "fixture-secret", "2026-08-29T06:00:00Z",
            ),
            gateway_caller=gateway,
            ocr_engine=lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        )

    assert captured.value.retryable is False
    assert captured.value.diagnostic_code == "invalid_analysis_input"
    assert gateway_calls == 0


def test_model_output_is_deterministically_normalized_before_contract_validation(tmp_path: Path) -> None:
    media_result_path = _media_result(tmp_path)
    fake_ocr = SimpleNamespace(
        boxes=[[[0, 0], [100, 0], [100, 30], [0, 30]]], txts=["直筒西裤"], scores=[0.99],
    )
    candidate = _semantic()
    candidate["evidence"][0]["start_ms"] = None
    candidate["evidence"][0]["end_ms"] = None
    candidate["emotion_curve"][0]["evidence_ids"] = ["evidence_invented_001"]
    for item in candidate["scores"]["items"]:
        item["weight"] = 1
    candidate["scores"]["total_score"] = 0

    artifacts = process_analysis(
        media_result_path=media_result_path,
        input_payload={"metric_snapshots": [], "comments": []},
        task_directory=tmp_path / "normalized-analysis",
        config=GatewayConfig(
            "http://127.0.0.1:9876/v1", "fixture-model", "responses",
            "fixture-secret", "2026-08-29T06:00:00Z",
        ),
        gateway_caller=lambda *_args, **_kwargs: GatewayResult(candidate, "normalized_fixture", 24),
        ocr_engine=lambda _path: fake_ocr,
    )

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    raw = json.loads((tmp_path / "normalized-analysis" / "model-output.candidate.json").read_text(encoding="utf-8"))
    normalized = json.loads((tmp_path / "normalized-analysis" / "model-output.normalized.json").read_text(encoding="utf-8"))
    validate_or_raise("analysis", report)
    assert raw["evidence"][0]["start_ms"] is None
    assert normalized["evidence"][0]["start_ms"] == 0
    assert normalized["evidence"][0]["end_ms"] == 1000
    assert normalized["emotion_curve"][0]["evidence_ids"] == ["evidence_fixture_001"]
    assert sum(item["weight"] for item in normalized["scores"]["items"]) == pytest.approx(1)
    assert normalized["scores"]["total_score"] == 50


def test_actual_qwen_timeline_boundary_drift_is_repaired_and_audited(tmp_path: Path) -> None:
    duration_ms = 42_051
    media_result_path = _media_result(tmp_path, duration_ms=duration_ms)
    candidate = _semantic()
    candidate["shots"][0]["end_ms"] = duration_ms
    candidate["content_structure"][0]["end_ms"] = duration_ms
    candidate["emotion_curve"][0]["end_ms"] = duration_ms
    candidate["evidence"][0]["end_ms"] = duration_ms
    candidate["timeline"] = [
        {
            "id": "timeline_fixture_001", "second_index": 0,
            "start_ms": 0, "end_ms": 2500, "visual_event": "开场展示",
            "transcript": "", "subtitle": "直筒西裤", "rhythm": "high", "metric_refs": [],
        },
        {
            "id": "timeline_fixture_002", "second_index": 2,
            "start_ms": 2500, "end_ms": 3580, "visual_event": "细节说明",
            "transcript": "", "subtitle": "版型细节", "rhythm": "medium", "metric_refs": [],
        },
        {
            "id": "timeline_fixture_003", "second_index": 29,
            "start_ms": 2944, "end_ms": 32944, "visual_event": "上身展示",
            "transcript": "", "subtitle": "上身效果", "rhythm": "medium", "metric_refs": [],
        },
        {
            "id": "timeline_fixture_004", "second_index": 35,
            "start_ms": 35328, "end_ms": duration_ms, "visual_event": "收尾展示",
            "transcript": "", "subtitle": "完整裤型", "rhythm": "low", "metric_refs": [],
        },
    ]
    task_directory = tmp_path / "qwen-timeline-repair"

    artifacts = process_analysis(
        media_result_path=media_result_path,
        input_payload={"metric_snapshots": [], "comments": []},
        task_directory=task_directory,
        config=GatewayConfig(
            "http://127.0.0.1:9876/v1", "qwen3-vl-plus", "chat_completions",
            "fixture-secret", "2026-09-07T00:00:00Z",
        ),
        gateway_caller=lambda *_args, **_kwargs: GatewayResult(candidate, "qwen_boundary_fixture", 2048),
        ocr_engine=lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
    )

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    raw = json.loads((task_directory / "model-output.candidate.json").read_text(encoding="utf-8"))
    normalized = json.loads((task_directory / "model-output.normalized.json").read_text(encoding="utf-8"))
    audit = json.loads((task_directory / "model-output.normalization-audit.json").read_text(encoding="utf-8"))

    validate_or_raise("analysis", report)
    assert [(item["start_ms"], item["end_ms"]) for item in raw["timeline"]] == [
        (0, 2500), (2500, 3580), (2944, 32944), (35328, duration_ms),
    ]
    assert [(item["start_ms"], item["end_ms"]) for item in normalized["timeline"]] == [
        (0, 2500), (2500, 3580), (3580, 32944), (32944, duration_ms),
    ]
    assert [item["second_index"] for item in normalized["timeline"]] == [0, 2, 3, 32]
    assert audit["decision"] == "repaired"
    assert audit["total_adjusted_ms"] == 3020
    assert [item["path"] for item in audit["changes"]] == [
        "timeline[2].start_ms", "timeline[3].start_ms",
    ]


def test_large_timeline_gap_is_rejected_before_a_successful_checkpoint(tmp_path: Path) -> None:
    duration_ms = 10_000
    media_result_path = _media_result(tmp_path, duration_ms=duration_ms)
    candidate = _semantic()
    candidate["shots"][0]["end_ms"] = duration_ms
    candidate["content_structure"][0]["end_ms"] = duration_ms
    candidate["emotion_curve"][0]["end_ms"] = duration_ms
    candidate["evidence"][0]["end_ms"] = duration_ms
    candidate["timeline"] = [
        {
            "id": "timeline_fixture_001", "second_index": 0,
            "start_ms": 0, "end_ms": 1000, "visual_event": "开场",
            "transcript": "", "subtitle": "开场", "rhythm": "high", "metric_refs": [],
        },
        {
            "id": "timeline_fixture_002", "second_index": 5,
            "start_ms": 5000, "end_ms": duration_ms, "visual_event": "收尾",
            "transcript": "", "subtitle": "收尾", "rhythm": "low", "metric_refs": [],
        },
    ]
    task_directory = tmp_path / "unsafe-timeline"

    with pytest.raises(AnalysisProcessingError) as captured:
        process_analysis(
            media_result_path=media_result_path,
            input_payload={"metric_snapshots": [], "comments": []},
            task_directory=task_directory,
            config=GatewayConfig(
                "http://127.0.0.1:9876/v1", "qwen3-vl-plus", "chat_completions",
                "fixture-secret", "2026-09-07T00:00:00Z",
            ),
            gateway_caller=lambda *_args, **_kwargs: GatewayResult(candidate, "unsafe_timeline_fixture", 1024),
            ocr_engine=lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        )

    assert captured.value.retryable is True
    assert captured.value.diagnostic_code == "model_timeline_invalid"
    assert not (task_directory / "analysis-report.json").exists()
    rejected = list((task_directory / "diagnostics" / "rejected-checkpoints").glob("*.json"))
    assert len(rejected) == 1
    rejection = json.loads(rejected[0].read_text(encoding="utf-8"))
    assert rejection["audit"]["decision"] == "rejected"
    assert rejection["provider_request_id"] == "unsafe_timeline_fixture"


def test_small_timeline_tail_overflow_is_repaired_within_threshold(tmp_path: Path) -> None:
    media_result_path = _media_result(tmp_path)
    candidate = _semantic()
    candidate["timeline"][0]["end_ms"] = 1001
    task_directory = tmp_path / "tail-overflow-repair"

    artifacts = process_analysis(
        media_result_path=media_result_path,
        input_payload={"metric_snapshots": [], "comments": []},
        task_directory=task_directory,
        config=GatewayConfig(
            "http://127.0.0.1:9876/v1", "fixture-model", "responses",
            "fixture-secret", "2026-09-07T00:00:00Z",
        ),
        gateway_caller=lambda *_args, **_kwargs: GatewayResult(candidate, "tail_overflow", 24),
        ocr_engine=lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
    )

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    audit = json.loads(
        (task_directory / "model-output.normalization-audit.json").read_text(encoding="utf-8")
    )
    assert report["timeline"][0]["end_ms"] == 1000
    assert audit["decision"] == "repaired"
    assert audit["changes"] == [{
        "path": "timeline[0].end_ms",
        "kind": "tail_overflow",
        "before": 1001,
        "after": 1000,
        "delta_ms": -1,
    }]


def test_existing_repairable_checkpoint_is_reused_without_calling_gateway(tmp_path: Path) -> None:
    media_result_path = _media_result(tmp_path)
    media_result = json.loads(media_result_path.read_text(encoding="utf-8"))
    config = GatewayConfig(
        "http://127.0.0.1:9876/v1", "qwen3-vl-plus", "chat_completions",
        "fixture-secret", "2026-09-07T00:00:00Z", provider="qwen",
    )
    spec = plan_analysis_segments(media_result, config)[0]
    candidate = _semantic()
    candidate["timeline"] = [
        {
            "id": "timeline_fixture_001", "second_index": 0,
            "start_ms": 0, "end_ms": 400, "visual_event": "开场",
            "transcript": "", "subtitle": "开场", "rhythm": "high", "metric_refs": [],
        },
        {
            "id": "timeline_fixture_002", "second_index": 0,
            "start_ms": 300, "end_ms": 1000, "visual_event": "展示",
            "transcript": "", "subtitle": "展示", "rhythm": "medium", "metric_refs": [],
        },
    ]
    task_directory = tmp_path / "legacy-checkpoint"
    checkpoint = task_directory / "segments" / "segment-0000.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps({
        "schema_version": "1.0.0",
        "segment_key": spec.segment_key,
        "segment_index": 0,
        "start_ms": 0,
        "end_ms": 1000,
        "provider_request_id": "legacy_qwen_response",
        "content": candidate,
    }, ensure_ascii=False), encoding="utf-8")
    gateway_calls = 0

    def gateway_must_not_run(*_args, **_kwargs):
        nonlocal gateway_calls
        gateway_calls += 1
        raise AssertionError("repairable legacy checkpoint must not call the provider again")

    artifacts = process_analysis(
        media_result_path=media_result_path,
        input_payload={"metric_snapshots": [], "comments": []},
        task_directory=task_directory,
        config=config,
        gateway_caller=gateway_must_not_run,
        ocr_engine=lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
    )

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    raw = json.loads((task_directory / "model-output.candidate.json").read_text(encoding="utf-8"))
    audit = json.loads((task_directory / "model-output.normalization-audit.json").read_text(encoding="utf-8"))
    validate_or_raise("analysis", report)
    assert gateway_calls == 0
    assert [(item["start_ms"], item["end_ms"]) for item in raw["timeline"]] == [(0, 400), (300, 1000)]
    assert [(item["start_ms"], item["end_ms"]) for item in report["timeline"]] == [(0, 400), (400, 1000)]
    assert report["processing"]["response_id"] == "legacy_qwen_response"
    assert audit["decision"] == "repaired"


def test_partial_timeline_evidence_clipping_is_recorded_in_normalization_audit(
    tmp_path: Path,
) -> None:
    media_result_path = _media_result(tmp_path)
    candidate = _semantic()
    candidate["timeline"] = [
        {
            "id": "timeline_fixture_001", "second_index": 0,
            "start_ms": 0, "end_ms": 400, "visual_event": "开场",
            "transcript": "", "subtitle": "开场", "rhythm": "high", "metric_refs": [],
        },
        {
            "id": "timeline_fixture_002", "second_index": 0,
            "start_ms": 300, "end_ms": 1000, "visual_event": "展示",
            "transcript": "", "subtitle": "展示", "rhythm": "medium", "metric_refs": [],
        },
    ]
    candidate["evidence"][0].update({
        "source_type": "timeline",
        "source_id": "timeline_fixture_002",
        "start_ms": 350,
        "end_ms": 500,
    })
    task_directory = tmp_path / "evidence-audit"

    artifacts = process_analysis(
        media_result_path=media_result_path,
        input_payload={"metric_snapshots": [], "comments": []},
        task_directory=task_directory,
        config=GatewayConfig(
            "http://127.0.0.1:9876/v1", "fixture-model", "responses",
            "fixture-secret", "2026-09-07T00:00:00Z",
        ),
        gateway_caller=lambda *_args, **_kwargs: GatewayResult(candidate, "evidence_clip", 24),
        ocr_engine=lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
    )

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    audit = json.loads(
        (task_directory / "model-output.normalization-audit.json").read_text(encoding="utf-8")
    )
    assert report["evidence"][0]["start_ms"] == 400
    assert audit["evidence_changes"] == [{
        "path": "evidence[0]",
        "source_id": "timeline_fixture_002",
        "before": {"start_ms": 350, "end_ms": 500},
        "after": {"start_ms": 400, "end_ms": 500},
    }]
