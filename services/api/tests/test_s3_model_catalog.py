from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from content_factory_api import s3_model_catalog
from content_factory_api.s3_model_catalog import (
    ModelDiscoveryError,
    _catalog_candidates,
    discover_models,
    normalize_connection_url,
)


@contextmanager
def _serve(handler: type[BaseHTTPRequestHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_openai_catalog_preserves_exact_model_ids_and_never_returns_the_key() -> None:
    class Handler(BaseHTTPRequestHandler):
        authorization = ""

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            type(self).authorization = self.headers.get("Authorization", "")
            payload = json.dumps({
                "object": "list",
                "data": [
                    {"id": "qwen3.7-plus", "object": "model"},
                    {"id": "GPT-Exact-Case", "object": "model"},
                    {"id": "qwen3.7-plus", "object": "model"},
                ],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args) -> None:
            return

    with _serve(Handler) as server:
        catalog = discover_models(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="do-not-return-this-secret",
        )

    assert Handler.authorization == "Bearer do-not-return-this-secret"
    assert [item.upstream_model_id for item in catalog.models] == [
        "qwen3.7-plus",
        "GPT-Exact-Case",
    ]
    assert catalog.catalog_source == "openai_models"
    assert "do-not-return-this-secret" not in json.dumps(catalog.public_dict())


def test_root_url_uses_the_catalog_path_that_succeeded_as_the_inference_base() -> None:
    class Handler(BaseHTTPRequestHandler):
        paths: list[str] = []

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            type(self).paths.append(self.path)
            if self.path == "/models":
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps({"data": [{"id": "relay-vision-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args) -> None:
            return

    with _serve(Handler) as server:
        catalog = discover_models(
            base_url=f"http://127.0.0.1:{server.server_port}",
            api_key="fixture-relay-secret",
        )

    assert Handler.paths == ["/models", "/v1/models"]
    assert catalog.normalized_base_url == f"http://127.0.0.1:{server.server_port}/v1"


def test_qwen_compatible_base_uses_same_origin_native_catalog_and_paginates(monkeypatch) -> None:
    class Handler(BaseHTTPRequestHandler):
        paths: list[str] = []

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            type(self).paths.append(self.path)
            parsed = urlsplit(self.path)
            assert parsed.path == "/api/v1/models"
            page = int(parse_qs(parsed.query)["page_no"][0])
            model = {
                "model": f"qwen3.7-vision-{page}",
                "name": f"千问视觉 {page}",
                "capabilities": ["TG", "VU"],
                "features": ["structured-outputs"],
                "inference_metadata": {
                    "request_modality": ["Text", "Image"],
                    "response_modality": ["Text"],
                },
            }
            payload = json.dumps({
                "success": True,
                "output": {"total": 2, "page_no": page, "page_size": 1, "models": [model]},
            }, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args) -> None:
            return

    monkeypatch.setattr(s3_model_catalog, "infer_provider_from_base_url", lambda _url: "qwen")
    with _serve(Handler) as server:
        catalog = discover_models(
            base_url=f"http://127.0.0.1:{server.server_port}/compatible-mode/v1",
            api_key="fixture-qwen-secret",
        )

    assert [item.upstream_model_id for item in catalog.models] == [
        "qwen3.7-vision-1",
        "qwen3.7-vision-2",
    ]
    assert catalog.models[0].input_modalities == ("text", "image")
    assert catalog.models[0].output_modalities == ("text",)
    assert catalog.models[0].supports_structured_output is True
    assert catalog.models[0].capability_source == "provider_metadata"
    assert len(Handler.paths) == 2


def test_qwen_catalog_does_not_truncate_a_model_just_beyond_the_old_500_limit(monkeypatch) -> None:
    requested_pages: list[int] = []

    def fake_fetch(url: str, _api_key: str, *, timeout_seconds: int):
        del timeout_seconds
        page = int(parse_qs(urlsplit(url).query)["page_no"][0])
        requested_pages.append(page)
        start = (page - 1) * 100
        end = min(start + 100, 501)
        return {
            "output": {
                "total": 501,
                "models": [
                    {"model": f"qwen-vision-{index:04d}"}
                    for index in range(start, end)
                ],
            },
        }

    monkeypatch.setattr(s3_model_catalog, "_fetch_catalog_json", fake_fetch)
    catalog = discover_models(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="fixture-qwen-secret",
    )

    assert len(catalog.models) == 501
    assert catalog.models[-1].upstream_model_id == "qwen-vision-0500"
    assert requested_pages == [1, 2, 3, 4, 5, 6]
    assert catalog.truncated is False


def test_qwen_catalog_has_a_bounded_2000_model_cap_and_explicit_warning(monkeypatch) -> None:
    def fake_fetch(url: str, _api_key: str, *, timeout_seconds: int):
        del timeout_seconds
        page = int(parse_qs(urlsplit(url).query)["page_no"][0])
        start = (page - 1) * 100
        end = min(start + 100, 2001)
        return {
            "output": {
                "total": 2001,
                "models": [
                    {"model": f"qwen-vision-{index:04d}"}
                    for index in range(start, end)
                ],
            },
        }

    monkeypatch.setattr(s3_model_catalog, "_fetch_catalog_json", fake_fetch)
    catalog = discover_models(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="fixture-qwen-secret",
    )

    assert len(catalog.models) == 2000
    assert catalog.truncated is True
    assert any("2000" in warning and "手动" in warning for warning in catalog.warnings)


def test_qwen_catalog_mapping_never_changes_the_credential_origin() -> None:
    candidates = _catalog_candidates(
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "qwen",
    )

    assert candidates[0].url.startswith(
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/models?"
    )
    assert all(urlsplit(item.url).netloc == "workspace.cn-beijing.maas.aliyuncs.com" for item in candidates)


@pytest.mark.parametrize(
    "pasted_url",
    [
        "https://dashscope.aliyuncs.com",
        "https://dashscope.aliyuncs.com/api/v1",
        "https://dashscope.aliyuncs.com/api/v1/models",
    ],
)
def test_qwen_native_or_root_base_is_normalized_to_official_compatible_inference_base(
    pasted_url: str,
) -> None:
    assert normalize_connection_url(
        pasted_url
    ) == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_catalog_authentication_failure_is_structured_and_does_not_echo_key() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            payload = json.dumps({"error": {"message": "bad key"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args) -> None:
            return

    with _serve(Handler) as server:
        with pytest.raises(ModelDiscoveryError) as caught:
            discover_models(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="never-echo-auth-secret",
            )

    assert caught.value.diagnostic_code == "authentication_failed"
    assert caught.value.upstream_status == 401
    assert caught.value.retryable is False
    assert "never-echo-auth-secret" not in str(caught.value)


def test_model_discovery_does_not_follow_redirect_or_forward_key() -> None:
    class DestinationHandler(BaseHTTPRequestHandler):
        hits = 0
        authorization: str | None = None

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            type(self).hits += 1
            type(self).authorization = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:
            return

    with _serve(DestinationHandler) as destination:
        destination_url = f"http://127.0.0.1:{destination.server_port}/capture"

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                self.send_response(307)
                self.send_header("Location", destination_url)
                self.end_headers()

            def log_message(self, _format: str, *_args) -> None:
                return

        with _serve(RedirectHandler) as source:
            with pytest.raises(ModelDiscoveryError) as caught:
                discover_models(
                    base_url=f"http://127.0.0.1:{source.server_port}/v1",
                    api_key="never-forward-this-key",
                )

    assert caught.value.diagnostic_code == "redirect_disallowed"
    assert DestinationHandler.hits == 0
    assert DestinationHandler.authorization is None
