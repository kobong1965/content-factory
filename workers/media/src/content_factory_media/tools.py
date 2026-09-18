"""Safe FFmpeg, FFprobe and filesystem helpers for S2."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from uuid import uuid4
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

from .models import MediaInfo

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
_verified_whisper_models: set[Path] = set()
_whisper_model_lock = threading.Lock()


class MediaToolError(RuntimeError):
    """A concise, user-safe error raised by a local media command."""

    def __init__(
        self,
        message: str,
        *,
        pid: int | None = None,
        exit_code: int | None = None,
        stderr_tail: str = "",
        diagnostic_code: str = "media_command_failed",
    ) -> None:
        self.retryable = False
        self.pid = pid
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail[:1000]
        self.diagnostic_code = diagnostic_code
        super().__init__(message)


def find_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise MediaToolError(f"找不到 {name}，请先安装并加入 PATH")
    return executable


def run_command(
    command: Sequence[str],
    *,
    timeout: int = 300,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command_list = list(command)
    if cwd is not None:
        temporary_root = Path(cwd).resolve()
    else:
        runtime_root = Path(
            os.environ.get("CONTENT_FACTORY_RUNTIME_ROOT", Path.cwd() / "data" / "runtime")
        ).resolve()
        temporary_root = Path(
            os.environ.get("CONTENT_FACTORY_MEDIA_TEMP_ROOT", runtime_root / "command-logs")
        ).resolve()
    temporary_root.mkdir(parents=True, exist_ok=True)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # Regular files continuously drain the child at the OS level and keep
    # verbose/corrupt-media diagnostics out of process memory.
    stdout_file = tempfile.TemporaryFile(mode="w+b", dir=temporary_root)
    stderr_file = tempfile.TemporaryFile(mode="w+b", dir=temporary_root)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command_list,
            cwd=cwd,
            stdout=stdout_file,
            stderr=stderr_file,
            shell=False,
            creationflags=creation_flags,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            stderr_file.seek(0, os.SEEK_END)
            size = stderr_file.tell()
            stderr_file.seek(max(0, size - 1000))
            tail = stderr_file.read(1000).decode("utf-8", errors="replace")
            raise MediaToolError(
                f"媒体处理超时（{timeout} 秒）",
                pid=process.pid,
                exit_code=process.returncode,
                stderr_tail=tail,
                diagnostic_code="media_timeout",
            ) from exc

        stdout_file.seek(0)
        stdout = stdout_file.read(20 * 1024 * 1024 + 1).decode("utf-8", errors="replace")
        stderr_file.seek(0, os.SEEK_END)
        stderr_size = stderr_file.tell()
        stderr_file.seek(max(0, stderr_size - 1000))
        stderr_tail = stderr_file.read(1000).decode("utf-8", errors="replace")
        if process.returncode != 0:
            detail = stderr_tail.strip().splitlines()
            concise = detail[-1][:500] if detail else "未知错误"
            lowered = stderr_tail.lower()
            diagnostic_code = (
                "disk_full" if "no space left" in lowered or "not enough space" in lowered
                else "media_decode_failed" if any(marker in lowered for marker in ("invalid data", "corrupt", "error while decoding"))
                else "media_command_failed"
            )
            raise MediaToolError(
                f"媒体命令失败：{concise}",
                pid=process.pid,
                exit_code=process.returncode,
                stderr_tail=stderr_tail,
                diagnostic_code=diagnostic_code,
            )
        return subprocess.CompletedProcess(
            args=command_list,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr_tail,
        )
    except OSError as exc:
        raise MediaToolError(
            f"无法启动媒体命令：{type(exc).__name__}",
            pid=process.pid if process is not None else None,
            diagnostic_code="media_process_start_failed",
        ) from exc
    finally:
        stdout_file.close()
        stderr_file.close()


def validate_video_path(path: str | Path) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MediaToolError(f"视频文件不存在：{source.name}")
    if source.stat().st_size <= 0:
        raise MediaToolError("视频文件为空")
    if source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        allowed = "、".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise MediaToolError(f"不支持 {source.suffix or '无扩展名'}；可用格式：{allowed}")
    return source


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_media(path: str | Path, ffprobe_path: str | None = None) -> MediaInfo:
    ffprobe = ffprobe_path or find_tool("ffprobe")
    completed = run_command(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        timeout=60,
    )
    try:
        payload = json.loads(completed.stdout)
        streams = payload["streams"]
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        duration_value = payload.get("format", {}).get("duration") or video.get("duration")
        duration_ms = round(float(duration_value) * 1000)
        frame_rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
        fps = float(Fraction(frame_rate))
        info = MediaInfo(
            duration_ms=duration_ms,
            width=int(video["width"]),
            height=int(video["height"]),
            fps=round(fps, 3),
            video_codec=str(video["codec_name"]),
            audio_codec=str(audio["codec_name"]) if audio else None,
            has_audio=audio is not None,
            format_name=str(payload.get("format", {}).get("format_name", "unknown")),
        )
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError) as exc:
        raise MediaToolError("FFprobe 无法读出有效的视频流和时长") from exc
    if info.duration_ms <= 0 or info.width <= 0 or info.height <= 0 or info.fps <= 0:
        raise MediaToolError("视频基础信息无效")
    return info


def safe_child(root: str | Path, *parts: str) -> Path:
    resolved_root = Path(root).expanduser().resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise MediaToolError("产物路径越出了媒体工作区")
    return candidate


def copy_atomic(source: Path, destination: Path, *, expected_sha256: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256_file(destination) == (expected_sha256 or sha256_file(source)):
        return
    temporary = destination.with_name(
        f"{destination.name}.{os.getpid()}.{threading.get_ident()}.partial"
    )
    with source.open("rb") as incoming, temporary.open("wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    os.replace(temporary, destination)


def write_json_atomic(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def whisper_filter_available(ffmpeg_path: str | None = None) -> bool:
    try:
        ffmpeg = ffmpeg_path or find_tool("ffmpeg")
        completed = run_command([ffmpeg, "-hide_banner", "-filters"], timeout=30)
    except MediaToolError:
        return False
    return " whisper " in f" {completed.stdout} "


@contextmanager
def whisper_relative_model(model_path: Path, working_directory: Path):
    """Give narrow-fopen Whisper an ASCII filename relative to its Unicode cwd.

    A same-volume hardlink avoids a model copy for every task. Cross-volume
    installs use a temporary copy. Never change process-global cwd or the model.
    """
    source = model_path.resolve()
    if not source.is_file():
        raise MediaToolError('Whisper 语音模型不存在')
    reference = working_directory.resolve() / ('whisper-model-' + uuid4().hex + '.bin')
    try:
        try:
            os.link(source, reference)
        except OSError:
            with source.open('rb') as incoming, reference.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        yield reference.name
    finally:
        reference.unlink(missing_ok=True)


def whisper_compatible_model_path(model_path: str | Path) -> Path:
    """Return an ASCII-only model path for FFmpeg builds that use narrow fopen()."""
    source = Path(model_path).expanduser().resolve()
    if not source.is_file():
        raise MediaToolError("Whisper 语音模型不存在")
    try:
        str(source).encode("ascii")
        return source
    except UnicodeEncodeError:
        pass

    cache_base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    cache_directory = cache_base / "ContentFactory" / "whisper-models"
    try:
        str(cache_directory).encode("ascii")
    except UnicodeEncodeError as exc:
        raise MediaToolError("当前 Windows 用户目录含非英文字符，FFmpeg 无法打开 Whisper 模型") from exc

    digest = sha256_file(source)
    cached_model = cache_directory / f"{source.stem}-{digest[:16]}{source.suffix}"
    with _whisper_model_lock:
        if cached_model in _verified_whisper_models:
            return cached_model
        if (
            cached_model.is_file()
            and cached_model.stat().st_size == source.stat().st_size
            and sha256_file(cached_model) == digest
        ):
            _verified_whisper_models.add(cached_model)
            return cached_model
        copy_atomic(source, cached_model)
        _verified_whisper_models.add(cached_model)
        return cached_model
