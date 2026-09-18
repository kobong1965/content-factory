from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading

import pytest
from fastapi.testclient import TestClient

from content_factory_api import s3, s5
from content_factory_api.main import app
from content_factory_api.s3_gateway import GatewayResult
from content_factory_api.s3_model_catalog import DiscoveredModel, ModelCatalog
from content_factory_api.s3_settings import GatewaySettingsStore


def _verified_model(
    model_id: str,
    *,
    base_url: str,
    upstream_model_id: str,
    api_key: str,
) -> dict[str, object]:
    return {
        "model_id": model_id,
        "display_name": model_id,
        "provider": "openai_compatible",
        "base_url": base_url,
        "model": upstream_model_id,
        "api_mode": "chat_completions",
        "api_key": api_key,
        "modalities": ["text", "image"],
        "purposes": ["analysis", "script", "material", "video_review"],
        "enabled": True,
    }


def _isolate_gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "analysis"))
    monkeypatch.setenv("CONTENT_FACTORY_S5_DATA_DIR", str(tmp_path / "s5"))
    s3._queue_instances.clear()
    s5._queue_instances.clear()
    monkeypatch.setattr(s3, "wake_analysis_worker", lambda: None)
    monkeypatch.setattr(s5, "_run_queue_safely", lambda: None)
    return config_path


@pytest.mark.skipif(os.name != "nt", reason="gateway persistence uses Windows DPAPI")
def test_concurrent_connects_competing_for_the_eighth_slot_save_only_one_complete_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = _isolate_gateway(monkeypatch, tmp_path)
    store = GatewaySettingsStore(config_path)
    initial_models = [
        _verified_model(
            f"model_{index:02d}",
            base_url="https://existing-relay.example/v1",
            upstream_model_id=f"existing-vision-{index}",
            api_key=f"existing-secret-{index}",
        )
        for index in range(7)
    ]
    initial_route = initial_models[0]["model_id"]
    store.save(
        models=initial_models,
        routing={purpose: initial_route for purpose in ("analysis", "script", "material", "video_review")},
        default_model_id=str(initial_route),
    )

    first_probe_started = threading.Event()
    release_first_probe = threading.Event()
    second_catalog_read = threading.Event()
    probe_models: list[str] = []
    probe_lock = threading.Lock()

    def fake_discover(*, base_url: str, api_key: str) -> ModelCatalog:
        del api_key
        if "second-relay" in base_url:
            second_catalog_read.set()
            upstream_model_id = "second-eighth-model"
        else:
            upstream_model_id = "first-eighth-model"
        return ModelCatalog(
            provider="openai_compatible",
            normalized_base_url=base_url,
            catalog_source="openai_models",
            models=(DiscoveredModel(upstream_model_id, upstream_model_id),),
        )

    def controlled_probe(config, **_kwargs) -> GatewayResult:
        with probe_lock:
            probe_models.append(config.model)
        if config.model == "first-eighth-model":
            first_probe_started.set()
            assert release_first_probe.wait(timeout=5), "test did not release the first probe"
        return GatewayResult({"image_token": "K7M2Q9"}, f"probe-{config.model}", 10)

    monkeypatch.setattr(s3, "discover_models", fake_discover)
    monkeypatch.setattr(s3, "call_gateway", controlled_probe)

    first_request = {
        "base_url": "https://first-relay.example/v1",
        "api_key": "first-eighth-secret",
        "upstream_model_id": "first-eighth-model",
    }
    second_request = {
        "base_url": "https://second-relay.example/v1",
        "api_key": "second-eighth-secret",
        "upstream_model_id": "second-eighth-model",
    }

    def post_connect(payload: dict[str, object]):
        return TestClient(app).post("/s3/gateway/connect", json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(post_connect, first_request)
        assert first_probe_started.wait(timeout=5), "first request never entered the reserved probe"
        second_future = executor.submit(post_connect, second_request)
        assert second_catalog_read.wait(timeout=5), "second request never reached model discovery"
        release_first_probe.set()
        first_response = first_future.result(timeout=10)
        second_response = second_future.result(timeout=10)

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 409, second_response.text
    assert second_response.json()["detail"]["diagnostic_code"] == "gateway_model_capacity_reached"
    assert probe_models == ["first-eighth-model"]

    persisted_json = json.loads(config_path.read_text(encoding="utf-8"))
    assert len(persisted_json["models"]) == 8
    assert not list(config_path.parent.glob(f".{config_path.name}.*.partial"))

    loaded = store.load()
    assert loaded is not None
    loaded_profiles = {model.model_id: model for model in loaded.available_models()}
    assert len(loaded_profiles) == 8
    for index in range(7):
        original = loaded_profiles[f"model_{index:02d}"]
        assert original.model == f"existing-vision-{index}"
        assert original.api_key == f"existing-secret-{index}"
    connected_id = first_response.json()["connected_model_id"]
    assert loaded_profiles[connected_id].model == "first-eighth-model"
    assert loaded_profiles[connected_id].api_key == "first-eighth-secret"
    assert all(model.model != "second-eighth-model" for model in loaded_profiles.values())
    assert all(model.api_key != "second-eighth-secret" for model in loaded_profiles.values())
    assert set(loaded.routing.values()) == {connected_id}


@pytest.mark.skipif(os.name != "nt", reason="gateway persistence uses Windows DPAPI")
def test_saved_model_id_reuses_the_exact_key_when_two_accounts_share_one_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = _isolate_gateway(monkeypatch, tmp_path)
    shared_base_url = "https://shared-relay.example/v1"
    models = [
        _verified_model(
            "model_account_a",
            base_url=shared_base_url,
            upstream_model_id="vision-account-a",
            api_key="account-a-secret-key",
        ),
        _verified_model(
            "model_account_b",
            base_url=shared_base_url,
            upstream_model_id="vision-account-b",
            api_key="account-b-secret-key",
        ),
    ]
    GatewaySettingsStore(config_path).save(
        models=models,
        routing={purpose: "model_account_a" for purpose in ("analysis", "script", "material", "video_review")},
        default_model_id="model_account_a",
    )
    observed_keys: list[str] = []

    def capture_discovery(*, base_url: str, api_key: str) -> ModelCatalog:
        observed_keys.append(api_key)
        return ModelCatalog(
            provider="openai_compatible",
            normalized_base_url=base_url,
            catalog_source="openai_models",
            models=(DiscoveredModel("available-vision-model", "available-vision-model"),),
        )

    monkeypatch.setattr(s3, "discover_models", capture_discovery)
    client = TestClient(app)

    account_b = client.post("/s3/gateway/discover", json={
        "base_url": shared_base_url,
        "saved_model_id": "model_account_b",
    })
    account_a = client.post("/s3/gateway/discover", json={
        "base_url": shared_base_url,
        "saved_model_id": "model_account_a",
    })

    assert account_b.status_code == 200, account_b.text
    assert account_a.status_code == 200, account_a.text
    assert observed_keys == ["account-b-secret-key", "account-a-secret-key"]
    assert "account-a-secret-key" not in account_a.text
    assert "account-b-secret-key" not in account_b.text
    assert "account-a-secret-key" not in account_b.text
    assert "account-b-secret-key" not in account_a.text
