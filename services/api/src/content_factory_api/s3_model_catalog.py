"""Safe model catalog discovery for OpenAI-compatible and DashScope endpoints."""

from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .s3_settings import (
    GatewayProvider,
    GatewaySettingsError,
    infer_provider_from_base_url,
    validate_base_url,
)

CatalogSource = Literal["openai_models", "dashscope_models"]
CapabilitySource = Literal["provider_metadata", "unknown"]
_MAX_CATALOG_BYTES = 2 * 1024 * 1024
_MAX_MODELS = 2000
_MAX_QWEN_PAGES = 20
_MODEL_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_SENSITIVE_TOKEN_PATTERN = re.compile(
    r"(?:bearer|api[_-]?key|apikey|secret|^sk-[A-Za-z0-9_-]{8,}$)",
    re.IGNORECASE,
)


class ModelDiscoveryError(RuntimeError):
    """A bounded, user-safe failure raised while reading a provider catalog."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic_code: str,
        retryable: bool,
        upstream_status: int | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        self.diagnostic_code = diagnostic_code
        self.retryable = retryable
        self.upstream_status = upstream_status
        self.endpoint_url = endpoint_url
        super().__init__(message)


@dataclass(frozen=True)
class CatalogCandidate:
    source: CatalogSource
    url: str
    inference_base_url: str


@dataclass(frozen=True)
class DiscoveredModel:
    upstream_model_id: str
    display_name: str
    input_modalities: tuple[str, ...] | None = None
    output_modalities: tuple[str, ...] | None = None
    supports_structured_output: bool | None = None
    capability_source: CapabilitySource = "unknown"

    def public_dict(self) -> dict[str, object]:
        return {
            "upstream_model_id": self.upstream_model_id,
            "display_name": self.display_name,
            "input_modalities": list(self.input_modalities) if self.input_modalities is not None else None,
            "output_modalities": list(self.output_modalities) if self.output_modalities is not None else None,
            "supports_structured_output": self.supports_structured_output,
            "capability_source": self.capability_source,
        }


@dataclass(frozen=True)
class ModelCatalog:
    provider: GatewayProvider
    normalized_base_url: str
    catalog_source: CatalogSource
    models: tuple[DiscoveredModel, ...]
    warnings: tuple[str, ...] = ()
    truncated: bool = False
    fetched_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )

    def public_dict(self) -> dict[str, object]:
        return {
            "status": "ok",
            "provider": self.provider,
            "normalized_base_url": self.normalized_base_url,
            "catalog_source": self.catalog_source,
            "models": [item.public_dict() for item in self.models],
            "warnings": list(self.warnings),
            "truncated": self.truncated,
            "fetched_at": self.fetched_at,
        }


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _safe_model_token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not _MODEL_TOKEN_PATTERN.fullmatch(candidate):
        return ""
    if _SENSITIVE_TOKEN_PATTERN.search(candidate):
        return ""
    return candidate


def _safe_display_name(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(character if character.isprintable() else " " for character in value).strip()
    return cleaned[:120] or fallback


def normalize_connection_url(value: str) -> str:
    """Normalize a pasted inference/catalog endpoint without changing origin."""

    cleaned = validate_base_url(value)
    cleaned = re.sub(r"/(?:chat/completions|responses|models)$", "", cleaned, flags=re.IGNORECASE)
    provider = infer_provider_from_base_url(cleaned)
    if provider == "qwen":
        parsed = urlsplit(cleaned)
        if parsed.path in {"", "/"}:
            cleaned = f"{urlunsplit((parsed.scheme, parsed.netloc, '', '', '')).rstrip('/')}/compatible-mode/v1"
        elif cleaned.lower().endswith("/api/v1"):
            cleaned = f"{cleaned[:-len('/api/v1')]}/compatible-mode/v1"
    return cleaned.rstrip("/")


def _catalog_candidates(base_url: str, provider: GatewayProvider) -> tuple[CatalogCandidate, ...]:
    normalized = normalize_connection_url(base_url)
    parsed = urlsplit(normalized)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    candidates: list[CatalogCandidate] = []
    if provider == "qwen":
        candidates.append(CatalogCandidate(
            "dashscope_models",
            f"{origin}/api/v1/models?{urlencode({'page_no': 1, 'page_size': 100})}",
            normalized,
        ))
    candidates.append(CatalogCandidate("openai_models", f"{normalized}/models", normalized))
    if not normalized.lower().endswith("/v1"):
        candidates.append(CatalogCandidate(
            "openai_models",
            f"{normalized}/v1/models",
            f"{normalized}/v1",
        ))
    unique: list[CatalogCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.url not in seen:
            unique.append(candidate)
            seen.add(candidate.url)
    return tuple(unique)


def _read_limited(response) -> bytes:  # noqa: ANN001
    raw = response.read(_MAX_CATALOG_BYTES + 1)
    if len(raw) > _MAX_CATALOG_BYTES:
        raise ModelDiscoveryError(
            "模型列表响应超过 2 MB 安全上限",
            diagnostic_code="catalog_response_too_large",
            retryable=False,
        )
    return raw


def _fetch_catalog_json(url: str, api_key: str, *, timeout_seconds: int) -> dict[str, Any]:
    request = Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with build_opener(_NoRedirectHandler()).open(
            request,
            timeout=max(1, min(int(timeout_seconds), 15)),
        ) as response:
            raw = _read_limited(response)
    except HTTPError as exc:
        status = int(exc.code)
        if 300 <= status < 400:
            raise ModelDiscoveryError(
                "模型列表地址返回了重定向；为防止密钥泄露，软件不会跟随跳转",
                diagnostic_code="redirect_disallowed",
                retryable=False,
                upstream_status=status,
                endpoint_url=url,
            ) from exc
        if status in {401, 403}:
            raise ModelDiscoveryError(
                "服务商拒绝了 API Key，请检查密钥是否与接口地址及地域匹配",
                diagnostic_code="authentication_failed",
                retryable=False,
                upstream_status=status,
                endpoint_url=url,
            ) from exc
        if status == 429:
            raise ModelDiscoveryError(
                "服务商正在限流，请稍后重新获取模型列表",
                diagnostic_code="rate_limited",
                retryable=True,
                upstream_status=status,
                endpoint_url=url,
            ) from exc
        if status in {404, 405, 501}:
            raise ModelDiscoveryError(
                "这个地址没有开放模型列表接口",
                diagnostic_code="catalog_endpoint_not_found",
                retryable=False,
                upstream_status=status,
                endpoint_url=url,
            ) from exc
        if status == 408 or status >= 500:
            raise ModelDiscoveryError(
                "服务商模型列表暂时不可用",
                diagnostic_code="upstream_unavailable",
                retryable=True,
                upstream_status=status,
                endpoint_url=url,
            ) from exc
        raise ModelDiscoveryError(
            f"模型列表请求不兼容（HTTP {status}）",
            diagnostic_code="catalog_request_incompatible",
            retryable=False,
            upstream_status=status,
            endpoint_url=url,
        ) from exc
    except ssl.SSLError as exc:
        raise ModelDiscoveryError(
            "获取模型列表时安全连接被中断",
            diagnostic_code="secure_connection_interrupted",
            retryable=True,
            endpoint_url=url,
        ) from exc
    except http.client.HTTPException as exc:
        raise ModelDiscoveryError(
            "获取模型列表时连接被中断",
            diagnostic_code="connection_interrupted",
            retryable=True,
            endpoint_url=url,
        ) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise ModelDiscoveryError(
            "无法连接模型列表接口或请求超时",
            diagnostic_code="connection_failed",
            retryable=True,
            endpoint_url=url,
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelDiscoveryError(
            "模型列表接口没有返回有效 JSON",
            diagnostic_code="catalog_invalid_response",
            retryable=False,
            endpoint_url=url,
        ) from exc
    if not isinstance(payload, dict):
        raise ModelDiscoveryError(
            "模型列表响应顶层不是对象",
            diagnostic_code="catalog_invalid_response",
            retryable=False,
            endpoint_url=url,
        )
    return payload


def _deduplicate(models: list[DiscoveredModel]) -> tuple[DiscoveredModel, ...]:
    result: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in models:
        if item.upstream_model_id in seen:
            continue
        seen.add(item.upstream_model_id)
        result.append(item)
        if len(result) == _MAX_MODELS:
            break
    return tuple(result)


def _parse_openai_catalog(payload: Mapping[str, Any]) -> tuple[DiscoveredModel, ...]:
    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raise ModelDiscoveryError(
            "模型列表不是 OpenAI 兼容格式",
            diagnostic_code="catalog_invalid_response",
            retryable=False,
        )
    models = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = _safe_model_token(raw.get("id"))
        if model_id:
            models.append(DiscoveredModel(model_id, model_id))
    return _deduplicate(models)


def _provider_modalities(raw: Mapping[str, Any]) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    metadata = raw.get("inference_metadata")
    request_values: set[str] = set()
    response_values: set[str] = set()
    if isinstance(metadata, dict):
        request_raw = metadata.get("request_modality")
        response_raw = metadata.get("response_modality")
        if isinstance(request_raw, list):
            request_values = {str(item).strip().lower() for item in request_raw}
        if isinstance(response_raw, list):
            response_values = {str(item).strip().lower() for item in response_raw}
    capabilities = raw.get("capabilities")
    capability_values = (
        {str(item).strip().upper() for item in capabilities}
        if isinstance(capabilities, list)
        else set()
    )
    if "TG" in capability_values:
        request_values.add("text")
        response_values.add("text")
    if "VU" in capability_values:
        request_values.add("image")
    order = ("text", "image", "audio", "video")
    request = tuple(value for value in order if value in request_values) or None
    response = tuple(value for value in order if value in response_values) or None
    return request, response


def _parse_dashscope_catalog(payload: Mapping[str, Any]) -> tuple[tuple[DiscoveredModel, ...], int]:
    output = payload.get("output")
    if not isinstance(output, dict) or not isinstance(output.get("models"), list):
        raise ModelDiscoveryError(
            "模型列表不是百炼目录格式",
            diagnostic_code="catalog_invalid_response",
            retryable=False,
        )
    models: list[DiscoveredModel] = []
    for raw in output["models"]:
        if not isinstance(raw, dict):
            continue
        model_id = _safe_model_token(raw.get("model"))
        if not model_id:
            continue
        input_modalities, output_modalities = _provider_modalities(raw)
        features = raw.get("features")
        structured = None
        if isinstance(features, list):
            structured = "structured-outputs" in {str(item).strip().lower() for item in features}
        source: CapabilitySource = (
            "provider_metadata"
            if input_modalities is not None or output_modalities is not None or structured is not None
            else "unknown"
        )
        models.append(DiscoveredModel(
            upstream_model_id=model_id,
            display_name=_safe_display_name(raw.get("name"), model_id),
            input_modalities=input_modalities,
            output_modalities=output_modalities,
            supports_structured_output=structured,
            capability_source=source,
        ))
    total_raw = output.get("total")
    total = int(total_raw) if isinstance(total_raw, int) and total_raw >= 0 else len(models)
    return _deduplicate(models), total


def _qwen_page_url(url: str, page_number: int) -> str:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    query["page_no"] = [str(page_number)]
    query["page_size"] = ["100"]
    flat = [(key, item) for key, values in query.items() for item in values]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(flat), parsed.fragment))


def _fetch_qwen_catalog(
    candidate: CatalogCandidate,
    api_key: str,
    *,
    timeout_seconds: int,
) -> tuple[tuple[DiscoveredModel, ...], bool]:
    collected: list[DiscoveredModel] = []
    total = 0
    for page in range(1, _MAX_QWEN_PAGES + 1):
        payload = _fetch_catalog_json(
            _qwen_page_url(candidate.url, page),
            api_key,
            timeout_seconds=timeout_seconds,
        )
        page_models, total = _parse_dashscope_catalog(payload)
        collected.extend(page_models)
        collected_models = _deduplicate(collected)
        if len(collected_models) >= min(total, _MAX_MODELS) or not page_models:
            return collected_models, total > len(collected_models)
    models = _deduplicate(collected)
    return models, total > len(models)


def discover_models(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: int = 10,
) -> ModelCatalog:
    """Read a live, same-origin provider catalog without persisting credentials."""

    if not isinstance(api_key, str) or not 8 <= len(api_key.strip()) <= 500:
        raise GatewaySettingsError("API Key 未填写或长度异常")
    normalized = normalize_connection_url(base_url)
    provider = infer_provider_from_base_url(normalized)
    fallback_errors: list[ModelDiscoveryError] = []
    for candidate in _catalog_candidates(normalized, provider):
        try:
            if candidate.source == "dashscope_models":
                models, truncated = _fetch_qwen_catalog(
                    candidate,
                    api_key.strip(),
                    timeout_seconds=timeout_seconds,
                )
            else:
                payload = _fetch_catalog_json(
                    candidate.url,
                    api_key.strip(),
                    timeout_seconds=timeout_seconds,
                )
                models = _parse_openai_catalog(payload)
                truncated = len(models) >= _MAX_MODELS
            if not models:
                raise ModelDiscoveryError(
                    "当前 API Key 没有返回可选择的模型",
                    diagnostic_code="catalog_empty",
                    retryable=False,
                    endpoint_url=candidate.url,
                )
            return ModelCatalog(
                provider=provider,
                # Some relays publish their catalog only under /v1/models
                # even when the user pasted the origin. Save the matching
                # inference base so connect does not later probe /chat/completions.
                normalized_base_url=candidate.inference_base_url,
                catalog_source=candidate.source,
                models=models,
                warnings=(
                    "模型目录超过 2000 项，已按安全上限截断；如目标模型未显示，可手动输入精确模型标识并继续真实图文验证。",
                ) if truncated else (),
                truncated=truncated,
            )
        except ModelDiscoveryError as exc:
            if exc.diagnostic_code in {
                "catalog_endpoint_not_found",
                "catalog_invalid_response",
                "catalog_empty",
            }:
                fallback_errors.append(exc)
                continue
            raise
    last = fallback_errors[-1] if fallback_errors else None
    raise ModelDiscoveryError(
        "该服务没有开放可读取的模型列表；请确认地址，或使用高级手动模型标识",
        diagnostic_code="catalog_unsupported",
        retryable=False,
        upstream_status=last.upstream_status if last else None,
        endpoint_url=last.endpoint_url if last else None,
    )
