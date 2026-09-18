from __future__ import annotations

import json

import pytest

from content_factory_api.s3_gateway import (
    GatewayError,
    build_request_body,
    parse_gateway_response,
    parse_gateway_stream,
    structured_output_schema,
)
from content_factory_api.s3_settings import GatewayConfig


def _config(mode: str = "responses") -> GatewayConfig:
    return GatewayConfig("https://relay.example.com/v1", "vision-model", mode, "secret-key-value", "2026-08-29T06:00:00Z")


def test_responses_request_uses_structured_output_and_minimal_images() -> None:
    body = build_request_body(
        _config("responses"),
        context_json='{"shots":[]}',
        keyframe_data_urls=["data:image/jpeg;base64,AA=="],
        output_schema={"type": "object"},
    )

    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["verbosity"] == "low"
    assert body["max_output_tokens"] == 16_000
    assert body["stream"] is True
    assert body["input"][0]["content"][1]["type"] == "input_image"


def test_gpt5_responses_request_uses_low_latency_reasoning() -> None:
    config = GatewayConfig(
        "https://relay.example.com/v1",
        "gpt-5.6-sol",
        "responses",
        "secret-key-value",
        "2026-08-29T06:00:00Z",
    )

    body = build_request_body(
        config,
        context_json='{"shots":[]}',
        keyframe_data_urls=[],
        output_schema={"type": "object"},
    )

    assert body["reasoning"] == {"effort": "none"}


def test_gpt5_chat_completions_request_uses_low_latency_reasoning() -> None:
    config = GatewayConfig(
        "https://relay.example.com/v1",
        "gpt-5.6-sol",
        "chat_completions",
        "secret-key-value",
        "2026-09-05T00:00:00Z",
        provider="openai_compatible",
    )

    body = build_request_body(
        config,
        context_json='{"probe":true}',
        keyframe_data_urls=["data:image/png;base64,AA=="],
        output_schema={"type": "object"},
    )

    assert body["reasoning_effort"] == "none"
    assert "max_completion_tokens" not in body
    assert body["stream"] is True


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_base_gpt5_does_not_receive_unsupported_none_reasoning(mode: str) -> None:
    config = GatewayConfig(
        "https://relay.example.com/v1",
        "gpt-5",
        mode,  # type: ignore[arg-type]
        "secret-key-value",
        "2026-09-05T00:00:00Z",
        provider="openai",
    )

    body = build_request_body(
        config,
        context_json='{"probe":true}',
        keyframe_data_urls=[],
        output_schema={"type": "object"},
    )

    assert "reasoning" not in body
    assert "reasoning_effort" not in body


def test_non_gpt_responses_request_does_not_send_provider_specific_reasoning() -> None:
    config = GatewayConfig(
        "https://relay.example.com/v1",
        "qwen3.8-max",
        "responses",
        "secret-key-value",
        "2026-08-29T06:00:00Z",
    )

    body = build_request_body(
        config,
        context_json='{"shots":[]}',
        keyframe_data_urls=[],
        output_schema={"type": "object"},
    )

    assert "reasoning" not in body


def test_chat_completions_request_uses_response_format() -> None:
    body = build_request_body(
        _config("chat_completions"),
        context_json='{"shots":[]}',
        keyframe_data_urls=[],
        output_schema={"type": "object"},
    )

    assert body["response_format"]["type"] == "json_schema"
    assert body["messages"][0]["role"] == "system"
    assert body["stream"] is True


def test_qwen_chat_completions_uses_standard_multimodal_parts() -> None:
    config = GatewayConfig(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-vl-model",
        "chat_completions",
        "secret-key-value",
        "2026-09-05T00:00:00Z",
        provider="qwen",
    )

    body = build_request_body(
        config,
        context_json='{"clips":[]}',
        keyframe_data_urls=["data:image/jpeg;base64,AA=="],
        output_schema={"type": "object"},
    )

    assert body["model"] == "qwen-vl-model"
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": '{"clips":[]}'},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}},
    ]
    assert body["response_format"]["type"] == "json_schema"
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body
    assert body["stream"] is True


@pytest.mark.parametrize(
    "model",
    ["qwen3.7-max-2026-06-08", "qwen3.8-max", "qwen3-vl-plus"],
)
def test_qwen_mixed_thinking_structured_request_explicitly_disables_thinking(
    model: str,
) -> None:
    config = GatewayConfig(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model,
        "chat_completions",
        "secret-key-value",
        "2026-09-05T00:00:00Z",
        provider="qwen",
    )

    body = build_request_body(
        config,
        context_json='{"clips":[]}',
        keyframe_data_urls=["data:image/png;base64,AA=="],
        output_schema={"type": "object"},
    )

    assert body["enable_thinking"] is False
    assert body["stream"] is True
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body


def test_structured_output_schema_removes_unsupported_keywords_without_mutating_contract() -> None:
    contract = {
        "type": "object",
        "additionalProperties": False,
        "required": ["evidence_ids"],
        "properties": {
            "evidence_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^evidence_"},
            },
            "not": {"type": "string"},
        },
        "$defs": {
            "branch": {
                "allOf": [{"type": "string"}],
                "not": {"const": "forbidden"},
            }
        },
    }

    relay_schema = structured_output_schema(contract)

    evidence_ids = relay_schema["properties"]["evidence_ids"]
    assert "uniqueItems" not in evidence_ids
    assert evidence_ids["minItems"] == 1
    assert evidence_ids["items"]["pattern"] == "^evidence_"
    assert relay_schema["properties"]["not"] == {"type": "string"}
    assert "allOf" not in relay_schema["$defs"]["branch"]
    assert "not" not in relay_schema["$defs"]["branch"]
    assert contract["properties"]["evidence_ids"]["uniqueItems"] is True
    assert "allOf" in contract["$defs"]["branch"]


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_every_gateway_mode_uses_relay_compatible_schema(mode: str) -> None:
    contract = {
        "type": "object",
        "properties": {
            "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
        },
        "required": ["tags"],
        "additionalProperties": False,
    }

    body = build_request_body(
        _config(mode),
        context_json='{"shots":[]}',
        keyframe_data_urls=[],
        output_schema=contract,
    )
    sent_schema = (
        body["text"]["format"]["schema"]
        if mode == "responses"
        else body["response_format"]["json_schema"]["schema"]
    )

    assert "uniqueItems" not in sent_schema["properties"]["tags"]
    assert contract["properties"]["tags"]["uniqueItems"] is True


@pytest.mark.parametrize(
    ("mode", "payload"),
    [
        ("responses", {"id": "resp_1", "status": "completed", "output_text": json.dumps({"ok": True})}),
        ("chat_completions", {"id": "chat_1", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"ok": True})}}]}),
    ],
)
def test_gateway_response_parses_both_modes(mode: str, payload: dict) -> None:
    result = parse_gateway_response(json.dumps(payload).encode(), mode)

    assert result.content == {"ok": True}
    assert result.response_id in {"resp_1", "chat_1"}


@pytest.mark.parametrize(
    ("mode", "payload"),
    [
        (
            "responses",
            {
                "id": "resp_array_1",
                "status": "completed",
                "output_text": '[{"image_token":"k7m2q9"}]',
            },
        ),
        (
            "chat_completions",
            {
                "id": "chat_array_1",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": '[{"image_token":"k7m2q9"}]'},
                }],
            },
        ),
    ],
)
def test_gateway_response_unwraps_exactly_one_object_from_model_array(
    mode: str,
    payload: dict,
) -> None:
    result = parse_gateway_response(json.dumps(payload).encode(), mode)

    assert result.content == {"image_token": "k7m2q9"}


@pytest.mark.parametrize(
    "model_output",
    [
        "[]",
        '[{"ok":true},{"ok":true}]',
        '["not-an-object"]',
    ],
)
def test_gateway_response_rejects_all_other_model_array_shapes(model_output: str) -> None:
    payload = {
        "id": "chat_invalid_array",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": model_output},
        }],
    }

    with pytest.raises(GatewayError) as caught:
        parse_gateway_response(json.dumps(payload).encode(), "chat_completions")

    assert caught.value.diagnostic_code == "incomplete_response"


def test_responses_stream_parses_completed_response() -> None:
    response = {
        "id": "resp_stream_1",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({"ok": True})}],
            }
        ],
    }
    raw = (
        "event: response.output_text.delta\n"
        f"data: {json.dumps({'type': 'response.output_text.delta', 'delta': '{'})}\n\n"
        "event: response.completed\n"
        f"data: {json.dumps({'type': 'response.completed', 'response': response})}\n\n"
    ).encode()

    result = parse_gateway_stream(raw, "responses")

    assert result.content == {"ok": True}
    assert result.response_id == "resp_stream_1"
    assert result.raw_bytes == len(raw)


def test_chat_completions_stream_joins_content_deltas() -> None:
    chunks = [
        {"id": "chat_stream_1", "choices": [{"delta": {"content": '{"ok":'}, "finish_reason": None}]},
        {"id": "chat_stream_1", "choices": [{"delta": {"content": "true}"}, "finish_reason": "stop"}]},
    ]
    raw = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode() + b"data: [DONE]\n\n"

    result = parse_gateway_stream(raw, "chat_completions")

    assert result.content == {"ok": True}
    assert result.response_id == "chat_stream_1"


def test_chat_stream_unwraps_exactly_one_object_from_model_array() -> None:
    chunks = [
        {
            "id": "chat_stream_array",
            "choices": [{
                "delta": {"content": '[{"image_token":"k7'},
                "finish_reason": None,
            }],
        },
        {
            "id": "chat_stream_array",
            "choices": [{
                "delta": {"content": 'm2q9"}]'},
                "finish_reason": "stop",
            }],
        },
    ]
    raw = (
        "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode()
        + b"data: [DONE]\n\n"
    )

    result = parse_gateway_stream(raw, "chat_completions")

    assert result.content == {"image_token": "k7m2q9"}


@pytest.mark.parametrize(
    "model_output",
    [
        "[]",
        '[{"ok":true},{"ok":true}]',
        '[42]',
    ],
)
def test_chat_stream_rejects_all_other_model_array_shapes(model_output: str) -> None:
    chunk = {
        "id": "chat_stream_invalid_array",
        "choices": [{
            "delta": {"content": model_output},
            "finish_reason": "stop",
        }],
    }
    raw = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()

    with pytest.raises(GatewayError) as caught:
        parse_gateway_stream(raw, "chat_completions")

    assert caught.value.diagnostic_code == "incomplete_response"
