from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory_api import s3_settings, speech_captions
from content_factory_api import auto_edit_worker, natural_clips
from content_factory_api.s3_settings import (
    GatewayConfig,
    GatewayModelConfig,
    GatewaySettingsError,
    GatewaySettingsStore,
)


def _text_image_speech_model() -> GatewayModelConfig:
    """A legacy profile that incorrectly declared speech on a vision model."""

    return GatewayModelConfig(
        model_id="model_legacy",
        display_name="旧图文模型",
        base_url="https://relay.example/v1",
        model="vision-only-model",
        api_mode="chat_completions",
        api_key="legacy-secret-key",
        modalities=("text", "image"),
        purposes=("analysis", "script", "material", "video_review", "speech"),
    )


def test_text_image_only_model_cannot_be_used_for_speech() -> None:
    """Declaring speech must not make a text/image-only profile an ASR model."""

    profile = _text_image_speech_model()
    config = GatewayConfig(
        base_url=profile.base_url,
        model=profile.model,
        api_mode=profile.api_mode,
        api_key=profile.api_key,
        updated_at="2026-09-26T00:00:00Z",
        model_id=profile.model_id,
        display_name=profile.display_name,
        modalities=profile.modalities,
        purposes=profile.purposes,
        models=(profile,),
        routing={"speech": profile.model_id},
    )

    with pytest.raises(GatewaySettingsError, match="语音|原音识别"):
        config.for_purpose("speech")


def test_loading_legacy_illegal_speech_route_strips_it_without_rewriting_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Old metadata remains byte-for-byte intact while its invalid speech route is ignored."""

    path = tmp_path / "gateway.json"
    payload = {
        "schema_version": "2.1.0",
        "default_model_id": "model_legacy",
        "routing": {
            "analysis": "model_legacy",
            "script": "model_legacy",
            "material": "model_legacy",
            "video_review": "model_legacy",
            "speech": "model_legacy",
        },
        "models": [{
            "model_id": "model_legacy",
            "display_name": "旧图文模型",
            "provider": "openai_compatible",
            "base_url": "https://relay.example/v1",
            "model": "vision-only-model",
            "api_mode": "chat_completions",
            "modalities": ["text", "image"],
            "purposes": ["analysis", "script", "material", "video_review", "speech"],
            "enabled": True,
            "protected_api_key": "legacy-dpapi-ciphertext",
        }],
        "updated_at": "2026-09-26T00:00:00Z",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    original_bytes = path.read_bytes()
    monkeypatch.setattr(s3_settings, "_unprotect_secret", lambda value: "legacy-secret-key")

    loaded = GatewaySettingsStore(path).load()

    assert loaded is not None
    assert "speech" not in loaded.routing
    assert "speech" not in loaded.public_dict()["routing"]
    assert "speech" not in loaded.for_model("model_legacy").purposes
    with pytest.raises(GatewaySettingsError, match="语音|原音识别"):
        loaded.for_purpose("speech")
    assert path.read_bytes() == original_bytes


def test_local_asr_mkl_malloc_is_reported_as_memory_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Whisper model allocation failure must not be reported as a generic network error."""

    model = tmp_path / "large-v3-turbo"
    model.mkdir()
    (model / "model.bin").write_bytes(b"placeholder")
    monkeypatch.setenv("CONTENT_FACTORY_SPEECH_MODEL", str(model))

    def failed_process(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            returncode=1,
            stderr=b"RuntimeError: mkl_malloc: failed to allocate memory",
        )

    monkeypatch.setattr(speech_captions.subprocess, "run", failed_process)

    with pytest.raises(speech_captions.SpeechRecognitionError, match="内存") as error:
        speech_captions._recognize_local(
            tmp_path / "source.mp4",
            tmp_path / "speech",
        )

    assert error.value.code == "local_asr_memory"


def test_auto_edit_uses_explicit_speech_route_not_video_review_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Natural-clip ASR must receive the audio route, not the planner route."""

    video_config = object()
    speech_config = object()
    gateway = SimpleNamespace(
        has_purpose=lambda purpose: purpose in {"video_review", "speech"},
        for_purpose=lambda purpose: video_config if purpose == "video_review" else speech_config,
    )
    monkeypatch.setattr(
        auto_edit_worker,
        "GatewaySettingsStore",
        lambda *args: SimpleNamespace(load=lambda: gateway),
    )

    class Pipeline:
        def __init__(self, **kwargs):
            del kwargs

        def process(self, source, workdir, task_id, *, original_name):
            del source, task_id, original_name
            workdir = Path(workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            transcript = workdir / "transcript.json"
            transcript.write_text(json.dumps({
                "segments": [{"start_ms": 0, "end_ms": 16000, "text": "完整口播"}],
            }), encoding="utf-8")
            keyframe = workdir / "frame.png"
            keyframe.write_bytes(b"png")
            result = workdir / "result.json"
            result.write_text(json.dumps({
                "asr": {"status": "completed"},
                "artifacts": {"transcript_path": str(transcript)},
                "shots": [{"id": "shot_1", "start_ms": 0, "end_ms": 16000, "keyframe_path": str(keyframe)}],
            }), encoding="utf-8")
            return result

    monkeypatch.setattr(auto_edit_worker, "MediaPipeline", Pipeline)
    seen: dict[str, object] = {}

    def recognize(*args, **kwargs):
        seen["gateway_config"] = kwargs.get("gateway_config")
        return {"words": [], "raw_segments": []}

    monkeypatch.setattr(speech_captions, "recognize", recognize)
    pool = [{"start_ms": 0, "end_ms": 16000, "text": "完整口播"}]
    monkeypatch.setattr(natural_clips, "build_clip_pool", lambda *args, **kwargs: pool)

    def planner(config, **kwargs):
        seen["planner_config"] = config
        return SimpleNamespace(content={
            "analysis_summary": "已读取",
            "selected_skill_id": "skill_1",
            "selection_reason": "匹配",
            "candidates": [{
                "candidate_id": "candidate_01",
                "title": "方案一",
                "source_id": "source_01",
                "skill_id": "skill_1",
                "selection_reason": "完整口播",
                "keywords": [],
                "clips": [{"start_ms": 0, "end_ms": 16000}],
            }],
        })

    monkeypatch.setattr(auto_edit_worker, "call_gateway", planner)
    project = {
        "project_id": "auto_edit_route_test",
        "source": {"source_id": "source_01", "path": str(tmp_path / "source.mp4"), "file_name": "source.mp4", "duration_ms": 16000},
        "settings": {"duration_policy": "bounded_15_30", "duration_min_ms": 15000, "duration_max_ms": 30000, "target_count": 1},
        "eligible_skill_snapshots": [{"skill_id": "skill_1", "revision": 1, "name": "方法", "steps": []}],
    }

    auto_edit_worker.analyze_and_plan_with_gateway(project, root=tmp_path / "work")
    assert seen["planner_config"] is video_config
    assert seen["gateway_config"] is speech_config
