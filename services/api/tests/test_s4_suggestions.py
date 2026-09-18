from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory_api.s3_gateway import GatewayResult
from content_factory_api.s3_settings import GatewayConfig
from content_factory_api.s4_suggestions import suggest_visible_facts


def _config() -> GatewayConfig:
    return GatewayConfig(
        "https://relay.example/v1",
        "vision-model",
        "chat_completions",
        "secret",
        "2026-09-07T00:00:00Z",
        model_id="model_material",
    )


def _source() -> dict[str, object]:
    return {
        "id": "source_product_image_001",
        "kind": "image",
        "label": "商品正面图.jpg",
    }


def test_visible_product_suggestions_send_images_without_local_paths(tmp_path: Path) -> None:
    image = tmp_path / "商品正面图.jpg"
    image.write_bytes(b"\xff\xd8\xfftest-image")
    captured: dict[str, object] = {}

    def fake_gateway(*_args, **kwargs) -> GatewayResult:
        captured.update(kwargs)
        return GatewayResult(
            content={"suggestions": [{
                "source_id": "source_product_image_001",
                "field": "feature",
                "label": "裤腰细节",
                "value": "腰头正面有双扣设计",
                "evidence": "正面图可见腰头并排的两个扣位",
                "confidence": "high",
            }]},
            response_id="response_test",
            raw_bytes=100,
        )

    result = suggest_visible_facts(
        {"product_id": "product_test_001", "revision": 1, "name": "临时商品", "sku": "TEMP-001"},
        [(_source(), image)],
        _config(),
        gateway_caller=fake_gateway,
    )

    context = str(captured["context_json"])
    assert str(tmp_path) not in context
    assert json.loads(context)["result_is_proposal_only"] is True
    assert str(captured["keyframe_data_urls"][0]).startswith("data:image/jpeg;base64,")
    assert result["saved"] is False
    assert result["suggestions"][0]["source_type"] == "media_evidence"


def test_visible_product_suggestions_reject_nonvisual_product_claims(tmp_path: Path) -> None:
    image = tmp_path / "商品正面图.jpg"
    image.write_bytes(b"\xff\xd8\xfftest-image")

    def fake_gateway(*_args, **_kwargs) -> GatewayResult:
        return GatewayResult(
            content={"suggestions": [{
                "source_id": "source_product_image_001",
                "field": "fabric",
                "label": "面料",
                "value": "100% 羊毛",
                "evidence": "看起来像羊毛",
                "confidence": "medium",
            }]},
            response_id="response_test",
            raw_bytes=100,
        )

    with pytest.raises(ValueError, match="不可由图片确认"):
        suggest_visible_facts(
            {"product_id": "product_test_001", "revision": 1, "name": "临时商品", "sku": "TEMP-001"},
            [(_source(), image)],
            _config(),
            gateway_caller=fake_gateway,
        )
