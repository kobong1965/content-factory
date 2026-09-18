from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from content_factory_api.s3_analysis import AnalysisArtifacts
from content_factory_api.s3_queue import AnalysisTaskQueue
from content_factory_contracts import validate_or_raise


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = (ROOT / "output" / "s3" / "ui-qa").resolve()
RUNTIME = OUTPUT_ROOT / "runtime"
ANALYSIS = OUTPUT_ROOT / "analysis"


def main() -> None:
    source_runtime = ROOT / "output" / "s2" / "api-acceptance" / "runtime"
    source_database = source_runtime / "s2-tasks.sqlite3"
    media_results = sorted((ROOT / "output" / "s2" / "api-acceptance" / "media" / "tasks").glob("*/result.json"))
    if not source_database.is_file() or not media_results:
        raise SystemExit("请先运行 pnpm verify:s2 生成 UI 验收数据")
    if not RUNTIME.resolve().is_relative_to(OUTPUT_ROOT):
        raise SystemExit("UI QA 运行目录越界")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    destination_database = RUNTIME / "s2-tasks.sqlite3"
    destination_database.unlink(missing_ok=True)
    with sqlite3.connect(source_database) as source_connection:
        with sqlite3.connect(destination_database) as destination_connection:
            source_connection.backup(destination_connection)
    analysis_database = RUNTIME / "s3-tasks.sqlite3"
    analysis_database.unlink(missing_ok=True)

    media_result_path = media_results[-1]
    media_result = json.loads(media_result_path.read_text(encoding="utf-8"))
    queue = AnalysisTaskQueue(analysis_database)
    queued = queue.enqueue(
        media_task_id=media_result["task_id"], media_result_path=media_result_path,
        workspace_path=ANALYSIS, input_payload={"metric_snapshots": [], "comments": []}, fixture_data=True,
    )
    claimed = queue.claim_next()
    if claimed is None or claimed.task_id != queued.task_id:
        raise SystemExit("无法建立 S3 UI 验收任务")

    task_dir = Path(claimed.workspace_path)
    task_dir.mkdir(parents=True, exist_ok=True)
    fixture_image = ROOT / "output" / "s3" / "fixture" / "ocr-fixture.png"
    frame_directory = task_dir / "frames"
    frame_directory.mkdir(parents=True, exist_ok=True)
    frame1 = frame_directory / "frame-001.png"
    frame2 = frame_directory / "frame-002.png"
    shutil.copy2(fixture_image, frame1); shutil.copy2(fixture_image, frame2)

    report = json.loads((ROOT / "packages" / "contracts" / "fixtures" / "analysis.valid.json").read_text(encoding="utf-8"))
    report["keyframes"][0]["local_path"] = str(frame1)
    report["keyframes"][1]["local_path"] = str(frame2)
    validate_or_raise("analysis", report)
    report_path = task_dir / "analysis-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ocr_path = task_dir / "ocr-result.json"
    shutil.copy2(ROOT / "packages" / "contracts" / "fixtures" / "ocr-result.valid.json", ocr_path)
    queue.complete(queued.task_id, AnalysisArtifacts(report["analysis_id"], report_path, ocr_path))
    print(json.dumps({"runtime": str(RUNTIME), "analysis": str(ANALYSIS), "task_id": queued.task_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
