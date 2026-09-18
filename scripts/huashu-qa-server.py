"""Isolated API for Huashu acceptance: genuine installed skill, simulated media/model.

Run only via verify-huashu-analysis.ps1. Does not open production data or secrets.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=18768)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.is_relative_to(Path("E:/Codex工作盘").resolve()) or args.port in {80, 443, 8766}:
        raise SystemExit("Only isolated E: storage and non-production ports are allowed")
    if (root / "runtime").exists():
        raise SystemExit("Refusing to overwrite an existing QA runtime")
    root.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    for name, directory in {
        "RUNTIME_ROOT": "runtime", "MEDIA_ROOT": "media", "MEDIA_TEMP_ROOT": "media-logs",
        "ANALYSIS_ROOT": "analysis", "S3_CONFIG_PATH": "gateway.json",
        **{f"S{stage}_DATA_DIR": f"s{stage}" for stage in range(4, 9)},
    }.items():
        os.environ[f"CONTENT_FACTORY_{name}"] = str(root / directory)
    sys.path.insert(0, str(repo / "services/api/tests"))
    from test_s3_segmented_analysis import _media_result, _merge_summary, _semantic_from_segment
    from content_factory_api import s2, s3, s3_queue
    from content_factory_api.main import app
    from content_factory_api.s3_analysis import process_analysis
    from content_factory_api.s3_analysis_method import DIMENSIONS, load_installed_method, method_readiness
    from content_factory_api.s3_gateway import GatewayResult
    from content_factory_api.s3_settings import GatewaySettingsStore
    import uvicorn

    snapshot = load_installed_method()
    if snapshot is None or method_readiness()["status"] != "ready":
        raise SystemExit("Install the pinned Huashu skill first; this test never downloads it")
    settings = GatewaySettingsStore(root / "gateway.json")
    settings.save(base_url=f"http://127.0.0.1:{args.port}/mock-never-called", model="qa-huashu-model",
                  api_mode="responses", api_key="qa-isolated-not-a-real-key")
    config = settings.load()
    assert config is not None
    media_path = _media_result(root, shot_count=3)
    media = json.loads(media_path.read_text(encoding="utf-8"))
    media_name = "QA-模拟视频-固定直播间-男装直筒裤-口播动作同步-长文件名验收.mp4"
    media_task = s2._queue().enqueue(source_path=root / media_name, workspace_path=root / "media", fixture_data=True)
    media["task_id"] = media_task.task_id
    media["source"]["original_name"] = media_name
    # Valid local placeholder frames, explicitly not evidence from a real garment.
    for shot in media["shots"]:
        frame = Path(shot["keyframe_path"]).with_suffix(".png")
        frame.write_bytes(base64.b64decode(s3._VISION_PROBE_DATA_URL.split(",", 1)[1]))
        shot["keyframe_path"] = str(frame)
    media_path.write_text(json.dumps(media, ensure_ascii=False), encoding="utf-8")
    s2._queue().claim_next()
    s2._queue().complete(media_task.task_id, media_path)
    s3.ocr_available = lambda: True
    calls: list[dict] = []

    def gateway(selected, **kwargs):
        assert selected.base_url == config.base_url and selected.model == "qa-huashu-model"
        context = json.loads(kwargs["context_json"])
        instructions = kwargs["developer_instructions"]
        huashu = "Huashu 七维方法参考" in instructions
        if huashu:
            assert snapshot["upstream_prompt"] in instructions
            assert all(d in instructions for d in DIMENSIONS)
            assert "固定直播间" in instructions and "算法因果" in instructions
        assert str(root) not in kwargs["context_json"]
        calls.append({"method": "huashu" if huashu else "legacy", "phase": context.get("summary_level", "segment"),
                      "prompt_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
                      "image_count": len(kwargs["keyframe_data_urls"]), "simulated_model": True})
        (root / "model-calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
        semantic = _merge_summary(context) if "structured_inputs" in context else _semantic_from_segment(context)
        semantic["summary"]["overall_conclusion"]["text"] = "QA 模拟结果：用于核对七维方法接入、保存与刷新，不是真实视频分析。"
        return GatewayResult(semantic, "qa-local-no-network", 100)

    def isolated_processing(**kwargs):
        return process_analysis(**kwargs, gateway_caller=gateway, ocr_engine=lambda _: None, segment_max_shots=1)

    s3_queue.process_analysis = isolated_processing
    queue = s3.get_analysis_queue()
    legacy = queue.enqueue(media_task_id=media_task.task_id, media_result_path=media_path,
                           workspace_path=root / "analysis", fixture_data=True, input_payload={})
    queue.run_pending(gateway_config=config, max_workers=1)
    assert queue.get(legacy.task_id).status == "succeeded"
    (root / "qa-manifest.json").write_text(json.dumps({
        "fixture_data": True, "media_task_id": media_task.task_id, "legacy_task_id": legacy.task_id,
        "method": {k: v for k, v in snapshot.items() if k not in {"upstream_prompt", "adapter_instructions"}},
        "validation": "Actual installed skill and application API; simulated OCR, media, model output.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
