"""Deterministic OpenAI-compatible relay for isolated gateway UI acceptance.

This helper never contacts a cloud provider. It exposes a small model catalog
and accepts the gateway's structured text-and-image probe so the complete
desktop connection flow can be tested without touching the user's saved data.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


MODEL_ID = "Fixture-Vision-Model-ExactCase"
VISION_TOKEN = "K7M2Q9"
_stats_lock = threading.Lock()
_stats = {
    "catalog_requests": 0,
    "probe_requests": 0,
    "image_data_seen": False,
    "expected_token_leaked_in_text_payload": False,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path.rstrip("/") == "/__qa__/stats":
            with _stats_lock:
                self._json_response(dict(_stats))
            return
        if self.path.rstrip("/") != "/v1/models":
            self.send_error(404)
            return
        with _stats_lock:
            _stats["catalog_requests"] += 1
        self._json_response({
            "object": "list",
            "data": [{
                "id": MODEL_ID,
                "object": "model",
                "owned_by": "content-factory-qa",
            }],
        })

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path not in {"/v1/chat/completions", "/v1/responses"}:
            self.send_error(404)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400)
            return
        if request.get("model") != MODEL_ID:
            self._json_response({
                "error": {
                    "code": "model_not_found",
                    "message": f"model {request.get('model')!r} was not found",
                },
            }, status=404)
            return
        serialized = raw.decode("utf-8", errors="replace")
        with _stats_lock:
            _stats["probe_requests"] += 1
            _stats["image_data_seen"] = (
                _stats["image_data_seen"]
                or "data:image/png;base64," in serialized
            )
            _stats["expected_token_leaked_in_text_payload"] = (
                _stats["expected_token_leaked_in_text_payload"]
                or VISION_TOKEN in serialized
            )
        token = (
            "BAD999"
            if self.headers.get("Authorization") == "Bearer fixture-bad-vision-key"
            else VISION_TOKEN
        )
        content = json.dumps({"image_token": token})
        if self.path.endswith("/responses"):
            response = {
                "id": "response_gateway_browser_qa",
                "status": "completed",
                "output_text": content,
            }
        else:
            response = {
                "id": "response_gateway_browser_qa",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": content},
                }],
            }
        self._json_response(response)

    def _json_response(self, value: object, *, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    arguments = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
