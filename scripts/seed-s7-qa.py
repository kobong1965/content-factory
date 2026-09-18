"""Seed an isolated S7 browser/native QA workspace with one real local render."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path


def load_fixture(project: Path, name: str) -> dict:
    return json.loads((project / "packages" / "contracts" / "fixtures" / name).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--video", required=True)
    arguments = parser.parse_args()
    root = Path(arguments.root).expanduser().resolve()
    video = Path(arguments.video).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[1]
    root.mkdir(parents=True, exist_ok=True)

    from content_factory_api.s6_matching import build_shooting_task
    from content_factory_api.s6_store import MaterialStore
    from content_factory_api.s7_projects import build_edit_project
    from content_factory_api.s7_queue import RenderQueue
    from content_factory_api.s7_renderer import render_project
    from content_factory_api.s7_store import EditStore
    from content_factory_media.pipeline import MediaPipeline
    from content_factory_media.tools import write_json_atomic

    media_root = root / "s6" / "media"
    result_path = MediaPipeline(asr_model_path=project_root / ".models" / "ggml-base.bin").process(
        video, media_root, "media_77777777777777777777777777777777",
        fixture_data=True, original_name="S7 竖屏剪辑验收.mp4",
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    duration = result["media"]["duration_ms"]
    material = load_fixture(project_root, "material.valid.json")
    material["file"].update({
        "original_name": result["source"]["original_name"], "sha256": result["source"]["sha256"],
        "size_bytes": result["source"]["size_bytes"], "duration_ms": duration,
        "width": result["media"]["width"], "height": result["media"]["height"], "fps": result["media"]["fps"],
        "has_audio": result["media"]["has_audio"],
    })
    material["processing"].update({
        "media_task_id": result["task_id"], "asr_status": result["asr"]["status"],
        "recognition_status": "not_configured", "provider": None, "model": None, "recognized_at": None,
    })
    template = material["clips"][0]
    boundaries = [0, duration // 3, (duration * 2) // 3, duration]
    material["clips"] = []
    for index in range(3):
        clip = deepcopy(template)
        clip.update({
            "id": f"clip_demo_00{index + 1}", "source_shot_id": f"shot_s7_{index + 1:03d}", "order": index + 1,
            "start_ms": boundaries[index], "end_ms": boundaries[index + 1],
            "keyframe_ref": f"keyframe_s7_{index + 1:03d}", "purpose_tags": ["broll"],
        })
        material["clips"].append(clip)

    material_store = MaterialStore(root / "s6" / "materials.sqlite3")
    material_store.create(material, media_result_path=result_path, actor="QA 摄影")
    script = load_fixture(project_root, "script.valid.json")
    for index, version in enumerate(script["versions"]):
        shot = version["shots"][0]
        try:
            material_store.confirm_match(
                script_id=script["script_id"], script_shot_id=shot["id"],
                suggestion={
                    "material_id": material["material_id"], "clip_id": material["clips"][index]["id"],
                    "score": 92, "repeat_risk": "low", "reason": "S7 界面验收匹配",
                }, actor="QA 编导",
            )
        except Exception as exc:
            if "已有素材" not in str(exc):
                raise
    edit_project = build_edit_project(script, build_shooting_task(material_store, script), material_store)
    edit_store = EditStore(root / "s7")
    edit_project, _ = edit_store.create_project(edit_project, actor="QA 剪辑")
    existing_outputs = edit_store.list_outputs(include_fixtures=True)
    if existing_outputs:
        output_id = existing_outputs[0]["output_id"]
    else:
        workspace = root / "s7" / "tasks" / "browser-acceptance"
        snapshot = workspace / "project.json"
        write_json_atomic(snapshot, edit_project)
        queue = RenderQueue(root / "s7" / "render-queue.sqlite3", retry_base_seconds=0.01)
        queue.enqueue(
            edit_project, edit_project["variants"][0]["id"], snapshot_path=snapshot, workspace_path=workspace,
            max_attempts=1,
        )
        completed = queue.run_pending(
            lambda record, progress: render_project(
                record, record.snapshot(), material_store=material_store, edit_store=edit_store,
                s6_media_root=media_root, progress=progress,
            ), max_workers=2,
        )[0]
        if completed.status != "completed" or not completed.output_id:
            raise RuntimeError(completed.error or "S7 样例渲染失败")
        output_id = completed.output_id
    print(json.dumps({
        "root": str(root), "s6_root": str(root / "s6"), "s7_root": str(root / "s7"),
        "project_id": edit_project["project_id"], "output_id": output_id,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
