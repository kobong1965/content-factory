from __future__ import annotations

import json

import pytest

from content_factory_api.s3_gateway import GatewayError, parse_gateway_response, parse_gateway_stream


def _assert_incomplete(error: GatewayError) -> None:
    assert error.retryable is True
    assert error.diagnostic_code == "incomplete_response"
    assert error.outcome == "incomplete"


def test_responses_stream_requires_explicit_completed_event() -> None:
    raw = (
        "event: response.output_text.done\n"
        f"data: {json.dumps({'type': 'response.output_text.done', 'text': '{\"ok\":true}'})}\n\n"
    ).encode()

    with pytest.raises(GatewayError) as caught:
        parse_gateway_stream(raw, "responses")

    _assert_incomplete(caught.value)


@pytest.mark.parametrize(
    ("status", "expected_outcome", "expected_retryable"),
    [
        ("incomplete", "incomplete", True),
        ("failed", "retryable_failure", True),
        ("cancelled", "cancelled", False),
        (None, "outcome_unknown", True),
    ],
)
def test_responses_json_requires_completed_status(
    status: str | None,
    expected_outcome: str,
    expected_retryable: bool,
) -> None:
    payload = {
        "id": "resp_incomplete",
        "status": status,
        "output_text": json.dumps({"ok": True}),
        "incomplete_details": {"reason": "max_output_tokens"},
    }

    with pytest.raises(GatewayError) as caught:
        parse_gateway_response(json.dumps(payload).encode(), "responses")

    assert caught.value.diagnostic_code == "incomplete_response"
    assert caught.value.outcome == expected_outcome
    assert caught.value.retryable is expected_retryable


@pytest.mark.parametrize("finish_reason", ["length", None])
def test_chat_json_rejects_nonterminal_finish_reason(finish_reason: str | None) -> None:
    payload = {
        "id": "chat_incomplete",
        "choices": [{
            "finish_reason": finish_reason,
            "message": {"content": json.dumps({"ok": True})},
        }],
    }

    with pytest.raises(GatewayError) as caught:
        parse_gateway_response(json.dumps(payload).encode(), "chat_completions")

    _assert_incomplete(caught.value)


def test_chat_stream_requires_terminal_finish_reason_and_done_marker() -> None:
    raw = (
        f"data: {json.dumps({'id': 'chat_partial', 'choices': [{'delta': {'content': '{\"ok\":true}'}, 'finish_reason': None}]})}\n\n"
        "data: [DONE]\n\n"
    ).encode()

    with pytest.raises(GatewayError) as caught:
        parse_gateway_stream(raw, "chat_completions")

    _assert_incomplete(caught.value)


def test_qwen_partial_response_header_cannot_be_marked_completed() -> None:
    payload = {
        "id": "chat_partial_header",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"ok": True})},
        }],
    }

    with pytest.raises(GatewayError) as caught:
        parse_gateway_response(
            json.dumps(payload).encode(),
            "chat_completions",
            provider_partial=True,
        )

    _assert_incomplete(caught.value)


def test_truncated_json_is_retryable_incomplete_not_permanent_success() -> None:
    payload = {
        "id": "chat_truncated_json",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": '{"ok":'},
        }],
    }

    with pytest.raises(GatewayError) as caught:
        parse_gateway_response(json.dumps(payload).encode(), "chat_completions")

    _assert_incomplete(caught.value)
