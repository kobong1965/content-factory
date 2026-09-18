"""OpenAI-compatible relay gateway with strict structured-output handling."""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import get_close_matches
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .s3_settings import GatewayConfig
from .s3_gateway_transport import TransportInterrupted, read_transport, transport_diagnostics

_DEVELOPER_INSTRUCTIONS = """你是抖音国内男装短视频分析器。只依据输入的镜头、关键帧、ASR、OCR、指标和评论分析。每个核心结论必须绑定输入中存在的证据 ID；没有数据就明确降低置信度并标记推断。不得虚构商品参数、经营数据、评论或平台规则。严格返回指定 JSON Schema。"""

# Structured Outputs accepts only a subset of JSON Schema.  Keep the canonical
# contract untouched for local validation, and remove output-only validation
# keywords only from the copy sent to an OpenAI-compatible relay.
_UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS = frozenset(
    {
        "allOf",
        "not",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
        "uniqueItems",
    }
)
_PUBLIC_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_SENSITIVE_TOKEN_PATTERN = re.compile(r"(?:bearer|api[_-]?key|apikey|secret|^sk-[A-Za-z0-9_-]{8,}$)", re.IGNORECASE)
_QWEN_IMAGE_REJECTING_ALIASES = frozenset({"qwen3.7-max"})

GatewayOutcome = Literal[
    "completed",
    "retryable_failure",
    "permanent_failure",
    "incomplete",
    "cancelled",
    "outcome_unknown",
]


class GatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        diagnostic_code: str | None = None,
        endpoint_url: str | None = None,
        outcome: GatewayOutcome | None = None,
        retry_after_seconds: float | None = None,
        provider_request_id: str | None = None,
        provider_error_type: str | None = None,
        provider_error_code: str | None = None,
        transport_diagnostics: dict[str, Any] | None = None,
    ) -> None:
        self.retryable = retryable
        self.status_code = status_code
        self.diagnostic_code = diagnostic_code
        self.endpoint_url = endpoint_url
        self.outcome: GatewayOutcome = outcome or (
            "retryable_failure" if retryable else "permanent_failure"
        )
        self.retry_after_seconds = retry_after_seconds
        self.provider_request_id = provider_request_id
        self.provider_error_type = provider_error_type
        self.provider_error_code = provider_error_code
        self.transport_diagnostics = transport_diagnostics or {}
        super().__init__(message)


def _bounded_environment_integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


class _ProviderModelRateLimiter:
    """Process-local concurrency and rolling-minute guard keyed by provider/model."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: dict[tuple[str, str], int] = {}
        self._requests: dict[tuple[str, str], deque[tuple[float, int]]] = {}

    @contextmanager
    def reserve(
        self,
        config: GatewayConfig,
        *,
        estimated_input_tokens: int,
        wait_timeout_seconds: float,
    ):
        maximum_concurrency = _bounded_environment_integer(
            "CONTENT_FACTORY_MODEL_MAX_CONCURRENCY", 2, minimum=1, maximum=8,
        )
        requests_per_minute = _bounded_environment_integer(
            "CONTENT_FACTORY_MODEL_RPM", 30, minimum=1, maximum=600,
        )
        input_tokens_per_minute = _bounded_environment_integer(
            "CONTENT_FACTORY_MODEL_INPUT_TPM", 300_000, minimum=10_000, maximum=10_000_000,
        )
        if estimated_input_tokens > input_tokens_per_minute:
            raise GatewayError(
                "单个片段输入超过本地模型 TPM 安全上限，请减少片段时长或关键帧数。",
                retryable=False,
                diagnostic_code="local_token_budget_exceeded",
                outcome="permanent_failure",
            )
        key = (str(config.provider), config.model)
        deadline = time.monotonic() + max(0.1, min(float(wait_timeout_seconds), 300.0))
        acquired = False
        with self._condition:
            request_window = self._requests.setdefault(key, deque())
            while True:
                now = time.monotonic()
                while request_window and now - request_window[0][0] >= 60.0:
                    request_window.popleft()
                active = self._active.get(key, 0)
                token_total = sum(tokens for _moment, tokens in request_window)
                if (
                    active < maximum_concurrency
                    and len(request_window) < requests_per_minute
                    and token_total + estimated_input_tokens <= input_tokens_per_minute
                ):
                    self._active[key] = active + 1
                    request_window.append((now, estimated_input_tokens))
                    acquired = True
                    break
                remaining = deadline - now
                if remaining <= 0:
                    retry_after = 1.0
                    if request_window:
                        retry_after = max(0.1, 60.0 - (now - request_window[0][0]))
                    raise GatewayError(
                        "本地供应商/模型限流器正在削峰，该片段稍后重试。",
                        retryable=True,
                        diagnostic_code="local_rate_limit_wait",
                        retry_after_seconds=min(300.0, retry_after),
                    )
                wake_after = min(remaining, 0.25)
                if request_window:
                    wake_after = min(wake_after, max(0.01, 60.0 - (now - request_window[0][0])))
                self._condition.wait(timeout=wake_after)
        try:
            yield
        finally:
            if acquired:
                with self._condition:
                    active = self._active.get(key, 0)
                    if active <= 1:
                        self._active.pop(key, None)
                    else:
                        self._active[key] = active - 1
                    self._condition.notify_all()


_MODEL_RATE_LIMITER = _ProviderModelRateLimiter()


class _NoRedirectHandler(HTTPRedirectHandler):
    """Never forward a bearer credential through an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


@dataclass(frozen=True)
class GatewayResult:
    content: dict[str, Any]
    response_id: str | None
    raw_bytes: int
    endpoint_url: str | None = None
    outcome: GatewayOutcome = "completed"
    provider_status: str | None = None
    finish_reason: str | None = None
    completed_event_received: bool = True
    provider_partial: bool = False
    request_started_at: str | None = None
    response_headers_at: str | None = None
    first_event_at: str | None = None
    last_event_at: str | None = None
    completed_at: str | None = None
    http_status: int | None = None


def gateway_endpoint(config: GatewayConfig) -> str:
    """Return the normalized primary endpoint used for a gateway request."""

    suffix = "responses" if config.api_mode == "responses" else "chat/completions"
    base = config.base_url.rstrip("/")
    if base.endswith(("/responses", "/chat/completions")):
        return re.sub(r"/(?:responses|chat/completions)$", f"/{suffix}", base)
    return f"{base}/{suffix}"


def _endpoint_candidates(config: GatewayConfig) -> tuple[str, ...]:
    """Return same-origin endpoint variants without ever redirecting a key."""

    primary = gateway_endpoint(config)
    base = config.base_url.rstrip("/")
    if base.endswith(("/v1", "/responses", "/chat/completions")):
        return (primary,)
    suffix = "responses" if config.api_mode == "responses" else "chat/completions"
    fallback = f"{base}/v1/{suffix}"
    return (primary,) if fallback == primary else (primary, fallback)


def _request_context(config: GatewayConfig, endpoint_url: str) -> str:
    model = "".join(character if character.isprintable() else "?" for character in config.model)[:120]
    return f"模型：{model}；模式：{config.api_mode}；请求：{endpoint_url}"


def _safe_public_token(value: str, *, limit: int = 120) -> str:
    candidate = value.strip()[:limit]
    if not _PUBLIC_TOKEN_PATTERN.fullmatch(candidate) or _SENSITIVE_TOKEN_PATTERN.search(candidate):
        return ""
    return candidate


def _supports_none_reasoning_effort(model: str) -> bool:
    """Return true only for the GPT family verified to accept `none`."""

    return model.strip().lower().startswith("gpt-5.6-")


def _reasoning_effort(model: str) -> str | None:
    if _supports_none_reasoning_effort(model):
        return "none"
    # GPT-6 Astra accepts low, not none; do not infer support from display names.
    if re.fullmatch(r"gpt-6-astra(?:-\d{4}-\d{2}-\d{2})?", model.strip().lower()):
        return "low"
    return None


def _supports_qwen_non_thinking_mode(model: str) -> bool:
    """Limit DashScope's non-standard flag to documented mixed-thinking families."""

    normalized = model.strip().lower()
    return normalized.startswith(("qwen3.7-", "qwen3.8-", "qwen3-vl-"))


def structured_output_schema(output_schema: dict[str, Any]) -> dict[str, Any]:
    """Return a relay-compatible schema without mutating the local contract."""

    def transform(value: Any, *, schema_node: bool = True) -> Any:
        if isinstance(value, dict):
            if not schema_node:
                return {key: transform(item) for key, item in value.items()}
            return {
                key: transform(item, schema_node=key not in {"properties", "$defs", "definitions"})
                for key, item in value.items()
                if key not in _UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS
            }
        if isinstance(value, list):
            return [transform(item) for item in value]
        return value

    return transform(output_schema)


def build_request_body(
    config: GatewayConfig,
    *,
    context_json: str,
    keyframe_data_urls: list[str],
    output_schema: dict[str, Any],
    developer_instructions: str = _DEVELOPER_INSTRUCTIONS,
    schema_name: str = "content_factory_deep_analysis",
    strict_schema: bool = True,
) -> dict[str, Any]:
    relay_schema = structured_output_schema(output_schema)
    reasoning_effort = _reasoning_effort(config.model)
    effective_instructions = developer_instructions
    if not strict_schema:
        effective_instructions = (
            f"{developer_instructions}\n"
            "当前中转站只接受 JSON Object 模式。仅输出一个 JSON 对象，"
            "不要输出 Markdown 代码块或解释，并严格满足以下 JSON Schema："
            f"{json.dumps(relay_schema, ensure_ascii=False, separators=(',', ':'))}"
        )
    if config.api_mode == "responses":
        content: list[dict[str, Any]] = [{"type": "input_text", "text": context_json}]
        content.extend({"type": "input_image", "image_url": value, "detail": "low"} for value in keyframe_data_urls)
        body: dict[str, Any] = {
            "model": config.model,
            "instructions": effective_instructions,
            "input": [{"role": "user", "content": content}],
            "text": {
                "verbosity": "low",
                "format": (
                    {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": relay_schema,
                    }
                    if strict_schema else {"type": "json_object"}
                )
            },
            "max_output_tokens": 16_000,
            "store": False,
            "stream": True,
        }
        if reasoning_effort is not None:
            # Deep analysis needs schema adherence, but not a long hidden
            # reasoning pass.  This keeps synchronous relays below common
            # upstream time limits without changing the visible contract.
            body["reasoning"] = {"effort": reasoning_effort}
        return body

    user_content: list[dict[str, Any]] = [{"type": "text", "text": context_json}]
    user_content.extend(
        {
            "type": "image_url",
            "image_url": (
                {"url": value}
                if config.provider == "qwen"
                else {"url": value, "detail": "low"}
            ),
        }
        for value in keyframe_data_urls
    )
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": effective_instructions},
            {"role": "user", "content": user_content},
        ],
        "response_format": (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": relay_schema,
                },
            }
            if strict_schema else {"type": "json_object"}
        ),
        "stream": True,
    }
    # Qwen output limits vary by exact model. Omitting an explicit token cap
    # uses the model's supported default instead of exceeding a smaller VL cap.
    if config.provider == "qwen" and _supports_qwen_non_thinking_mode(config.model):
        # DashScope exposes this non-standard top-level flag for its
        # mixed-thinking model families. Structured jobs need only the final
        # JSON content, so make the mode deterministic and avoid returning a
        # separate reasoning pass that this client intentionally does not use.
        body["enable_thinking"] = False
    elif reasoning_effort is not None:
        # OpenAI-compatible GPT-5 Chat endpoints otherwise spend enough time
        # on hidden reasoning that some synchronous relays close the socket.
        body["reasoning_effort"] = reasoning_effort
    return body


def _incomplete_response(
    message: str,
    *,
    outcome: GatewayOutcome = "incomplete",
    retryable: bool = True,
    response_id: str | None = None,
) -> GatewayError:
    return GatewayError(
        message,
        retryable=retryable,
        diagnostic_code="incomplete_response",
        outcome=outcome,
        provider_request_id=response_id,
    )


def _response_id(payload: dict[str, Any]) -> str | None:
    candidate = payload.get("id")
    return candidate if isinstance(candidate, str) else None


def _validate_responses_completion(payload: dict[str, Any]) -> str:
    """Require the explicit terminal status defined by the Responses API."""

    status = payload.get("status")
    response_id = _response_id(payload)
    if status == "completed":
        if payload.get("error") or payload.get("incomplete_details"):
            raise _incomplete_response(
                "模型响应状态与错误详情冲突，不能确认输出完整",
                outcome="outcome_unknown",
                response_id=response_id,
            )
        return status
    if status == "cancelled":
        raise _incomplete_response(
            "模型响应已被上游取消",
            outcome="cancelled",
            retryable=False,
            response_id=response_id,
        )
    if status == "failed":
        raise _incomplete_response(
            "模型响应明确失败，未产生可提交的完整结果",
            outcome="retryable_failure",
            response_id=response_id,
        )
    if status == "incomplete":
        reason = ""
        details = payload.get("incomplete_details")
        if isinstance(details, dict) and isinstance(details.get("reason"), str):
            reason = f"（{details['reason'][:120]}）"
        raise _incomplete_response(
            f"模型响应未完整结束{reason}",
            response_id=response_id,
        )
    raise _incomplete_response(
        "模型响应缺少可确认成功的 completed 状态",
        outcome="outcome_unknown" if status is None else "incomplete",
        response_id=response_id,
    )


def _validate_chat_completion(payload: dict[str, Any]) -> str:
    response_id = _response_id(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise _incomplete_response(
            "模型响应缺少完整的 choices[0]",
            response_id=response_id,
        )
    finish_reason = choices[0].get("finish_reason")
    if finish_reason == "stop":
        return finish_reason
    if finish_reason in {None, "length"}:
        label = "null" if finish_reason is None else str(finish_reason)
        raise _incomplete_response(
            f"模型输出未正常结束（finish_reason={label}）",
            response_id=response_id,
        )
    raise _incomplete_response(
        f"模型没有返回可提交的结构化文本（finish_reason={str(finish_reason)[:80]}）",
        outcome="permanent_failure",
        retryable=False,
        response_id=response_id,
    )


def _extract_text(payload: dict[str, Any], mode: str) -> str:
    if mode == "responses":
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        for item in payload.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    return content["text"]
        raise _incomplete_response("中转站返回中没有可读取的结构化文本", response_id=_response_id(payload))

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise _incomplete_response(
            "中转站返回中没有 choices[0].message.content",
            response_id=_response_id(payload),
        ) from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        if any(texts):
            return "".join(texts)
    raise _incomplete_response("中转站返回的消息内容不是文本", response_id=_response_id(payload))


def _decode_model_json(text: str, *, streamed: bool = False) -> dict[str, Any]:
    candidate = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    try:
        content = json.loads(candidate)
    except json.JSONDecodeError as exc:
        qualifier = "流式" if streamed else ""
        raise _incomplete_response(f"模型{qualifier}输出不是符合约定的 JSON") from exc
    # Some otherwise OpenAI-compatible providers wrap a structured object in
    # one array element even when given an object schema. Accept only that
    # unambiguous shape; broad array coercion could silently discard results.
    if isinstance(content, list):
        if len(content) != 1 or not isinstance(content[0], dict):
            raise _incomplete_response("模型输出数组必须恰好包含一个对象")
        content = content[0]
    if not isinstance(content, dict):
        raise _incomplete_response("模型输出顶层不是对象")
    return content


def parse_gateway_response(
    raw: bytes,
    mode: str,
    *,
    provider_partial: bool = False,
) -> GatewayResult:
    if provider_partial:
        raise _incomplete_response("上游明确标记返回内容为部分响应")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _incomplete_response(
            "中转站没有返回完整的 JSON 响应",
            outcome="outcome_unknown",
        ) from exc
    if not isinstance(payload, dict):
        raise _incomplete_response("中转站响应顶层不是对象")
    if mode == "responses":
        provider_status = _validate_responses_completion(payload)
        finish_reason = None
    else:
        provider_status = None
        finish_reason = _validate_chat_completion(payload)
    text = _extract_text(payload, mode)
    content = _decode_model_json(text)
    response_id = _response_id(payload)
    return GatewayResult(
        content=content,
        response_id=response_id,
        raw_bytes=len(raw),
        provider_status=provider_status,
        finish_reason=finish_reason,
        provider_partial=provider_partial,
    )


def parse_gateway_stream(
    raw: bytes,
    mode: str,
    *,
    provider_partial: bool = False,
) -> GatewayResult:
    """Parse OpenAI-compatible Server-Sent Events into one JSON result."""

    try:
        stream_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _incomplete_response(
            "中转站流式响应不是完整的 UTF-8",
            outcome="outcome_unknown",
        ) from exc

    if provider_partial:
        raise _incomplete_response("上游明确标记流式内容为部分响应")

    response_id: str | None = None
    completed_response: dict[str, Any] | None = None
    completed_text: str | None = None
    deltas: list[str] = []
    done_marker_received = False
    finish_reason: str | None = None
    # EOF is not an SSE event delimiter. Never treat an unterminated final
    # [DONE]/response.completed block as proof of successful completion.
    for block in stream_text.replace("\r\n", "\n").split("\n\n")[:-1]:
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        if data == "[DONE]":
            done_marker_received = True
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise _incomplete_response("中转站流式响应包含截断或无效 JSON 事件") from exc
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if event.get("error") is not None or (mode != "responses" and event_type == "error"):
            raise _incomplete_response(
                "中转站流式生成返回错误，未保存为成功结果",
                outcome="retryable_failure",
            )
        if mode == "responses":
            if event_type == "response.completed" and isinstance(event.get("response"), dict):
                completed_response = event["response"]
                candidate_id = completed_response.get("id")
                response_id = candidate_id if isinstance(candidate_id, str) else response_id
            elif event_type == "response.output_text.done" and isinstance(event.get("text"), str):
                completed_text = event["text"]
            elif event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
                deltas.append(event["delta"])
            elif event_type == "response.incomplete":
                response = event.get("response")
                if isinstance(response, dict):
                    _validate_responses_completion(response)
                raise _incomplete_response("中转站流式生成未完整结束")
            elif event_type == "response.failed":
                response = event.get("response")
                if isinstance(response, dict):
                    _validate_responses_completion(response)
                raise _incomplete_response(
                    "中转站流式生成失败",
                    outcome="retryable_failure",
                )
            elif event_type == "response.cancelled":
                raise _incomplete_response(
                    "中转站流式生成已取消",
                    outcome="cancelled",
                    retryable=False,
                )
            elif event_type == "error":
                raise _incomplete_response(
                    "中转站流式生成失败，任务稍后可重试",
                    outcome="retryable_failure",
                )
            continue

        candidate_id = event.get("id")
        if isinstance(candidate_id, str):
            response_id = candidate_id
        choices = event.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            candidate_finish = choice.get("finish_reason")
            if isinstance(candidate_finish, str):
                finish_reason = candidate_finish
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                deltas.append(delta["content"])

    if completed_response is not None:
        encoded = json.dumps(completed_response, ensure_ascii=False).encode("utf-8")
        result = parse_gateway_response(encoded, "responses")
        return GatewayResult(
            result.content,
            result.response_id or response_id,
            len(raw),
            outcome="completed",
            provider_status=result.provider_status,
            completed_event_received=True,
        )

    if mode == "responses":
        raise _incomplete_response(
            "流式输出在 response.completed 事件前结束",
            response_id=response_id,
        )

    if not done_marker_received or finish_reason not in {"stop"}:
        marker = "未收到 [DONE]" if not done_marker_received else f"finish_reason={finish_reason or 'null'}"
        raise _incomplete_response(
            f"流式输出未完整结束（{marker}）",
            response_id=response_id,
        )

    final_text = completed_text if completed_text is not None else "".join(deltas)
    if not final_text.strip():
        raise _incomplete_response("中转站流式响应中没有可读取的结构化文本", response_id=response_id)
    content = _decode_model_json(final_text, streamed=True)
    return GatewayResult(
        content=content,
        response_id=response_id,
        raw_bytes=len(raw),
        finish_reason=finish_reason,
        completed_event_received=done_marker_received,
    )


def _provider_error(raw: bytes) -> tuple[str, str, str]:
    """Extract bounded diagnostic fields without echoing arbitrary response data."""

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "", "", ""
    if not isinstance(payload, dict):
        return "", "", ""
    detail = payload.get("error", payload)
    if not isinstance(detail, dict):
        return "", "", ""

    def clean(field: str, limit: int) -> str:
        value = detail.get(field)
        if not isinstance(value, (str, int)):
            return ""
        return "".join(character if character.isprintable() else " " for character in str(value))[:limit]

    return clean("type", 100), clean("code", 100), clean("message", 500)


def _looks_like_model_error(error_type: str, error_code: str, message: str) -> bool:
    diagnostic = " ".join((error_type, error_code, message)).lower()
    return (
        "model_not_found" in diagnostic
        or "model not found" in diagnostic
        or "模型不存在" in diagnostic
        or "不支持该模型" in diagnostic
        or ("model" in diagnostic and any(marker in diagnostic for marker in (
            "not supported", "not configured", "does not exist", "unknown model", "invalid model",
        )))
    )


def _looks_like_schema_compatibility_error(error_type: str, error_code: str, message: str) -> bool:
    diagnostic = " ".join((error_type, error_code, message)).lower()
    mentions_schema = any(marker in diagnostic for marker in (
        "response_format", "json_schema", "json schema", "structured output", "响应格式", "结构化输出",
    ))
    rejects_feature = any(marker in diagnostic for marker in (
        "unsupported", "not support", "invalid", "unknown", "not allowed", "not available",
        "不支持", "无效", "未知",
    ))
    return mentions_schema and rejects_feature


def _looks_like_exhausted_quota(error_type: str, error_code: str, message: str) -> bool:
    diagnostic = " ".join((error_type, error_code, message)).lower()
    return any(marker in diagnostic for marker in (
        "insufficient_quota",
        "billing_hard_limit",
        "billing_not_active",
        "quota_exhausted",
        "account_balance",
        "balance insufficient",
        "余额不足",
        "配额耗尽",
        "欠费",
    ))


def _header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    expected = name.lower()
    for key, value in headers.items():
        if str(key).lower() == expected:
            return str(value)
    return None


def _parse_retry_after(headers: Mapping[str, str] | None) -> float | None:
    value = _header_value(headers, "retry-after")
    if not value:
        return None
    try:
        return max(0.0, min(300.0, float(value.strip())))
    except ValueError:
        try:
            instant = parsedate_to_datetime(value)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=UTC)
            return max(0.0, min(300.0, (instant - datetime.now(UTC)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None


def _looks_like_invalid_image(error_type: str, error_code: str, message: str) -> bool:
    diagnostic = " ".join((error_type, error_code, message)).lower()
    return any(marker in diagnostic for marker in (
        "image length and width",
        "image dimensions",
        "image resolution",
        "invalid image",
        "image format",
        "image size",
        "图片尺寸",
        "图片格式",
    ))


def _looks_like_unsupported_image_input(
    error_type: str,
    error_code: str,
    message: str,
) -> bool:
    diagnostic = " ".join((error_type, error_code, message)).lower()
    return any(marker in diagnostic for marker in (
        "unexpected item type in content",
        "image input is not supported",
        "image inputs are not supported",
        "does not support image",
        "unsupported image input",
        "不支持图片输入",
        "不支持图像输入",
    ))


def _api_roots(config: GatewayConfig) -> tuple[str, ...]:
    base = re.sub(r"/(?:responses|chat/completions)$", "", config.base_url.rstrip("/"))
    if base.endswith("/v1"):
        return (base,)
    return (base, f"{base}/v1")


def _discover_model_ids(config: GatewayConfig, *, timeout_seconds: int) -> list[str]:
    for root in _api_roots(config):
        request = Request(
            f"{root}/models",
            headers={"Authorization": f"Bearer {config.api_key}", "Accept": "application/json"},
        )
        try:
            with build_opener(_NoRedirectHandler()).open(request, timeout=max(1, min(timeout_seconds, 10))) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            socket.timeout,
            ssl.SSLError,
            http.client.HTTPException,
        ):
            continue
        if len(raw) > 2 * 1024 * 1024:
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            continue
        model_ids: list[str] = []
        for item in payload["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            model_id = _safe_public_token(item["id"])
            if model_id:
                model_ids.append(model_id)
            if len(model_ids) == 500:
                break
        return model_ids
    return []


def _http_gateway_error(
    config: GatewayConfig,
    endpoint_url: str,
    status: int,
    raw: bytes,
    *,
    timeout_seconds: int,
    has_image_input: bool = False,
    response_headers: Mapping[str, str] | None = None,
) -> GatewayError:
    error_type, error_code, message = _provider_error(raw)
    context = _request_context(config, endpoint_url)
    upstream_code = (
        _safe_public_token(error_code, limit=100)
        or _safe_public_token(error_type, limit=100)
    )
    provider_request_id = (
        _safe_public_token(_header_value(response_headers, "x-request-id") or "")
        or _safe_public_token(_header_value(response_headers, "request-id") or "")
        or _safe_public_token(_header_value(response_headers, "openai-request-id") or "")
    ) or None
    if status in {401, 403}:
        return GatewayError(
            f"中转站拒绝了密钥，请检查 API Key。（{context}）",
            retryable=False, status_code=status, diagnostic_code="authentication_failed",
            endpoint_url=endpoint_url, provider_request_id=provider_request_id,
            provider_error_type=error_type or None, provider_error_code=error_code or None,
        )
    if status == 429:
        if _looks_like_exhausted_quota(error_type, error_code, message):
            return GatewayError(
                f"中转站账户余额或配额不足，自动重试已停止。（{context}）",
                retryable=False,
                status_code=status,
                diagnostic_code="quota_exhausted",
                endpoint_url=endpoint_url,
                outcome="permanent_failure",
                provider_request_id=provider_request_id,
                provider_error_type=error_type or None,
                provider_error_code=error_code or None,
            )
        return GatewayError(
            f"中转站当前限流，任务稍后可重试。（{context}）",
            retryable=True, status_code=status, diagnostic_code="rate_limited", endpoint_url=endpoint_url,
            retry_after_seconds=_parse_retry_after(response_headers),
            provider_request_id=provider_request_id,
            provider_error_type=error_type or None, provider_error_code=error_code or None,
        )
    if status == 408 or status >= 500:
        return GatewayError(
            f"中转站暂时不可用或请求超时，任务稍后可重试。（{context}）",
            retryable=True, status_code=status, diagnostic_code="upstream_unavailable",
            endpoint_url=endpoint_url, retry_after_seconds=_parse_retry_after(response_headers),
            provider_request_id=provider_request_id,
            provider_error_type=error_type or None, provider_error_code=error_code or None,
        )

    model_ids: list[str] = []
    model_not_found = _looks_like_model_error(error_type, error_code, message)
    if status == 404 or model_not_found:
        model_ids = _discover_model_ids(config, timeout_seconds=timeout_seconds)
    if status == 404 and not model_not_found and model_ids and config.model not in model_ids:
        model_not_found = True
    if model_not_found:
        suggestions = [
            safe
            for candidate in get_close_matches(config.model, model_ids, n=3, cutoff=0.55)
            if (safe := _safe_public_token(candidate))
        ]
        suggestion = f"；可用模型建议：{'、'.join(suggestions)}" if suggestions else ""
        code_hint = f"；上游代码：{upstream_code}" if upstream_code else ""
        return GatewayError(
            f"中转站找不到模型标识“{config.model}”（HTTP {status}）{suggestion}{code_hint}。"
            f"请从该中转站的模型列表复制完整标识，注意中间的连字符。（{context}）",
            retryable=False,
            status_code=status,
            diagnostic_code="model_not_found",
            endpoint_url=endpoint_url,
        )
    if status == 404:
        return GatewayError(
            f"中转站没有这个接口路径（HTTP 404）。请检查 API 接口地址是否应以 /v1 "
            f"或服务商指定的兼容路径结尾。（{context}）",
            retryable=False, status_code=status, diagnostic_code="endpoint_not_found",
            endpoint_url=endpoint_url,
        )
    if has_image_input and _looks_like_invalid_image(error_type, error_code, message):
        return GatewayError(
            f"中转站拒绝了测试图片或关键帧的尺寸、格式或编码（HTTP {status}）。"
            f"请改用宽高均大于 10 像素的有效 PNG/JPEG 图片。（{context}）",
            retryable=False,
            status_code=status,
            diagnostic_code="image_input_invalid",
            endpoint_url=endpoint_url,
        )
    if has_image_input and _looks_like_unsupported_image_input(
        error_type, error_code, message,
    ):
        recommendation = (
            "当前账户与地域下，qwen3.7-max 浮动别名未接受这次图片输入；"
            "请优先改用已通过图像与严格结构化输出测试的 qwen3.7-plus，"
            "或该服务商其他明确支持图像输入的模型。"
            if config.provider == "qwen" and config.model.strip().lower() in _QWEN_IMAGE_REJECTING_ALIASES
            else "请改用该服务商明确支持图像输入的多模态模型。"
        )
        return GatewayError(
            f"模型存在且接口可访问，但当前模型拒绝图像输入（HTTP {status}）。"
            f"{recommendation}（{context}）",
            retryable=False,
            status_code=status,
            diagnostic_code="image_input_unsupported",
            endpoint_url=endpoint_url,
        )
    if _looks_like_schema_compatibility_error(error_type, error_code, message):
        return GatewayError(
            f"该模型或中转站不支持结构化 JSON 输出（HTTP {status}）。（{context}）",
            retryable=False, status_code=status, diagnostic_code="structured_output_unsupported",
            endpoint_url=endpoint_url,
        )
    code_hint = f"；上游代码：{upstream_code}" if upstream_code else ""
    return GatewayError(
        f"中转站请求不兼容（HTTP {status}{code_hint}）。（{context}）",
        retryable=False, status_code=status, diagnostic_code="request_incompatible",
        endpoint_url=endpoint_url,
    )


def _send_request(
    config: GatewayConfig,
    endpoint_url: str,
    body: dict[str, Any],
    *,
    timeout_seconds: int,
) -> tuple[str, bytes, dict[str, str]]:
    request = Request(
        endpoint_url,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json", "Accept": "text/event-stream, application/json"},
        method="POST",
    )
    try:
        return read_transport(
            build_opener(_NoRedirectHandler()), request,
            mode=config.api_mode, timeout_seconds=timeout_seconds,
        )
    except TransportInterrupted as exc:
        diagnostics = transport_diagnostics(exc.headers)
        stage = {
            "waiting_headers": "尚未收到响应头",
            "waiting_first_byte": "已收到响应头，但尚未收到正文",
            "receiving_response": "接收内容途中中断",
        }[diagnostics["transport_stage"]]
        raise GatewayError(
            f"中转站连接未完整结束：{stage}；已等待 {diagnostics['elapsed_seconds']:.1f} 秒，"
            f"收到 {diagnostics['received_bytes']} 字节。已完成片段保留，未完成内容不会当作成功结果。"
            f"若总在约 60 秒中断，请核对中转站的上游超时和流式转发支持。"
            f"（{_request_context(config, endpoint_url)}）",
            retryable=True,
            diagnostic_code=exc.diagnostic_code,
            endpoint_url=endpoint_url,
            status_code=int(exc.headers["_content_factory_http_status"]) if "_content_factory_http_status" in exc.headers else None,
            provider_request_id=_request_id_header(exc.headers, config.api_key),
            transport_diagnostics=diagnostics,
        ) from exc


def _request_id_header(headers: Mapping[str, str], secret: str) -> str | None:
    for name in ("x-request-id", "request-id", "openai-request-id"):
        candidate = _safe_public_token(_header_value(headers, name) or "")
        if candidate and (not secret or secret not in candidate):
            return candidate
    return None


def _unpack_transport_result(
    result: tuple[str, bytes] | tuple[str, bytes, Mapping[str, str]],
) -> tuple[str, bytes, dict[str, str]]:
    """Keep compatibility with existing relay/test transports returning two fields."""

    if len(result) == 2:
        content_type, raw = result
        return content_type, raw, {}
    content_type, raw, headers = result
    return content_type, raw, {str(key).lower(): str(value) for key, value in headers.items()}


def _read_http_error_body(
    error: HTTPError,
    *,
    config: GatewayConfig,
    endpoint_url: str,
) -> bytes:
    """Read a bounded HTTP error body while preserving transport diagnostics."""

    try:
        return error.read(64 * 1024 + 1)
    except ssl.SSLError as exc:
        raise GatewayError(
            f"中转站安全连接或错误响应读取被中断，任务稍后可重试。"
            f"（{_request_context(config, endpoint_url)}）",
            retryable=True,
            diagnostic_code="secure_connection_interrupted",
            endpoint_url=endpoint_url,
        ) from exc
    except http.client.HTTPException as exc:
        raise GatewayError(
            f"中转站连接或错误响应读取被中断，任务稍后可重试。"
            f"（{_request_context(config, endpoint_url)}）",
            retryable=True,
            diagnostic_code="connection_interrupted",
            endpoint_url=endpoint_url,
        ) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise GatewayError(
            f"中转站错误响应读取超时。（{_request_context(config, endpoint_url)}）",
            retryable=True,
            diagnostic_code="connection_failed",
            endpoint_url=endpoint_url,
        ) from exc


def _call_gateway_unlimited(
    config: GatewayConfig,
    *,
    context_json: str,
    keyframe_data_urls: list[str],
    output_schema: dict[str, Any],
    timeout_seconds: int = 180,
    developer_instructions: str = _DEVELOPER_INSTRUCTIONS,
    schema_name: str = "content_factory_deep_analysis",
) -> GatewayResult:
    strict_body = build_request_body(
        config,
        context_json=context_json,
        keyframe_data_urls=keyframe_data_urls,
        output_schema=output_schema,
        developer_instructions=developer_instructions,
        schema_name=schema_name,
    )
    content_type = ""
    raw = b""
    response_headers: dict[str, str] = {}
    used_endpoint = gateway_endpoint(config)
    for endpoint_index, endpoint_url in enumerate(_endpoint_candidates(config)):
        used_endpoint = endpoint_url
        try:
            content_type, raw, response_headers = _unpack_transport_result(
                _send_request(config, endpoint_url, strict_body, timeout_seconds=timeout_seconds)
            )
            break
        except HTTPError as exc:
            status = exc.code
            error_raw = _read_http_error_body(
                exc, config=config, endpoint_url=endpoint_url,
            )
            error_type, error_code, message = _provider_error(error_raw)
            if (
                status in {400, 422}
                and not _looks_like_model_error(error_type, error_code, message)
                and _looks_like_schema_compatibility_error(error_type, error_code, message)
            ):
                compatibility_body = build_request_body(
                    config,
                    context_json=context_json,
                    keyframe_data_urls=keyframe_data_urls,
                    output_schema=output_schema,
                    developer_instructions=developer_instructions,
                    schema_name=schema_name,
                    strict_schema=False,
                )
                try:
                    content_type, raw, response_headers = _unpack_transport_result(
                        _send_request(config, endpoint_url, compatibility_body, timeout_seconds=timeout_seconds)
                    )
                    break
                except HTTPError as compatibility_exc:
                    compatibility_raw = _read_http_error_body(
                        compatibility_exc, config=config, endpoint_url=endpoint_url,
                    )
                    raise _http_gateway_error(
                        config, endpoint_url, compatibility_exc.code, compatibility_raw,
                        timeout_seconds=timeout_seconds,
                        has_image_input=bool(keyframe_data_urls),
                        response_headers=dict(compatibility_exc.headers.items()) if compatibility_exc.headers else None,
                    ) from compatibility_exc
                except ssl.SSLError as compatibility_exc:
                    raise GatewayError(
                        f"中转站安全连接或流式响应被中断，任务稍后可重试。"
                        f"（{_request_context(config, endpoint_url)}）",
                        retryable=True,
                        diagnostic_code="secure_connection_interrupted",
                        endpoint_url=endpoint_url,
                    ) from compatibility_exc
                except http.client.HTTPException as compatibility_exc:
                    raise GatewayError(
                        f"中转站连接或响应读取被中断，任务稍后可重试。"
                        f"（{_request_context(config, endpoint_url)}）",
                        retryable=True,
                        diagnostic_code="connection_interrupted",
                        endpoint_url=endpoint_url,
                    ) from compatibility_exc
                except (URLError, TimeoutError, socket.timeout) as compatibility_exc:
                    raise GatewayError(
                        f"无法连接中转站或请求超时。（{_request_context(config, endpoint_url)}）",
                        retryable=True,
                        diagnostic_code="connection_failed",
                        endpoint_url=endpoint_url,
                    ) from compatibility_exc
            can_try_v1 = (
                status == 404
                and not _looks_like_model_error(error_type, error_code, message)
                and endpoint_index + 1 < len(_endpoint_candidates(config))
            )
            if can_try_v1:
                continue
            raise _http_gateway_error(
                config,
                endpoint_url,
                status,
                error_raw,
                timeout_seconds=timeout_seconds,
                has_image_input=bool(keyframe_data_urls),
                response_headers=dict(exc.headers.items()) if exc.headers else None,
            ) from exc
        except ssl.SSLError as exc:
            raise GatewayError(
                f"中转站安全连接或流式响应被中断，任务稍后可重试。"
                f"（{_request_context(config, endpoint_url)}）",
                retryable=True,
                diagnostic_code="secure_connection_interrupted",
                endpoint_url=endpoint_url,
            ) from exc
        except http.client.HTTPException as exc:
            raise GatewayError(
                f"中转站连接或响应读取被中断，任务稍后可重试。"
                f"（{_request_context(config, endpoint_url)}）",
                retryable=True,
                diagnostic_code="connection_interrupted",
                endpoint_url=endpoint_url,
            ) from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise GatewayError(
                f"无法连接中转站或请求超时。（{_request_context(config, endpoint_url)}）",
                retryable=True,
                diagnostic_code="connection_failed",
                endpoint_url=endpoint_url,
            ) from exc
    if len(raw) > 20 * 1024 * 1024:
        raise GatewayError(
            f"中转站响应超过 20 MB 安全上限。（{_request_context(config, used_endpoint)}）",
            retryable=False,
            diagnostic_code="response_too_large",
            endpoint_url=used_endpoint,
        )
    provider_partial = (_header_value(response_headers, "x-dashscope-partialresponse") or "").strip().lower() in {
        "true", "1", "yes",
    }
    header_request_id = _request_id_header(response_headers, config.api_key)
    try:
        if content_type == "text/event-stream" or raw.lstrip().startswith((b"data:", b"event:")):
            result = parse_gateway_stream(raw, config.api_mode, provider_partial=provider_partial)
        else:
            result = parse_gateway_response(raw, config.api_mode, provider_partial=provider_partial)
    except GatewayError as exc:
        exc.transport_diagnostics = transport_diagnostics(response_headers)
        exc.endpoint_url = used_endpoint
        exc.provider_request_id = header_request_id or exc.provider_request_id
        exc.status_code = int(_header_value(response_headers, "_content_factory_http_status") or 200)
        raise
    return GatewayResult(
        content=result.content,
        response_id=result.response_id or header_request_id,
        raw_bytes=result.raw_bytes,
        endpoint_url=used_endpoint,
        outcome=result.outcome,
        provider_status=result.provider_status,
        finish_reason=result.finish_reason,
        completed_event_received=result.completed_event_received,
        provider_partial=provider_partial,
        request_started_at=_header_value(response_headers, "_content_factory_request_started_at"),
        response_headers_at=_header_value(response_headers, "_content_factory_response_headers_at"),
        first_event_at=_header_value(response_headers, "_content_factory_first_event_at"),
        last_event_at=_header_value(response_headers, "_content_factory_last_event_at"),
        completed_at=_header_value(response_headers, "_content_factory_completed_at"),
        http_status=int(_header_value(response_headers, "_content_factory_http_status") or 200),
    )


def call_gateway(
    config: GatewayConfig,
    *,
    context_json: str,
    keyframe_data_urls: list[str],
    output_schema: dict[str, Any],
    timeout_seconds: int = 180,
    developer_instructions: str = _DEVELOPER_INSTRUCTIONS,
    schema_name: str = "content_factory_deep_analysis",
) -> GatewayResult:
    """Call one model under provider/model concurrency, RPM and input-TPM guards."""

    # Chinese UI text is commonly close to one token per character. The
    # conservative estimate plus a fixed low-detail image allowance avoids a
    # tokenizer dependency while still bounding burst traffic.
    estimated_input_tokens = max(1, len(context_json) + len(keyframe_data_urls) * 1_200)
    with _MODEL_RATE_LIMITER.reserve(
        config,
        estimated_input_tokens=estimated_input_tokens,
        wait_timeout_seconds=min(30.0, max(1.0, timeout_seconds / 3)),
    ):
        return _call_gateway_unlimited(
            config,
            context_json=context_json,
            keyframe_data_urls=keyframe_data_urls,
            output_schema=output_schema,
            timeout_seconds=timeout_seconds,
            developer_instructions=developer_instructions,
            schema_name=schema_name,
        )
