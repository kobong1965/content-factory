from __future__ import annotations

import io
import json
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from content_factory_api import s4
from content_factory_api.main import app
from content_factory_api.s4_products import script_product_eligibility
from content_factory_api.s4_store import ProductStore


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("CONTENT_FACTORY_S4_DATA_DIR", str(tmp_path / "s4"))
    s4._stores.clear()
    return TestClient(app)


def _create(client: TestClient, sku: str = "XZ-2308") -> dict:
    response = client.post("/s4/products", json={"sku": sku, "name": "垂感直筒西裤", "actor": "运营甲"})
    assert response.status_code == 201, response.text
    return response.json()


def _complete_payload(profile: dict, fixture_path: Path) -> dict:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    return {
        "expected_revision": profile["revision"],
        "actor": "运营甲",
        "sku": profile["sku"],
        "name": profile["name"],
        "sources": fixture["sources"],
        "facts": fixture["facts"],
        "selling_point_fact_ids": fixture["selling_point_fact_ids"],
        "forbidden_expressions": ["全网最低", "全网最低", " 百分百 "],
        "unprovable_claims": fixture["unprovable_claims"],
        "brand_boundary_confirmed_by": "运营甲",
        "brand_boundary_confirmed_at": "2026-08-29T08:00:00Z",
        "shooting_constraints": {
            **fixture["shooting_constraints"],
            "confirmed_by": "运营甲",
            "confirmed_at": "2026-08-29T08:00:00Z",
        },
    }


def _fixture() -> Path:
    return Path(__file__).resolve().parents[3] / "packages" / "contracts" / "fixtures" / "product.valid.json"


def test_s4_routes_are_exposed() -> None:
    paths = app.openapi()["paths"]
    assert "/s4/readiness" in paths
    assert "/s4/products" in paths
    assert "/s4/products/{product_id}/assets" in paths
    assert "/s4/products/{product_id}/versions" in paths
    assert "/s4/products/{product_id}/script-eligibility" in paths
    assert "/s4/products/{product_id}/suggestions" in paths


def test_product_lifecycle_is_persistent_and_versioned(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    assert created["completeness"]["ratio"] == 0.1
    assert "资料来源" in created["completeness"]["missing_fields"]

    saved_response = client.put(
        f"/s4/products/{created['product_id']}", json=_complete_payload(created, _fixture()),
    )
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["revision"] == 2
    assert saved["completeness"]["ratio"] == 1
    assert saved["forbidden_expressions"] == ["全网最低", "百分百"]

    active_response = client.post(
        f"/s4/products/{created['product_id']}/status",
        json={"expected_revision": 2, "status": "active", "actor": "负责人乙"},
    )
    assert active_response.status_code == 200, active_response.text
    assert active_response.json()["revision"] == 3
    assert active_response.json()["status"] == "active"

    versions = client.get(f"/s4/products/{created['product_id']}/versions").json()
    assert [item["revision"] for item in versions] == [3, 2, 1]
    assert versions[0]["action"] == "activated"

    reopened = ProductStore(tmp_path / "s4").get(created["product_id"])
    assert reopened["revision"] == 3
    assert reopened["status"] == "active"


def test_product_can_start_without_a_manual_sku(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/s4/products",
        json={"name": "黑灰色男士牛仔裤", "actor": "本机操作员"},
    )

    assert response.status_code == 201, response.text
    product = response.json()
    assert product["sku"].startswith("TEMP-")
    assert product["name"] == "黑灰色男士牛仔裤"


def test_generated_temporary_skus_are_unique_and_stable(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    first = client.post(
        "/s4/products", json={"name": "第一款临时商品", "actor": "本机操作员"},
    ).json()
    second = client.post(
        "/s4/products", json={"sku": "   ", "name": "第二款临时商品", "actor": "本机操作员"},
    ).json()

    assert re.fullmatch(r"TEMP-\d{8}-[0-9A-F]{8}", first["sku"])
    assert re.fullmatch(r"TEMP-\d{8}-[0-9A-F]{8}", second["sku"])
    assert first["sku"] != second["sku"]
    assert ProductStore(tmp_path / "s4").get(first["product_id"])["sku"] == first["sku"]


def test_new_product_has_fixed_livestream_physical_defaults_without_brand_claims(
    monkeypatch, tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)

    product = client.post(
        "/s4/products", json={"name": "直播间临时商品", "actor": "本机操作员"},
    ).json()
    constraints = product["shooting_constraints"]

    assert constraints["models"] == ["主播 1 人"]
    assert constraints["locations"] == ["固定直播间"]
    assert constraints["equipment"] == ["固定直播间竖屏机位（不移动）"]
    assert constraints["lights"] == ["固定直播间灯光"]
    assert constraints["max_duration_ms"] == 60_000
    assert constraints["brand_tone"] == ""
    assert constraints["required_disclosures"] == []


def test_visible_evidence_product_is_script_eligible_without_optional_catalog_fields(
    monkeypatch, tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/s4/products", json={"sku": "", "name": "黑灰色男士牛仔裤", "actor": "本机操作员"},
    ).json()
    image = b"\x89PNG\r\n\x1a\n" + b"fixture-png-body"
    uploaded_response = client.post(
        f"/s4/products/{created['product_id']}/assets",
        data={"expected_revision": "1", "actor": "本机操作员"},
        files={"upload": ("商品正面.png", image, "image/png")},
    )
    assert uploaded_response.status_code == 200, uploaded_response.text
    uploaded = uploaded_response.json()["profile"]
    source = uploaded["sources"][0]
    fact_id = "fact_visible_color_001"
    saved_response = client.put(
        f"/s4/products/{created['product_id']}",
        json={
            "expected_revision": uploaded["revision"],
            "actor": "本机操作员",
            "sku": uploaded["sku"],
            "name": uploaded["name"],
            "sources": uploaded["sources"],
            "facts": [{
                "id": fact_id,
                "field": "color",
                "label": "可见颜色",
                "value": "黑灰色",
                "unit": None,
                "source_type": "media_evidence",
                "source_id": source["id"],
                "source_ref": "商品正面图可见",
                "confirmed_by": "本机操作员",
                "confirmed_at": "2026-09-07T05:00:00Z",
            }],
            "selling_point_fact_ids": [fact_id],
            "forbidden_expressions": [],
            "unprovable_claims": [],
            "brand_boundary_confirmed_by": None,
            "brand_boundary_confirmed_at": None,
            "shooting_constraints": uploaded["shooting_constraints"],
        },
    )
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["completeness"]["ratio"] < 1

    eligibility = script_product_eligibility(saved)
    assert saved["status"] == "draft"
    assert eligibility["eligible"] is True
    assert eligibility["usable_fact_ids"] == [fact_id]
    assert set(eligibility["unknown_fields"]) >= {"面料", "版型", "尺码", "价格"}

    response = client.get(f"/s4/products/{created['product_id']}/script-eligibility")
    assert response.status_code == 200, response.text
    assert response.json() == eligibility


def test_product_suggestion_route_uses_managed_images_and_never_saves_candidates(
    monkeypatch, tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/s4/products", json={"name": "黑灰色男士牛仔裤", "actor": "本机操作员"},
    ).json()
    uploaded = client.post(
        f"/s4/products/{created['product_id']}/assets",
        data={"expected_revision": "1", "actor": "本机操作员"},
        files={"upload": ("商品正面.png", b"\x89PNG\r\n\x1a\nfixture-png-body", "image/png")},
    ).json()["profile"]
    captured: dict[str, object] = {}

    def fake_suggest(profile, image_sources, config):
        captured["profile"] = profile
        captured["image_sources"] = image_sources
        captured["config"] = config
        source = image_sources[0][0]
        return {
            "product_id": profile["product_id"],
            "product_revision": profile["revision"],
            "model_profile_id": "model_material_test",
            "model": "vision-test",
            "suggestions": [{
                "source_id": source["id"], "field": "color", "label": "可见颜色",
                "value": "黑灰色", "evidence": "商品正面图可见", "confidence": "high",
                "source_type": "media_evidence",
            }],
            "saved": False,
            "notice": "候选未保存",
        }

    monkeypatch.setattr(s4, "_suggestion_gateway", lambda: object())
    monkeypatch.setattr(s4, "suggest_visible_facts", fake_suggest)
    response = client.post(f"/s4/products/{created['product_id']}/suggestions")

    assert response.status_code == 200, response.text
    assert response.json()["saved"] is False
    assert Path(captured["image_sources"][0][1]).is_file()
    assert ProductStore(tmp_path / "s4").get(created["product_id"])["facts"] == []
    assert uploaded["revision"] == 2


def test_incomplete_product_cannot_be_activated(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = _create(client)

    response = client.post(
        f"/s4/products/{created['product_id']}/status",
        json={"expected_revision": 1, "status": "active", "actor": "运营甲"},
    )
    assert response.status_code == 422
    assert "100%" in response.json()["detail"]


def test_revision_conflict_never_overwrites_newer_product(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    payload = _complete_payload(created, _fixture())
    assert client.put(f"/s4/products/{created['product_id']}", json=payload).status_code == 200

    conflict = client.put(f"/s4/products/{created['product_id']}", json=payload)
    assert conflict.status_code == 409
    assert "刷新" in conflict.json()["detail"]


def test_list_search_status_and_readiness(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    _create(client, "XZ-2308")
    _create(client, "KZ-8812")

    assert len(client.get("/s4/products", params={"query": "2308"}).json()) == 1
    assert len(client.get("/s4/products", params={"status": "draft"}).json()) == 2
    readiness = client.get("/s4/readiness").json()
    assert readiness["total_products"] == 2
    assert readiness["accepted_real_products"] == 0
    assert readiness["pending_reason"] == "待补 3 款真实且完整的商品资料"


def test_asset_upload_is_managed_deduplicated_and_readable(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    image = b"\x89PNG\r\n\x1a\n" + b"fixture-png-body"

    first = client.post(
        f"/s4/products/{created['product_id']}/assets",
        data={"expected_revision": "1", "actor": "运营甲"},
        files={"upload": ("裤子正面.png", image, "image/png")},
    )
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["duplicate"] is False
    assert result["profile"]["revision"] == 2
    assert "\\" not in result["source"]["source_ref"]
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)

    second = client.post(
        f"/s4/products/{created['product_id']}/assets",
        data={"expected_revision": "2", "actor": "运营甲"},
        files={"upload": ("另一个名字.png", image, "image/png")},
    )
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["profile"]["revision"] == 2

    source = result["source"]
    download = client.get(f"/s4/products/{created['product_id']}/assets/{source['asset_id']}")
    assert download.status_code == 200
    assert download.content == image
    assert download.headers["content-type"].startswith("image/png")


def test_asset_content_must_match_extension(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    response = client.post(
        f"/s4/products/{created['product_id']}/assets",
        data={"expected_revision": "1", "actor": "运营甲"},
        files={"upload": ("伪造.png", b"not-an-image", "image/png")},
    )
    assert response.status_code == 422
    assert "扩展名" in response.json()["detail"]


def test_asset_file_is_removed_when_database_save_fails(monkeypatch, tmp_path: Path) -> None:
    store = ProductStore(tmp_path / "s4")
    created = store.create(sku="XZ-2308", name="垂感直筒西裤", actor="运营甲")

    def fail_save(*_args, **_kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(store, "_save", fail_save)
    image = b"\x89PNG\r\n\x1a\n" + b"fixture-png-body"

    with pytest.raises(RuntimeError, match="simulated database failure"):
        store.add_asset(
            created["product_id"], expected_revision=1, actor="运营甲",
            file_name="裤子正面.png", stream=io.BytesIO(image),
        )

    assert list(store.assets_root.rglob("*.png")) == []
