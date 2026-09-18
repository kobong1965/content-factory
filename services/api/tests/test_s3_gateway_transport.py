from __future__ import annotations

import http.client
import json
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.error import HTTPError

import pytest

from content_factory_api import s3_gateway
from content_factory_api.s3_gateway import GatewayError, call_gateway
from content_factory_api.s3_settings import GatewayConfig


def _config(mode: str = "responses") -> GatewayConfig:
    return GatewayConfig("https://relay.example.com/v1", "vision-model", mode, "secret-key-value", "2026-08-29T06:00:00Z")


@pytest.mark.parametrize(
    ("status", "diagnostic_code", "retryable"),
    [
        (401, "authentication_failed", False),
        (403, "authentication_failed", False),
        (429, "rate_limited", True),
        (408, "upstream_unavailable", True),
        (500, "upstream_unavailable", True),
        (502, "upstream_unavailable", True),
        (503, "upstream_unavailable", True),
        (504, "upstream_unavailable", True),
    ],
)
def test_http_status_precedence_wins_over_model_semantics(
    monkeypatch,
    status: int,
    diagnostic_code: str,
    retryable: bool,
) -> None:
    def must_not_discover(*_args, **_kwargs):
        raise AssertionError("priority HTTP statuses must not trigger model discovery")

    monkeypatch.setattr(s3_gateway, "_discover_model_ids", must_not_discover)
    error = s3_gateway._http_gateway_error(
        _config("chat_completions"),
        "https://relay.example.com/v1/chat/completions",
        status,
        json.dumps({
            "error": {
                "type": "model_not_found",
                "code": "model_not_found",
                "message": "response_format json_schema unsupported for unknown model",
            }
        }).encode(),
        timeout_seconds=2,
    )

    assert error.status_code == status
    assert error.diagnostic_code == diagnostic_code
    assert error.retryable is retryable


def test_generic_bad_request_is_permanent_and_never_auto_retried(monkeypatch) -> None:
    monkeypatch.setattr(s3_gateway, "_discover_model_ids", lambda *_args, **_kwargs: [])
    error = s3_gateway._http_gateway_error(
        _config("chat_completions"),
        "https://relay.example.com/v1/chat/completions",
        400,
        json.dumps({"error": {"type": "invalid_parameter", "message": "bad input"}}).encode(),
        timeout_seconds=2,
    )

    assert error.retryable is False
    assert error.diagnostic_code == "request_incompatible"
    assert error.outcome == "permanent_failure"


def test_rate_limit_honours_retry_after_header() -> None:
    error = s3_gateway._http_gateway_error(
        _config("chat_completions"),
        "https://relay.example.com/v1/chat/completions",
        429,
        json.dumps({"error": {"type": "rate_limit_error", "code": "rate_limit_exceeded"}}).encode(),
        timeout_seconds=2,
        response_headers={"retry-after": "11"},
    )

    assert error.retryable is True
    assert error.retry_after_seconds == 11
    assert error.outcome == "retryable_failure"


def test_provider_model_limiter_caps_concurrent_gateway_calls(monkeypatch) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_MODEL_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("CONTENT_FACTORY_MODEL_RPM", "100")
    monkeypatch.setenv("CONTENT_FACTORY_MODEL_INPUT_TPM", "100000")
    monkeypatch.setattr(s3_gateway, "_MODEL_RATE_LIMITER", s3_gateway._ProviderModelRateLimiter())
    active = 0
    peak = 0
    lock = threading.Lock()

    def send(*_args, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return (
            "application/json",
            json.dumps({
                "id": "limited",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            }).encode(),
            {},
        )

    monkeypatch.setattr(s3_gateway, "_send_request", send)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(
            lambda _index: call_gateway(
                _config("chat_completions"),
                context_json='{"probe":true}',
                keyframe_data_urls=[],
                output_schema={"type": "object"},
                timeout_seconds=3,
            ),
            range(3),
        ))

    assert peak == 1
    assert [result.content for result in results] == [{"ok": True}] * 3


def test_local_input_tpm_guard_fails_before_a_costly_request(monkeypatch) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_MODEL_INPUT_TPM", "10000")
    monkeypatch.setattr(s3_gateway, "_MODEL_RATE_LIMITER", s3_gateway._ProviderModelRateLimiter())
    monkeypatch.setattr(
        s3_gateway,
        "_send_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("request must not be sent")),
    )

    with pytest.raises(GatewayError) as caught:
        call_gateway(
            _config("chat_completions"),
            context_json="x" * 10_001,
            keyframe_data_urls=[],
            output_schema={"type": "object"},
            timeout_seconds=2,
        )

    assert caught.value.retryable is False
    assert caught.value.diagnostic_code == "local_token_budget_exceeded"


@pytest.mark.parametrize("marker", ["insufficient_quota", "billing_hard_limit_reached", "余额不足"])
def test_quota_or_balance_429_fails_without_retry_storm(marker: str) -> None:
    error = s3_gateway._http_gateway_error(
        _config("chat_completions"),
        "https://relay.example.com/v1/chat/completions",
        429,
        json.dumps({"error": {"type": marker, "code": marker, "message": marker}}).encode(),
        timeout_seconds=2,
        response_headers={"retry-after": "1"},
    )

    assert error.retryable is False
    assert error.diagnostic_code == "quota_exhausted"
    assert error.outcome == "permanent_failure"


def test_transport_partial_header_rejects_otherwise_valid_qwen_response(monkeypatch) -> None:
    payload = json.dumps({
        "id": "qwen_partial",
        "choices": [{"finish_reason": "stop", "message": {"content": '{"ok": true}'}}],
    }).encode()
    monkeypatch.setattr(
        s3_gateway,
        "_send_request",
        lambda *_args, **_kwargs: (
            "application/json",
            payload,
            {"x-dashscope-partialresponse": "true", "x-request-id": "request-safe-id"},
        ),
    )

    with pytest.raises(GatewayError) as caught:
        call_gateway(
            _config("chat_completions"),
            context_json='{"probe":true}',
            keyframe_data_urls=[],
            output_schema={"type": "object"},
            timeout_seconds=2,
        )

    assert caught.value.outcome == "incomplete"


def test_authentication_error_does_not_retry_as_schema_compatibility(monkeypatch) -> None:
    calls = 0

    def send(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        payload = json.dumps({
            "error": {
                "type": "invalid_request_error",
                "message": "response_format json_schema unsupported",
            }
        }).encode()
        raise HTTPError(
            "https://relay.example.com/v1/chat/completions",
            401,
            "Unauthorized",
            {},
            BytesIO(payload),
        )

    monkeypatch.setattr(s3_gateway, "_send_request", send)

    with pytest.raises(GatewayError) as caught:
        call_gateway(
            _config("chat_completions"),
            context_json='{"probe":true}',
            keyframe_data_urls=[],
            output_schema={"type": "object"},
            timeout_seconds=2,
        )

    assert calls == 1
    assert caught.value.diagnostic_code == "authentication_failed"


@pytest.mark.parametrize(
    ("catalog", "diagnostic_code"),
    [
        (["gpt-5.6-sol", "qwen-vl-max"], "model_not_found"),
        (["vision-model", "qwen-vl-max"], "endpoint_not_found"),
        ([], "endpoint_not_found"),
    ],
)
def test_generic_404_uses_same_origin_model_catalog_for_diagnosis(
    monkeypatch,
    catalog: list[str],
    diagnostic_code: str,
) -> None:
    monkeypatch.setattr(
        s3_gateway,
        "_discover_model_ids",
        lambda *_args, **_kwargs: catalog,
    )

    error = s3_gateway._http_gateway_error(
        _config("chat_completions"),
        "https://relay.example.com/v1/chat/completions",
        404,
        json.dumps({"error": {"message": "route not found"}}).encode(),
        timeout_seconds=2,
    )

    assert error.diagnostic_code == diagnostic_code


def test_public_diagnostics_filter_model_catalog_and_upstream_secret_tokens(monkeypatch) -> None:
    monkeypatch.setattr(
        s3_gateway,
        "_discover_model_ids",
        lambda *_args, **_kwargs: [
            "gpt-5.6-sol",
            "Bearer-private-token",
            "sk-1234567890abcdef",
            "bad\nmodel",
        ],
    )
    # Discovery normally applies the same filter while decoding /models. Keep
    # this direct stub adversarial to verify the final presentation boundary too.
    monkeypatch.setattr(
        s3_gateway,
        "get_close_matches",
        lambda *_args, **_kwargs: [
            "gpt-5.6-sol",
            "Bearer-private-token",
            "sk-1234567890abcdef",
            "bad\nmodel",
        ],
    )
    raw = json.dumps({
        "error": {
            "type": "model_not_found",
            "code": "Bearer-private-token",
            "message": "unknown model",
        }
    }).encode()

    error = s3_gateway._http_gateway_error(
        GatewayConfig(
            "https://relay.example.com/v1",
            "gpt-5.6sol",
            "chat_completions",
            "sk-never-display-this-key",
            "2026-09-05T00:00:00Z",
        ),
        "https://relay.example.com/v1/chat/completions",
        404,
        raw,
        timeout_seconds=2,
    )
    public_message = str(error)

    assert "gpt-5.6-sol" in public_message
    assert "Bearer-private-token" not in public_message
    assert "sk-1234567890abcdef" not in public_message
    assert "bad\nmodel" not in public_message
    assert "sk-never-display-this-key" not in public_message


def test_qwen_floating_alias_image_rejection_reports_unsupported() -> None:
    config = GatewayConfig(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-max",
        "chat_completions",
        "fixture-secret-key",
        "2026-09-05T00:00:00Z",
        provider="qwen",
    )
    raw = json.dumps({
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_parameter_error",
            "message": (
                "InternalError.Algo.InvalidParameter: The provided messages input is invalid. "
                "The error info is [Unexpected item type in content.]."
            ),
        }
    }).encode()

    error = s3_gateway._http_gateway_error(
        config,
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        400,
        raw,
        timeout_seconds=2,
        has_image_input=True,
    )

    assert error.diagnostic_code == "image_input_unsupported"
    assert error.retryable is False
    assert "当前账户与地域" in str(error)
    assert "qwen3.7-plus" in str(error)
    assert "qwen3-vl-plus" not in str(error)
    assert "纯文本模型" not in str(error)
    assert "fixture-secret-key" not in str(error)


def test_call_gateway_propagates_qwen_image_rejection_diagnostic(monkeypatch) -> None:
    config = GatewayConfig(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-max",
        "chat_completions",
        "fixture-secret-key",
        "2026-09-05T00:00:00Z",
        provider="qwen",
    )
    payload = json.dumps({
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_parameter_error",
            "message": (
                "InternalError.Algo.InvalidParameter: The provided messages input is invalid. "
                "The error info is [Unexpected item type in content.]."
            ),
        }
    }).encode()

    def reject_image(_config, endpoint_url, body, **_kwargs):
        assert any(
            item.get("type") == "image_url"
            for item in body["messages"][1]["content"]
        )
        raise HTTPError(endpoint_url, 400, "Bad Request", {}, BytesIO(payload))

    monkeypatch.setattr(s3_gateway, "_send_request", reject_image)

    with pytest.raises(GatewayError) as caught:
        call_gateway(
            config,
            context_json='{"probe":true}',
            keyframe_data_urls=["data:image/png;base64,AA=="],
            output_schema={"type": "object"},
            timeout_seconds=2,
        )

    assert caught.value.diagnostic_code == "image_input_unsupported"
    assert caught.value.status_code == 400
    assert caught.value.endpoint_url == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert "qwen3.7-plus" in str(caught.value)
    assert "qwen3-vl-plus" not in str(caught.value)
    assert "fixture-secret-key" not in str(caught.value)


def test_same_qwen_parameter_error_without_image_is_not_misclassified() -> None:
    config = GatewayConfig(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-max",
        "chat_completions",
        "fixture-secret-key",
        "2026-09-05T00:00:00Z",
        provider="qwen",
    )
    raw = json.dumps({
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_parameter_error",
            "message": "Unexpected item type in content.",
        }
    }).encode()

    error = s3_gateway._http_gateway_error(
        config,
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        400,
        raw,
        timeout_seconds=2,
        has_image_input=False,
    )

    assert error.diagnostic_code == "request_incompatible"


def test_qwen_generic_parameter_error_with_image_is_not_misclassified() -> None:
    config = GatewayConfig(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-max",
        "chat_completions",
        "fixture-secret-key",
        "2026-09-05T00:00:00Z",
        provider="qwen",
    )
    raw = json.dumps({
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_parameter_error",
            "message": "The temperature parameter must be between zero and two.",
        }
    }).encode()

    error = s3_gateway._http_gateway_error(
        config,
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        400,
        raw,
        timeout_seconds=2,
        has_image_input=True,
    )

    assert error.diagnostic_code == "request_incompatible"
    assert "qwen3.7-plus" not in str(error)


def test_qwen_invalid_image_dimensions_have_distinct_diagnostic() -> None:
    config = GatewayConfig(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-max-2026-06-08",
        "chat_completions",
        "fixture-secret-key",
        "2026-09-05T00:00:00Z",
        provider="qwen",
    )
    raw = json.dumps({
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_parameter_error",
            "message": "The image length and width do not meet the model restrictions.",
        }
    }).encode()

    error = s3_gateway._http_gateway_error(
        config,
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        400,
        raw,
        timeout_seconds=2,
        has_image_input=True,
    )

    assert error.diagnostic_code == "image_input_invalid"
    assert "宽高均大于 10" in str(error)


def test_gateway_model_not_found_reports_catalog_suggestion_and_request_context() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            payload = json.dumps({"data": [{"id": "gpt-5.6-sol"}, {"id": "qwen-vl-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            payload = json.dumps({
                "error": {
                    "message": 'Model "gpt-5.6sol" is not supported by any configured account',
                    "type": "model_not_found",
                }
            }).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = GatewayConfig(
        f"http://127.0.0.1:{server.server_port}", "gpt-5.6sol", "chat_completions",
        "do-not-echo-this-key", "2026-09-05T00:00:00Z",
    )
    try:
        with pytest.raises(GatewayError, match="gpt-5.6-sol") as caught:
            call_gateway(
                config, context_json='{"probe":true}', keyframe_data_urls=[],
                output_schema={"type": "object"}, timeout_seconds=2,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert caught.value.status_code == 404
    assert caught.value.diagnostic_code == "model_not_found"
    assert caught.value.endpoint_url is not None and caught.value.endpoint_url.endswith("/chat/completions")
    assert "chat_completions" in str(caught.value)
    assert "do-not-echo-this-key" not in str(caught.value)


def test_gateway_retries_same_origin_v1_path_after_generic_404() -> None:
    class Handler(BaseHTTPRequestHandler):
        paths: list[str] = []

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            type(self).paths.append(self.path)
            if self.path == "/chat/completions":
                payload = json.dumps({"error": {"message": "route not found"}}).encode()
                self.send_response(404)
            else:
                payload = json.dumps({
                    "id": "chat_v1", "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                }).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args) -> None:
            return

    Handler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = GatewayConfig(
        f"http://127.0.0.1:{server.server_port}", "qwen-vl-model", "chat_completions",
        "fixture-secret", "2026-09-05T00:00:00Z", provider="qwen",
    )
    try:
        result = call_gateway(
            config, context_json='{"probe":true}', keyframe_data_urls=[],
            output_schema={"type": "object"}, timeout_seconds=2,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.content == {"ok": True}
    assert result.endpoint_url is not None and result.endpoint_url.endswith("/v1/chat/completions")
    assert Handler.paths == ["/chat/completions", "/v1/chat/completions"]


def test_gateway_falls_back_to_json_object_when_relay_rejects_json_schema() -> None:
    class Handler(BaseHTTPRequestHandler):
        bodies: list[dict] = []

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            type(self).bodies.append(body)
            if body["response_format"]["type"] == "json_schema":
                payload = json.dumps({
                    "error": {"message": "response_format json_schema is unsupported", "type": "invalid_request_error"},
                }).encode()
                self.send_response(400)
            else:
                payload = json.dumps({
                    "id": "chat_json_object", "choices": [{"finish_reason": "stop", "message": {"content": "```json\n{\"ok\": true}\n```"}}],
                }).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args) -> None:
            return

    Handler.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = GatewayConfig(
        f"http://127.0.0.1:{server.server_port}/v1", "qwen-vl-model", "chat_completions",
        "fixture-secret", "2026-09-05T00:00:00Z", provider="qwen",
    )
    try:
        result = call_gateway(
            config, context_json='{"probe":true}', keyframe_data_urls=[],
            output_schema={
                "type": "object", "additionalProperties": False, "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
            },
            timeout_seconds=2,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.content == {"ok": True}
    assert [body["response_format"]["type"] for body in Handler.bodies] == ["json_schema", "json_object"]
    assert "JSON Schema" in Handler.bodies[1]["messages"][0]["content"]


def test_ssl_record_layer_failure_during_stream_read_becomes_retryable(monkeypatch) -> None:
    config = GatewayConfig(
        "https://relay.example.com/v1", "gpt-5.6-sol", "responses",
        "fixture-secret", "2026-09-05T00:00:00Z",
    )

    def interrupted_read(*_args, **_kwargs):
        raise ssl.SSLError("[SSL] record layer failure")

    monkeypatch.setattr(s3_gateway, "_send_request", interrupted_read)

    with pytest.raises(GatewayError, match="流式响应被中断") as caught:
        call_gateway(
            config, context_json='{"probe":true}', keyframe_data_urls=[],
            output_schema={"type": "object"}, timeout_seconds=2,
        )

    assert caught.value.retryable is True
    assert caught.value.status_code is None
    assert caught.value.diagnostic_code == "secure_connection_interrupted"
    assert caught.value.endpoint_url == "https://relay.example.com/v1/responses"


@pytest.mark.parametrize(
    "failure",
    [
        http.client.RemoteDisconnected("Remote end closed connection without response"),
        http.client.IncompleteRead(b'{"partial":'),
    ],
    ids=["remote-disconnected", "incomplete-read"],
)
def test_http_transport_interruptions_become_retryable(monkeypatch, failure: Exception) -> None:
    config = GatewayConfig(
        "https://relay.example.com/v1", "qwen-vl-model", "chat_completions",
        "fixture-secret", "2026-09-05T00:00:00Z", provider="qwen",
    )

    def interrupted_read(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(s3_gateway, "_send_request", interrupted_read)

    with pytest.raises(GatewayError, match="响应读取被中断") as caught:
        call_gateway(
            config, context_json='{"probe":true}', keyframe_data_urls=[],
            output_schema={"type": "object"}, timeout_seconds=2,
        )

    assert caught.value.retryable is True
    assert caught.value.status_code is None
    assert caught.value.diagnostic_code == "connection_interrupted"
    assert caught.value.endpoint_url == "https://relay.example.com/v1/chat/completions"


def test_strict_http_error_body_interruption_becomes_retryable(monkeypatch) -> None:
    config = _config("chat_completions")

    class InterruptedHttpError(HTTPError):
        def read(self, *_args, **_kwargs):
            raise http.client.IncompleteRead(b'{"partial":')

    def send(*_args, **_kwargs):
        raise InterruptedHttpError(
            "https://relay.example.com/v1/chat/completions",
            400,
            "Bad Request",
            {},
            None,
        )

    monkeypatch.setattr(s3_gateway, "_send_request", send)

    with pytest.raises(GatewayError, match="错误响应读取被中断") as caught:
        call_gateway(
            config,
            context_json='{"probe":true}',
            keyframe_data_urls=[],
            output_schema={"type": "object"},
            timeout_seconds=2,
        )

    assert caught.value.retryable is True
    assert caught.value.diagnostic_code == "connection_interrupted"
    assert caught.value.endpoint_url == "https://relay.example.com/v1/chat/completions"


def test_compatibility_http_error_body_ssl_interruption_becomes_retryable(monkeypatch) -> None:
    config = _config("chat_completions")
    calls = 0

    class InterruptedHttpError(HTTPError):
        def read(self, *_args, **_kwargs):
            raise ssl.SSLError("[SSL] record layer failure")

    def send(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        endpoint = "https://relay.example.com/v1/chat/completions"
        if calls == 1:
            payload = json.dumps({
                "error": {
                    "type": "invalid_request_error",
                    "message": "response_format json_schema is unsupported",
                }
            }).encode()
            raise HTTPError(endpoint, 400, "Bad Request", {}, BytesIO(payload))
        raise InterruptedHttpError(endpoint, 400, "Bad Request", {}, None)

    monkeypatch.setattr(s3_gateway, "_send_request", send)

    with pytest.raises(GatewayError, match="错误响应读取被中断") as caught:
        call_gateway(
            config,
            context_json='{"probe":true}',
            keyframe_data_urls=[],
            output_schema={"type": "object"},
            timeout_seconds=2,
        )

    assert calls == 2
    assert caught.value.retryable is True
    assert caught.value.diagnostic_code == "secure_connection_interrupted"
    assert caught.value.endpoint_url == "https://relay.example.com/v1/chat/completions"


def test_gateway_does_not_follow_redirect_or_forward_api_key() -> None:
    class DestinationHandler(BaseHTTPRequestHandler):
        hits = 0
        authorization: str | None = None

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            type(self).hits += 1
            type(self).authorization = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:
            return

    destination = ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)

    destination_url = f"http://127.0.0.1:{destination.server_port}/capture"

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            self.send_response(307)
            self.send_header("Location", destination_url)
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:
            return

    source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    destination_thread = threading.Thread(target=destination.serve_forever, daemon=True)
    source_thread = threading.Thread(target=source.serve_forever, daemon=True)
    destination_thread.start()
    source_thread.start()
    try:
        config = GatewayConfig(
            f"http://127.0.0.1:{source.server_port}/v1",
            "vision-model",
            "responses",
            "do-not-forward-this-key",
            "2026-08-31T00:00:00Z",
        )
        with pytest.raises(GatewayError, match="HTTP 307") as caught:
            call_gateway(
                config,
                context_json='{"probe":true}',
                keyframe_data_urls=[],
                output_schema={"type": "object", "additionalProperties": True},
                timeout_seconds=2,
            )
    finally:
        source.shutdown()
        destination.shutdown()
        source.server_close()
        destination.server_close()
        source_thread.join(timeout=2)
        destination_thread.join(timeout=2)

    assert caught.value.retryable is False
    assert DestinationHandler.hits == 0
    assert DestinationHandler.authorization is None
