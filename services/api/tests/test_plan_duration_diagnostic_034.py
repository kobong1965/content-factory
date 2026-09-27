"""Duration diagnostics identify invalid plans without leaking source metadata."""

import pytest

from content_factory_api.auto_edit_store import validate_edit_plan


def _settings():
    return {
        "target_count": 1,
        "duration_min_ms": 20_000,
        "duration_max_ms": 40_000,
        "subtitle_font_size": 68,
        "keyword_color": "#FFD400",
        "keyword_scale": 1.3,
        "top_title_enabled": False,
    }


@pytest.mark.parametrize("duration_ms, seconds", [(19_999, "19.999"), (40_001, "40.001")])
def test_duration_error_identifies_candidate_actual_seconds_and_allowed_range(duration_ms, seconds):
    plan = [{
        "candidate_id": "candidate_04_checked",
        "title": r"C:\private\sensitive-title.mp4",
        "selection_reason": "private internal note",
        "clips": [{"start_ms": 1_000, "end_ms": 1_000 + duration_ms}],
    }]
    with pytest.raises(ValueError) as error:
        validate_edit_plan(plan, source_duration_ms=300_000, settings=_settings())

    message = str(error.value)
    assert "candidate_04_checked" in message
    assert f"{seconds} 秒" in message
    assert "20.000—40.000 秒" in message
    assert "private" not in message
    assert "sensitive-title" not in message


def test_duration_diagnostic_sums_selected_clips_not_source_span():
    plan = [{
        "candidate_id": "candidate_02",
        "title": "两段选片",
        "clips": [{"start_ms": 1_000, "end_ms": 11_000}, {"start_ms": 100_000, "end_ms": 109_500}],
    }]
    with pytest.raises(ValueError) as error:
        validate_edit_plan(plan, source_duration_ms=300_000, settings=_settings())
    assert "19.500 秒" in str(error.value)
    assert "candidate_02" in str(error.value)


def test_invalid_candidate_identity_is_rejected_before_duration_diagnostic():
    unsafe_id = r"C:\private\source.mp4"
    plan = [{"candidate_id": unsafe_id, "title": "安全标题", "clips": [{"start_ms": 0, "end_ms": 1_000}]}]
    with pytest.raises(ValueError) as error:
        validate_edit_plan(plan, source_duration_ms=300_000, settings=_settings())
    assert "内部编号" in str(error.value)
    assert unsafe_id not in str(error.value)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("duration_ms", [20_000, 40_000])
def test_duration_boundaries_remain_valid_and_unchanged(duration_ms):
    plan = [{"candidate_id": "candidate_01", "title": "边界选片", "clips": [{"start_ms": 0, "end_ms": duration_ms}]}]
    validated = validate_edit_plan(plan, source_duration_ms=300_000, settings=_settings())
    assert validated[0]["duration_ms"] == duration_ms
    assert validated[0]["clips"] == plan[0]["clips"]
