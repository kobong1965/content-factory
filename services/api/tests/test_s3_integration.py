from __future__ import annotations

import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from content_factory_api.s3_analysis import process_analysis
from content_factory_api.s3_settings import GatewayConfig
from content_factory_contracts import validate_or_raise

pytestmark = pytest.mark.skipif(os.environ.get("CONTENT_FACTORY_S3_INTEGRATION") != "1", reason="S3 integration disabled")


def _claim(text: str, evidence_id: str) -> dict:
    return {"text": text, "evidence_ids": [evidence_id], "confidence": 0.7, "is_inference": True}


def _semantic_from_context(context: dict) -> dict:
    evidence = []
    shots = []
    timeline = []
    structures = []
    emotions = []
    for index, source in enumerate(context["shots"], start=1):
        evidence_id = f"evidence_http_{index:03d}"
        evidence.append({
            "id": evidence_id, "claim": f"镜头 {index} 提供画面证据", "source_type": "keyframe",
            "source_id": source["keyframe_id"], "start_ms": source["start_ms"], "end_ms": source["end_ms"],
            "confidence": 0.8, "is_inference": False,
        })
        shots.append({
            "id": source["id"], "start_ms": source["start_ms"], "end_ms": source["end_ms"],
            "shot_size": "full", "camera_movement": "static", "composition": "商品居中展示",
            "performer_action": "展示裤型", "product_exposure": "裤型正面", "transcript": "",
            "subtitle": " ".join(item["text"] for item in source["ocr_lines"]), "keyframe_refs": [source["keyframe_id"]],
        })
        timeline.append({
            "id": f"timeline_http_{index:03d}", "second_index": source["start_ms"] // 1000,
            "start_ms": source["start_ms"], "end_ms": source["end_ms"], "visual_event": "展示男装裤型",
            "transcript": "", "subtitle": shots[-1]["subtitle"], "rhythm": "medium", "metric_refs": [],
        })
        structures.append({
            "id": f"structure_http_{index:03d}", "order": index, "label": f"结构 {index}",
            "description": "展示画面", "start_ms": source["start_ms"], "end_ms": source["end_ms"],
            "evidence_ids": [evidence_id],
        })
        emotions.append({
            "id": f"emotion_http_{index:03d}", "start_ms": source["start_ms"], "end_ms": source["end_ms"],
            "intensity": 50, "label": "平稳", "reason": "工程验证", "evidence_ids": [evidence_id],
        })
    first = evidence[0]["id"]
    dimensions = [
        ("hook_strength", 0.2), ("proof_strength", 0.2), ("rhythm_efficiency", 0.15),
        ("product_fit", 0.15), ("consumer_psychology", 0.1), ("interaction_design", 0.1), ("data_result", 0.1),
    ]
    return {
        "summary": {
            "video_type": "product_seeding", "target_audience": _claim("男装裤型用户", first),
            "main_promise": _claim("展示裤型", first), "overall_conclusion": _claim("工程网关返回可追溯分析", first),
            "hook_analysis": _claim("开场展示", first), "consumer_psychology": _claim("降低判断成本", first),
            "risks": [_claim("没有真实经营数据", first)],
        },
        "shots": shots, "timeline": timeline, "content_structure": structures, "emotion_curve": emotions,
        "comment_insights": [], "evidence": evidence,
        "scores": {"score_version": "1.0.0", "total_score": 50, "items": [
            {"dimension": dimension, "weight": weight, "score": 50, "evidence_ids": [first]}
            for dimension, weight in dimensions
        ]},
        "pattern_candidates": [{
            "id": "pattern_http_001", "name": "画面先行", "mechanism": "先展示商品再解释",
            "mechanism_key": "result_then_visual_proof", "reuse_mode": "reuse",
            "steps": [
                {"id": "pattern_step_http_001", "order": 1, "description": "展示商品", "evidence_ids": [first]},
                {"id": "pattern_step_http_002", "order": 2, "description": "补充说明", "evidence_ids": [first]},
            ],
            "necessary_conditions": ["商品清晰可见"], "failure_signals": ["画面遮挡"],
        }],
    }


class _GatewayHandler(BaseHTTPRequestHandler):
    captured = b""

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        type(self).captured = raw
        request = json.loads(raw)
        context_text = request["input"][0]["content"][0]["text"]
        semantic = _semantic_from_context(json.loads(context_text))
        response = json.dumps({
            "id": "resp_local_fixture",
            "status": "completed",
            "output_text": json.dumps(semantic, ensure_ascii=False),
        }, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args) -> None:
        return


def test_real_ocr_and_actual_http_gateway_end_to_end(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[3] / "output" / "s3" / "fixture" / "ocr-fixture.png"
    assert fixture.is_file()
    media_dir = tmp_path / "media-task" / "keyframes"
    media_dir.mkdir(parents=True)
    frame1 = media_dir / "shot-001.jpg"
    frame2 = media_dir / "shot-002.jpg"
    shutil.copy2(fixture, frame1); shutil.copy2(fixture, frame2)
    result = {
        "schema_version": "1.0.0", "fixture_data": True, "task_id": "media_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "source": {"original_name": "S3工程样片.mp4", "sha256": "b" * 64, "size_bytes": 100, "managed_original_path": str(tmp_path / "original.mp4")},
        "media": {"duration_ms": 2000, "width": 1200, "height": 600, "fps": 25, "video_codec": "h264", "audio_codec": None, "has_audio": False, "format_name": "mp4"},
        "artifacts": {"proxy_path": str(tmp_path / "proxy.mp4"), "audio_path": None, "transcript_path": None, "scene_manifest_path": str(tmp_path / "scenes.json")},
        "asr": {"status": "no_audio", "model_name": None, "language": None, "segment_count": 0},
        "shots": [
            {"id": "shot_001", "start_ms": 0, "end_ms": 1000, "scene_score": 0, "keyframe_path": str(frame1)},
            {"id": "shot_002", "start_ms": 1000, "end_ms": 2000, "scene_score": 20, "keyframe_path": str(frame2)},
        ],
        "timings_ms": {"total": 1}, "created_at": "2026-08-29T06:00:00Z",
    }
    media_result_path = media_dir.parent / "result.json"
    media_result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        artifacts = process_analysis(
            media_result_path=media_result_path, input_payload={"metric_snapshots": [], "comments": []},
            task_directory=tmp_path / "analysis-task",
            config=GatewayConfig(f"http://127.0.0.1:{server.server_port}/v1", "fixture-model", "responses", "fixture-secret", "2026-08-29T06:00:00Z"),
        )
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)

    report = json.loads(artifacts.report_path.read_text(encoding="utf-8"))
    ocr = json.loads(artifacts.ocr_result_path.read_text(encoding="utf-8"))
    validate_or_raise("analysis", report)
    validate_or_raise("ocr_result", ocr)
    assert any("XZ-2308" in line["text"] for frame in ocr["frames"] for line in frame["lines"])
    assert str(tmp_path).encode() not in _GatewayHandler.captured
    assert b"original.mp4" not in _GatewayHandler.captured
    assert report["processing"]["response_id"] == "resp_local_fixture"
