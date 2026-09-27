"""Shared candidate identities and actionable, non-leaking batch validation."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from content_factory_api import edit_batches


def contract():
    return importlib.import_module("content_factory_api.candidate_contract")


def manifest_payload():
    return {
        "schema_version": 1, "id": "batch-034", "title": "中文标题与款号 J85",
        "analysis_summary": "按连续原声选段，不修改人工字幕。",
        "candidates": [{
            "id": f"candidate_{index:02d}", "title": f"第 {index} 条裤子展示",
            "source_path": "E:/素材/原声.mp4", "source_start_ms": 0, "source_end_ms": 1000,
            "video_path": f"中文成片{index}.mp4", "subtitle_path": f"中文字幕{index}.srt",
            "cover_path": f"中文封面{index}.jpg", "hook": "裤腰弹力展示",
            "benchmark_refs": [], "review_notes": [],
        } for index in range(1, 6)],
    }


@pytest.mark.parametrize("value", ["candidate_04", "A-z_09", "a" * 100])
def test_legal_existing_identity_is_preserved(value):
    module = contract()
    assert module.valid_candidate_id(value)
    assert module.stable_candidate_id(4, value) == value


@pytest.mark.parametrize("value", ["c4_banxing_zh展sh", "c4_banxing_zhչsh", "../escape", "C:\\private\\secret", "a/b", "a" * 101, "", None, 4, "candidate\n", " leading", "trailing "])
def test_invalid_identity_is_safely_normalized_before_any_render(value):
    module = contract()
    assert not module.valid_candidate_id(value)
    identity = module.stable_candidate_id(4, value)
    assert identity.startswith("candidate_04_")
    assert module.valid_candidate_id(identity)
    assert identity == module.stable_candidate_id(4, value)
    assert "/" not in identity and "\\" not in identity


def test_different_invalid_identities_and_positions_do_not_collapse():
    normalize = contract().stable_candidate_id
    values = [normalize(4, "款一"), normalize(4, "款二"), normalize(5, "款一"),
              normalize(4, None), normalize(4, ""), normalize(4, 4)]
    assert len(set(values)) == len(values)


@pytest.mark.parametrize("value", ["CON", "prn", "AuX", "NUL", "COM1", "com9", "LPT1", "lpt9"])
def test_windows_device_identity_is_replaced_only_for_artifact_generation(value):
    module = contract()
    assert module.valid_candidate_id(value), "Legacy logical IDs remain readable"
    assert not module.artifact_safe_candidate_id(value)
    assert module.artifact_safe_candidate_id(module.stable_candidate_id(4, value))
    assert module.stable_candidate_id(4, value) != value


@pytest.mark.parametrize("value", ["CON_01", "complete", "LPT10", "AUX-item"])
def test_non_device_ascii_artifact_identity_is_preserved(value):
    module = contract()
    assert module.artifact_safe_candidate_id(value)
    assert module.stable_candidate_id(4, value) == value


def test_unicode_titles_and_artifact_names_are_not_identity_errors():
    payload = manifest_payload()
    parsed = edit_batches.Manifest.model_validate(payload)
    assert parsed.title == payload["title"]
    assert parsed.candidates[0].video_path == "中文成片1.mp4"
    payload["candidates"][3]["id"] = "c4_banxing_zh展sh"
    with pytest.raises(ValidationError):
        edit_batches.Manifest.model_validate(payload)


def import_invalid(payload, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def unexpected_write_or_probe(*args, **kwargs):
        pytest.fail("Invalid manifest must fail before opening the batch DB or probing media")

    monkeypatch.setattr(edit_batches, "_db", unexpected_write_or_probe)
    monkeypatch.setattr(edit_batches, "_probe", unexpected_write_or_probe)
    with pytest.raises(HTTPException) as error:
        edit_batches.import_batch(edit_batches.ImportRequest(manifest_path=str(path.resolve())))
    assert error.value.status_code == 422
    return error.value.detail


def test_bad_fourth_id_reports_exact_position_without_echoing_user_input(tmp_path, monkeypatch):
    payload = manifest_payload()
    payload["candidates"][3]["id"] = "c4_banxing_zh展sh"
    detail = import_invalid(payload, tmp_path, monkeypatch)
    assert "第4条成片的内部编号" in detail
    assert "字母" in detail and "数字" in detail
    assert "c4_banxing" not in detail and "E:/素材" not in detail
    assert "原有批次不受影响" in detail


def test_overlong_title_reports_the_field_before_media_processing(tmp_path, monkeypatch):
    payload = manifest_payload()
    payload["candidates"][1]["title"] = "secret-text-" * 30
    detail = import_invalid(payload, tmp_path, monkeypatch)
    assert "第2条成片的标题" in detail and "200" in detail
    assert "secret-text" not in detail


def test_unknown_field_name_cannot_leak_arbitrary_model_text(tmp_path, monkeypatch):
    payload = manifest_payload()
    payload["candidates"][0]["sk-sensitive-credential-123"] = "private-path"
    detail = import_invalid(payload, tmp_path, monkeypatch)
    assert "第1条成片" in detail and "未支持的字段" in detail
    assert "sk-sensitive" not in detail and "private-path" not in detail


def test_duplicate_ids_fail_before_any_database_access(tmp_path, monkeypatch):
    payload = manifest_payload()
    payload["candidates"][3]["id"] = payload["candidates"][0]["id"]
    detail = import_invalid(payload, tmp_path, monkeypatch)
    assert "第4条成片的内部编号" in detail and "重复" in detail


def test_malformed_json_has_safe_specific_error(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text('{"candidates":["sk-private-', encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        edit_batches.import_batch(edit_batches.ImportRequest(manifest_path=str(path.resolve())))
    assert error.value.status_code == 422
    assert "JSON" in error.value.detail
    assert "sk-private" not in error.value.detail
