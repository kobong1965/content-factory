"""Local-only S3 gateway configuration with Windows DPAPI secret protection."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlparse
from uuid import uuid4

GatewayApiMode = Literal["responses", "chat_completions"]
GatewayInputModality = Literal["text", "image"]
GatewayProvider = Literal["openai", "qwen", "openai_compatible", "custom"]
GatewayPurpose = Literal["analysis", "script", "material", "video_review"]
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_MODEL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_VALID_MODALITIES = ("text", "image")
_VALID_PROVIDERS = ("openai", "qwen", "openai_compatible", "custom")
_VALID_PURPOSES = ("analysis", "script", "material", "video_review")
_QWEN_REGIONAL_DASHSCOPE_HOST = re.compile(
    r"^dashscope-[a-z0-9-]+\.aliyuncs\.com$"
)
_PURPOSE_REQUIREMENTS = {
    "analysis": frozenset(("text", "image")),
    "script": frozenset(("text",)),
    "material": frozenset(("text", "image")),
    "video_review": frozenset(("text", "image")),
}
_PURPOSE_LABELS = {
    "analysis": "深度分析",
    "script": "脚本生成",
    "material": "素材识别",
    "video_review": "视频审核",
}
_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[Path, threading.RLock] = {}


class GatewaySettingsError(ValueError):
    """Raised when public gateway settings or protected local state are invalid."""

    retryable = False


class GatewayVerificationRequired(GatewaySettingsError):
    """Raised when an API update would bypass the verified connect workflow."""


class GatewayModelCapacityReached(GatewaySettingsError):
    """Raised before probing when a new verified profile cannot be persisted."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _config_path() -> Path:
    override = os.environ.get("CONTENT_FACTORY_S3_CONFIG_PATH")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise GatewaySettingsError("找不到 Windows 本地应用数据目录")
    return (Path(local_app_data) / "爆款内容工厂" / "gateway-config.json").resolve()


def validate_base_url(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    try:
        parsed.port
    except ValueError as exc:
        raise GatewaySettingsError("中转地址端口格式不正确") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise GatewaySettingsError("中转地址必须是完整的 HTTP 或 HTTPS 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GatewaySettingsError("中转地址不能包含账号、密码、查询参数或片段")
    if parsed.scheme == "http" and host not in _LOCAL_HOSTS:
        raise GatewaySettingsError("远程中转站必须使用 HTTPS；只有本机地址允许 HTTP")
    return cleaned


def _credential_origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlparse(value)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def is_official_qwen_host(host: str) -> bool:
    """Return whether *host* is an Alibaba Cloud Model Studio endpoint.

    Match complete DNS label boundaries only. This includes the documented
    regional DashScope hosts, workspace-specific ``*.maas.aliyuncs.com``
    endpoints, and official services such as ``coding.dashscope.aliyuncs.com``
    without trusting lookalike relay domains that merely contain a keyword.
    """

    normalized = host.strip().lower().rstrip(".")
    return (
        normalized == "dashscope.aliyuncs.com"
        or normalized.endswith(".dashscope.aliyuncs.com")
        or _QWEN_REGIONAL_DASHSCOPE_HOST.fullmatch(normalized) is not None
        or normalized.endswith(".maas.aliyuncs.com")
    )


def infer_provider_from_base_url(base_url: str) -> GatewayProvider:
    """Infer a protocol family before a model has been selected."""

    host = (urlparse(base_url).hostname or "").lower()
    if host == "api.openai.com":
        return "openai"
    if is_official_qwen_host(host):
        return "qwen"
    return "openai_compatible"


def infer_provider(base_url: str, model: str) -> GatewayProvider:
    """Infer only a display/protocol-family hint for legacy configurations."""

    del model
    # A model name supplied by an arbitrary relay is not proof that the
    # credential belongs to Alibaba Cloud. Provider-specific URL rewriting is
    # enabled only for an official hostname boundary.
    return infer_provider_from_base_url(base_url)


def _connection_identity_url(value: str) -> str:
    return re.sub(
        r"/(?:responses|chat/completions)$",
        "",
        value.rstrip("/"),
        flags=re.IGNORECASE,
    )


def _store_lock(path: Path) -> threading.RLock:
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(path, threading.RLock())


def _protect_secret(secret: str) -> str:
    if os.name != "nt":
        raise GatewaySettingsError("API Key 只能在 Windows 上使用系统加密保存")
    raw = secret.encode("utf-8")
    source_buffer = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptProtectData(
        ctypes.byref(source), "Content Factory S3", None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(destination),
    )
    if not success:
        raise GatewaySettingsError("Windows 无法加密保存 API Key")
    try:
        protected = ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)
    return base64.b64encode(protected).decode("ascii")


def _unprotect_secret(encoded: str) -> str:
    if os.name != "nt":
        raise GatewaySettingsError("API Key 只能在保存它的 Windows 用户下解密")
    try:
        protected = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise GatewaySettingsError("本地 API Key 密文损坏") from exc
    source_buffer = ctypes.create_string_buffer(protected)
    source = _DataBlob(len(protected), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    destination = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(destination),
    )
    if not success:
        raise GatewaySettingsError("当前 Windows 用户无法解密 API Key")
    try:
        raw = ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)
    return raw.decode("utf-8")


@dataclass(frozen=True)
class GatewayModelConfig:
    model_id: str
    display_name: str
    base_url: str
    model: str
    api_mode: GatewayApiMode
    api_key: str = field(repr=False)
    modalities: tuple[GatewayInputModality, ...]
    purposes: tuple[GatewayPurpose, ...]
    provider: GatewayProvider = "openai_compatible"
    enabled: bool = True

    def public_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_mode": self.api_mode,
            "modalities": list(self.modalities),
            "purposes": list(self.purposes),
            "enabled": self.enabled,
            "api_key_configured": bool(self.api_key),
        }


@dataclass(frozen=True)
class GatewayConfig:
    # The first five fields intentionally preserve the original positional
    # constructor used by the analysis/script/material processors and tests.
    base_url: str
    model: str
    api_mode: GatewayApiMode
    api_key: str = field(repr=False)
    updated_at: str
    model_id: str = "model_default"
    display_name: str = "默认多模态模型"
    modalities: tuple[GatewayInputModality, ...] = ("text", "image")
    purposes: tuple[GatewayPurpose, ...] = ("analysis", "script", "material", "video_review")
    provider: GatewayProvider = "openai_compatible"
    models: tuple[GatewayModelConfig, ...] = field(default=(), repr=False)
    routing: Mapping[GatewayPurpose, str] = field(default_factory=dict, repr=False)

    def available_models(self) -> tuple[GatewayModelConfig, ...]:
        if self.models:
            return self.models
        return (
            GatewayModelConfig(
                model_id=self.model_id,
                display_name=self.display_name,
                base_url=self.base_url,
                model=self.model,
                api_mode=self.api_mode,
                api_key=self.api_key,
                modalities=self.modalities,
                purposes=self.purposes,
                provider=self.provider,
            ),
        )

    def for_model(self, model_id: str | None = None) -> GatewayConfig:
        target = model_id or self.model_id
        profile = next(
            (item for item in self.available_models() if item.model_id == target and item.enabled),
            None,
        )
        if profile is None:
            raise GatewaySettingsError("找不到已启用的模型配置")
        return self._with_profile(profile)

    def for_model_purpose(self, model_id: str, purpose: GatewayPurpose) -> GatewayConfig:
        required = _PURPOSE_REQUIREMENTS[purpose]
        profile = next(
            (
                item
                for item in self.available_models()
                if item.model_id == model_id
                and item.enabled
                and purpose in item.purposes
                and required.issubset(item.modalities)
            ),
            None,
        )
        if profile is None:
            raise GatewaySettingsError(
                f"任务绑定的模型已不再支持{_PURPOSE_LABELS[purpose]}"
            )
        return self._with_profile(profile)

    def for_purpose(self, purpose: GatewayPurpose) -> GatewayConfig:
        required = _PURPOSE_REQUIREMENTS[purpose]
        routed_model_id = self.routing.get(purpose)
        profile = None
        if routed_model_id:
            try:
                return self.for_model_purpose(routed_model_id, purpose)
            except GatewaySettingsError:
                profile = None
        elif not self.routing:
            # Directly constructed legacy configs and test fixtures do not
            # carry routing; preserve their original single-model behavior.
            profile = next(
                (
                    item
                    for item in self.available_models()
                    if item.enabled and purpose in item.purposes and required.issubset(item.modalities)
                ),
                None,
            )
        if profile is None:
            raise GatewaySettingsError(f"没有可用于{_PURPOSE_LABELS[purpose]}的模型配置")
        return self._with_profile(profile)

    def has_purpose(self, purpose: GatewayPurpose) -> bool:
        try:
            self.for_purpose(purpose)
        except GatewaySettingsError:
            return False
        return True

    def execution_snapshot(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "base_url": self.base_url,
            "model": self.model,
            "api_mode": self.api_mode,
            "provider": self.provider,
            "modalities": list(self.modalities),
            "purposes": list(self.purposes),
        }

    def matches_snapshot(self, snapshot: Mapping[str, object]) -> bool:
        identity = self.execution_snapshot()
        if not all(
            str(snapshot.get(key) or "") == str(identity[key])
            for key in ("model_id", "base_url", "model", "api_mode")
        ):
            return False
        metadata_keys = ("provider", "modalities", "purposes")
        present = tuple(key in snapshot for key in metadata_keys)
        if not any(present):
            # Snapshots created before settings schema 2.1 carried only the
            # four identity fields. Queues may finish those tasks only after
            # for_model_purpose() verifies today's declared capability. Their
            # provider semantics are reconstructed with the same legacy
            # inference rule so an explicit provider change cannot silently
            # alter a queued request body.
            legacy_provider = infer_provider(
                str(snapshot.get("base_url") or ""),
                str(snapshot.get("model") or ""),
            )
            return legacy_provider == self.provider
        if not all(present):
            return False
        if str(snapshot["provider"]) != self.provider:
            return False
        for key in ("modalities", "purposes"):
            value = snapshot[key]
            if not isinstance(value, (list, tuple)):
                return False
            if frozenset(str(item) for item in value) != frozenset(identity[key]):
                return False
        return True

    def _with_profile(self, profile: GatewayModelConfig) -> GatewayConfig:
        return GatewayConfig(
            base_url=profile.base_url,
            model=profile.model,
            api_mode=profile.api_mode,
            api_key=profile.api_key,
            updated_at=self.updated_at,
            model_id=profile.model_id,
            display_name=profile.display_name,
            modalities=profile.modalities,
            purposes=profile.purposes,
            provider=profile.provider,
            models=self.available_models(),
            routing=self.routing,
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": "2.1.0",
            "base_url": self.base_url,
            "model": self.model,
            "api_mode": self.api_mode,
            "api_key_configured": any(item.enabled and bool(item.api_key) for item in self.available_models()),
            "default_model_id": self.model_id,
            "models": [item.public_dict() for item in self.available_models()],
            "routing": {purpose: self.routing.get(purpose) for purpose in _VALID_PURPOSES},
            "updated_at": self.updated_at,
        }


def _ordered_values(values: object, allowed: tuple[str, ...], field_label: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise GatewaySettingsError(f"{field_label}格式不正确")
    selected = {str(value) for value in values}
    if not selected or not selected.issubset(allowed):
        raise GatewaySettingsError(f"{field_label}至少选择一项有效值")
    return tuple(value for value in allowed if value in selected)


def _normalize_model(
    raw: Mapping[str, object],
    *,
    existing_models: Mapping[str, GatewayModelConfig],
) -> GatewayModelConfig:
    model_id = str(raw.get("model_id") or "").strip()
    if not _MODEL_ID_PATTERN.fullmatch(model_id):
        raise GatewaySettingsError("模型配置 ID 格式不正确")
    display_name = str(raw.get("display_name") or "").strip()
    if not display_name or len(display_name) > 60:
        raise GatewaySettingsError("显示名称不能为空且不能超过 60 个字符")
    model = str(raw.get("model") or "").strip()
    if not model or len(model) > 120:
        raise GatewaySettingsError("模型标识不能为空且不能超过 120 个字符")
    normalized_url = validate_base_url(str(raw.get("base_url") or ""))
    provider_value = str(raw.get("provider") or infer_provider(normalized_url, model))
    if provider_value not in _VALID_PROVIDERS:
        raise GatewaySettingsError("模型提供方只能选择 openai、qwen、openai_compatible 或 custom")
    mode = str(raw.get("api_mode") or "")
    if mode not in {"responses", "chat_completions"}:
        raise GatewaySettingsError("接口模式只能选择 responses 或 chat_completions")
    modalities = _ordered_values(raw.get("modalities"), _VALID_MODALITIES, "输入模态")
    purposes = _ordered_values(raw.get("purposes"), _VALID_PURPOSES, "使用场景")
    if "text" not in modalities:
        raise GatewaySettingsError("当前业务模型必须支持文本输入")
    image_purposes = {"analysis", "material", "video_review"} & set(purposes)
    if image_purposes and "image" not in modalities:
        labels = "、".join(_PURPOSE_LABELS[purpose] for purpose in _VALID_PURPOSES if purpose in image_purposes)
        raise GatewaySettingsError(f"{labels}模型必须支持图片输入")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise GatewaySettingsError("模型启用状态格式不正确")
    incoming_secret = raw.get("api_key")
    existing_model = existing_models.get(model_id)
    if isinstance(incoming_secret, str) and incoming_secret.strip():
        secret = incoming_secret.strip()
    elif existing_model is not None and _credential_origin(existing_model.base_url) == _credential_origin(normalized_url):
        secret = existing_model.api_key
    else:
        secret = ""
    if len(secret) < 8 or len(secret) > 500:
        if existing_model is not None and _credential_origin(existing_model.base_url) != _credential_origin(normalized_url):
            raise GatewaySettingsError(f"{display_name}更换了接口主机，请重新输入 API Key")
        raise GatewaySettingsError(f"{display_name}的 API Key 未填写或长度异常")
    return GatewayModelConfig(
        model_id=model_id,
        display_name=display_name,
        base_url=normalized_url,
        model=model,
        api_mode=mode,  # type: ignore[arg-type]
        api_key=secret,
        modalities=modalities,  # type: ignore[arg-type]
        purposes=purposes,  # type: ignore[arg-type]
        provider=provider_value,  # type: ignore[arg-type]
        enabled=enabled,
    )


def _validated_routing(
    raw: Mapping[str, object],
    models: tuple[GatewayModelConfig, ...],
) -> dict[GatewayPurpose, str]:
    if {str(key) for key in raw} - set(_VALID_PURPOSES):
        raise GatewaySettingsError("任务路由包含不支持的用途")
    model_map = {item.model_id: item for item in models}
    routing: dict[GatewayPurpose, str] = {}
    for purpose in _VALID_PURPOSES:
        model_id = str(raw.get(purpose) or "")
        profile = model_map.get(model_id)
        required = _PURPOSE_REQUIREMENTS[purpose]
        if (
            profile is None
            or not profile.enabled
            or purpose not in profile.purposes
            or not required.issubset(profile.modalities)
        ):
            raise GatewaySettingsError(f"请为{_PURPOSE_LABELS[purpose]}选择能力匹配的已启用模型")
        routing[purpose] = model_id  # type: ignore[assignment]
    return routing


class GatewaySettingsStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser().resolve() if path is not None else _config_path()
        self._lock = _store_lock(self.path)

    def load(self) -> GatewayConfig | None:
        with self._lock:
            return self._load_unlocked()

    @contextmanager
    def reserve_verified_model_capacity(
        self,
        *,
        base_url: str,
        upstream_model_id: str,
        provider: GatewayProvider,
    ) -> Iterator[tuple[GatewayApiMode, ...] | None]:
        """Reserve capacity across probe and save, rejecting a ninth profile early.

        ``None`` means a new profile may be created and all protocol modes may
        be probed. At the eight-profile limit, the yielded tuple contains only
        modes whose exact identity can be updated idempotently. The per-path
        re-entrant lock deliberately remains held across the caller's probe and
        atomic save so concurrent connects cannot both spend a request for the
        final slot.
        """

        normalized_url = validate_base_url(base_url)
        with self._lock:
            existing = self._load_unlocked() if self.path.is_file() else None
            profiles = existing.available_models() if existing else ()
            if len(profiles) < 8:
                yield None
                return
            reusable_modes = tuple(dict.fromkeys(
                item.api_mode
                for item in profiles
                if _connection_identity_url(item.base_url)
                == _connection_identity_url(normalized_url)
                and item.model == upstream_model_id
                and item.provider == provider
            ))
            if not reusable_modes:
                raise GatewayModelCapacityReached(
                    "已达到 8 个模型上限；当前连接不会复用现有已验证模型"
                )
            yield reusable_modes

    def _load_unlocked(self) -> GatewayConfig | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload.get("models"), list):
                schema_version = payload.get("schema_version")
                if schema_version not in {"2.0.0", "2.1.0"} or not 1 <= len(payload["models"]) <= 8:
                    raise KeyError("schema_version")
                models: list[GatewayModelConfig] = []
                for raw in payload["models"]:
                    if not isinstance(raw, dict):
                        raise KeyError("models")
                    if schema_version == "2.1.0" and raw.get("provider") not in _VALID_PROVIDERS:
                        raise KeyError("provider")
                    normalized = dict(raw)
                    normalized["api_key"] = _unprotect_secret(str(raw["protected_api_key"]))
                    # Version 2.0 predates the explicit video-review route.
                    # A visual analysis profile already had the exact input
                    # capabilities required for that work, so expose it as a
                    # migration candidate without rewriting the encrypted file.
                    if schema_version == "2.0.0":
                        purposes = list(normalized.get("purposes") or [])
                        modalities = set(normalized.get("modalities") or [])
                        if "analysis" in purposes and {"text", "image"}.issubset(modalities):
                            purposes.append("video_review")
                        normalized["purposes"] = purposes
                    models.append(_normalize_model(normalized, existing_models={}))
                if len({item.model_id for item in models}) != len(models):
                    raise KeyError("duplicate model_id")
                model_tuple = tuple(models)
                raw_routing = payload.get("routing")
                if not isinstance(raw_routing, dict):
                    raise KeyError("routing")
                migrated_routing = dict(raw_routing)
                if schema_version == "2.0.0" and not migrated_routing.get("video_review"):
                    migrated_routing["video_review"] = migrated_routing.get("analysis")
                routing = _validated_routing(migrated_routing, model_tuple)
                default_model_id = str(payload.get("default_model_id") or routing["analysis"])
                default = next((item for item in models if item.model_id == default_model_id and item.enabled), None)
                if default is None:
                    raise KeyError("default_model_id")
                return GatewayConfig(
                    base_url=default.base_url,
                    model=default.model,
                    api_mode=default.api_mode,
                    api_key=default.api_key,
                    updated_at=str(payload["updated_at"]),
                    model_id=default.model_id,
                    display_name=default.display_name,
                    modalities=default.modalities,
                    purposes=default.purposes,
                    provider=default.provider,
                    models=model_tuple,
                    routing=routing,
                )

            # Schema 1.x migration: expose the original single model as one
            # multimodal profile and write schema 2.0 only on an explicit save.
            if payload.get("schema_version") not in {None, "1.0.0"}:
                raise KeyError("unsupported schema_version")
            legacy = _normalize_model(
                {
                    "model_id": "model_legacy",
                    "display_name": "默认多模态模型",
                    "base_url": payload["base_url"],
                    "model": payload["model"],
                    "api_mode": str(payload["api_mode"]),
                    "api_key": _unprotect_secret(str(payload["protected_api_key"])),
                    "modalities": ["text", "image"],
                    "purposes": ["analysis", "script", "material", "video_review"],
                    "enabled": True,
                },
                existing_models={},
            )
            routing: dict[GatewayPurpose, str] = {
                "analysis": legacy.model_id,
                "script": legacy.model_id,
                "material": legacy.model_id,
                "video_review": legacy.model_id,
            }
            return GatewayConfig(
                legacy.base_url,
                legacy.model,
                legacy.api_mode,
                legacy.api_key,
                str(payload["updated_at"]),
                model_id=legacy.model_id,
                display_name=legacy.display_name,
                modalities=legacy.modalities,
                purposes=legacy.purposes,
                provider=legacy.provider,
                models=(legacy,),
                routing=routing,
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, UnicodeError, GatewaySettingsError) as exc:
            raise GatewaySettingsError("本地中转站配置损坏，请重新保存") from exc

    def save(
        self,
        *,
        models: list[dict[str, object]] | None = None,
        routing: Mapping[str, object] | None = None,
        default_model_id: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_mode: GatewayApiMode | None = None,
        api_key: str | None = None,
    ) -> GatewayConfig:
        with self._lock:
            existing = self._load_unlocked() if self.path.is_file() else None
            existing_models = {
                item.model_id: item for item in existing.available_models()
            } if existing else {}
            if models is None:
                if base_url is None or model is None or api_mode is None:
                    raise GatewaySettingsError("请至少配置一个模型")
                legacy_id = existing.model_id if existing else "model_default"
                if existing:
                    models = []
                    for item in existing.available_models():
                        models.append({
                            "model_id": item.model_id,
                            "display_name": item.display_name,
                            "provider": item.provider,
                            "base_url": base_url if item.model_id == legacy_id else item.base_url,
                            "model": model if item.model_id == legacy_id else item.model,
                            "api_mode": api_mode if item.model_id == legacy_id else item.api_mode,
                            "api_key": api_key if item.model_id == legacy_id else None,
                            "modalities": list(item.modalities),
                            "purposes": list(item.purposes),
                            "enabled": item.enabled,
                        })
                    routing = routing or existing.routing
                else:
                    models = [{
                        "model_id": legacy_id,
                        "display_name": "默认多模态模型",
                        "provider": infer_provider(str(base_url), str(model)),
                        "base_url": base_url,
                        "model": model,
                        "api_mode": api_mode,
                        "api_key": api_key,
                        "modalities": ["text", "image"],
                        "purposes": ["analysis", "script", "material", "video_review"],
                        "enabled": True,
                    }]
                    routing = {purpose: legacy_id for purpose in _VALID_PURPOSES}
                default_model_id = legacy_id
            if not 1 <= len(models) <= 8:
                raise GatewaySettingsError("模型数量必须在 1 到 8 个之间")
            legacy_routing = routing is not None and not routing.get("video_review")
            if legacy_routing:
                migrated_models: list[dict[str, object]] = []
                for raw in models:
                    migrated = dict(raw)
                    purposes = list(migrated.get("purposes") or [])
                    modalities = set(migrated.get("modalities") or [])
                    if "analysis" in purposes and {"text", "image"}.issubset(modalities):
                        purposes.append("video_review")
                    migrated["purposes"] = purposes
                    migrated_models.append(migrated)
                models = migrated_models
                routing = {**routing, "video_review": routing.get("analysis")}
            normalized_models = tuple(
                _normalize_model(raw, existing_models=existing_models) for raw in models
            )
            model_ids = [item.model_id for item in normalized_models]
            if len(set(model_ids)) != len(model_ids):
                raise GatewaySettingsError("模型配置 ID 不能重复")
            if not any(item.enabled for item in normalized_models):
                raise GatewaySettingsError("至少需要启用一个模型")
            if routing is None:
                if existing is not None:
                    routing = existing.routing
                else:
                    routing = {purpose: model_ids[0] for purpose in _VALID_PURPOSES}
            normalized_routing = _validated_routing(routing, normalized_models)
            selected_id = default_model_id or (
                existing.model_id if existing and existing.model_id in model_ids else normalized_routing["analysis"]
            )
            selected = next((item for item in normalized_models if item.model_id == selected_id and item.enabled), None)
            if selected is None:
                raise GatewaySettingsError("默认模型必须是已启用的模型")
            updated_at = _now_iso()
            payload = {
                "schema_version": "2.1.0",
                "default_model_id": selected.model_id,
                "routing": normalized_routing,
                "models": [
                    {
                        "model_id": item.model_id,
                        "display_name": item.display_name,
                        "provider": item.provider,
                        "base_url": item.base_url,
                        "model": item.model,
                        "api_mode": item.api_mode,
                        "modalities": list(item.modalities),
                        "purposes": list(item.purposes),
                        "enabled": item.enabled,
                        "protected_api_key": _protect_secret(item.api_key),
                    }
                    for item in normalized_models
                ],
                "updated_at": updated_at,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.partial")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            return GatewayConfig(
                selected.base_url,
                selected.model,
                selected.api_mode,
                selected.api_key,
                updated_at,
                model_id=selected.model_id,
                display_name=selected.display_name,
                modalities=selected.modalities,
                purposes=selected.purposes,
                provider=selected.provider,
                models=normalized_models,
                routing=normalized_routing,
            )

    def upsert_verified_model(
        self,
        *,
        base_url: str,
        upstream_model_id: str,
        display_name: str,
        provider: GatewayProvider,
        api_mode: GatewayApiMode,
        api_key: str,
    ) -> GatewayConfig:
        """Atomically merge one verified multimodal model and route all work to it."""

        normalized_url = validate_base_url(base_url)
        with self._lock:
            existing = self._load_unlocked() if self.path.is_file() else None
            existing_profiles = list(existing.available_models()) if existing else []

            matched = next(
                (
                    item
                    for item in existing_profiles
                    if _connection_identity_url(item.base_url)
                    == _connection_identity_url(normalized_url)
                    and item.model == upstream_model_id
                    and item.provider == provider
                    and item.api_mode == api_mode
                ),
                None,
            )
            if matched is None and len(existing_profiles) >= 8:
                raise GatewayModelCapacityReached(
                    "已达到 8 个模型上限；当前连接不会复用现有已验证模型"
                )
            selected_model_id = matched.model_id if matched else f"model_{uuid4().hex[:16]}"
            raw_models: list[dict[str, object]] = []
            replaced = False
            for item in existing_profiles:
                if item.model_id == selected_model_id:
                    raw_models.append({
                        "model_id": selected_model_id,
                        "display_name": display_name,
                        "provider": provider,
                        "base_url": normalized_url,
                        "model": upstream_model_id,
                        "api_mode": api_mode,
                        "api_key": api_key,
                        "modalities": ["text", "image"],
                        "purposes": list(_VALID_PURPOSES),
                        "enabled": True,
                    })
                    replaced = True
                else:
                    raw_models.append({
                        "model_id": item.model_id,
                        "display_name": item.display_name,
                        "provider": item.provider,
                        "base_url": item.base_url,
                        "model": item.model,
                        "api_mode": item.api_mode,
                        "api_key": None,
                        "modalities": list(item.modalities),
                        "purposes": list(item.purposes),
                        "enabled": item.enabled,
                    })
            if not replaced:
                raw_models.append({
                    "model_id": selected_model_id,
                    "display_name": display_name,
                    "provider": provider,
                    "base_url": normalized_url,
                    "model": upstream_model_id,
                    "api_mode": api_mode,
                    "api_key": api_key,
                    "modalities": ["text", "image"],
                    "purposes": list(_VALID_PURPOSES),
                    "enabled": True,
                })
            routing = {purpose: selected_model_id for purpose in _VALID_PURPOSES}
            # RLock is intentionally re-entrant: keeping it held across the
            # final load/merge/save prevents another settings write from being
            # lost between a successful probe and the atomic os.replace().
            return self.save(
                models=raw_models,
                routing=routing,
                default_model_id=selected_model_id,
            )

    def update_verified_metadata(
        self,
        *,
        models: list[dict[str, object]],
        routing: Mapping[str, object] | None,
        default_model_id: str | None,
    ) -> GatewayConfig:
        """Update presentation/routing without changing a verified connection."""

        with self._lock:
            existing = self._load_unlocked() if self.path.is_file() else None
            if existing is None:
                raise GatewayVerificationRequired(
                    "请先通过获取模型并连接完成真实图文能力验证"
                )
            existing_by_id = {
                item.model_id: item for item in existing.available_models()
            }
            incoming_ids = {
                str(raw.get("model_id") or "").strip() for raw in models
            }
            if incoming_ids != set(existing_by_id):
                raise GatewayVerificationRequired(
                    "为保护已绑定模型的排队任务，当前不允许添加或移除已保存模型"
                )
            safe_models: list[dict[str, object]] = []
            for raw in models:
                model_id = str(raw.get("model_id") or "").strip()
                profile = existing_by_id.get(model_id)
                if profile is None:
                    raise GatewayVerificationRequired(
                        "新模型必须先通过获取模型并连接完成真实图文能力验证"
                    )
                try:
                    incoming_url = validate_base_url(str(raw.get("base_url") or ""))
                except GatewaySettingsError as exc:
                    raise GatewayVerificationRequired(
                        "连接地址变更必须重新完成真实图文能力验证"
                    ) from exc
                incoming_provider = str(raw.get("provider") or profile.provider)
                incoming_secret = raw.get("api_key")
                identity_changed = (
                    incoming_url != profile.base_url
                    or str(raw.get("model") or "").strip() != profile.model
                    or incoming_provider != profile.provider
                    or str(raw.get("api_mode") or "") != profile.api_mode
                    or frozenset(str(item) for item in (raw.get("modalities") or []))
                    != frozenset(profile.modalities)
                    or (isinstance(incoming_secret, str) and bool(incoming_secret.strip()))
                    or (incoming_secret is not None and not isinstance(incoming_secret, str))
                )
                task_continuity_changed = (
                    raw.get("enabled", True) != profile.enabled
                    or frozenset(str(item) for item in (raw.get("purposes") or []))
                    != frozenset(profile.purposes)
                )
                if identity_changed or task_continuity_changed:
                    raise GatewayVerificationRequired(
                        "连接身份、密钥、能力、启用状态或任务用途不能在高级设置中改变"
                    )
                safe = dict(raw)
                safe["provider"] = profile.provider
                safe["api_key"] = None
                safe_models.append(safe)
            # The RLock remains held while save reloads and atomically replaces
            # the file, closing the validation/write race.
            return self.save(
                models=safe_models,
                routing=routing,
                default_model_id=default_model_id,
            )

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)
