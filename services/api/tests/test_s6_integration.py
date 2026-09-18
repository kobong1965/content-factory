from __future__ import annotations

from copy import deepcopy
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from content_factory_api import s6
from content_factory_api.s3_gateway import call_gateway
from content_factory_api.s3_settings import GatewayConfig
from content_factory_api.s6_queue import MaterialImportQueue
from content_factory_api.s6_recognition import recognize_material

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"


class _MaterialRelayHandler(BaseHTTPRequestHandler):
    captured: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).captured.append(raw)
        request = json.loads(raw)
        if self.path.endswith("/responses"):
            context = json.loads(request["input"][0]["content"][0]["text"])
        else:
            context = json.loads(request["messages"][1]["content"][0]["text"])
        result = {"clips": [{
            "clip_id": item["clip_id"], "purpose_tags": ["proof"], "visual_tags": ["男装正面"],
            "garment_views": ["front"], "action_tags": ["站立"], "scene_tags": ["白墙"], "shot_size": "中景",
            "people_count": 1, "standalone_usable": True,
            "quality": {"clarity": 85, "stability": 80, "audio": 75, "exposure": 80},
        } for item in context["clips"]]}
        if self.path.endswith("/responses"):
            payload = {
                "id": "s6-response", "status": "completed",
                "output_text": json.dumps(result, ensure_ascii=False),
            }
        else:
            payload = {"id": "s6-chat", "choices": [{
                "finish_reason": "stop", "message": {"content": json.dumps(result, ensure_ascii=False)},
            }]}
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args) -> None:
        return


@pytest.mark.skipif(os.environ.get("CONTENT_FACTORY_S6_INTEGRATION") != "1", reason="仅在 S6 总验收运行真实 FFmpeg")
def test_actual_video_enters_material_library_without_uploading_original(monkeypatch, tmp_path: Path) -> None:
    fixture = PROJECT_ROOT / "output" / "s2" / "fixture" / "s2-fixture.mp4"
    assert fixture.is_file(), "请先运行 scripts/create-s2-fixture.ps1"
    monkeypatch.setenv("CONTENT_FACTORY_S6_DATA_DIR", str(tmp_path / "s6"))
    s6._stores.clear()
    s6._queues.clear()

    class NoGateway:
        @staticmethod
        def load():
            return None

    monkeypatch.setattr(s6, "get_gateway_settings_store", lambda: NoGateway())
    product = {
        **__import__("json").loads((PROJECT_ROOT / "packages" / "contracts" / "fixtures" / "product.valid.json").read_text(encoding="utf-8")),
        "status": "active",
    }
    queue = MaterialImportQueue(tmp_path / "queue.sqlite3")
    task = queue.enqueue(
        fixture, tmp_path / "s6", fixture_data=True, source_name="S6真实媒体管线验收.mp4",
        product_id=product["product_id"], source_script_id=None,
        input_payload={"product": product, "archive": {
            "model_name": "验收模特", "scene": "三色切换场景", "shot_date": "2026-08-29",
            "batch": "S6-ACCEPTANCE", "imported_by": "工程验收", "note": "隔离测试",
        }},
    )

    result = queue.run_pending(s6._process_import, max_workers=2)[0]
    assert result.status == "completed", result.error
    profile = s6.get_material_store().get(result.material_id or "")
    media = s6.load_media_result_for_material(
        s6.get_material_store().media_result_path(profile["material_id"]), allowed_root=tmp_path / "s6" / "media",
    )

    assert task.task_id == result.task_id
    assert profile["processing"]["recognition_status"] == "not_configured"
    assert profile["processing"]["original_video_uploaded"] is False
    assert len(profile["clips"]) >= 2
    assert Path(media["artifacts"]["proxy_path"]).is_file()
    assert all(Path(shot["keyframe_path"]).is_file() for shot in media["shots"])


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.skipif(os.environ.get("CONTENT_FACTORY_S6_INTEGRATION") != "1", reason="仅在 S6 总验收运行真实 HTTP 中转站")
def test_actual_http_relays_receive_only_low_resolution_keyframes(tmp_path: Path, api_mode: str) -> None:
    material = json.loads((FIXTURES / "material.valid.json").read_text(encoding="utf-8"))
    shots = []
    for clip in material["clips"]:
        frame = tmp_path / f"{clip['id']}.jpg"
        frame.write_bytes(b"low-resolution-keyframe")
        shots.append({"id": clip["source_shot_id"], "keyframe_path": str(frame)})
    _MaterialRelayHandler.captured = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MaterialRelayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = GatewayConfig(
            f"http://127.0.0.1:{server.server_port}/v1", "fixture-model", api_mode,
            "fixture-secret", "2026-08-29T06:00:00Z",
        )
        recognized = recognize_material(deepcopy(material), {"shots": shots}, config, gateway_caller=call_gateway)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    raw = _MaterialRelayHandler.captured[-1]
    assert raw.count(b"data:image/jpeg;base64,") == len(material["clips"])
    assert b"managed_original_path" not in raw and str(tmp_path).encode() not in raw
    assert b"fixture-secret" not in raw
    assert recognized["processing"]["recognition_status"] == "completed"
    assert recognized["processing"]["original_video_uploaded"] is False
