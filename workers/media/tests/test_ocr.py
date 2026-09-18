from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory_contracts import validate_or_raise
from content_factory_media.ocr import OcrEngineError, _extract_lines, recognize_keyframes


def _media_result(tmp_path: Path) -> dict:
    keyframe = tmp_path / "关键帧.jpg"
    keyframe.write_bytes(b"fixture-image")
    return {
        "schema_version": "1.0.0",
        "fixture_data": True,
        "task_id": "media_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "source": {
            "original_name": "样片.mp4",
            "sha256": "a" * 64,
            "size_bytes": 100,
            "managed_original_path": str(tmp_path / "样片.mp4"),
        },
        "media": {
            "duration_ms": 1000, "width": 360, "height": 640, "fps": 25,
            "video_codec": "h264", "audio_codec": None, "has_audio": False, "format_name": "mp4",
        },
        "artifacts": {
            "proxy_path": str(tmp_path / "proxy.mp4"), "audio_path": None,
            "transcript_path": None, "scene_manifest_path": str(tmp_path / "scenes.json"),
        },
        "asr": {"status": "no_audio", "model_name": None, "language": None, "segment_count": 0},
        "shots": [{"id": "shot_001", "start_ms": 0, "end_ms": 1000, "scene_score": 0, "keyframe_path": str(keyframe)}],
        "timings_ms": {"total": 1},
        "created_at": "2026-08-29T06:00:00Z",
    }


def test_recognize_keyframes_writes_contract_valid_result(tmp_path: Path) -> None:
    fake = SimpleNamespace(
        boxes=[[[10, 20], [110, 20], [110, 50], [10, 50]]],
        txts=["直筒西裤"],
        scores=[0.96],
    )

    payload = recognize_keyframes(
        media_result=_media_result(tmp_path),
        destination=tmp_path / "ocr.json",
        engine=lambda _path: fake,
    )

    validate_or_raise("ocr_result", payload)
    assert payload["frames"][0]["lines"][0]["text"] == "直筒西裤"
    assert payload["frames"][0]["lines"][0]["bbox"] == [10.0, 20.0, 100.0, 30.0]
    assert (tmp_path / "ocr.json").is_file()


def test_empty_ocr_result_is_not_fabricated() -> None:
    assert _extract_lines(SimpleNamespace(boxes=None, txts=None, scores=None), 1) == []


def test_mismatched_ocr_arrays_are_rejected() -> None:
    with pytest.raises(OcrEngineError, match="数量不一致"):
        _extract_lines(SimpleNamespace(boxes=[[]], txts=["字幕"], scores=[]), 1)
