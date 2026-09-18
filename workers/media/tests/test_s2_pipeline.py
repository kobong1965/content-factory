from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from content_factory_contracts import validate_or_raise
from content_factory_media import pipeline
from content_factory_media.pipeline import MediaPipeline
from content_factory_media.tools import MediaToolError, probe_media, sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = PROJECT_ROOT / "output" / "s2" / "fixture" / "s2-fixture.mp4"
MODEL = PROJECT_ROOT / ".models" / "whisper" / "ggml-tiny.bin"


@pytest.mark.skipif(
    os.environ.get("CONTENT_FACTORY_S2_INTEGRATION") != "1",
    reason="S2 media integration is run by verify-s2.ps1",
)
def test_real_ffmpeg_pipeline_handles_chinese_workspace_without_mutating_source(tmp_path: Path) -> None:
    assert FIXTURE.is_file()
    assert MODEL.is_file()
    source_hash = sha256_file(FIXTURE)
    result_path = MediaPipeline(asr_model_path=MODEL).process(
        FIXTURE,
        tmp_path / "中文媒体工作区",
        "media_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        fixture_data=True,
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    validate_or_raise("media_result", result)
    assert result["asr"]["status"] == "completed"
    assert result["asr"]["segment_count"] >= 1
    assert len(result["shots"]) >= 2
    assert sha256_file(FIXTURE) == source_hash
    assert Path(result["source"]["managed_original_path"]).is_file()
    assert Path(result["artifacts"]["proxy_path"]).is_file()
    assert Path(result["artifacts"]["transcript_path"]).is_file()
    assert probe_media(result["artifacts"]["proxy_path"]).height <= 1280


@pytest.mark.skipif(
    os.environ.get("CONTENT_FACTORY_S2_INTEGRATION") != "1",
    reason="S2 media integration is run by verify-s2.ps1",
)
def test_missing_asr_model_never_creates_a_fake_transcript(tmp_path: Path) -> None:
    result_path = MediaPipeline(asr_model_path=tmp_path / "missing-model.bin").process(
        FIXTURE,
        tmp_path / "workspace",
        "media_cccccccccccccccccccccccccccccccc",
        fixture_data=True,
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert result["asr"]["status"] == "model_missing"
    assert result["asr"]["segment_count"] == 0
    assert result["artifacts"]["transcript_path"] is None


def test_generated_output_integrity_requires_the_expected_stream(tmp_path: Path, monkeypatch) -> None:
    candidate = tmp_path / "proxy.partial.mp4"
    candidate.write_bytes(b"not-empty")
    monkeypatch.setattr(
        pipeline,
        "run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"streams": [{"codec_type": "audio"}]}),
            stderr="",
        ),
    )

    with pytest.raises(MediaToolError) as caught:
        pipeline._verify_generated_stream(
            candidate,
            label="代理视频",
            ffprobe="ffprobe",
            stream_type="video",
        )

    assert caught.value.diagnostic_code == "media_output_invalid"
