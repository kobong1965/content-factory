"""Serializable S2 media domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None
    has_audio: bool
    format_name: str


@dataclass(frozen=True)
class SourceInfo:
    original_name: str
    sha256: str
    size_bytes: int
    managed_original_path: str


@dataclass(frozen=True)
class MediaArtifacts:
    proxy_path: str
    audio_path: str | None
    transcript_path: str | None
    scene_manifest_path: str


@dataclass(frozen=True)
class AsrSummary:
    status: Literal["completed", "no_audio", "model_missing"]
    model_name: str | None
    language: str | None
    segment_count: int


@dataclass(frozen=True)
class ShotCandidate:
    id: str
    start_ms: int
    end_ms: int
    scene_score: float
    keyframe_path: str


@dataclass(frozen=True)
class MediaResult:
    schema_version: Literal["1.0.0"]
    fixture_data: bool
    task_id: str
    source: SourceInfo
    media: MediaInfo
    artifacts: MediaArtifacts
    asr: AsrSummary
    shots: tuple[ShotCandidate, ...]
    timings_ms: dict[str, int]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["shots"] = list(payload["shots"])
        return payload
