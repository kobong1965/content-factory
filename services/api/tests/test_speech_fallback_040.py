from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from content_factory_api import speech_captions


def _api_words() -> dict[str, object]:
    return {
        "engine": "api-transcription",
        "alignment": "api-word-timestamps",
        "duration_ms": 18_000,
        "raw_segments": [{"text": "这是一段测试口播。", "start_ms": 0, "end_ms": 3_000}],
        "words": [
            {"text": "这是一段测试口播。", "start_ms": 0, "end_ms": 3_000, "probability": 0.98},
        ],
    }


def test_recognize_falls_back_to_api_when_local_asr_is_unavailable(tmp_path, monkeypatch):
    local_error = speech_captions.SpeechRecognitionError(
        "本地语音识别出现异常重复，未生成伪造字幕",
        code="local_asr_repetition",
    )
    monkeypatch.setattr(speech_captions, "_recognize_local", lambda *args, **kwargs: (_ for _ in ()).throw(local_error))
    seen: dict[str, object] = {}

    def api_stub(audio, workdir, *, config, hotwords):
        seen.update(audio=str(audio), workdir=str(workdir), config=config, hotwords=hotwords)
        return _api_words()

    monkeypatch.setattr(speech_captions, "_recognize_api", api_stub)
    config = SimpleNamespace(model="gpt-4o-mini-transcribe", model_id="speech_model")

    result = speech_captions.recognize(
        Path("sample.mp4"), tmp_path / "speech", gateway_config=config,
    )

    assert result["alignment"] == "api-word-timestamps"
    assert result["words"][0]["text"] == "这是一段测试口播。"
    assert seen["config"] is config


def test_recognize_can_prefer_configured_api_before_local_asr(tmp_path, monkeypatch):
    monkeypatch.setattr(
        speech_captions,
        "_recognize_local",
        lambda *args, **kwargs: pytest.fail("配置 API 语音路由时不应先消耗本地 ASR"),
    )
    config = SimpleNamespace(model="qwen-asr", model_id="speech_model")
    monkeypatch.setattr(speech_captions, "_recognize_api", lambda *args, **kwargs: _api_words())

    result = speech_captions.recognize(
        Path("sample.mp4"), tmp_path / "speech", gateway_config=config, prefer_api=True,
    )

    assert result["alignment"] == "api-word-timestamps"


def test_recognize_reports_local_and_api_failure_without_calling_paid_model(tmp_path, monkeypatch):
    monkeypatch.setattr(
        speech_captions,
        "_recognize_local",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            speech_captions.SpeechRecognitionError("本地模型输出异常", code="local_asr_repetition")
        ),
    )

    def api_error(*args, **kwargs):
        raise speech_captions.SpeechRecognitionError("API 返回 404", code="speech_api_endpoint_missing")

    monkeypatch.setattr(speech_captions, "_recognize_api", api_error)

    with pytest.raises(speech_captions.SpeechRecognitionError, match="本地模型输出异常") as error:
        speech_captions.recognize(
            Path("sample.mp4"), tmp_path / "speech", gateway_config=SimpleNamespace(model="transcribe"),
        )

    assert error.value.code == "speech_recognition_failed"
    assert "API 语音识别也不可用" in str(error.value)


def test_api_timing_normalizes_word_and_segment_timestamps():
    result = speech_captions._api_timing(
        {
            "text": "你好世界",
            "duration": 4.25,
            "words": [
                {"word": "你好", "start": 0.0, "end": 1.25},
                {"word": "世界", "start": 1.25, "end": 4.25},
            ],
            "segments": [
                {"text": "你好世界", "start": 0.0, "end": 4.25},
            ],
        }
    )
    assert result["duration_ms"] == 4_250
    assert [word["text"] for word in result["words"]] == ["你好", "世界"]
    assert result["words"][1]["start_ms"] == 1_250
    assert result["raw_segments"][0]["end_ms"] == 4_250


def test_api_transcription_posts_multipart_without_echoing_the_key(tmp_path, monkeypatch):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF-test-audio")
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "duration": 2.0,
                "words": [{"word": "测试", "start": 0.0, "end": 2.0}],
                "segments": [{"text": "测试", "start": 0.0, "end": 2.0}],
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["body"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(speech_captions, "urlopen", fake_urlopen)
    result = speech_captions._recognize_api(
        audio,
        tmp_path / "speech",
        config=SimpleNamespace(
            base_url="https://relay.example/v1",
            model="transcribe-model",
            api_key="never-log-this-key",
        ),
        hotwords=("裤子",),
    )

    assert captured["url"] == "https://relay.example/v1/audio/transcriptions"
    assert captured["authorization"] == "Bearer never-log-this-key"
    assert b"transcribe-model" in captured["body"]
    assert result["words"][0]["text"] == "测试"


def test_api_transcription_retries_v1_endpoint_for_unversioned_relay(tmp_path, monkeypatch):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF-test-audio")
    seen: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "duration": 2.0,
                "words": [{"word": "测试", "start": 0.0, "end": 2.0}],
            }).encode("utf-8")

    def relay_urlopen(request, timeout):
        del timeout
        seen.append(request.full_url)
        if request.full_url.endswith("/audio/transcriptions") and "/v1/" not in request.full_url:
            raise HTTPError(request.full_url, 404, "not found", {}, None)
        return Response()

    monkeypatch.setattr(speech_captions, "urlopen", relay_urlopen)
    result = speech_captions._recognize_api(
        audio,
        tmp_path / "speech",
        config=SimpleNamespace(
            base_url="https://api.apikey.fan",
            model="transcribe-model",
            api_key="test-key",
        ),
    )

    assert seen == [
        "https://api.apikey.fan/audio/transcriptions",
        "https://api.apikey.fan/v1/audio/transcriptions",
    ]
    assert result["words"][0]["text"] == "测试"


def test_api_probe_accepts_empty_json_without_timestamp_payload(tmp_path, monkeypatch):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF-test-audio")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr(speech_captions, "urlopen", lambda request, timeout: Response())
    result = speech_captions._recognize_api(
        audio,
        tmp_path / "speech",
        config=SimpleNamespace(
            base_url="https://relay.example/v1",
            model="transcribe-model",
            api_key="test-key",
        ),
        require_timestamps=False,
    )
    assert result["duration_ms"] == 0
    assert result["words"] == []


def test_api_404_is_reported_as_missing_transcription_endpoint(tmp_path, monkeypatch):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF-test-audio")

    def missing_endpoint(request, timeout):
        raise HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(speech_captions, "urlopen", missing_endpoint)
    with pytest.raises(speech_captions.SpeechRecognitionError, match="未提供 /audio/transcriptions") as error:
        speech_captions._recognize_api(
            audio,
            tmp_path / "speech",
            config=SimpleNamespace(
                base_url="https://relay.example/v1",
                model="text-only-model",
                api_key="test-key",
            ),
        )
    assert error.value.code == "speech_api_endpoint_missing"
