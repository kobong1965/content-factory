from __future__ import annotations

import os
from pathlib import Path

import pytest

from content_factory_api.s8_imports import recognize_metric_image

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(os.environ.get("CONTENT_FACTORY_S8_INTEGRATION") != "1", reason="S8 actual OCR integration is opt-in")
def test_actual_local_ocr_reads_metric_screenshot() -> None:
    screenshot = PROJECT_ROOT / "output" / "s8" / "fixture" / "metric-screenshot.png"
    assert screenshot.is_file()

    raw_text, candidates = recognize_metric_image(
        screenshot, publication_id="publication_demo_001", captured_at="2026-08-31T01:00:00Z",
    )

    assert "播放量" in raw_text
    assert candidates[0]["confidence"] == "unconfirmed"
    assert candidates[0]["metrics"]["views"] == 12800
    assert candidates[0]["metrics"]["completion_rate"] == 0.31
