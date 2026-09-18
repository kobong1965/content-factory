"""Local structured-output relay used only for isolated S5 browser acceptance."""

from __future__ import annotations

import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    candidate: dict = {}

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        request = json.loads(raw)
        if self.path.endswith("/responses"):
            schema_name = request["text"]["format"]["name"]
            candidate = self.response_candidate(schema_name, request)
            response = {
                "id": "response_s5_browser_qa",
                "status": "completed",
                "output_text": json.dumps(candidate, ensure_ascii=False),
            }
        else:
            schema_name = request["response_format"]["json_schema"]["name"]
            candidate = self.response_candidate(schema_name, request)
            response = {"id": "response_s5_browser_qa", "choices": [{"message": {"content": json.dumps(candidate, ensure_ascii=False)}}]}
        if candidate is None:
            self.send_error(422, "unexpected schema")
            return
        payload = json.dumps(response, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @classmethod
    def response_candidate(cls, schema_name: str, request: dict) -> dict | None:
        if schema_name == "content_factory_script_package":
            return cls.candidate
        if schema_name != "content_factory_material_tags":
            return None
        if "input" in request:
            context_text = request["input"][0]["content"][0]["text"]
        else:
            context_text = request["messages"][-1]["content"][0]["text"]
        context = json.loads(context_text)
        return {"clips": [{
            "clip_id": item["clip_id"],
            "purpose_tags": ["hook", "proof"],
            "visual_tags": ["男模特", "正面展示", "面料细节"],
            "garment_views": ["front", "full_body"],
            "action_tags": ["自然站立", "展示面料"],
            "scene_tags": ["室内白墙"],
            "shot_size": "中景转特写",
            "people_count": 1,
            "standalone_usable": True,
            "quality": {"clarity": 86, "stability": 84, "audio": 82, "exposure": 85},
        } for item in context["clips"]]}

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    arguments = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    fixture = json.loads((project / "packages" / "contracts" / "fixtures" / "script.valid.json").read_text(encoding="utf-8"))
    Handler.candidate = {key: deepcopy(fixture[key]) for key in ("versions", "shooting_order", "material_checklist")}
    ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
