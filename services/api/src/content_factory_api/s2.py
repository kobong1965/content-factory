from __future__ import annotations

import json
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from content_factory_contracts import validate_or_raise
from content_factory_media.queue import MediaTaskQueue
from content_factory_media.tools import (
    SUPPORTED_VIDEO_EXTENSIONS,
    MediaToolError,
    find_tool,
    probe_media,
    whisper_filter_available,
)
from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, HttpUrl

router = APIRouter(prefix="/s2", tags=["S2 media"])
_queue_runner_lock = threading.Lock()
_queue_instances_lock = threading.Lock()
_queue_instances: dict[Path, MediaTaskQueue] = {}
_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024 * 1024


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _runtime_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_RUNTIME_ROOT", _project_root() / "data" / "runtime")).resolve()


def _media_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_MEDIA_ROOT", _project_root() / "data" / "media")).resolve()


def _model_path() -> Path:
    return Path(
        os.environ.get(
            "CONTENT_FACTORY_WHISPER_MODEL",
            _project_root() / ".models" / "whisper" / "ggml-tiny.bin",
        )
    ).resolve()


def _queue() -> MediaTaskQueue:
    database_path = (_runtime_root() / "s2-tasks.sqlite3").resolve()
    with _queue_instances_lock:
        queue = _queue_instances.get(database_path)
        if queue is None:
            queue = MediaTaskQueue(database_path)
            queue.recover_interrupted()
            _queue_instances[database_path] = queue
        return queue


def _run_queue_safely() -> None:
    with _queue_runner_lock:
        queue = _queue()
        queue.run_pending(max_workers=4, asr_model_path=_model_path())
        _cleanup_completed_uploads(queue)


def _cleanup_completed_uploads(queue: MediaTaskQueue) -> None:
    upload_directory = (_runtime_root() / "uploads").resolve()
    for task in queue.list(limit=500):
        source = Path(task.source_path)
        result = Path(task.result_path) if task.result_path else None
        if (
            task.status == "completed"
            and source.is_relative_to(upload_directory)
            and result is not None
            and result.is_file()
        ):
            source.unlink(missing_ok=True)


def _accepted_real_case(item: object) -> bool:
    if not isinstance(item, dict) or item.get("status") != "accepted":
        return False
    source_sha256 = item.get("source")
    result_value = item.get("result")
    if not isinstance(source_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", source_sha256) is None:
        return False
    if not isinstance(result_value, str) or not result_value:
        return False
    result_path = Path(result_value)
    if not result_path.is_absolute():
        result_path = _project_root() / result_path
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        validate_or_raise("media_result", payload)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if payload.get("fixture_data") is not False or payload.get("source", {}).get("sha256") != source_sha256:
        return False
    if payload.get("asr", {}).get("status") == "model_missing":
        return False
    artifact_paths = [value for value in payload.get("artifacts", {}).values() if value]
    keyframe_paths = [shot.get("keyframe_path") for shot in payload.get("shots", [])]
    return all(Path(path).is_file() for path in [*artifact_paths, *keyframe_paths] if isinstance(path, str))


def _read_real_validation() -> tuple[int, int]:
    manifest_path = _project_root() / "data" / "media-validation" / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 10
    cases = payload.get("cases", [])
    try:
        required = int(payload.get("required_count", 10))
    except (TypeError, ValueError):
        return 0, 10
    accepted = sum(1 for item in cases if _accepted_real_case(item))
    return accepted, max(required, 1)


@lru_cache(maxsize=8)
def _local_capabilities(model_path: str) -> tuple[bool, bool, bool, bool]:
    try:
        ffmpeg = find_tool("ffmpeg")
        ffmpeg_ready = True
    except MediaToolError:
        ffmpeg = None
        ffmpeg_ready = False
    try:
        find_tool("ffprobe")
        ffprobe_ready = True
    except MediaToolError:
        ffprobe_ready = False
    filter_ready = bool(ffmpeg and whisper_filter_available(ffmpeg))
    return ffmpeg_ready, ffprobe_ready, filter_ready, Path(model_path).is_file()


class S2ReadinessResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: Literal["S2"] = "S2"
    engineering_ready: bool
    ffmpeg_ready: bool
    ffprobe_ready: bool
    whisper_filter_ready: bool
    whisper_model_ready: bool
    queue_ready: bool = True
    max_local_workers: Literal[4] = 4
    pending_tasks: int
    running_tasks: int
    completed_tasks: int
    failed_tasks: int
    accepted_real_videos: int
    required_real_videos: int
    business_ready: bool
    pending_reason: str | None


class LinkImportRequest(BaseModel):
    url: HttpUrl


class LinkImportResponse(BaseModel):
    status: Literal["manual_file_required"] = "manual_file_required"
    normalized_host: str
    message: str


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    fixture_data: bool
    task_id: str
    status: str
    progress: int
    current_step: str
    source_name: str
    source_path: str
    workspace_path: str
    attempt_count: int
    max_attempts: int
    error: str | None
    result_path: str | None
    created_at: str
    updated_at: str


class QueueRunResponse(BaseModel):
    status: Literal["started"] = "started"


@router.get("/readiness", response_model=S2ReadinessResponse)
def get_s2_readiness() -> S2ReadinessResponse:
    ffmpeg_ready, ffprobe_ready, filter_ready, model_ready = _local_capabilities(str(_model_path()))
    real_accepted, real_required = _read_real_validation()
    required_successes = (real_required * 9 + 9) // 10
    business_ready = real_accepted >= required_successes
    engineering_ready = ffmpeg_ready and ffprobe_ready and filter_ready and model_ready
    counts = _queue().counts()
    return S2ReadinessResponse(
        engineering_ready=engineering_ready,
        ffmpeg_ready=ffmpeg_ready,
        ffprobe_ready=ffprobe_ready,
        whisper_filter_ready=filter_ready,
        whisper_model_ready=model_ready,
        pending_tasks=counts["pending"] + counts["retry_wait"],
        running_tasks=counts["running"],
        completed_tasks=counts["completed"],
        failed_tasks=counts["failed"],
        accepted_real_videos=real_accepted,
        required_real_videos=real_required,
        business_ready=business_ready,
        pending_reason=None if business_ready else f"待完成 {max(0, real_required - real_accepted)} 条真实视频验收",
    )


@router.post("/import/file", response_model=TaskResponse, status_code=202)
async def import_file(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    fixture_header: str | None = Header(default=None, alias="X-Content-Factory-Fixture"),
) -> TaskResponse:
    original_name = Path(video.filename or "").name
    extension = Path(original_name).suffix.lower()
    if not original_name or extension not in SUPPORTED_VIDEO_EXTENSIONS:
        allowed = "、".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"请选择视频文件；支持格式：{allowed}")

    upload_directory = _runtime_root() / "uploads"
    upload_directory.mkdir(parents=True, exist_ok=True)
    incoming = upload_directory / f"{uuid4().hex}{extension}.uploading"
    stored = incoming.with_suffix("")
    total_bytes = 0
    try:
        with incoming.open("wb") as handle:
            while chunk := await video.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > _UPLOAD_LIMIT_BYTES:
                    raise HTTPException(status_code=413, detail="单个视频不能超过 4 GB")
                handle.write(chunk)
        os.replace(incoming, stored)
        probe_media(stored)
        fixture_data = (
            os.environ.get("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS") == "1"
            and fixture_header == "true"
        )
        queued = _queue().enqueue(
            stored,
            _media_root(),
            fixture_data=fixture_data,
            source_name=original_name,
        )
    except HTTPException:
        incoming.unlink(missing_ok=True)
        stored.unlink(missing_ok=True)
        raise
    except (OSError, MediaToolError) as exc:
        incoming.unlink(missing_ok=True)
        stored.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await video.close()

    background_tasks.add_task(_run_queue_safely)
    return TaskResponse(**queued.to_dict())


@router.post("/import/link", response_model=LinkImportResponse)
def import_link(request: LinkImportRequest) -> LinkImportResponse:
    parsed = urlparse(str(request.url))
    host = (parsed.hostname or "").lower()
    if host != "douyin.com" and not host.endswith(".douyin.com"):
        raise HTTPException(status_code=400, detail="目前只接受抖音链接")
    return LinkImportResponse(
        normalized_host=host,
        message="链接已识别。S2 不抓取平台视频，请下载获授权的原视频后，从本机导入。",
    )


@router.get("/tasks", response_model=list[TaskResponse])
def list_tasks() -> list[TaskResponse]:
    return [TaskResponse(**task.to_dict()) for task in _queue().list()]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    task = _queue().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到这个媒体任务")
    return TaskResponse(**task.to_dict())


@router.post("/queue/run", response_model=QueueRunResponse, status_code=202)
def run_queue(background_tasks: BackgroundTasks) -> QueueRunResponse:
    background_tasks.add_task(_run_queue_safely)
    return QueueRunResponse()
