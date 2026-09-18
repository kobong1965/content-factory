"""Create an isolated S6 browser/native QA material using the real local media pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--video", required=True)
    arguments = parser.parse_args()
    root = Path(arguments.root).expanduser().resolve()
    video = Path(arguments.video).expanduser().resolve()
    project = Path(__file__).resolve().parents[1]
    os.environ["CONTENT_FACTORY_S6_DATA_DIR"] = str(root)
    os.environ["CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS"] = "1"
    os.environ["CONTENT_FACTORY_S3_CONFIG_PATH"] = str(root / "no-gateway.json")

    from content_factory_api.s2 import _model_path
    from content_factory_api.s6_materials import build_material_profile
    from content_factory_api.s6_store import MaterialStore
    from content_factory_media.pipeline import MediaPipeline

    product = json.loads((project / "packages" / "contracts" / "fixtures" / "product.valid.json").read_text(encoding="utf-8"))
    result = MediaPipeline(asr_model_path=_model_path()).process(
        video, root / "media", "media_77777777777777777777777777777777",
        fixture_data=True, original_name="男装三场景实拍验收.mp4",
    )
    profile = build_material_profile(
        result, product=product, source_script_id=None,
        archive={
            "model_name": "QA 模特阿杰", "scene": "室内三色背景", "shot_date": "2026-08-29",
            "batch": "QA-0829-A", "imported_by": "QA 摄影", "note": "浏览器和 Windows 原生窗口隔离验收",
        },
        recognition_configured=False,
    )
    store = MaterialStore(root / "materials.sqlite3")
    stored, _duplicate = store.create(profile, media_result_path=result, actor="QA 摄影")
    print(json.dumps({"root": str(root), "material_id": stored["material_id"], "clips": len(stored["clips"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
