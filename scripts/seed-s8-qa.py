"""Seed an isolated S8 browser QA workspace with approved outputs and feedback data."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path


def load_fixture(project: Path, name: str) -> dict:
    return json.loads((project / "packages" / "contracts" / "fixtures" / name).read_text(encoding="utf-8"))


def add_output(store, template: dict, variant_id: str, suffix: str) -> dict:
    output = deepcopy(template)
    output["output_id"] = f"render_output_s8_{suffix}"
    output["variant_id"] = variant_id
    output["render"]["task_id"] = f"render_task_s8_{suffix}"
    output["media"]["filename"] = f"S8-版本-{suffix}.mp4"
    output["media"]["sha256"] = suffix[0] * 64
    for key, value in list(output["resources"].items()):
        if isinstance(value, str):
            output["resources"][key] = f"render_{key}_s8_{suffix}"
    output_root = store.output_root / output["output_id"]
    output_root.mkdir(parents=True, exist_ok=True)
    resources = {}
    for key, reference in output["resources"].items():
        if key == "jianying_experimental":
            continue
        path = output_root / f"{key}.bin"
        path.write_bytes(b"S8 browser QA fixture")
        resources[reference] = (path, "application/octet-stream", path.name)
    store.create_output(output, resources=resources)
    return store.review_output(output["output_id"], decision="approved", reviewer="QA 成片审核", note="界面验收")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    arguments = parser.parse_args()
    root = Path(arguments.root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parents[1]

    from content_factory_api.s7_store import EditStore
    from content_factory_api.s8_learning import build_learning_report
    from content_factory_api.s8_store import FeedbackStore

    edit_store = EditStore(root / "s7")
    project, _ = edit_store.create_project(load_fixture(project_root, "edit-project.valid.json"), actor="QA 剪辑")
    project = edit_store.get_project(project["project_id"])
    outputs = []
    for output in edit_store.list_outputs(include_fixtures=True):
        if output["project_id"] != project["project_id"] or output["project_revision"] != project["revision"]:
            continue
        if output["status"] == "video_review":
            output = edit_store.review_output(
                output["output_id"], decision="approved", reviewer="QA 成片审核", note="S8 隔离验收准备",
            )
        if output["status"] == "approved":
            outputs.append(output)
    if len(outputs) < 2:
        template = load_fixture(project_root, "render-output.valid.json")
        existing_ids = {item["output_id"] for item in edit_store.list_outputs(include_fixtures=True)}
        for suffix, variant in zip(("aaa", "bbb", "ccc"), project["variants"], strict=True):
            if len(outputs) >= 2:
                break
            if f"render_output_s8_{suffix}" in existing_ids:
                continue
            outputs.append(add_output(edit_store, template, variant["id"], suffix))

    feedback = FeedbackStore(root / "s8")
    publications = feedback.list_publications(include_fixtures=True)
    if not publications:
        for index, output in enumerate(outputs[:2], start=1):
            publication, _ = feedback.create_publication(
                output=output, project=project,
                work_url=f"https://www.douyin.com/video/760000000000000010{index}",
                work_id=f"760000000000000010{index}", account_label="S8 工程验收账号",
                title=f"男装开场对比版 {index}", published_at="2026-08-30T01:00:00Z",
                actor="QA 运营", note="隔离样例，不计真实验收",
            )
            publications.append(publication)
        metrics = (
            {"views": 12800, "likes": 920, "comments": 74, "favorites": 188, "shares": 96, "product_clicks": 540, "orders": 31, "gmv_cents": 928000, "retention_3s": .64, "completion_rate": .31, "product_ctr": .0422, "conversion_rate": .0574},
            {"views": 11900, "likes": 710, "comments": 61, "favorites": 145, "shares": 86, "product_clicks": 369, "orders": 20, "gmv_cents": 598000, "retention_3s": .57, "completion_rate": .27, "product_ctr": .031, "conversion_rate": .0542},
        )
        snapshots = [
            feedback.add_snapshot(item["publication_id"], captured_at="2026-08-31T01:00:00Z", source="manual", confidence="confirmed", confirmed_by="QA 运营", metrics=metrics[index])
            for index, item in enumerate(publications)
        ]
        report = build_learning_report(
            product_id=project["product_id"], publications=publications, snapshots=snapshots,
            primary_metric="product_ctr", target_window_minutes=1440,
        )
        feedback.save_report(report)
        candidate = deepcopy(load_fixture(project_root, "metric-import-draft.valid.json")["candidates"][0])
        candidate["publication_id"] = publications[0]["publication_id"]
        candidate["captured_at"] = "2026-09-02T01:00:00Z"
        candidate["metrics"] = {"views": 18800, "likes": 1310, "completion_rate": .34}
        feedback.create_import_draft(
            import_kind="ocr", fixture_data=True, source_name="待核对-72小时截图.png", source_sha256="f" * 64,
            raw_text="播放量 18800 完播率 34%", candidates=[candidate], errors=[], created_by="QA 运营",
        )
    print(json.dumps({
        "root": str(root), "s7_root": str(root / "s7"), "s8_root": str(root / "s8"),
        "publication_ids": [item["publication_id"] for item in publications],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
