"""Transport regressions: real SSE sockets, not just assembled byte fixtures."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from content_factory_api import s3_gateway
from content_factory_api.s3_gateway import GatewayError, build_request_body, call_gateway
from content_factory_api.s3_settings import GatewayConfig


def _config(base="https://relay.example.com/v1", mode="chat_completions", model="gpt-6-astra"):
    return GatewayConfig(base, model, mode, "fixture-secret-do-not-log", "2026-09-09T00:00:00Z")


def _event(value):
    return b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\r\n\r\n"


def _chat_stream():
    return _event({"id": "chat_stream", "choices": [{"delta": {"content": '{"卖点":"裤型"}'}, "finish_reason": None}]}) + _event({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + b"data: [DONE]\r\n\r\n"


@contextmanager
def _relay(payload, *, hold_open=False, disconnect_before_headers=False, heartbeat=False, content_type='text/event-stream', length_padding=None):
    release = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if disconnect_before_headers:
                self.close_connection = True
                return
            body = payload(requests[-1]) if callable(payload) else payload
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("x-request-id", "req_local_stream")
            if length_padding is not None:
                self.send_header('Content-Length', str(len(body) + length_padding))
            self.end_headers()
            # Split UTF-8 and CRLF boundaries as well as JSON fields across writes.
            try:
                for index in range(0, len(body), 7):
                    self.wfile.write(body[index:index + 7])
                    self.wfile.flush()
                if heartbeat:
                    while not release.wait(0.025):
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                elif hold_open:
                    release.wait(4)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _config(f"http://127.0.0.1:{server.server_port}/v1"), requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
def test_astra_uses_streaming_and_supported_low_effort(mode):
    body = build_request_body(_config(mode=mode), context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"})
    assert body["stream"] is True
    assert (body["reasoning_effort"] if mode == "chat_completions" else body["reasoning"]["effort"]) == "low"


def test_chat_completes_on_done_without_waiting_for_socket_eof():
    with _relay(_chat_stream(), hold_open=True) as (config, requests):
        result = call_gateway(config, context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"}, timeout_seconds=0.4)
    assert requests[0]["stream"] is True
    assert result.content == {"卖点": "裤型"}
    assert result.completed_event_received is True
    assert result.first_event_at is not None
    assert result.request_started_at <= result.response_headers_at <= result.first_event_at <= result.completed_at


def test_missing_done_never_saves_parseable_partial_json_and_keeps_diagnostics():
    payload = _chat_stream().removesuffix(b"data: [DONE]\r\n\r\n")
    with _relay(payload) as (config, _requests):
        with pytest.raises(GatewayError) as caught:
            call_gateway(config, context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"}, timeout_seconds=1)
    error = caught.value
    assert error.outcome != "completed"
    assert error.transport_diagnostics["received_bytes"] == len(payload)
    assert error.transport_diagnostics["transport_stage"] == "receiving_response"
    assert error.transport_diagnostics["first_event_at"] is not None
    assert error.provider_request_id == "req_local_stream"
    assert "fixture-secret" not in str(error)


def test_disconnect_before_headers_is_distinguished_from_mid_stream():
    with _relay(b"", disconnect_before_headers=True) as (config, requests):
        with pytest.raises(GatewayError) as caught:
            call_gateway(config, context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"}, timeout_seconds=1)
    error = caught.value
    assert len(requests) == 1  # No silent endpoint or model hopping after disconnection.
    assert error.diagnostic_code == "connection_interrupted"
    assert error.transport_diagnostics["transport_stage"] == "waiting_headers"
    assert error.transport_diagnostics["transport_exception"] == "RemoteDisconnected"
    assert error.transport_diagnostics["received_bytes"] == 0
    assert "尚未收到响应头" in str(error)


def test_mid_stream_timeout_reports_received_bytes_without_leaking_partial_content():
    payload = _event({"choices": [{"delta": {"content": "fixture-secret-do-not-log"}}]})
    with _relay(payload, hold_open=True) as (config, _requests):
        with pytest.raises(GatewayError) as caught:
            call_gateway(config, context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"}, timeout_seconds=0.2)
    error = caught.value
    assert error.transport_diagnostics["received_bytes"] == len(payload)
    assert error.transport_diagnostics["transport_stage"] == "receiving_response"
    assert error.transport_diagnostics["transport_exception"] == "TimeoutError"
    assert error.outcome != "completed"
    assert "fixture-secret" not in str(error)


def test_chat_error_event_is_never_ignored_even_after_stop():
    payload = _chat_stream().removesuffix(b"data: [DONE]\r\n\r\n") + _event({"error": {"message": "fixture-secret-do-not-log", "code": "server_error"}}) + b"data: [DONE]\n\n"
    with pytest.raises(GatewayError) as caught:
        s3_gateway.parse_gateway_stream(payload, "chat_completions")
    assert caught.value.outcome != "completed"
    assert "fixture-secret" not in str(caught.value)


def test_responses_completed_event_does_not_wait_for_eof():
    payload = _event({"type": "response.completed", "response": {
        "id": "resp_local", "status": "completed", "error": None,
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": '{"ok":true}'}]}],
    }})
    with _relay(payload, hold_open=True) as (config, _requests):
        from dataclasses import replace
        result = call_gateway(replace(config, api_mode="responses"), context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"}, timeout_seconds=0.4)
    assert result.content == {"ok": True}
    assert result.provider_status == "completed"


def test_heartbeat_stream_has_a_total_deadline_not_an_infinite_idle_reset():
    started = time.monotonic()
    with _relay(b": starting\n\n", heartbeat=True) as (config, _requests):
        with pytest.raises(GatewayError) as caught:
            call_gateway(config, context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"}, timeout_seconds=0.2)
    assert time.monotonic() - started < 3
    assert 0.5 <= caught.value.transport_diagnostics["elapsed_seconds"] < 2
    assert caught.value.transport_diagnostics["received_bytes"] > 0


def test_no_body_timeout_has_no_invented_first_event():
    with _relay(b"", hold_open=True) as (config, _requests):
        with pytest.raises(GatewayError) as caught:
            call_gateway(config, context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"}, timeout_seconds=0.2)
    assert caught.value.transport_diagnostics["transport_stage"] == "waiting_first_byte"
    assert caught.value.transport_diagnostics["first_event_at"] is None
    assert caught.value.transport_diagnostics["received_bytes"] == 0


def test_end_marker_text_inside_content_does_not_end_the_stream_early():
    payload = _event({"choices": [{"delta": {"content": '{"text":"data: [DONE]\\n\\n"}'}, "finish_reason": None}]})
    payload += _event({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n"
    with _relay(payload, hold_open=True) as (config, _requests):
        result = call_gateway(config, context_json="{}", keyframe_data_urls=[], output_schema={"type": "object"}, timeout_seconds=0.4)
    assert result.content == {"text": "data: [DONE]\n\n"}


def test_interrupted_analysis_keeps_failure_evidence_then_completed_retry_reopens(tmp_path):
    from content_factory_api.s3_analysis import process_analysis
    from content_factory_contracts import validate_or_raise
    from test_s3_segmented_analysis import _media_result, _semantic_from_segment

    finish = False
    def payload(body):
        context = json.loads(body['messages'][1]['content'][0]['text'])
        output = _semantic_from_segment(context)
        stream = _event({'id': 'chat_analysis', 'choices': [{'delta': {'content': json.dumps(output, ensure_ascii=False)}, 'finish_reason': None}]})
        if finish:
            stream += _event({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}) + b'data: [DONE]\n\n'
        return stream

    task_dir = tmp_path / 'analysis'
    media_path = _media_result(tmp_path, shot_count=1)
    with _relay(payload) as (config, requests):
        def run():
            return process_analysis(media_result_path=media_path, input_payload={}, task_directory=task_dir, config=config, ocr_engine=lambda _: None)
        with pytest.raises(GatewayError):
            run()
        assert not (task_dir / 'analysis-report.json').exists()
        assert not list((task_dir / 'segments').glob('*.json'))
        events = [json.loads(line) for line in (task_dir / 'diagnostics/analysis-events.jsonl').read_text(encoding='utf-8').splitlines()]
        failed = next(item for item in events if item['event'] == 'segment_failed')
        assert failed['transport_stage'] == 'receiving_response'
        assert failed['received_bytes'] > 0 and failed['first_event_at'] is not None
        finish = True
        result = run()
        assert len(requests) == 2
        report = json.loads(result.report_path.read_text(encoding='utf-8'))
        validate_or_raise('analysis', report)
        assert report['status'] == 'draft' and report['revision'] == 1
        assert report['timeline'] and report['evidence'] and report['pattern_candidates']
        # Reopen the persisted checkpoint with no third network request.
        reopened = run()
        validate_or_raise('analysis', json.loads(reopened.report_path.read_text(encoding='utf-8')))
        assert len(requests) == 2


@pytest.mark.parametrize('mode', ['chat_completions', 'responses'])
def test_unterminated_final_sse_event_at_eof_is_not_a_completed_result(mode):
    if mode == 'chat_completions':
        payload = _chat_stream().removesuffix(b'\r\n\r\n')
    else:
        payload = _event({'type': 'response.completed', 'response': {
            'id': 'resp_truncated', 'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}],
        }}).removesuffix(b'\r\n\r\n')
    with pytest.raises(GatewayError):
        s3_gateway.parse_gateway_stream(payload, mode)


def test_short_content_length_json_is_not_accepted_just_because_json_is_parseable():
    payload = json.dumps({'id': 'chat_short', 'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]}).encode()
    with _relay(payload, content_type='application/json', length_padding=80) as (config, _requests):
        with pytest.raises(GatewayError) as caught:
            call_gateway(config, context_json='{}', keyframe_data_urls=[], output_schema={'type': 'object'}, timeout_seconds=1)
    assert caught.value.diagnostic_code == 'connection_interrupted'
    assert caught.value.transport_diagnostics['received_bytes'] == len(payload)
    assert caught.value.transport_diagnostics['transport_exception'] == 'IncompleteRead'


def test_incremental_reader_retains_the_20mb_hard_limit(monkeypatch):
    from email.message import Message
    from io import BytesIO
    from types import SimpleNamespace
    from content_factory_api.s3_gateway_transport import MAX_RESPONSE_BYTES

    class Response(BytesIO):
        status = 200
        headers = Message()
    response = Response(b'x' * (MAX_RESPONSE_BYTES + 50_000))
    response.headers['Content-Type'] = 'application/json'
    monkeypatch.setattr(s3_gateway, 'build_opener', lambda *_: SimpleNamespace(open=lambda *_a, **_k: response))
    with pytest.raises(GatewayError) as caught:
        call_gateway(_config(), context_json='{}', keyframe_data_urls=[], output_schema={'type': 'object'}, timeout_seconds=3)
    assert caught.value.diagnostic_code == 'response_too_large'
    assert caught.value.retryable is False
