"""Huashu integration tests use synthetic skill text, never a remote model or user data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory_api import s3, s3_analysis_method as method
from content_factory_api.s3_analysis import (
    AnalysisProcessingError, _gateway_arguments, plan_analysis_segments, process_analysis,
)
from content_factory_api.s3_gateway import GatewayError, GatewayResult
from content_factory_api.s3_queue import AnalysisTaskQueue
from test_s3_api import _configure
from test_s3_segmented_analysis import CONFIG, _media_result, _merge_summary, _semantic_from_segment


@pytest.fixture
def installed_method(monkeypatch, tmp_path):
    # Original test fixture, not redistributed third-party skill content.
    prompt = "测试方法：" + "、".join(method.DIMENSIONS)
    source = f"# 测试 Skill\n**爆款分析Prompt**\n```\n{prompt}\n```\n不会执行的其他步骤"
    directory = tmp_path / "installed-skill"
    directory.mkdir()
    (directory / "SKILL.md").write_bytes(source.encode("utf-8"))
    monkeypatch.setenv("CONTENT_FACTORY_HUASHU_SKILL_DIR", str(directory))
    monkeypatch.setattr(method, "SOURCE_SHA256", hashlib.sha256(source.encode()).hexdigest())
    monkeypatch.setattr(method, "UPSTREAM_PROMPT_SHA256", hashlib.sha256(prompt.encode()).hexdigest())
    return directory


def test_installed_method_loads_only_reviewed_analysis_and_freezes_it(installed_method):
    snapshot = method.load_installed_method()
    assert snapshot["id"] == "huashu-douyin-script"
    assert snapshot["source_commit"] == "49a55ba8a975ebda6bb55ea5ca4388942e3f6f18"
    assert "不会执行的其他步骤" not in json.dumps(snapshot, ensure_ascii=False)
    assert all(dimension in snapshot["upstream_prompt"] for dimension in method.DIMENSIONS)
    (installed_method / "SKILL.md").unlink()
    assert method.validate_method_snapshot(snapshot) == snapshot


def test_method_missing_and_changed_are_reported_truthfully(installed_method):
    path = installed_method / "SKILL.md"
    path.write_text("被替换的方法", encoding="utf-8")
    with pytest.raises(method.AnalysisMethodError, match="版本校验"):
        method.load_installed_method()
    assert method.method_readiness()["status"] == "invalid"
    path.unlink()
    assert method.load_installed_method() is None
    assert method.method_readiness()["status"] == "not_installed"


def test_changed_snapshot_is_rejected_even_with_recomputed_digest(installed_method):
    snapshot = method.load_installed_method()
    snapshot["adapter_instructions"] = "绕过证据验证"
    snapshot["instructions_sha256"] = method.instructions_digest(snapshot)
    with pytest.raises(method.AnalysisMethodError):
        method.validate_method_snapshot(snapshot)


def test_gateway_receives_actual_method_without_replacing_provider_or_schema(installed_method):
    snapshot = method.load_installed_method()
    args = _gateway_arguments(context_json="{}", keyframe_data_urls=[], purpose="analysis",
                              schema_name="test", analysis_method=snapshot)
    instructions = args["developer_instructions"]
    assert all(dimension in instructions for dimension in method.DIMENSIONS)
    for boundary in ("固定直播间", "连续长镜头", "原话", "时间码", "证据 ID", "算法因果", "JSON Schema", "不确定"):
        assert boundary in instructions
    assert "不会执行的其他步骤" not in instructions
    assert "google-genai" not in instructions
    assert set(args) == {"context_json", "keyframe_data_urls", "output_schema", "developer_instructions"}
    old = _gateway_arguments(context_json="{}", keyframe_data_urls=[], purpose="video_review", schema_name="test")
    review = _gateway_arguments(context_json="{}", keyframe_data_urls=[], purpose="video_review",
                                schema_name="test", analysis_method=snapshot)
    assert review == old


def test_api_freezes_method_only_for_analysis_and_does_not_expose_prompt(monkeypatch, tmp_path, installed_method):
    client = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(s3, "ocr_available", lambda: True)
    monkeypatch.setattr(s3, "wake_analysis_worker", lambda: None)
    media_path = _media_result(tmp_path)
    media_task = SimpleNamespace(task_id="media_" + "b" * 32, status="completed",
                                 result_path=str(media_path), fixture_data=True)
    monkeypatch.setattr(s3, "media_queue", lambda: SimpleNamespace(get=lambda _: media_task))
    for purpose in ("analysis", "video_review"):
        response = client.post("/s3/analyses", json={"media_task_id": media_task.task_id, "model_purpose": purpose})
        assert response.status_code == 202, response.text
        task = s3._queue().get(response.json()["task_id"])
        payload = json.loads((Path(task.workspace_path) / "input.json").read_text(encoding="utf-8"))
        assert ("_analysis_method_snapshot" in payload) == (purpose == "analysis")
        assert payload["_gateway_model_snapshot"]["model"] == "fixture-model"
    public = client.get("/s3/readiness").json()["analysis_method"]
    assert public["status"] == "ready"
    assert public["id"] == "huashu-douyin-script"
    assert "upstream_prompt" not in public
    assert str(installed_method) not in json.dumps(public)
    before = len(s3._queue().list())
    (installed_method / "SKILL.md").write_text("损坏", encoding="utf-8")
    response = client.post("/s3/analyses", json={"media_task_id": media_task.task_id})
    assert response.status_code == 409
    assert len(s3._queue().list()) == before


def test_old_tasks_keep_identity_but_new_method_has_distinct_checkpoints(installed_method, tmp_path):
    media_path = _media_result(tmp_path)
    media = json.loads(media_path.read_text(encoding="utf-8"))
    legacy = plan_analysis_segments(media, CONFIG)
    explicit_legacy = plan_analysis_segments(media, CONFIG, analysis_method=None)
    new = plan_analysis_segments(media, CONFIG, analysis_method=method.load_installed_method())
    assert legacy == explicit_legacy
    assert legacy[0].segment_key != new[0].segment_key
    queue = AnalysisTaskQueue(tmp_path / "queue.db")
    def enqueue(payload):
        return queue.enqueue(media_task_id=media["task_id"], media_result_path=media_path,
                             workspace_path=tmp_path / "tasks", input_payload=payload, fixture_data=True)
    old_task = enqueue({})
    new_task = enqueue({"_analysis_method_snapshot": method.load_installed_method()})
    assert old_task.task_id != new_task.task_id


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_frozen_method_survives_retry_and_all_summary_levels(installed_method, tmp_path, api_mode):
    from dataclasses import replace
    snapshot = method.load_installed_method()
    media_path = _media_result(tmp_path, shot_count=7)
    task_dir = tmp_path / "analysis"
    payload = {"_analysis_method_snapshot": snapshot}
    calls = []
    fail = True
    def gateway(config, **kwargs):
        nonlocal fail
        assert config.api_mode == api_mode
        assert config.model == CONFIG.model
        assert all(d in kwargs["developer_instructions"] for d in method.DIMENSIONS)
        context = json.loads(kwargs["context_json"])
        assert str(tmp_path) not in kwargs["context_json"]
        phase = context.get("phase", "segment")
        calls.append((phase, context.get("segment", {}).get("start_ms")))
        if len(calls) == 2 and fail:
            fail = False
            raise GatewayError("模拟断线", retryable=True)
        output = _merge_summary(context) if "structured_inputs" in context else _semantic_from_segment(context)
        return GatewayResult(output, "fixture-response", 123)
    def run():
        return process_analysis(media_result_path=media_path, input_payload=payload, task_directory=task_dir,
                                config=replace(CONFIG, api_mode=api_mode), gateway_caller=gateway,
                                ocr_engine=lambda _: None, segment_max_shots=1)
    with pytest.raises((AnalysisProcessingError, GatewayError)):
        run()
    checkpoints = list((task_dir / "segments").glob("*.json"))
    assert checkpoints
    before = {str(p): p.read_bytes() for p in checkpoints if "audit" not in p.name}
    (installed_method / "SKILL.md").unlink()
    result = run()
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["processing"]["prompt_version"] == method.PROMPT_VERSION
    assert len(report["shots"]) == 7
    assert report["timeline"][-1]["end_ms"] == 7000
    assert all(Path(p).read_bytes() == content for p, content in before.items())
    audit = json.loads((task_dir / "analysis-method.json").read_text(encoding="utf-8"))
    assert audit["instructions_sha256"] == snapshot["instructions_sha256"]
    # 1 successful + 1 interrupted + 6 resumed segments + 2 chapters + 1 final summary.
    assert len(calls) == 11


def test_invalid_snapshot_stops_before_ocr_or_remote_request(installed_method, tmp_path):
    snapshot = method.load_installed_method()
    snapshot["instructions_sha256"] = "0" * 64
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid snapshot must not perform OCR or model requests")
    with pytest.raises(AnalysisProcessingError, match="方法快照"):
        process_analysis(media_result_path="missing.json", input_payload={"_analysis_method_snapshot": snapshot},
                         task_directory=tmp_path / "bad", config=CONFIG,
                         gateway_caller=forbidden, ocr_engine=forbidden)


@pytest.mark.parametrize("crash_index", [0, 1])
def test_default_worker_recovers_frozen_method_before_and_after_commit(monkeypatch, installed_method, tmp_path, crash_index):
    from content_factory_api import s3_queue
    media_path = _media_result(tmp_path)
    database = tmp_path / "persistent.sqlite3"
    queue = AnalysisTaskQueue(database, retry_base_seconds=0.01)
    task = queue.enqueue(media_task_id="media_" + "b" * 32, media_result_path=media_path,
                         workspace_path=tmp_path / "tasks", fixture_data=True,
                         input_payload={"_gateway_purpose": "analysis",
                                        "_gateway_model_snapshot": CONFIG.execution_snapshot(),
                                        "_analysis_method_snapshot": method.load_installed_method()})
    input_bytes = Path(task.input_path).read_bytes()
    calls = []
    interrupted = False
    def gateway(_config, **kwargs):
        nonlocal interrupted
        assert "固定直播间" in kwargs["developer_instructions"]
        assert "测试方法" in kwargs["developer_instructions"]
        context = json.loads(kwargs["context_json"])
        index = context.get("segment_index", "final")
        calls.append(index)
        if index == crash_index and not interrupted:
            interrupted = True
            raise SystemExit("simulated process termination")
        output = _merge_summary(context) if "structured_inputs" in context else _semantic_from_segment(context)
        return GatewayResult(output, "fixture-recovery", 100)
    def isolated_processing(**kwargs):
        return process_analysis(**kwargs, gateway_caller=gateway, ocr_engine=lambda _: None, segment_max_shots=1)
    monkeypatch.setattr(s3_queue, "process_analysis", isolated_processing)
    with pytest.raises(SystemExit, match="simulated process termination"):
        queue.run_pending(gateway_config=CONFIG, max_workers=1)
    assert queue.get(task.task_id).status == "running"
    assert sum(segment.status == "succeeded" for segment in queue.list_segments(task.task_id)) == crash_index
    (installed_method / "SKILL.md").unlink()
    restarted = AnalysisTaskQueue(database, retry_base_seconds=0.01)
    assert restarted.recover_interrupted(force=True) == 1
    restarted.run_pending(gateway_config=CONFIG, max_workers=1)
    completed = restarted.get(task.task_id)
    assert completed.status == "succeeded"
    assert Path(completed.input_path).read_bytes() == input_bytes
    report = json.loads(Path(completed.result_path).read_text(encoding="utf-8"))
    assert report["processing"]["prompt_version"] == method.PROMPT_VERSION
    assert len(report["shots"]) == 3
    assert calls == ([0, 0, 1, 2, "final"] if crash_index == 0 else [0, 1, 1, 2, "final"])
