from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from content_factory_api import s3_settings
from content_factory_api.s3_settings import (
    GatewayConfig,
    GatewayModelConfig,
    GatewaySettingsError,
    GatewaySettingsStore,
    infer_provider,
    infer_provider_from_base_url,
    validate_base_url,
)



def test_remote_http_gateway_is_rejected_but_loopback_is_allowed() -> None:
    with pytest.raises(GatewaySettingsError, match="HTTPS"):
        validate_base_url("http://relay.example.com/v1")
    assert validate_base_url("http://127.0.0.1:9876/v1/") == "http://127.0.0.1:9876/v1"


def test_invalid_port_is_rejected_before_gateway_save() -> None:
    with pytest.raises(GatewaySettingsError, match="端口"):
        validate_base_url("https://relay.example.com:not-a-port/v1")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        "https://coding.dashscope.aliyuncs.com/v1",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_qwen_provider_inference_accepts_only_official_aliyun_host_boundaries(
    base_url: str,
) -> None:
    assert infer_provider_from_base_url(base_url) == "qwen"
    assert infer_provider(base_url, "qwen3-vl-plus") == "qwen"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://relay-dashscope.example.com/v1",
        "https://evilaliyuncs.com/v1",
        "https://dashscope.aliyuncs.com.evil.example/v1",
        "https://workspace.cn-beijing.maas.aliyuncs.com.evil.example/v1",
        "https://dashscope-relay.aliyuncs.example/v1",
    ],
)
def test_qwen_provider_inference_rejects_lookalike_hosts_even_for_qwen_model(
    base_url: str,
) -> None:
    assert infer_provider_from_base_url(base_url) == "openai_compatible"
    assert infer_provider(base_url, "qwen3-vl-plus") == "openai_compatible"


def _model_input(
    model_id: str,
    model: str,
    api_key: str | None,
    *,
    image: bool,
    base_url: str = "https://relay.example.com/v1",
) -> dict[str, object]:
    return {
        "model_id": model_id,
        "display_name": model,
        "base_url": base_url,
        "model": model,
        "api_mode": "responses",
        "api_key": api_key,
        "modalities": ["text", "image"] if image else ["text"],
        "purposes": ["analysis", "script", "material", "video_review"] if image else ["script"],
        "enabled": True,
    }


@pytest.mark.skipif(os.name != "nt", reason="DPAPI only exists on Windows")
def test_multiple_models_keep_isolated_keys_and_explicit_routing(tmp_path: Path) -> None:
    store = GatewaySettingsStore(tmp_path / "gateway.json")
    routing = {
        "analysis": "model_vision", "script": "model_text", "material": "model_vision",
        "video_review": "model_vision",
    }
    store.save(
        models=[
            _model_input("model_vision", "vision-model", "vision-secret-key", image=True),
            _model_input("model_text", "text-model", "text-secret-key", image=False),
        ],
        routing=routing,
        default_model_id="model_vision",
    )

    loaded = store.load()
    assert loaded is not None
    assert loaded.for_purpose("analysis").model == "vision-model"
    assert loaded.for_purpose("script").model == "text-model"
    assert loaded.for_purpose("analysis").api_key == "vision-secret-key"
    assert loaded.for_purpose("script").api_key == "text-secret-key"
    raw = store.path.read_text(encoding="utf-8")
    assert "vision-secret-key" not in raw and "text-secret-key" not in raw

    # Reordering the registry cannot change an explicit route, and blank keys
    # retain only the matching model's credential on the same origin.
    store.save(
        models=[
            _model_input("model_text", "text-model", None, image=False),
            _model_input("model_vision", "vision-model", None, image=True),
        ],
        routing=routing,
        default_model_id="model_vision",
    )
    reordered = store.load()
    assert reordered is not None and reordered.for_purpose("script").model_id == "model_text"
    assert reordered.for_purpose("script").api_key == "text-secret-key"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI only exists on Windows")
def test_legacy_single_model_update_preserves_other_registered_models(tmp_path: Path) -> None:
    store = GatewaySettingsStore(tmp_path / "gateway.json")
    routing = {
        "analysis": "model_vision", "script": "model_text", "material": "model_vision",
        "video_review": "model_vision",
    }
    store.save(
        models=[
            _model_input("model_vision", "vision-model", "vision-secret-key", image=True),
            _model_input("model_text", "text-model", "text-secret-key", image=False),
        ],
        routing=routing,
        default_model_id="model_vision",
    )

    updated = store.save(
        base_url="https://relay.example.com/v1",
        model="vision-model-v2",
        api_mode="responses",
        api_key=None,
    )

    assert len(updated.available_models()) == 2
    assert updated.for_purpose("script").model == "text-model"
    assert updated.for_purpose("analysis").model == "vision-model-v2"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI only exists on Windows")
def test_changing_model_origin_requires_a_new_key(tmp_path: Path) -> None:
    store = GatewaySettingsStore(tmp_path / "gateway.json")
    routing = {
        "analysis": "model_vision", "script": "model_vision", "material": "model_vision",
        "video_review": "model_vision",
    }
    store.save(
        models=[_model_input("model_vision", "vision-model", "vision-secret-key", image=True)],
        routing=routing,
        default_model_id="model_vision",
    )

    with pytest.raises(GatewaySettingsError, match="重新输入 API Key"):
        store.save(
            models=[_model_input(
                "model_vision", "vision-model", None, image=True,
                base_url="https://different-relay.example.com/v1",
            )],
            routing=routing,
            default_model_id="model_vision",
        )


def test_unknown_settings_schema_is_not_misread_as_legacy(tmp_path: Path) -> None:
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps({
        "schema_version": "9.0.0",
        "base_url": "https://relay.example.com/v1",
        "model": "future-model",
        "api_mode": "responses",
        "protected_api_key": "not-used",
        "updated_at": "2026-08-31T00:00:00Z",
    }), encoding="utf-8")

    with pytest.raises(GatewaySettingsError, match="配置损坏"):
        GatewaySettingsStore(path).load()


def test_v1_single_model_file_loads_as_registry_without_rewriting(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "gateway.json"
    legacy_payload = {
        "schema_version": "1.0.0",
        "base_url": "https://relay.example.com/v1",
        "model": "legacy-vision-model",
        "api_mode": "responses",
        "protected_api_key": "legacy-dpapi-ciphertext",
        "updated_at": "2026-08-29T06:00:00Z",
    }
    path.write_text(json.dumps(legacy_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(s3_settings, "_unprotect_secret", lambda value: "legacy-secret-key")

    loaded = GatewaySettingsStore(path).load()

    assert loaded is not None
    assert loaded.model_id == "model_legacy"
    assert loaded.for_purpose("analysis").model == "legacy-vision-model"
    assert loaded.for_purpose("script").model_id == "model_legacy"
    assert loaded.for_purpose("material").modalities == ("text", "image")
    assert loaded.for_purpose("video_review").model_id == "model_legacy"
    assert loaded.public_dict()["routing"] == {
        "analysis": "model_legacy",
        "script": "model_legacy",
        "material": "model_legacy",
        "video_review": "model_legacy",
    }
    assert json.loads(path.read_text(encoding="utf-8")) == legacy_payload


def test_v2_registry_adds_provider_and_video_review_in_memory_without_rewriting(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "gateway.json"
    legacy_payload = {
        "schema_version": "2.0.0",
        "default_model_id": "model_qwen",
        "routing": {
            "analysis": "model_qwen", "script": "model_qwen", "material": "model_qwen",
        },
        "models": [{
            "model_id": "model_qwen", "display_name": "Qwen 多模态",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-vl-model", "api_mode": "chat_completions",
            "modalities": ["text", "image"], "purposes": ["analysis", "script", "material"],
            "enabled": True, "protected_api_key": "legacy-dpapi-ciphertext",
        }],
        "updated_at": "2026-09-05T00:00:00Z",
    }
    path.write_text(json.dumps(legacy_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(s3_settings, "_unprotect_secret", lambda value: "legacy-secret-key")

    loaded = GatewaySettingsStore(path).load()

    assert loaded is not None
    assert loaded.for_purpose("video_review").model_id == "model_qwen"
    assert loaded.for_purpose("video_review").provider == "qwen"
    assert loaded.public_dict()["schema_version"] == "2.1.0"
    assert json.loads(path.read_text(encoding="utf-8")) == legacy_payload


def test_v21_persisted_model_requires_explicit_provider(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps({
        "schema_version": "2.1.0",
        "default_model_id": "model_vision",
        "routing": {
            "analysis": "model_vision",
            "script": "model_vision",
            "material": "model_vision",
            "video_review": "model_vision",
        },
        "models": [{
            "model_id": "model_vision",
            "display_name": "视觉模型",
            "base_url": "https://relay.example.com/v1",
            "model": "vision-model",
            "api_mode": "responses",
            "modalities": ["text", "image"],
            "purposes": ["analysis", "script", "material", "video_review"],
            "enabled": True,
            "protected_api_key": "fixture-ciphertext",
        }],
        "updated_at": "2026-09-05T00:00:00Z",
    }), encoding="utf-8")
    monkeypatch.setattr(s3_settings, "_unprotect_secret", lambda _value: "fixture-secret-key")

    with pytest.raises(GatewaySettingsError, match="配置损坏"):
        GatewaySettingsStore(path).load()


def test_persisted_routing_rejects_unknown_purpose(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps({
        "schema_version": "2.1.0",
        "default_model_id": "model_vision",
        "routing": {
            "analysis": "model_vision",
            "script": "model_vision",
            "material": "model_vision",
            "video_review": "model_vision",
            "admin_override": "model_vision",
        },
        "models": [{
            "model_id": "model_vision",
            "display_name": "视觉模型",
            "provider": "openai_compatible",
            "base_url": "https://relay.example.com/v1",
            "model": "vision-model",
            "api_mode": "responses",
            "modalities": ["text", "image"],
            "purposes": ["analysis", "script", "material", "video_review"],
            "enabled": True,
            "protected_api_key": "fixture-ciphertext",
        }],
        "updated_at": "2026-09-05T00:00:00Z",
    }), encoding="utf-8")
    monkeypatch.setattr(s3_settings, "_unprotect_secret", lambda _value: "fixture-secret-key")

    with pytest.raises(GatewaySettingsError, match="配置损坏"):
        GatewaySettingsStore(path).load()


def test_video_review_model_must_accept_text_and_images(tmp_path: Path) -> None:
    store = GatewaySettingsStore(tmp_path / "gateway.json")

    with pytest.raises(GatewaySettingsError, match="视频审核.*图片"):
        store.save(
            models=[{
                "model_id": "model_text", "display_name": "文本模型", "provider": "qwen",
                "base_url": "https://relay.example.com/v1", "model": "qwen-text-model",
                "api_mode": "chat_completions", "api_key": "fixture-secret-key",
                "modalities": ["text"], "purposes": ["script", "video_review"], "enabled": True,
            }],
            routing={
                "analysis": "model_text", "script": "model_text", "material": "model_text",
                "video_review": "model_text",
            },
            default_model_id="model_text",
        )


def test_explicit_routing_selects_capability_matched_models() -> None:
    vision = GatewayModelConfig(
        "model_vision", "视觉", "https://relay.example.com/v1", "vision-model", "responses",
        "vision-secret", ("text", "image"), ("analysis", "script", "material", "video_review"),
    )
    text = GatewayModelConfig(
        "model_text", "文本", "https://relay.example.com/v1", "text-model", "responses",
        "text-secret", ("text",), ("script",),
    )
    config = GatewayConfig(
        vision.base_url, vision.model, vision.api_mode, vision.api_key, "2026-08-31T00:00:00Z",
        model_id=vision.model_id, display_name=vision.display_name, modalities=vision.modalities,
        purposes=vision.purposes, models=(text, vision),
        routing={
            "analysis": vision.model_id, "script": text.model_id, "material": vision.model_id,
            "video_review": vision.model_id,
        },
    )

    assert config.for_purpose("analysis").model_id == "model_vision"
    assert config.for_purpose("script").model_id == "model_text"
    assert config.for_purpose("video_review").model_id == "model_vision"
    assert config.execution_snapshot()["model_id"] == "model_vision"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI only exists on Windows")
def test_gateway_secret_is_dpapi_protected_and_never_returned(tmp_path: Path) -> None:
    store = GatewaySettingsStore(tmp_path / "gateway.json")
    saved = store.save(
        base_url="https://relay.example.com/v1",
        model="vision-model",
        api_mode="responses",
        api_key="super-secret-relay-key",
    )

    raw = (tmp_path / "gateway.json").read_text(encoding="utf-8")
    loaded = store.load()

    assert "super-secret-relay-key" not in raw
    assert loaded is not None and loaded.api_key == "super-secret-relay-key"
    assert "api_key" not in saved.public_dict()
    assert saved.public_dict()["api_key_configured"] is True


@pytest.mark.skipif(os.name != "nt", reason="DPAPI only exists on Windows")
def test_verified_model_upsert_is_idempotent_but_protocol_change_preserves_old_snapshot(
    tmp_path: Path,
) -> None:
    store = GatewaySettingsStore(tmp_path / "gateway.json")
    first = store.upsert_verified_model(
        base_url="https://relay.example.com/v1",
        upstream_model_id="relay-vision-model",
        display_name="视觉模型",
        provider="openai_compatible",
        api_mode="chat_completions",
        api_key="fixture-secret-key",
    )
    first_id = first.model_id
    first_snapshot = first.execution_snapshot()

    repeated = store.upsert_verified_model(
        base_url="https://relay.example.com/v1",
        upstream_model_id="relay-vision-model",
        display_name="视觉模型",
        provider="openai_compatible",
        api_mode="chat_completions",
        api_key="fixture-secret-key",
    )
    changed_protocol = store.upsert_verified_model(
        base_url="https://relay.example.com/v1",
        upstream_model_id="relay-vision-model",
        display_name="视觉模型 Responses",
        provider="openai_compatible",
        api_mode="responses",
        api_key="fixture-secret-key",
    )

    assert repeated.model_id == first_id
    assert len(repeated.available_models()) == 1
    assert changed_protocol.model_id != first_id
    assert len(changed_protocol.available_models()) == 2
    old_profile = changed_protocol.for_model(first_id)
    assert old_profile.api_mode == "chat_completions"
    assert old_profile.matches_snapshot(first_snapshot) is True


@pytest.mark.skipif(os.name != "nt", reason="DPAPI only exists on Windows")
def test_verified_model_upsert_at_eight_model_limit_leaves_configuration_unchanged(
    tmp_path: Path,
) -> None:
    store = GatewaySettingsStore(tmp_path / "gateway.json")
    models = [
        _model_input(f"model_{index}", f"vision-model-{index}", "fixture-secret-key", image=True)
        for index in range(8)
    ]
    routing = {
        "analysis": "model_0",
        "script": "model_0",
        "material": "model_0",
        "video_review": "model_0",
    }
    store.save(models=models, routing=routing, default_model_id="model_0")
    before = store.path.read_bytes()

    with pytest.raises(GatewaySettingsError, match="8 个模型上限"):
        store.upsert_verified_model(
            base_url="https://another-relay.example.com/v1",
            upstream_model_id="ninth-vision-model",
            display_name="第九个模型",
            provider="openai_compatible",
            api_mode="chat_completions",
            api_key="ninth-secret-key",
        )

    assert store.path.read_bytes() == before
