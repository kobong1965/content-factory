"""Deterministic local S2 media preprocessing pipeline."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from content_factory_contracts import validate_or_raise

from .models import AsrSummary, MediaArtifacts, MediaResult, ShotCandidate, SourceInfo
from .tools import (
    MediaToolError,
    copy_atomic,
    find_tool,
    probe_media,
    run_command,
    safe_child,
    sha256_file,
    validate_video_path,
    whisper_relative_model,
    write_json_atomic,
)

ProgressCallback = Callable[[str, int], None]
_SCENE_TIME = re.compile(r"pts_time:(?P<time>[0-9.]+)")
_SCENE_SCORE = re.compile(r"lavfi\.scd\.score=(?P<score>[0-9.]+)")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _temporary_output(path: Path) -> Path:
    return path.with_name(f"{path.stem}.partial{path.suffix}")


def _escape_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _commit_generated(temporary: Path, destination: Path, label: str) -> None:
    if not temporary.is_file() or temporary.stat().st_size <= 0:
        raise MediaToolError(f"{label}命令结束但没有生成有效文件")
    os.replace(temporary, destination)


def _verify_generated_stream(
    path: Path,
    *,
    label: str,
    ffprobe: str,
    stream_type: str,
) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise MediaToolError(f"{label}命令结束但没有生成有效文件", diagnostic_code="media_output_invalid")
    try:
        completed = run_command(
            [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
            timeout=60,
            cwd=path.parent,
        )
        payload = json.loads(completed.stdout)
        streams = payload.get("streams", []) if isinstance(payload, dict) else []
    except (MediaToolError, json.JSONDecodeError) as exc:
        raise MediaToolError(
            f"{label}文件完整性校验失败",
            pid=getattr(exc, "pid", None),
            exit_code=getattr(exc, "exit_code", None),
            stderr_tail=getattr(exc, "stderr_tail", ""),
            diagnostic_code="media_output_invalid",
        ) from exc
    if not any(isinstance(stream, dict) and stream.get("codec_type") == stream_type for stream in streams):
        raise MediaToolError(
            f"{label}文件中缺少可读取的{'视频' if stream_type == 'video' else '音频'}流",
            diagnostic_code="media_output_invalid",
        )


def _segment_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("segments", "transcription", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        return 1 if payload else 0
    return 0


class MediaPipeline:
    def __init__(
        self,
        *,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        asr_model_path: str | Path | None = None,
        scene_threshold: float = 8.0,
    ) -> None:
        self.ffmpeg = ffmpeg_path or find_tool("ffmpeg")
        self.ffprobe = ffprobe_path or find_tool("ffprobe")
        self.asr_model = Path(asr_model_path).expanduser().resolve() if asr_model_path else None
        self.scene_threshold = scene_threshold

    def process(
        self,
        source_path: str | Path,
        workspace_path: str | Path,
        task_id: str,
        *,
        fixture_data: bool = False,
        original_name: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        notify = progress or (lambda _step, _progress: None)
        source = validate_video_path(source_path)
        workspace = Path(workspace_path).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        task_directory = safe_child(workspace, "tasks", task_id)
        task_directory.mkdir(parents=True, exist_ok=True)
        timings: dict[str, int] = {}

        def timed(name: str, action: Callable[[], Any]) -> Any:
            started = time.perf_counter()
            try:
                return action()
            finally:
                timings[name] = round((time.perf_counter() - started) * 1000)

        try:
            notify("probe", 5)
            initial_hash = timed("hash", lambda: sha256_file(source))
            info = timed("probe", lambda: probe_media(source, self.ffprobe))

            notify("copy", 15)
            managed_original = safe_child(
                workspace,
                "originals",
                initial_hash[:2],
                f"{initial_hash}{source.suffix.lower()}",
            )
            timed("copy", lambda: copy_atomic(source, managed_original, expected_sha256=initial_hash))

            notify("proxy", 30)
            proxy_path = safe_child(task_directory, "proxy.mp4")
            timed("proxy", lambda: self._create_proxy(managed_original, proxy_path))

            notify("audio", 45)
            audio_path: Path | None = None
            if info.has_audio:
                audio_path = safe_child(task_directory, "audio-16k-mono.wav")
                timed("audio", lambda: self._extract_audio(managed_original, audio_path))
            else:
                timings["audio"] = 0

            notify("asr", 60)
            transcript_path, asr_summary = timed("asr", lambda: self._transcribe(audio_path, task_directory))

            notify("scenes", 72)
            boundaries = timed("scenes", lambda: self._detect_scenes(managed_original, info.duration_ms, task_directory))

            notify("keyframes", 85)
            shots = timed(
                "keyframes",
                lambda: self._create_keyframes(managed_original, info.duration_ms, boundaries, task_directory),
            )

            scene_manifest_path = safe_child(task_directory, "scenes.json")
            write_json_atomic(
                scene_manifest_path,
                {
                    "schema_version": "1.0.0",
                    "fixture_data": fixture_data,
                    "algorithm": "ffmpeg_scdet",
                    "threshold": self.scene_threshold,
                    "shots": [shot.__dict__ for shot in shots],
                },
            )

            notify("finalize", 95)
            if sha256_file(source) != initial_hash:
                raise MediaToolError("处理期间源视频发生变化，任务已停止")
            result = MediaResult(
                schema_version="1.0.0",
                fixture_data=fixture_data,
                task_id=task_id,
                source=SourceInfo(
                    original_name=original_name or source.name,
                    sha256=initial_hash,
                    size_bytes=source.stat().st_size,
                    managed_original_path=str(managed_original),
                ),
                media=info,
                artifacts=MediaArtifacts(
                    proxy_path=str(proxy_path),
                    audio_path=str(audio_path) if audio_path else None,
                    transcript_path=str(transcript_path) if transcript_path else None,
                    scene_manifest_path=str(scene_manifest_path),
                ),
                asr=asr_summary,
                shots=tuple(shots),
                timings_ms=timings,
                created_at=_now_iso(),
            )
            result_payload = result.to_dict()
            validate_or_raise("media_result", result_payload)
            result_path = safe_child(task_directory, "result.json")
            write_json_atomic(result_path, result_payload)
            notify("completed", 100)
            return result_path
        finally:
            for partial in task_directory.rglob("*.partial.*"):
                partial.unlink(missing_ok=True)

    def _create_proxy(self, source: Path, destination: Path) -> None:
        temporary = _temporary_output(destination)
        run_command(
            [
                self.ffmpeg, "-y", "-v", "error", "-i", str(source),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "scale=w='min(720\\,iw)':h='min(1280\\,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(temporary),
            ],
            timeout=600,
            cwd=destination.parent,
        )
        _verify_generated_stream(
            temporary,
            label="代理视频",
            ffprobe=self.ffprobe,
            stream_type="video",
        )
        _commit_generated(temporary, destination, "代理视频")

    def _extract_audio(self, source: Path, destination: Path) -> None:
        temporary = _temporary_output(destination)
        run_command(
            [
                self.ffmpeg, "-y", "-v", "error", "-i", str(source),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(temporary),
            ],
            timeout=300,
            cwd=destination.parent,
        )
        _verify_generated_stream(
            temporary,
            label="音频提取",
            ffprobe=self.ffprobe,
            stream_type="audio",
        )
        _commit_generated(temporary, destination, "音频提取")

    def _transcribe(self, audio_path: Path | None, task_directory: Path) -> tuple[Path | None, AsrSummary]:
        if audio_path is None:
            return None, AsrSummary(status="no_audio", model_name=None, language=None, segment_count=0)
        if self.asr_model is None or not self.asr_model.is_file():
            return None, AsrSummary(status="model_missing", model_name=None, language=None, segment_count=0)

        transcript_path = safe_child(task_directory, "transcript.json")
        temporary = _temporary_output(transcript_path)
        with whisper_relative_model(self.asr_model, task_directory) as model_name:
            filter_value = (
                f"whisper=model='{model_name}':language=zh:queue=3:"
                f"use_gpu=false:destination={temporary.name}:format=json:max_len=12"
            )
            run_command(
                [
                    self.ffmpeg, "-y", "-v", "warning", "-i", str(audio_path),
                    "-vn", "-af", filter_value, "-f", "null", "-",
                ],
                timeout=600,
                cwd=task_directory,
            )
        if not temporary.is_file():
            raise MediaToolError("ASR 没有生成转写文件")
        text = temporary.read_text(encoding="utf-8").strip()
        try:
            payload = json.loads(text) if text else []
        except json.JSONDecodeError:
            try:
                payload = [json.loads(line) for line in text.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise MediaToolError("ASR 输出不是有效 JSON") from exc
        canonical_payload = {
            "schema_version": "1.0.0",
            "format": "ffmpeg_whisper_json",
            "segments": payload if isinstance(payload, list) else payload.get("segments", [payload]),
        }
        write_json_atomic(transcript_path, canonical_payload)
        temporary.unlink(missing_ok=True)
        return transcript_path, AsrSummary(
            status="completed",
            model_name=self.asr_model.name,
            language="zh",
            segment_count=_segment_count(canonical_payload),
        )

    def _detect_scenes(self, source: Path, duration_ms: int, task_directory: Path) -> list[tuple[int, float]]:
        metadata_path = safe_child(task_directory, "scene-metadata.txt")
        metadata_path.unlink(missing_ok=True)
        run_command(
            [
                self.ffmpeg, "-y", "-v", "error", "-i", str(source),
                "-vf", f"scdet=threshold={self.scene_threshold}:sc_pass=1,metadata=print:file={metadata_path.name}",
                "-an", "-f", "null", "-",
            ],
            timeout=600,
            cwd=task_directory,
        )
        boundaries: list[tuple[int, float]] = [(0, 0.0)]
        if metadata_path.is_file():
            current_time: float | None = None
            for line in metadata_path.read_text(encoding="utf-8", errors="replace").splitlines():
                time_match = _SCENE_TIME.search(line)
                if time_match:
                    current_time = float(time_match.group("time"))
                score_match = _SCENE_SCORE.search(line)
                if score_match and current_time is not None:
                    boundary_ms = round(current_time * 1000)
                    if 250 <= boundary_ms <= duration_ms - 250 and boundary_ms - boundaries[-1][0] >= 250:
                        boundaries.append((boundary_ms, round(float(score_match.group("score")), 3)))
                    current_time = None
        metadata_path.unlink(missing_ok=True)
        return boundaries

    def _create_keyframes(
        self,
        source: Path,
        duration_ms: int,
        boundaries: list[tuple[int, float]],
        task_directory: Path,
    ) -> list[ShotCandidate]:
        keyframe_directory = safe_child(task_directory, "keyframes")
        keyframe_directory.mkdir(parents=True, exist_ok=True)
        starts = [boundary[0] for boundary in boundaries]
        ends = starts[1:] + [duration_ms]
        shots: list[ShotCandidate] = []
        for index, ((start_ms, score), end_ms) in enumerate(zip(boundaries, ends, strict=True), start=1):
            keyframe_path = safe_child(keyframe_directory, f"shot-{index:03d}.jpg")
            temporary = _temporary_output(keyframe_path)
            midpoint_seconds = ((start_ms + end_ms) / 2) / 1000
            run_command(
                [
                    self.ffmpeg, "-y", "-v", "error", "-ss", f"{midpoint_seconds:.3f}",
                    "-i", str(source), "-frames:v", "1",
                    "-vf", "scale=w='min(720\\,iw)':h=-2",
                    "-q:v", "2", str(temporary),
                ],
                timeout=120,
                cwd=task_directory,
            )
            _verify_generated_stream(
                temporary,
                label=f"第 {index} 张关键帧",
                ffprobe=self.ffprobe,
                stream_type="video",
            )
            _commit_generated(temporary, keyframe_path, f"第 {index} 张关键帧")
            shots.append(
                ShotCandidate(
                    id=f"shot_{index:03d}",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    scene_score=score,
                    keyframe_path=str(keyframe_path),
                )
            )
        return shots
