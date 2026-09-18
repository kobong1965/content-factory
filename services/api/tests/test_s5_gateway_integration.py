from __future__ import annotations

from copy import deepcopy
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from content_factory_api.s3_settings import GatewayConfig
from content_factory_api.s5_generation import process_script_generation
from content_factory_api.s5_production_policy import (
    LOCKED_CAMERA,
    LOCKED_EQUIPMENT,
    LOCKED_SCENE,
    build_production_policy,
)
from content_factory_api.s5_sources import script_product_eligibility
from content_factory_contracts import validate_or_raise

pytestmark = pytest.mark.skipif(
    os.environ.get("CONTENT_FACTORY_S5_INTEGRATION") != "1", reason="S5 integration disabled",
)

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures"


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _input() -> dict:
    product = _json("product.valid.json")
    product["fixture_data"] = False
    analysis = _json("analysis.valid.json")
    return {
        "product": product,
        "analysis": analysis,
        "pattern": analysis["pattern_candidates"][0],
        "request": {"content_goal": "seeding", "target_audience": "关注通勤男装的成年男性", "version_count": 3},
        "production_policy": build_production_policy(product, script_product_eligibility(product)),
    }


class _ScriptGatewayHandler(BaseHTTPRequestHandler):
    captured: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).captured.append(raw)
        request = json.loads(raw)
        fixture = _json("script.valid.json")
        candidate = {key: deepcopy(fixture[key]) for key in ("versions", "shooting_order", "material_checklist")}
        for version in candidate["versions"]:
            for shot in version["shots"]:
                shot["camera"] = LOCKED_CAMERA
                shot["transition"] = "无（连续长镜头）"
                shot["source_shot_id"] = None
        candidate["shooting_order"] = [{
            "scene": LOCKED_SCENE,
            "equipment": LOCKED_EQUIPMENT,
            "shot_ids": [shot["id"] for version in candidate["versions"] for shot in version["shots"]],
        }]
        if self.path.endswith("/responses"):
            assert request["text"]["format"]["name"] == "content_factory_script_package"
            assert request["input"][0]["content"][0]["type"] == "input_text"
            response_payload = {
                "id": "response_s5_http",
                "status": "completed",
                "output_text": json.dumps(candidate, ensure_ascii=False),
            }
        else:
            assert request["response_format"]["json_schema"]["name"] == "content_factory_script_package"
            response_payload = {
                "id": "response_s5_chat_http",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(candidate, ensure_ascii=False)},
                }],
            }
        response = json.dumps(response_payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args) -> None:
        return


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_actual_http_relay_generates_text_only_script_package(tmp_path: Path, api_mode: str) -> None:
    _ScriptGatewayHandler.captured = []
    task_id = f"script_task_{'1' * 32 if api_mode == 'responses' else '2' * 32}"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        artifacts = process_script_generation(
            task_id=task_id,
            input_payload=_input(), task_directory=tmp_path / api_mode,
            config=GatewayConfig(
                f"http://127.0.0.1:{server.server_port}/v1", "fixture-model", api_mode,
                "fixture-secret", "2026-08-29T06:00:00Z",
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    script = json.loads(artifacts.result_path.read_text(encoding="utf-8"))
    validate_or_raise("script", script, related={"product": _input()["product"], "analysis": _input()["analysis"]})
    raw = _ScriptGatewayHandler.captured[-1]
    assert b"input_image" not in raw and b"image_url" not in raw
    assert str(tmp_path).encode() not in raw
    assert script["generation"]["original_video_uploaded"] is False
