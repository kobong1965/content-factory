from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory_api.s3_analysis import AnalysisArtifacts, _canonical_json_sha256, process_analysis
from content_factory_api.s3_gateway import GatewayError, GatewayResult
from content_factory_api.s3_queue import AnalysisTaskQueue
from content_factory_api.s3_settings import GatewayConfig
from content_factory_contracts import validate_or_raise


CONFIG = GatewayConfig(
    "http://127.0.0.1:9999/v1",
    "fixture-model",
    "responses",
    "fixture-secret",
    "2026-09-06T00:00:00Z",
)


def _claim(text: str, evidence_id: str) -> dict:
    return {"text": text, "evidence_ids": [evidence_id], "confidence": 0.8, "is_inference": False}


def _semantic_from_segment(context: dict) -> dict:
    evidence = []
    shots = []
    timeline = []
    structures = []
    emotions = []
    for order, source in enumerate(context["shots"], start=1):
        suffix = source["id"].removeprefix("shot_")
        evidence_id = f"evidence_{suffix}"
        evidence.append({
            "id": evidence_id,
            "claim": "关键帧展示裤型",
            "source_type": "keyframe",
            "source_id": source["keyframe_id"],
            "start_ms": source["start_ms"],
            "end_ms": source["end_ms"],
            "confidence": 0.9,
            "is_inference": False,
        })
        shots.append({
            "id": source["id"],
            "start_ms": source["start_ms"],
            "end_ms": source["end_ms"],
            "shot_size": "full",
            "camera_movement": "static",
            "composition": "商品居中",
            "performer_action": "展示裤型",
            "product_exposure": "裤型正面",
            "transcript": "",
            "subtitle": "男装裤型",
            "keyframe_refs": [source["keyframe_id"]],
        })
        timeline.append({
            "id": f"timeline_{suffix}",
            "second_index": source["start_ms"] // 1000,
            "start_ms": source["start_ms"],
            "end_ms": source["end_ms"],
            "visual_event": "展示裤型",
            "transcript": "",
            "subtitle": "男装裤型",
            "rhythm": "medium",
            "metric_refs": [],
        })
        structures.append({
            "id": f"structure_{suffix}",
            "order": order,
            "label": "展示",
            "description": "展示裤型",
            "start_ms": source["start_ms"],
            "end_ms": source["end_ms"],
            "evidence_ids": [evidence_id],
        })
        emotions.append({
            "id": f"emotion_{suffix}",
            "start_ms": source["start_ms"],
            "end_ms": source["end_ms"],
            "intensity": 50,
            "label": "平稳",
            "reason": "商品展示",
            "evidence_ids": [evidence_id],
        })
    first = evidence[0]["id"]
    dimensions = [
        ("hook_strength", 0.2), ("proof_strength", 0.2), ("rhythm_efficiency", 0.15),
        ("product_fit", 0.15), ("consumer_psychology", 0.1),
        ("interaction_design", 0.1), ("data_result", 0.1),
    ]
    return {
        "summary": {
            "video_type": "product_seeding",
            "target_audience": _claim("男装用户", first),
            "main_promise": _claim("展示裤型", first),
            "overall_conclusion": _claim("画面可追溯", first),
            "hook_analysis": _claim("开场展示", first),
            "consumer_psychology": _claim("降低判断成本", first),
            "risks": [_claim("缺少经营数据", first)],
        },
        "shots": shots,
        "timeline": timeline,
        "content_structure": structures,
        "emotion_curve": emotions,
        "comment_insights": [],
        "evidence": evidence,
        "scores": {
            "score_version": "1.0.0",
            "total_score": 50,
            "items": [
                {"dimension": dimension, "weight": weight, "score": 50, "evidence_ids": [first]}
                for dimension, weight in dimensions
            ],
        },
        "pattern_candidates": [{
            "id": "pattern_segment_001",
            "name": "画面先行",
            "mechanism": "先展示商品",
            "mechanism_key": "result_then_visual_proof",
            "reuse_mode": "reuse",
            "steps": [
                {"id": "pattern_step_segment_001", "order": 1, "description": "展示", "evidence_ids": [first]},
                {"id": "pattern_step_segment_002", "order": 2, "description": "说明", "evidence_ids": [first]},
            ],
            "necessary_conditions": ["商品可见"],
            "failure_signals": ["画面遮挡"],
        }],
    }


def _merge_summary(context: dict) -> dict:
    inputs = context["structured_inputs"]
    merged = deepcopy(inputs[0])
    for candidate in inputs[1:]:
        for field in ("shots", "timeline", "content_structure", "emotion_curve", "evidence"):
            merged[field].extend(deepcopy(candidate[field]))
    merged["shots"].sort(key=lambda item: item["start_ms"])
    merged["timeline"].sort(key=lambda item: item["start_ms"])
    merged["content_structure"].sort(key=lambda item: item["start_ms"])
    for order, item in enumerate(merged["content_structure"], start=1):
        item["order"] = order
    merged["emotion_curve"].sort(key=lambda item: item["start_ms"])
    return merged


def _media_result(tmp_path: Path, *, shot_count: int = 3) -> Path:
    media_dir = tmp_path / "media" / "keyframes"
    media_dir.mkdir(parents=True)
    shots = []
    for index in range(shot_count):
        frame = media_dir / f"shot-{index + 1:03d}.jpg"
        frame.write_bytes(f"fixture-{index}".encode())
        shots.append({
            "id": f"shot_{index + 1:03d}",
            "start_ms": index * 1000,
            "end_ms": (index + 1) * 1000,
            "scene_score": 0,
            "keyframe_path": str(frame),
        })
    result = {
        "schema_version": "1.0.0",
        "fixture_data": True,
        "task_id": "media_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "source": {
            "original_name": "long.mp4", "sha256": "b" * 64, "size_bytes": 100,
            "managed_original_path": str(tmp_path / "original.mp4"),
        },
        "media": {
            "duration_ms": shot_count * 1000, "width": 360, "height": 640, "fps": 25,
            "video_codec": "h264", "audio_codec": None, "has_audio": False, "format_name": "mp4",
        },
        "artifacts": {
            "proxy_path": str(tmp_path / "proxy.mp4"), "audio_path": None, "transcript_path": None,
            "scene_manifest_path": str(tmp_path / "scenes.json"),
        },
        "asr": {"status": "no_audio", "model_name": None, "language": None, "segment_count": 0},
        "shots": shots,
        "timings_ms": {"total": 1},
        "created_at": "2026-09-06T00:00:00Z",
    }
    path = media_dir.parent / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return path


def _task(queue: AnalysisTaskQueue, media_result: Path, tmp_path: Path):
    return queue.enqueue(
        media_task_id="media_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        media_result_path=media_result,
        workspace_path=tmp_path / "analysis",
        input_payload={"metric_snapshots": [], "comments": []},
        fixture_data=True,
    )


def _processor(queue, gateway, ocr, *, segment_max_shots: int = 1):
    def run(task, callback) -> AnalysisArtifacts:
        return process_analysis(
            media_result_path=task.media_result_path,
            input_payload={"metric_snapshots": [], "comments": []},
            task_directory=task.workspace_path,
            config=CONFIG,
            progress=callback,
            gateway_caller=gateway,
            ocr_engine=ocr,
            task_id=task.task_id,
            task_worker_id=task.worker_id,
            checkpoint_store=queue,
            segment_max_shots=segment_max_shots,
        )

    return run


def test_summary_checkpoint_key_is_stable_across_python_hash_seeds() -> None:
    project_root = Path(__file__).resolve().parents[3]
    python_path = os.pathsep.join([
        str(project_root / "services" / "api" / "src"),
        str(project_root / "packages" / "contracts" / "python"),
        str(project_root / "workers" / "media" / "src"),
        os.environ.get("PYTHONPATH", ""),
    ])
    script = """
import json

from content_factory_api.s3_analysis import (
    _SEMANTIC_FIELDS,
    _summary_checkpoint_key,
    _summary_context,
)

payload = {
    "summary": {"overall_conclusion": "中文结论"},
    "shots": [],
    "timeline": [],
    "content_structure": [],
    "emotion_curve": [],
    "comment_insights": [],
    "evidence": [],
    "scores": {"total_score": 0},
    "pattern_candidates": [],
}
candidate = {field: payload[field] for field in set(_SEMANTIC_FIELDS)}
context_json = _summary_context(
    media_result={"media": {"duration_ms": 1_000}, "shots": []},
    metrics=[],
    comments=[],
    candidates=[candidate],
    level="final",
    expected_start_ms=0,
    expected_end_ms=1_000,
)
print(json.dumps({
    "semantic_fields": list(_SEMANTIC_FIELDS),
    "context_json": context_json,
    "checkpoint_key": _summary_checkpoint_key(
        level="final",
        input_keys=["segment-a", "segment-b"],
        context_json=context_json,
    ),
}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
"""

    outputs = []
    for hash_seed in ("1", "8675309"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONPATH"] = python_path
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        outputs.append(completed.stdout.strip())

    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0])["semantic_fields"] == [
        "summary",
        "shots",
        "timeline",
        "content_structure",
        "emotion_curve",
        "comment_insights",
        "evidence",
        "scores",
        "pattern_candidates",
    ]


def test_long_video_segments_checkpoint_and_final_summary(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    calls: list[int | str] = []

    def gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        if "shots" in context:
            calls.append(context["segment_index"])
            content = _semantic_from_segment(context)
        else:
            calls.append(context["summary_level"])
            content = _merge_summary(context)
        return GatewayResult(content, f"response_{len(calls)}", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert calls == [0, 1, 2, "final"]
    assert [item.status for item in queue.list_segments(task.task_id)] == ["succeeded"] * 3
    report = json.loads(Path(completed.result_path).read_text(encoding="utf-8"))
    validate_or_raise("analysis", report)
    assert [item["id"] for item in report["shots"]] == ["shot_001", "shot_002", "shot_003"]


def test_failed_segment_retry_never_calls_completed_segment_again(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    segment_calls: list[int] = []
    failed_once = False

    def gateway(_config, *, context_json, **_kwargs):
        nonlocal failed_once
        context = json.loads(context_json)
        if "shots" not in context:
            return GatewayResult(_merge_summary(context), "summary", 100)
        index = context["segment_index"]
        segment_calls.append(index)
        if index == 1 and not failed_once:
            failed_once = True
            raise GatewayError("temporary disconnect", retryable=True, diagnostic_code="connection_interrupted")
        return GatewayResult(_semantic_from_segment(context), f"segment_{index}", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert segment_calls == [0, 1, 1, 2]
    assert segment_calls.count(0) == 1
    assert completed.attempt_count == 1


def test_summary_failure_retries_summary_only(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    segment_calls: list[int] = []
    summary_calls = 0

    def gateway(_config, *, context_json, **_kwargs):
        nonlocal summary_calls
        context = json.loads(context_json)
        if "shots" in context:
            index = context["segment_index"]
            segment_calls.append(index)
            return GatewayResult(_semantic_from_segment(context), f"segment_{index}", 100)
        summary_calls += 1
        if summary_calls == 1:
            raise GatewayError("summary connection reset", retryable=True, diagnostic_code="connection_interrupted")
        return GatewayResult(_merge_summary(context), "summary", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    assert queue.get(task.task_id).status == "succeeded"  # type: ignore[union-attr]
    assert segment_calls == [0, 1, 2]
    assert summary_calls == 2


def test_separate_temporary_failures_use_each_segments_own_retry_budget(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    calls: list[int] = []
    failed_once: set[int] = set()

    def gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        if "shots" not in context:
            return GatewayResult(_merge_summary(context), "summary", 100)
        index = context["segment_index"]
        calls.append(index)
        if index in {0, 1} and index not in failed_once:
            failed_once.add(index)
            raise GatewayError("temporary", retryable=True, diagnostic_code="connection_interrupted")
        return GatewayResult(_semantic_from_segment(context), f"segment_{index}", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert calls == [0, 0, 1, 1, 2]
    assert [item.attempt_count for item in queue.list_segments(task.task_id)] == [2, 2, 1]


def test_summary_retry_limit_ends_in_failed_without_recalling_segments(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    segment_calls: list[int] = []
    summary_calls = 0

    def gateway(_config, *, context_json, **_kwargs):
        nonlocal summary_calls
        context = json.loads(context_json)
        if "shots" in context:
            index = context["segment_index"]
            segment_calls.append(index)
            return GatewayResult(_semantic_from_segment(context), f"segment_{index}", 100)
        summary_calls += 1
        raise GatewayError("summary unavailable", retryable=True, diagnostic_code="upstream_unavailable")

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert segment_calls == [0, 1, 2]
    assert summary_calls == failed.max_attempts


def test_process_restart_recovers_from_last_segment_checkpoint(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = AnalysisTaskQueue(database, retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    claimed_task = queue.claim_next()
    assert claimed_task is not None
    queue.prepare_segments(claimed_task.task_id, [
        {"segment_index": 0, "start_ms": 0, "end_ms": 1000, "segment_key": "checkpoint-0"},
        {"segment_index": 1, "start_ms": 1000, "end_ms": 2000, "segment_key": "checkpoint-1"},
    ])
    claimed_segment = queue.claim_segment(task.task_id, worker_id="worker-before-crash")
    assert claimed_segment is not None
    checkpoint = Path(task.workspace_path) / "segments" / "manual-checkpoint.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text('{"durable": true}', encoding="utf-8")
    queue.complete_segment(
        task.task_id,
        0,
        worker_id="worker-before-crash",
        result_path=checkpoint,
        provider_request_id="provider-call-before-crash",
    )

    reopened = AnalysisTaskQueue(database)
    assert reopened.recover_interrupted(force=True) == 1
    recovered = reopened.get(task.task_id)
    assert recovered is not None and recovered.status == "pending" and recovered.recovering is True
    assert reopened.list_segments(task.task_id)[0].status == "succeeded"
    assert reopened.list_segments(task.task_id)[0].provider_request_id == "provider-call-before-crash"


def test_real_restart_resume_does_not_recall_or_duplicate_completed_segments(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = AnalysisTaskQueue(database, retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    provider_calls: list[int | str] = []

    def crashing_gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        assert "shots" in context
        index = context["segment_index"]
        provider_calls.append(index)
        if index == 1:
            raise SystemExit("simulated worker termination")
        return GatewayResult(_semantic_from_segment(context), f"before_restart_{index}", 100)

    with pytest.raises(SystemExit, match="simulated worker termination"):
        queue.run_pending(
            gateway_config=CONFIG,
            processor=_processor(
                queue,
                crashing_gateway,
                lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
            ),
        )

    assert queue.get(task.task_id).status == "running"  # type: ignore[union-attr]
    assert queue.list_segments(task.task_id)[0].status == "succeeded"

    reopened = AnalysisTaskQueue(database, retry_base_seconds=0.01)
    assert reopened.recover_interrupted(force=True) == 1

    def resumed_gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        if "shots" in context:
            index = context["segment_index"]
            provider_calls.append(index)
            return GatewayResult(_semantic_from_segment(context), f"after_restart_{index}", 100)
        provider_calls.append(context["summary_level"])
        return GatewayResult(_merge_summary(context), "summary_once", 100)

    reopened.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            reopened,
            resumed_gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    completed = reopened.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert provider_calls == [0, 1, 1, 2, "final"]
    assert provider_calls.count(0) == 1
    report = json.loads(Path(completed.result_path).read_text(encoding="utf-8"))
    assert [shot["id"] for shot in report["shots"]] == ["shot_001", "shot_002", "shot_003"]


def test_user_cancel_during_provider_call_never_commits_the_returned_segment(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    provider_calls: list[int] = []

    def gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        assert "shots" in context
        provider_calls.append(context["segment_index"])
        queue.cancel(task.task_id)
        return GatewayResult(_semantic_from_segment(context), "late-provider-response", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    cancelled = queue.get(task.task_id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert provider_calls == [0]
    assert all(item.status == "cancelled" for item in queue.list_segments(task.task_id))
    assert not (Path(task.workspace_path) / "segments" / "segment-0000.json").exists()


def test_invalid_segment_timeline_is_not_checkpointed_and_provider_is_recalled(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    segment_calls: list[int] = []

    def gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        if "shots" not in context:
            return GatewayResult(_merge_summary(context), "summary", 100)
        index = context["segment_index"]
        segment_calls.append(index)
        content = _semantic_from_segment(context)
        if index == 0 and segment_calls.count(0) == 1:
            content["timeline"][0]["start_ms"] = 900
            content["timeline"][0]["second_index"] = 0
        return GatewayResult(content, f"segment_{index}_{segment_calls.count(index)}", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert segment_calls == [0, 0, 1, 2]
    assert queue.list_segments(task.task_id)[0].attempt_count == 2
    rejected = list((Path(task.workspace_path) / "diagnostics" / "rejected-checkpoints").glob("*.json"))
    assert len(rejected) == 1


def test_invalid_final_summary_is_regenerated_without_recalling_segments(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    segment_calls: list[int] = []
    summary_calls = 0

    def gateway(_config, *, context_json, **_kwargs):
        nonlocal summary_calls
        context = json.loads(context_json)
        if "shots" in context:
            index = context["segment_index"]
            segment_calls.append(index)
            return GatewayResult(_semantic_from_segment(context), f"segment_{index}", 100)
        summary_calls += 1
        content = _merge_summary(context)
        if summary_calls == 1:
            content["timeline"][0]["start_ms"] = 900
        return GatewayResult(content, f"summary_{summary_calls}", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert segment_calls == [0, 1, 2]
    assert summary_calls == 2
    rejected = list((Path(task.workspace_path) / "diagnostics" / "rejected-checkpoints").glob("*.json"))
    assert len(rejected) == 1


def test_single_segment_final_contract_failure_revokes_checkpoint_and_recalls_provider(
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    segment_calls = 0

    def gateway(_config, *, context_json, **_kwargs):
        nonlocal segment_calls
        context = json.loads(context_json)
        assert "shots" in context
        segment_calls += 1
        content = _semantic_from_segment(context)
        if segment_calls == 1:
            # JSON Schema allows this integer, but the complete analysis
            # contract rejects the broken all-shot continuity.
            content["shots"][0]["start_ms"] = 100
        return GatewayResult(content, f"segment_{segment_calls}", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
            segment_max_shots=8,
        ),
    )

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert segment_calls == 2
    assert queue.list_segments(task.task_id)[0].attempt_count == 2
    rejected = list((Path(task.workspace_path) / "diagnostics" / "rejected-checkpoints").glob("*.json"))
    assert len(rejected) == 1


def test_final_contract_failure_discards_only_final_summary_checkpoint(
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    segment_calls: list[int] = []
    summary_calls = 0

    def gateway(_config, *, context_json, **_kwargs):
        nonlocal summary_calls
        context = json.loads(context_json)
        if "shots" in context:
            index = context["segment_index"]
            segment_calls.append(index)
            return GatewayResult(_semantic_from_segment(context), f"segment_{index}", 100)
        summary_calls += 1
        content = _merge_summary(context)
        if summary_calls == 1:
            content["shots"][0]["start_ms"] = 100
        return GatewayResult(content, f"summary_{summary_calls}", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert segment_calls == [0, 1, 2]
    assert summary_calls == 2
    rejected = list((Path(task.workspace_path) / "diagnostics" / "rejected-checkpoints").glob("*.json"))
    assert len(rejected) == 1


def test_malformed_persisted_checkpoint_is_revoked_and_provider_recalled(
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    calls: list[str] = []

    def gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        if "shots" in context:
            calls.append(f"segment-{context['segment_index']}")
            return GatewayResult(
                _semantic_from_segment(context),
                f"segment_{context['segment_index']}_{len(calls)}",
                100,
            )
        calls.append("summary")
        return GatewayResult(_merge_summary(context), f"summary_{len(calls)}", 100)

    processor = _processor(
        queue,
        gateway,
        lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
    )
    queue.run_pending(gateway_config=CONFIG, processor=processor)
    assert queue.get(task.task_id).status == "succeeded"  # type: ignore[union-attr]

    persisted_segment = queue.list_segments(task.task_id)[0]
    assert persisted_segment.result_path is not None
    checkpoint = Path(persisted_segment.result_path)
    checkpoint.write_text("{broken", encoding="utf-8")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed' WHERE task_id=?",
            (task.task_id,),
        )
    queue.retry(task.task_id)
    calls.clear()

    queue.run_pending(gateway_config=CONFIG, processor=processor)

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert calls == ["segment-0"]
    assert queue.list_segments(task.task_id)[0].attempt_count == 2
    quarantined = list(
        (Path(task.workspace_path) / "diagnostics" / "rejected-checkpoints").glob("checkpoint-*")
    )
    assert quarantined


def test_chapter_context_exposes_exact_range_and_only_its_shots(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path, shot_count=7), tmp_path)
    chapter_contexts: list[dict] = []

    def gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        if "shots" in context:
            return GatewayResult(
                _semantic_from_segment(context),
                f"segment_{context['segment_index']}",
                100,
            )
        if context["summary_level"] == "chapter":
            chapter_contexts.append(context)
        return GatewayResult(
            _merge_summary(context),
            f"{context['summary_level']}_{context.get('group_index', 0)}",
            100,
        )

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        ),
    )

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert [item["summary_range"] for item in chapter_contexts] == [
        {"start_ms": 0, "end_ms": 6000},
        {"start_ms": 6000, "end_ms": 7000},
    ]
    assert [[shot["id"] for shot in item["all_shots"]] for item in chapter_contexts] == [
        [f"shot_{index:03d}" for index in range(1, 7)],
        ["shot_007"],
    ]


def test_database_persisted_repairable_checkpoint_is_reused_without_provider_call(
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    initial_calls = 0

    def initial_gateway(_config, *, context_json, **_kwargs):
        nonlocal initial_calls
        initial_calls += 1
        context = json.loads(context_json)
        assert "shots" in context
        content = _semantic_from_segment(context)
        content["timeline"][1]["start_ms"] = 900
        content["timeline"][1]["second_index"] = 0
        return GatewayResult(content, "legacy_repairable_provider", 100)

    processor = _processor(
        queue,
        initial_gateway,
        lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
        segment_max_shots=8,
    )
    queue.run_pending(gateway_config=CONFIG, processor=processor)
    assert queue.get(task.task_id).status == "succeeded"  # type: ignore[union-attr]
    assert initial_calls == 1
    before = queue.list_segments(task.task_id)[0]
    assert before.status == "succeeded" and before.attempt_count == 1

    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed' WHERE task_id=?",
            (task.task_id,),
        )
    queue.retry(task.task_id)

    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("repairable persisted checkpoint must not call the provider")

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            provider_must_not_run,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
            segment_max_shots=8,
        ),
    )

    completed = queue.get(task.task_id)
    after = queue.list_segments(task.task_id)[0]
    assert completed is not None and completed.status == "succeeded"
    assert after.attempt_count == before.attempt_count
    assert after.provider_request_id == "legacy_repairable_provider"
    report = json.loads(Path(completed.result_path).read_text(encoding="utf-8"))
    validate_or_raise("analysis", report)
    audit = json.loads(
        Path(after.result_path).with_name(
            f"{Path(after.result_path).stem}.normalization-audit.json"
        ).read_text(encoding="utf-8")
    )
    assert audit["decision"] == "repaired"
    assert audit["total_adjusted_ms"] == 100


def test_rejected_audit_written_before_crash_revokes_persisted_checkpoint(
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)

    def initial_gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        return GatewayResult(_semantic_from_segment(context), "provider-before-rejection", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            initial_gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
            segment_max_shots=8,
        ),
    )
    segment = queue.list_segments(task.task_id)[0]
    assert segment.result_path is not None
    checkpoint = Path(segment.result_path)
    raw_content = json.loads(checkpoint.read_text(encoding="utf-8"))["content"]
    audit_path = checkpoint.with_name(f"{checkpoint.stem}.normalization-audit.json")
    audit_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "policy_version": "analysis-contract-v1",
        "decision": "rejected",
        "reason": "simulated_crash_before_db_revoke",
        "raw_sha256": _canonical_json_sha256(raw_content),
    }), encoding="utf-8")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed' WHERE task_id=?",
            (task.task_id,),
        )
    queue.retry(task.task_id)
    provider_calls = 0

    def replacement_gateway(_config, *, context_json, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return GatewayResult(_semantic_from_segment(json.loads(context_json)), "provider-after-rejection", 100)

    queue.run_pending(
        gateway_config=CONFIG,
        processor=_processor(
            queue,
            replacement_gateway,
            lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
            segment_max_shots=8,
        ),
    )

    completed = queue.get(task.task_id)
    refreshed = queue.list_segments(task.task_id)[0]
    assert completed is not None and completed.status == "succeeded"
    assert provider_calls == 1
    assert refreshed.attempt_count == 2
    assert refreshed.provider_request_id == "provider-after-rejection"


def test_malformed_final_summary_is_quarantined_before_regeneration(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "queue.sqlite3", retry_base_seconds=0.01)
    task = _task(queue, _media_result(tmp_path), tmp_path)
    calls: list[str] = []

    def gateway(_config, *, context_json, **_kwargs):
        context = json.loads(context_json)
        if "shots" in context:
            calls.append(f"segment-{context['segment_index']}")
            return GatewayResult(_semantic_from_segment(context), calls[-1], 100)
        calls.append("summary")
        return GatewayResult(_merge_summary(context), f"summary-{calls.count('summary')}", 100)

    processor = _processor(
        queue,
        gateway,
        lambda _path: SimpleNamespace(boxes=[], txts=[], scores=[]),
    )
    queue.run_pending(gateway_config=CONFIG, processor=processor)
    assert queue.get(task.task_id).status == "succeeded"  # type: ignore[union-attr]
    summary = Path(task.workspace_path) / "summaries" / "final.json"
    summary.write_text("{broken", encoding="utf-8")
    with sqlite3.connect(queue.database_path) as connection:
        connection.execute(
            "UPDATE analysis_tasks SET status='failed', current_step='failed' WHERE task_id=?",
            (task.task_id,),
        )
    queue.retry(task.task_id)
    calls.clear()

    queue.run_pending(gateway_config=CONFIG, processor=processor)

    assert queue.get(task.task_id).status == "succeeded"  # type: ignore[union-attr]
    assert calls == ["summary"]
    quarantined = list(
        (Path(task.workspace_path) / "diagnostics" / "rejected-checkpoints").glob("checkpoint-*.invalid")
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{broken"
