"""Seed an isolated S5 browser-QA workspace with accepted S3 and active S4 inputs."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--relay-port", type=int, required=True)
    arguments = parser.parse_args()
    root = Path(arguments.root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[1]
    fixtures = project / "packages" / "contracts" / "fixtures"
    os.environ["CONTENT_FACTORY_RUNTIME_ROOT"] = str(root / "runtime")
    os.environ["CONTENT_FACTORY_ANALYSIS_ROOT"] = str(root / "analysis")
    os.environ["CONTENT_FACTORY_S4_DATA_DIR"] = str(root / "s4")
    os.environ["CONTENT_FACTORY_S5_DATA_DIR"] = str(root / "s5")
    os.environ["CONTENT_FACTORY_S3_CONFIG_PATH"] = str(root / "gateway.json")

    from content_factory_api.s3 import get_analysis_queue, get_viral_skill_store
    from content_factory_api.s3_analysis import AnalysisArtifacts
    from content_factory_api.s3_settings import GatewaySettingsStore
    from content_factory_api.s3_skills import refresh_skill_candidates
    from content_factory_api.s4 import get_product_store

    analysis_queue = get_analysis_queue()
    analyses: list[dict] = []
    for index, marker in enumerate(("a", "b"), start=1):
        analysis = json.loads((fixtures / "analysis.valid.json").read_text(encoding="utf-8"))
        analysis.update({
            "fixture_data": False,
            "analysis_id": f"analysis_{marker * 32}",
            "video_id": f"video_{marker * 24}",
            "status": "accepted",
            "revision": 2,
            "review": {
                "reviewer": "QA 分析审核人",
                "reviewed_at": f"2026-09-07T08:0{index}:00Z",
                "note": "浏览器隔离验收，非真实经营数据",
            },
        })
        analysis["source"].update({
            "source_id": f"source_{marker * 24}",
            "source_uri": str(root / f"QA-主播长镜头-{marker.upper()}.mp4"),
        })
        analysis["processing"]["purpose"] = "analysis"
        analysis["pattern_candidates"][0].update({
            "name": "结果先行配合细节证明",
            "mechanism": "先展示上身结果，再以主播动作或同款细节画面补充可信证明。",
            "mechanism_key": "result_then_visual_proof",
            "reuse_mode": "reuse",
        })
        media = root / f"media-result-{marker}.json"
        media.write_text("{}", encoding="utf-8")
        analysis_task = analysis_queue.enqueue(
            media_task_id=f"media_{marker * 32}", media_result_path=media,
            workspace_path=root / "analysis", input_payload={"metric_snapshots": [], "comments": []}, fixture_data=False,
        )
        claimed = analysis_queue.claim_next()
        if claimed is None:
            raise RuntimeError("无法建立 QA 分析任务")
        task_dir = Path(claimed.workspace_path)
        task_dir.mkdir(parents=True, exist_ok=True)
        report_path = task_dir / "analysis-report.json"
        ocr_path = task_dir / "ocr-result.json"
        report_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        ocr_path.write_text("{}", encoding="utf-8")
        analysis_queue.complete(
            analysis_task.task_id,
            AnalysisArtifacts(analysis["analysis_id"], report_path, ocr_path),
            worker_id=claimed.worker_id or "",
        )
        analyses.append(analysis)

    skill_store = get_viral_skill_store()
    refresh_skill_candidates(skill_store, analysis_queue)
    candidate = skill_store.list_candidates()[0]
    skill = skill_store.approve_candidate(
        candidate["candidate_id"],
        expected_candidate_revision=candidate["revision"],
        expected_skill_revision=None,
        reviewer="QA 内容负责人",
        reuse_mode="reuse",
        name=candidate["suggested_name"],
        mechanism=candidate["suggested_mechanism"],
        note="仅用于隔离界面验收",
    )

    fixture_product = json.loads((fixtures / "product.valid.json").read_text(encoding="utf-8"))
    product_store = get_product_store()
    created = product_store.create(sku="QA-XZ-2308", name="QA 垂感直筒西裤", actor="QA 运营")
    changes = {
        key: deepcopy(fixture_product[key])
        for key in (
            "sources", "facts", "selling_point_fact_ids", "forbidden_expressions", "unprovable_claims",
            "brand_boundary_confirmed_by", "brand_boundary_confirmed_at", "shooting_constraints",
        )
    }
    saved = product_store.update(created["product_id"], expected_revision=1, changes=changes, actor="QA 运营")
    product = product_store.set_status(
        saved["product_id"], expected_revision=2, status="active", actor="QA 负责人",
    )

    settings = GatewaySettingsStore(root / "gateway.json")
    settings.save(
        base_url=f"http://127.0.0.1:{arguments.relay_port}/v1", model="qa-script-model",
        api_mode="responses", api_key="qa-fixture-secret",
    )
    print(json.dumps({
        "root": str(root), "analysis_ids": [item["analysis_id"] for item in analyses],
        "skill_id": skill["skill_id"], "product_id": product["product_id"],
        "config_path": str(root / "gateway.json"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
