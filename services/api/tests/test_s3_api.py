from __future__ import annotations

import json
import base64
import shutil
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from content_factory_api import s3, s5
from content_factory_api.main import app
from content_factory_api.s3_gateway import GatewayError, GatewayResult, parse_gateway_response
from content_factory_api.s3_model_catalog import DiscoveredModel, ModelCatalog, ModelDiscoveryError


@pytest.fixture(autouse=True)
def _isolate_script_queue(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CONTENT_FACTORY_S5_DATA_DIR", str(tmp_path / "s5"))
    s5._queue_instances.clear()
    yield
    s5._queue_instances.clear()


def _configure(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "analysis"))
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    s3._queue_instances.clear()
    s3._settings_store().save(
        base_url="http://127.0.0.1:9876/v1",
        model="fixture-model",
        api_mode="responses",
        api_key="fixture-secret-key",
    )
    client = TestClient(app)
    return client


def _configure_multiple_models(monkeypatch, tmp_path: Path) -> tuple[TestClient, dict]:
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "analysis"))
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    s3._queue_instances.clear()
    client = TestClient(app)
    payload = {
        "default_model_id": "model_vision",
        "routing": {
            "analysis": "model_vision",
            "script": "model_text",
            "material": "model_vision",
            "video_review": "model_vision",
        },
        "models": [
            {
                "model_id": "model_vision",
                "display_name": "视觉模型",
                "provider": "openai",
                "base_url": "http://127.0.0.1:9876/v1",
                "model": "vision-model",
                "api_mode": "responses",
                "modalities": ["text", "image"],
                "purposes": ["analysis", "script", "material", "video_review"],
                "enabled": True,
                "api_key": "fixture-vision-key",
            },
            {
                "model_id": "model_text",
                "display_name": "文本模型",
                "provider": "qwen",
                "base_url": "http://127.0.0.1:9876/v1",
                "model": "text-model",
                "api_mode": "chat_completions",
                "modalities": ["text"],
                "purposes": ["script"],
                "enabled": True,
                "api_key": "fixture-text-key",
            },
        ],
    }
    s3._settings_store().save(**payload)
    return client, payload


def test_s3_routes_are_exposed() -> None:
    paths = app.openapi()["paths"]

    assert "/s3/readiness" in paths
    assert "/s3/gateway" in paths
    assert "/s3/analyses" in paths
    assert "/s3/tasks/{task_id}/retry" in paths
    assert "/s3/tasks/{task_id}/review" in paths


def test_gateway_settings_never_echo_secret(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)

    response = client.get("/s3/gateway")
    raw = (tmp_path / "gateway.json").read_text(encoding="utf-8")

    assert response.json()["api_key_configured"] is True
    assert "api_key" not in response.json()
    assert "fixture-secret-key" not in raw


def test_legacy_single_model_payload_is_exposed_as_v2_registry(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)

    settings = client.get("/s3/gateway").json()

    assert settings["schema_version"] == "2.1.0"
    assert settings["default_model_id"] == "model_default"
    assert settings["routing"] == {
        "analysis": "model_default",
        "script": "model_default",
        "material": "model_default",
        "video_review": "model_default",
    }
    assert settings["models"] == [{
        "model_id": "model_default",
        "display_name": "默认多模态模型",
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:9876/v1",
        "model": "fixture-model",
        "api_mode": "responses",
        "modalities": ["text", "image"],
        "purposes": ["analysis", "script", "material", "video_review"],
        "enabled": True,
        "api_key_configured": True,
    }]


def test_multiple_models_and_routing_round_trip_through_api(monkeypatch, tmp_path: Path) -> None:
    client, submitted = _configure_multiple_models(monkeypatch, tmp_path)

    settings = client.get("/s3/gateway").json()

    assert settings["default_model_id"] == submitted["default_model_id"]
    assert settings["routing"] == submitted["routing"]
    assert [model["model_id"] for model in settings["models"]] == ["model_vision", "model_text"]
    assert [model["modalities"] for model in settings["models"]] == [["text", "image"], ["text"]]
    assert [model["purposes"] for model in settings["models"]] == [
        ["analysis", "script", "material", "video_review"], ["script"],
    ]
    assert [model["provider"] for model in settings["models"]] == ["openai", "qwen"]
    assert all(model["api_key_configured"] is True for model in settings["models"])
    assert "api_key" not in settings
    assert all("api_key" not in model for model in settings["models"])
    serialized = json.dumps(settings)
    assert "fixture-vision-key" not in serialized and "fixture-text-key" not in serialized


def test_gpt_and_qwen_can_both_cover_every_business_purpose(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    client = TestClient(app)
    purposes = ["analysis", "script", "material", "video_review"]
    payload = {
        "default_model_id": "model_gpt",
        "routing": {
            "analysis": "model_gpt", "script": "model_qwen",
            "material": "model_qwen", "video_review": "model_gpt",
        },
        "models": [
            {
                "model_id": "model_gpt", "display_name": "GPT 多模态", "provider": "openai",
                "base_url": "https://api.openai.com/v1", "model": "gpt-multimodal-model",
                "api_mode": "responses", "api_key": "fixture-gpt-secret",
                "modalities": ["text", "image"], "purposes": purposes, "enabled": True,
            },
            {
                "model_id": "model_qwen", "display_name": "Qwen 多模态", "provider": "qwen",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-vl-model", "api_mode": "chat_completions",
                "api_key": "fixture-qwen-secret", "modalities": ["text", "image"],
                "purposes": purposes, "enabled": True,
            },
        ],
    }

    s3._settings_store().save(**payload)
    settings_response = client.get("/s3/gateway")

    assert settings_response.status_code == 200, settings_response.text
    settings = settings_response.json()
    assert settings["schema_version"] == "2.1.0"
    assert settings["routing"] == payload["routing"]
    assert [(item["provider"], item["api_mode"]) for item in settings["models"]] == [
        ("openai", "responses"), ("qwen", "chat_completions"),
    ]
    assert all(item["purposes"] == purposes for item in settings["models"])
    assert "fixture-gpt-secret" not in json.dumps(settings)
    assert "fixture-qwen-secret" not in json.dumps(settings)


def test_named_model_probe_reports_modalities_and_text_model_sends_no_image(monkeypatch, tmp_path: Path) -> None:
    client, _submitted = _configure_multiple_models(monkeypatch, tmp_path)
    calls: list[tuple[str, list[str]]] = []

    def fake_gateway(config, **kwargs):
        calls.append((config.model_id, kwargs["keyframe_data_urls"]))
        content = (
            {"image_token": "K7M2Q9"}
            if kwargs["keyframe_data_urls"]
            else {"ok": True}
        )
        return GatewayResult(content, f"resp_{config.model_id}", 12)

    monkeypatch.setattr(s3, "call_gateway", fake_gateway)

    text_response = client.post("/s3/gateway/test", params={"model_id": "model_text"})
    assert text_response.status_code == 200
    assert text_response.json()["model_id"] == "model_text"
    assert text_response.json()["tested_modalities"] == ["text"]
    assert calls[-1] == ("model_text", [])

    vision_response = client.post("/s3/gateway/test", params={"model_id": "model_vision"})
    assert vision_response.status_code == 200
    assert vision_response.json()["model_id"] == "model_vision"
    assert vision_response.json()["tested_modalities"] == ["text", "image"]
    assert calls[-1][0] == "model_vision"
    assert len(calls[-1][1]) == 1
    assert calls[-1][1][0].startswith("data:image/png;base64,")

    routed_response = client.post("/s3/gateway/test", params={"purpose": "video_review"})
    assert routed_response.status_code == 200
    assert routed_response.json()["model_id"] == "model_vision"
    assert routed_response.json()["provider"] == "openai"


def test_multimodal_connection_probe_uses_qwen_compatible_image_dimensions() -> None:
    encoded = s3._VISION_PROBE_DATA_URL.split(",", 1)[1]
    png = base64.b64decode(encoded, validate=True)

    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (384, 128)
    assert width > 10 and height > 10


def test_gateway_test_uses_configured_relay_without_exposing_key(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        s3,
        "call_gateway",
        lambda *_args, **_kwargs: GatewayResult(
            {"image_token": "K7M2Q9"}, "resp_fixture", 12,
        ),
    )

    response = client.post("/s3/gateway/test")

    assert response.status_code == 200
    assert response.json()["response_id"] == "resp_fixture"


def test_gateway_test_rejects_a_false_positive_multimodal_response(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        s3,
        "call_gateway",
        lambda *_args, **_kwargs: GatewayResult(
            {"ok": True, "image_token": "WRONG1"},
            "blind-test",
            12,
        ),
    )

    response = client.post("/s3/gateway/test")

    assert response.status_code == 502
    assert response.json()["detail"]["diagnostic_code"] == "visual_probe_failed"


def test_gateway_test_returns_structured_error_and_normalizes_complete_endpoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _configure(monkeypatch, tmp_path)
    complete_endpoint = "http://127.0.0.1:9876/v1/chat/completions"
    s3._settings_store().save(
        base_url=complete_endpoint,
        model="fixture-model",
        api_mode="chat_completions",
        api_key=None,
    )

    monkeypatch.setattr(
        s3,
        "call_gateway",
        lambda *_args, **_kwargs: GatewayResult(
            {"image_token": "K7M2Q9"}, "resp_fixture", 12,
        ),
    )
    success = client.post("/s3/gateway/test")
    assert success.status_code == 200
    assert success.json()["endpoint_url"] == complete_endpoint

    def fail_gateway(*_args, **_kwargs):
        raise GatewayError(
            "中转站找不到模型标识",
            retryable=False,
            status_code=404,
            diagnostic_code="model_not_found",
        )

    monkeypatch.setattr(s3, "call_gateway", fail_gateway)
    failed = client.post("/s3/gateway/test")
    assert failed.status_code == 502
    assert failed.json()["detail"] == {
        "message": "中转站找不到模型标识",
        "diagnostic_code": "model_not_found",
        "endpoint_url": complete_endpoint,
        "model": "fixture-model",
        "api_mode": "chat_completions",
    }


def _catalog(base_url: str, model: str = "qwen3.7-plus") -> ModelCatalog:
    return ModelCatalog(
        provider="qwen" if "dashscope" in base_url else "openai_compatible",
        normalized_base_url=base_url,
        catalog_source="dashscope_models" if "dashscope" in base_url else "openai_models",
        models=(DiscoveredModel(
            upstream_model_id=model,
            display_name=model,
            input_modalities=("text", "image"),
            output_modalities=("text",),
            supports_structured_output=True,
            capability_source="provider_metadata",
        ),),
        warnings=(),
        truncated=False,
    )


def test_gateway_discover_returns_exact_catalog_without_persisting_or_echoing_key(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"]),
    )
    client = TestClient(app)

    response = client.post("/s3/gateway/discover", json={
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "do-not-echo-this-key",
    })

    assert response.status_code == 200, response.text
    assert response.json()["models"][0]["upstream_model_id"] == "qwen3.7-plus"
    assert "do-not-echo-this-key" not in response.text
    assert not config_path.exists()


def test_gateway_discover_reuses_saved_key_only_for_the_same_origin(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)
    observed: list[str] = []

    def fake_discover(**kwargs):
        observed.append(kwargs["api_key"])
        return _catalog(kwargs["base_url"], "fixture-model")

    monkeypatch.setattr(s3, "discover_models", fake_discover)
    same_origin = client.post("/s3/gateway/discover", json={
        "base_url": "http://127.0.0.1:9876/v1/chat/completions",
        "saved_model_id": "model_default",
    })
    changed_origin = client.post("/s3/gateway/discover", json={
        "base_url": "http://127.0.0.1:9877/v1",
        "saved_model_id": "model_default",
    })

    assert same_origin.status_code == 200, same_origin.text
    assert observed == ["fixture-secret-key"]
    assert changed_origin.status_code == 400
    assert changed_origin.json()["detail"]["diagnostic_code"] == "credential_origin_changed"


def test_gateway_connect_probes_exact_model_before_atomic_save_and_routes_all_tasks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"]),
    )
    calls: list[tuple[str, str, list[str]]] = []

    def successful_probe(config, **kwargs):
        calls.append((config.model, config.api_mode, kwargs["keyframe_data_urls"]))
        serialized_instructions = json.dumps({
            "context": kwargs["context_json"],
            "schema": kwargs["output_schema"],
        })
        assert "K7M2Q9" not in serialized_instructions
        assert kwargs["output_schema"]["properties"]["image_token"] == {
            "type": "string",
        }
        return GatewayResult({"image_token": "K7M2Q9"}, "probe-success", 24)

    monkeypatch.setattr(s3, "call_gateway", successful_probe)
    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "new-qwen-secret-key",
        "upstream_model_id": "qwen3.7-plus",
    })

    assert response.status_code == 200, response.text
    assert calls[0][0:2] == ("qwen3.7-plus", "chat_completions")
    assert len(calls[0][2]) == 1 and calls[0][2][0].startswith("data:image/png;base64,")
    settings = client.get("/s3/gateway").json()
    selected = next(item for item in settings["models"] if item["model"] == "qwen3.7-plus")
    assert response.json()["connected_model_id"] == selected["model_id"]
    assert all(value == selected["model_id"] for value in settings["routing"].values())
    assert "new-qwen-secret-key" not in response.text


def test_gateway_connect_accepts_qwen_singleton_array_and_ascii_lowercase_visual_token(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"]),
    )

    def qwen_real_shape_probe(*_args, **_kwargs):
        payload = {
            "id": "chatcmpl-qwen-real-shape",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '[{"image_token":"k7m2q9"}]'},
            }],
        }
        return parse_gateway_response(
            json.dumps(payload).encode(),
            "chat_completions",
        )

    monkeypatch.setattr(s3, "call_gateway", qwen_real_shape_probe)

    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "new-qwen-secret-key",
        "upstream_model_id": "qwen3.7-plus",
    })

    assert response.status_code == 200, response.text
    assert response.json()["response_id"] == "chatcmpl-qwen-real-shape"
    selected = next(
        item for item in response.json()["settings"]["models"]
        if item["model"] == "qwen3.7-plus"
    )
    assert response.json()["connected_model_id"] == selected["model_id"]


@pytest.mark.parametrize(
    ("model_output", "diagnostic_code"),
    [
        ("[]", "incomplete_response"),
        ('[{"image_token":"k7m2q9"},{"image_token":"k7m2q9"}]', "incomplete_response"),
        ('["k7m2q9"]', "incomplete_response"),
        ('[{"image_token":"k7m2qx"}]', "visual_probe_failed"),
    ],
)
def test_gateway_connect_rejects_unsafe_qwen_array_or_wrong_visual_code_without_writing(
    monkeypatch,
    tmp_path: Path,
    model_output: str,
    diagnostic_code: str,
) -> None:
    client = _configure(monkeypatch, tmp_path)
    config_path = tmp_path / "gateway.json"
    before = config_path.read_bytes()
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"]),
    )

    def qwen_invalid_shape_probe(*_args, **_kwargs):
        payload = {
            "id": "chatcmpl-qwen-invalid-shape",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": model_output},
            }],
        }
        return parse_gateway_response(
            json.dumps(payload).encode(),
            "chat_completions",
        )

    monkeypatch.setattr(s3, "call_gateway", qwen_invalid_shape_probe)

    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "new-qwen-secret-key",
        "upstream_model_id": "qwen3.7-plus",
    })

    assert response.status_code == 502
    assert response.json()["detail"]["diagnostic_code"] == diagnostic_code
    assert config_path.read_bytes() == before


def test_gateway_connect_rejects_a_model_that_did_not_read_the_probe_image_without_writing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _configure(monkeypatch, tmp_path)
    config_path = tmp_path / "gateway.json"
    before = config_path.read_bytes()
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"]),
    )
    monkeypatch.setattr(
        s3,
        "call_gateway",
        lambda *_args, **_kwargs: GatewayResult(
            {"ok": True, "image_token": "WRONG1"},
            "blind-probe",
            24,
        ),
    )

    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "new-qwen-secret-key",
        "upstream_model_id": "qwen3.7-plus",
    })

    assert response.status_code == 502
    assert response.json()["detail"]["diagnostic_code"] == "visual_probe_failed"
    assert config_path.read_bytes() == before


def test_gateway_connect_failure_keeps_previous_configuration_byte_for_byte(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _configure(monkeypatch, tmp_path)
    config_path = tmp_path / "gateway.json"
    before = config_path.read_bytes()
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"]),
    )

    def failed_probe(*_args, **_kwargs):
        raise GatewayError(
            "模型拒绝图片输入",
            retryable=False,
            status_code=400,
            diagnostic_code="image_input_unsupported",
        )

    monkeypatch.setattr(s3, "call_gateway", failed_probe)
    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "new-qwen-secret-key",
        "upstream_model_id": "qwen3.7-plus",
    })

    assert response.status_code == 502
    assert response.json()["detail"]["diagnostic_code"] == "image_input_unsupported"
    assert config_path.read_bytes() == before


def test_gateway_connect_tries_second_mode_only_for_protocol_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"], "relay-vision-model"),
    )
    attempted: list[str] = []

    def protocol_probe(config, **_kwargs):
        attempted.append(config.api_mode)
        if config.api_mode == "chat_completions":
            raise GatewayError(
                "中转站没有 Chat Completions",
                retryable=False,
                status_code=404,
                diagnostic_code="endpoint_not_found",
            )
        return GatewayResult({"image_token": "K7M2Q9"}, "responses-probe", 24)

    monkeypatch.setattr(s3, "call_gateway", protocol_probe)
    client = TestClient(app)
    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://relay.example.com/v1",
        "api_key": "relay-secret-key",
        "upstream_model_id": "relay-vision-model",
    })

    assert response.status_code == 200, response.text
    assert attempted == ["chat_completions", "responses"]
    assert client.get("/s3/gateway").json()["api_mode"] == "responses"


def test_gateway_connect_same_source_is_idempotent_and_preserves_internal_model_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"], "relay-vision-model"),
    )
    monkeypatch.setattr(
        s3,
        "call_gateway",
        lambda *_args, **_kwargs: GatewayResult(
            {"image_token": "K7M2Q9"}, "probe", 24,
        ),
    )
    client = TestClient(app)
    request = {
        "base_url": "https://relay.example.com/v1",
        "api_key": "relay-secret-key",
        "upstream_model_id": "relay-vision-model",
    }

    first = client.post("/s3/gateway/connect", json=request)
    second = client.post("/s3/gateway/connect", json=request)

    assert first.status_code == second.status_code == 200
    assert first.json()["connected_model_id"] == second.json()["connected_model_id"]
    assert len(second.json()["settings"]["models"]) == 1


def test_gateway_connect_rejects_a_ninth_identity_before_running_the_paid_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "analysis"))
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(config_path))
    s3._queue_instances.clear()
    store = s3._settings_store()
    models = [
        {
            "model_id": f"model_{index:02d}",
            "display_name": f"已验证模型 {index + 1}",
            "provider": "openai_compatible",
            "base_url": "https://existing-relay.example/v1",
            "model": f"existing-model-{index + 1}",
            "api_mode": "chat_completions",
            "api_key": f"fixture-existing-secret-{index + 1}",
            "modalities": ["text", "image"],
            "purposes": ["analysis", "script", "material", "video_review"],
            "enabled": True,
        }
        for index in range(8)
    ]
    store.save(
        models=models,
        routing={
            "analysis": "model_00",
            "script": "model_00",
            "material": "model_00",
            "video_review": "model_00",
        },
        default_model_id="model_00",
    )
    before = config_path.read_bytes()
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **_kwargs: ModelCatalog(
            provider="openai_compatible",
            normalized_base_url="https://new-relay.example/v1",
            catalog_source="openai_models",
            models=(DiscoveredModel("new-vision-model", "New vision model"),),
        ),
    )
    paid_probe_calls = 0

    def fail_if_paid_probe_runs(*_args, **_kwargs):
        nonlocal paid_probe_calls
        paid_probe_calls += 1
        raise AssertionError("容量已满时不得执行可能付费的模型探针")

    monkeypatch.setattr(s3, "call_gateway", fail_if_paid_probe_runs)
    client = TestClient(app)

    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://new-relay.example/v1",
        "api_key": "fixture-new-provider-secret",
        "upstream_model_id": "new-vision-model",
    })

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["diagnostic_code"] == "gateway_model_capacity_reached"
    assert "8" in response.json()["detail"]["message"]
    assert paid_probe_calls == 0
    assert config_path.read_bytes() == before


def test_gateway_connect_authentication_failure_never_falls_through_to_another_protocol(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: _catalog(kwargs["base_url"], "relay-vision-model"),
    )
    attempted: list[str] = []

    def rejected_probe(config, **_kwargs):
        attempted.append(config.api_mode)
        raise GatewayError(
            "密钥被拒绝",
            retryable=False,
            status_code=401,
            diagnostic_code="authentication_failed",
        )

    monkeypatch.setattr(s3, "call_gateway", rejected_probe)
    client = TestClient(app)
    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://relay.example.com/v1",
        "api_key": "relay-secret-key",
        "upstream_model_id": "relay-vision-model",
    })

    assert response.status_code == 502
    assert attempted == ["chat_completions"]
    assert not config_path.exists()


def test_gateway_connect_manual_fallback_still_requires_a_successful_multimodal_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(config_path))

    def catalog_unavailable(**_kwargs):
        raise ModelDiscoveryError(
            "中转站未开放模型目录",
            diagnostic_code="catalog_unsupported",
            retryable=False,
            upstream_status=404,
        )

    monkeypatch.setattr(s3, "discover_models", catalog_unavailable)
    probes: list[tuple[str, str, int]] = []

    def successful_probe(config, **kwargs):
        probes.append((config.model, config.api_mode, len(kwargs["keyframe_data_urls"])))
        return GatewayResult({"image_token": "K7M2Q9"}, "manual-probe", 24)

    monkeypatch.setattr(s3, "call_gateway", successful_probe)
    client = TestClient(app)
    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://relay-without-catalog.example.com/v1",
        "api_key": "relay-secret-key",
        "upstream_model_id": "relay-private-vision-model",
        "manual_model_id": True,
    })

    assert response.status_code == 200, response.text
    assert probes == [("relay-private-vision-model", "chat_completions", 1)]
    assert response.json()["settings"]["model"] == "relay-private-vision-model"
    assert config_path.exists()


def test_gateway_connect_manual_model_outside_a_truncated_catalog_still_requires_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: ModelCatalog(
            provider="openai_compatible",
            normalized_base_url=kwargs["base_url"],
            catalog_source="openai_models",
            models=(DiscoveredModel("listed-model", "listed-model"),),
            warnings=("目录已截断",),
            truncated=True,
        ),
    )
    probes: list[str] = []

    def successful_probe(config, **_kwargs):
        probes.append(config.model)
        return GatewayResult({"image_token": "K7M2Q9"}, "truncated-probe", 24)

    monkeypatch.setattr(s3, "call_gateway", successful_probe)
    client = TestClient(app)
    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://large-relay.example.com/v1",
        "api_key": "relay-secret-key",
        "upstream_model_id": "model-after-truncation",
        "manual_model_id": True,
    })

    assert response.status_code == 200, response.text
    assert probes == ["model-after-truncation"]
    assert response.json()["settings"]["model"] == "model-after-truncation"
    assert config_path.exists()


def test_gateway_connect_truncates_only_the_saved_display_name_after_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    long_display_name = "百炼视觉模型" * 20
    exact_model_id = "qwen-vision-exact-id"
    monkeypatch.setattr(
        s3,
        "discover_models",
        lambda **kwargs: ModelCatalog(
            provider="qwen",
            normalized_base_url=kwargs["base_url"],
            catalog_source="dashscope_models",
            models=(DiscoveredModel(
                upstream_model_id=exact_model_id,
                display_name=long_display_name,
                input_modalities=("text", "image"),
            ),),
        ),
    )
    monkeypatch.setattr(
        s3,
        "call_gateway",
        lambda *_args, **_kwargs: GatewayResult(
            {"image_token": "K7M2Q9"},
            "long-name-probe",
            24,
        ),
    )
    client = TestClient(app)

    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "qwen-secret-key",
        "upstream_model_id": exact_model_id,
    })

    assert response.status_code == 200, response.text
    saved_model = response.json()["settings"]["models"][0]
    assert len(saved_model["display_name"]) <= 60
    assert saved_model["model"] == exact_model_id


def test_gateway_connect_manual_fallback_cannot_bypass_authentication_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(config_path))

    def authentication_failure(**_kwargs):
        raise ModelDiscoveryError(
            "密钥被拒绝",
            diagnostic_code="authentication_failed",
            retryable=False,
            upstream_status=401,
        )

    monkeypatch.setattr(s3, "discover_models", authentication_failure)
    monkeypatch.setattr(
        s3,
        "call_gateway",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probe must not run")),
    )
    client = TestClient(app)
    response = client.post("/s3/gateway/connect", json={
        "base_url": "https://relay.example.com/v1",
        "api_key": "rejected-secret-key",
        "upstream_model_id": "relay-private-vision-model",
        "manual_model_id": True,
    })

    assert response.status_code == 502
    assert response.json()["detail"]["diagnostic_code"] == "authentication_failed"
    assert "rejected-secret-key" not in response.text
    assert not config_path.exists()


def test_gateway_discover_invalid_short_key_is_not_echoed_by_validation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    client = TestClient(app)

    response = client.post("/s3/gateway/discover", json={
        "base_url": "https://relay.example.com/v1",
        "api_key": "leakme",
    })

    assert response.status_code == 400
    assert response.json()["detail"]["diagnostic_code"] == "credential_invalid"
    assert "leakme" not in response.text


def test_gateway_put_legacy_short_key_is_never_echoed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    client = TestClient(app)

    response = client.put("/s3/gateway", json={
        "base_url": "https://relay.example.com/v1",
        "model": "relay-vision-model",
        "api_mode": "chat_completions",
        "api_key": "leakme",
    })

    assert response.status_code == 409
    assert response.json()["detail"]["diagnostic_code"] == "gateway_verification_required"
    assert "leakme" not in response.text


def test_gateway_put_nested_short_key_is_never_echoed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    client = TestClient(app)

    response = client.put("/s3/gateway", json={
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
            "provider": "openai_compatible",
            "base_url": "https://relay.example.com/v1",
            "model": "relay-vision-model",
            "api_mode": "chat_completions",
            "modalities": ["text", "image"],
            "purposes": ["analysis", "script", "material", "video_review"],
            "enabled": True,
            "api_key": "nestleak",
        }],
    })

    assert response.status_code == 409
    assert response.json()["detail"]["diagnostic_code"] == "gateway_verification_required"
    assert "nestleak" not in response.text


def test_gateway_put_oversized_model_list_never_echoes_nested_keys_or_writes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _configure(monkeypatch, tmp_path)
    config_path = tmp_path / "gateway.json"
    before = config_path.read_bytes()
    unique_secret = "NINTH-LIST-SECRET-MUST-NOT-LEAK"
    models = [
        {
            "model_id": f"model_extra_{index}",
            "display_name": f"模型 {index}",
            "provider": "openai_compatible",
            "base_url": "https://relay.example.com/v1",
            "model": f"vision-model-{index}",
            "api_mode": "chat_completions",
            "modalities": ["text", "image"],
            "purposes": ["analysis", "script", "material", "video_review"],
            "enabled": True,
            "api_key": unique_secret if index == 8 else f"fixture-secret-{index}",
        }
        for index in range(9)
    ]

    response = client.put("/s3/gateway", json={
        "default_model_id": "model_extra_0",
        "routing": {
            "analysis": "model_extra_0",
            "script": "model_extra_0",
            "material": "model_extra_0",
            "video_review": "model_extra_0",
        },
        "models": models,
    })

    assert response.status_code == 400
    assert response.json()["detail"]["diagnostic_code"] == "invalid_model_count"
    assert unique_secret not in response.text
    assert config_path.read_bytes() == before


@pytest.mark.parametrize(
    ("method", "path", "payload", "sentinel"),
    [
        (
            "put",
            "/s3/gateway",
            {"models": {"api_key": "MALFORMED-MODELS-OBJECT-SECRET"}},
            "MALFORMED-MODELS-OBJECT-SECRET",
        ),
        (
            "post",
            "/s3/gateway/discover",
            {
                "base_url": "https://relay.example.com/v1",
                "api_key": {"key": "MALFORMED-DISCOVER-SECRET"},
            },
            "MALFORMED-DISCOVER-SECRET",
        ),
        (
            "put",
            "/s3/gateway",
            {"models": ["MALFORMED-LIST-ITEM-SECRET"]},
            "MALFORMED-LIST-ITEM-SECRET",
        ),
    ],
)
def test_request_validation_errors_never_echo_malformed_secret_inputs(
    monkeypatch,
    tmp_path: Path,
    method: str,
    path: str,
    payload: object,
    sentinel: str,
) -> None:
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(config_path))
    response = TestClient(app).request(method, path, json=payload)

    assert response.status_code == 422
    assert sentinel not in response.text
    for error in response.json()["detail"]:
        assert set(error).issubset({"loc", "msg", "type"})
    assert not config_path.exists()


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("base_url", "https://other-relay.example.com/v1"),
        ("model", "another-vision-model"),
        ("provider", "openai"),
        ("api_mode", "chat_completions"),
        ("api_key", "replacement-secret-key"),
        ("modalities", ["text"]),
    ],
)
def test_gateway_put_cannot_change_an_existing_unverified_connection_identity(
    monkeypatch,
    tmp_path: Path,
    field: str,
    changed_value: object,
) -> None:
    client = _configure(monkeypatch, tmp_path)
    config_path = tmp_path / "gateway.json"
    before = config_path.read_bytes()
    settings = client.get("/s3/gateway").json()
    settings["models"][0].pop("api_key_configured")
    settings["models"][0][field] = changed_value

    response = client.put("/s3/gateway", json={
        "default_model_id": settings["default_model_id"],
        "routing": settings["routing"],
        "models": settings["models"],
    })

    assert response.status_code == 409
    assert response.json()["detail"]["diagnostic_code"] == "gateway_verification_required"
    assert "replacement-secret-key" not in response.text
    assert config_path.read_bytes() == before


def test_gateway_put_cannot_add_an_unverified_model(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)
    config_path = tmp_path / "gateway.json"
    before = config_path.read_bytes()
    settings = client.get("/s3/gateway").json()
    settings["models"][0].pop("api_key_configured")
    settings["models"].append({
        "model_id": "model_unverified",
        "display_name": "未验证模型",
        "provider": "openai_compatible",
        "base_url": "https://relay.example.com/v1",
        "model": "unverified-vision-model",
        "api_mode": "chat_completions",
        "modalities": ["text", "image"],
        "purposes": ["analysis", "script", "material", "video_review"],
        "enabled": True,
        "api_key": "unverified-secret-key",
    })

    response = client.put("/s3/gateway", json={
        "default_model_id": settings["default_model_id"],
        "routing": settings["routing"],
        "models": settings["models"],
    })

    assert response.status_code == 409
    assert response.json()["detail"]["diagnostic_code"] == "gateway_verification_required"
    assert "unverified-secret-key" not in response.text
    assert config_path.read_bytes() == before


def test_gateway_put_still_allows_non_identity_metadata_updates(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)
    settings = client.get("/s3/gateway").json()
    settings["models"][0].pop("api_key_configured")
    settings["models"][0]["display_name"] = "已验证视觉模型"

    response = client.put("/s3/gateway", json={
        "default_model_id": settings["default_model_id"],
        "routing": settings["routing"],
        "models": settings["models"],
    })

    assert response.status_code == 200, response.text
    assert response.json()["models"][0]["display_name"] == "已验证视觉模型"


@pytest.mark.parametrize(
    ("mutation", "use_multiple"),
    [
        ("omit_profile", True),
        ("disable_profile", False),
        ("remove_purpose", False),
    ],
)
def test_gateway_put_preserves_profiles_required_by_existing_task_snapshots(
    monkeypatch,
    tmp_path: Path,
    mutation: str,
    use_multiple: bool,
) -> None:
    if use_multiple:
        client, _payload = _configure_multiple_models(monkeypatch, tmp_path)
    else:
        client = _configure(monkeypatch, tmp_path)
    config_path = tmp_path / "gateway.json"
    before = config_path.read_bytes()
    settings = client.get("/s3/gateway").json()
    for model in settings["models"]:
        model.pop("api_key_configured")
    if mutation == "omit_profile":
        settings["models"] = settings["models"][:1]
        kept_id = settings["models"][0]["model_id"]
        settings["default_model_id"] = kept_id
        settings["routing"] = {
            purpose: kept_id
            for purpose in ("analysis", "script", "material", "video_review")
        }
    elif mutation == "disable_profile":
        settings["models"][0]["enabled"] = False
    else:
        settings["models"][0]["purposes"].remove("script")

    response = client.put("/s3/gateway", json={
        "default_model_id": settings["default_model_id"],
        "routing": settings["routing"],
        "models": settings["models"],
    })

    assert response.status_code == 409
    assert response.json()["detail"]["diagnostic_code"] == "gateway_verification_required"
    assert config_path.read_bytes() == before


def test_completed_media_task_can_enter_analysis_queue(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(s3, "ocr_available", lambda: True)
    media_result = tmp_path / "media-result.json"
    media_result.write_text("{}", encoding="utf-8")
    media_task = SimpleNamespace(
        task_id="media_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", status="completed",
        result_path=str(media_result), fixture_data=True,
    )
    monkeypatch.setattr(s3, "media_queue", lambda: SimpleNamespace(get=lambda _task_id: media_task))
    monkeypatch.setattr(s3, "_run_queue_safely", lambda: None)

    response = client.post(
        "/s3/analyses",
        json={
            "media_task_id": media_task.task_id,
            "metric_snapshots": [{"source_type": "manual", "confidence": 1, "values": {"play_count": 1000}}],
            "comments": [{"text": "裤型看着挺利落", "source_label": "人工摘录"}],
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.json()["model_purpose"] == "analysis"
    assert response.json()["metric_count"] == 1
    assert response.json()["comment_count"] == 1


def test_analysis_api_rejects_invalid_metric_timestamp_before_enqueue(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)

    response = client.post(
        "/s3/analyses",
        json={
            "media_task_id": "media_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "metric_snapshots": [{
                "captured_at": "not-a-date",
                "source_type": "manual",
                "confidence": 1,
                "values": {"play_count": 1000},
            }],
        },
    )

    assert response.status_code == 422


def test_video_review_reuses_analysis_queue_with_its_own_model_route(monkeypatch, tmp_path: Path) -> None:
    client, payload = _configure_multiple_models(monkeypatch, tmp_path)
    payload["models"].append({
        "model_id": "model_review",
        "display_name": "视频审核模型",
        "provider": "qwen",
        "base_url": "http://127.0.0.1:9876/v1",
        "model": "qwen-vl-review",
        "api_mode": "chat_completions",
        "modalities": ["text", "image"],
        "purposes": ["video_review"],
        "enabled": True,
        "api_key": "fixture-review-key",
    })
    payload["routing"]["video_review"] = "model_review"
    s3._settings_store().save(**payload)

    media_result = tmp_path / "media-review-result.json"
    media_result.write_text("{}", encoding="utf-8")
    media_task = SimpleNamespace(
        task_id="media_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        status="completed",
        result_path=str(media_result),
        fixture_data=True,
    )
    monkeypatch.setattr(s3, "ocr_available", lambda: True)
    monkeypatch.setattr(s3, "media_queue", lambda: SimpleNamespace(get=lambda _task_id: media_task))
    monkeypatch.setattr(s3, "_run_queue_safely", lambda: None)

    response = client.post(
        "/s3/analyses",
        json={"media_task_id": media_task.task_id, "model_purpose": "video_review"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["model_purpose"] == "video_review"
    task = s3._queue().get(response.json()["task_id"])
    assert task is not None
    queued_input = json.loads(Path(task.input_path).read_text(encoding="utf-8"))
    assert queued_input["_gateway_purpose"] == "video_review"
    assert queued_input["_gateway_model_snapshot"]["model_id"] == "model_review"
    assert queued_input["_gateway_model_snapshot"]["provider"] == "qwen"


def test_report_review_endpoint_advances_revision(monkeypatch, tmp_path: Path) -> None:
    client = _configure(monkeypatch, tmp_path)
    fixture = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures" / "analysis.valid.json"
    report = tmp_path / "analysis-report.json"
    shutil.copy2(fixture, report)
    task = SimpleNamespace(status="completed", result_path=str(report))
    monkeypatch.setattr(s3, "_task_or_404", lambda _task_id: task)

    response = client.post(
        "/s3/tasks/analysis_task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/review",
        json={"expected_revision": 1, "status": "reviewed", "reviewer": "运营甲", "note": "已核对证据"},
    )

    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert response.json()["status"] == "reviewed"
