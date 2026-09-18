"""Source-led automatic editing project API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .auto_edit_store import (
    AutoEditConflictError,
    AutoEditNotFoundError,
    AutoEditProjectStore,
)
from .auto_edit_worker import process_one
from .s3 import get_viral_skill_store


router = APIRouter(prefix="/s7/auto-edit-projects", tags=["automatic editing projects"])
_stores: dict[Path, AutoEditProjectStore] = {}
_store_lock = threading.Lock()
_runner_lock = threading.Lock()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_S7_DATA_DIR", _project_root() / "data" / "s7")).resolve() / "auto-edit"


def get_auto_edit_store() -> AutoEditProjectStore:
    database = _root() / "auto-edit-projects.sqlite3"
    with _store_lock:
        store = _stores.get(database)
        if store is None:
            store = AutoEditProjectStore(database)
            _stores[database] = store
        return store


def _probe_video(path: Path) -> int:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, check=True, timeout=60,
    )
    duration = float(json.loads(completed.stdout)["format"]["duration"])
    if not duration > 0:
        raise ValueError("视频时长无效")
    return round(duration * 1000)


def _hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _run_queue() -> None:
    if not _runner_lock.acquire(blocking=False):
        return
    try:
        store = get_auto_edit_store()
        while True:
            skills = get_viral_skill_store().list_skills(status="approved", reuse_mode="reuse")
            result = process_one(store, available_skills=skills, worker_id=f"auto-worker-{uuid4().hex[:8]}", root=_root())
            if result is None:
                return
    finally:
        _runner_lock.release()


def recover_auto_edit_queue() -> None:
    store = get_auto_edit_store()
    store.recover_interrupted()
    _run_queue()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SettingsPayload(StrictModel):
    target_count: int = Field(ge=1, le=10)
    duration_min_ms: int = Field(ge=5_000, le=180_000)
    duration_max_ms: int = Field(ge=5_000, le=180_000)
    subtitle_font_size: int = Field(ge=32, le=120)
    keyword_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    keyword_scale: float = Field(ge=1, le=2)
    top_title_enabled: bool = False
    subtitle_font: Literal['heiti', 'yahei', 'songti', 'kaiti'] = 'heiti'
    subtitle_effect: Literal['none', 'fade', 'pop'] = 'none'


class UpdateProjectRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=100)
    settings: SettingsPayload


class RevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)


def _raise_domain(exc: Exception) -> None:
    if isinstance(exc, AutoEditNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AutoEditConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.post("", status_code=201)
async def create_auto_edit_project(
    title: str = Form(min_length=1, max_length=100),
    settings_json: str = Form(max_length=10_000),
    source: UploadFile | None = File(default=None),
    sources: list[UploadFile] | None = File(default=None),
) -> dict[str, Any]:
    uploads = list(sources or []) + ([source] if source else [])
    if not 1 <= len(uploads) <= 20:
        for upload in uploads:
            await upload.close()
        raise HTTPException(status_code=422, detail='每个项目请选择 1—20 条录播视频')
    root = _root()
    token = uuid4().hex
    directory = (root / "sources" / token).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    try:
        try:
            settings = SettingsPayload.model_validate_json(settings_json).model_dump()
        except Exception as exc:
            raise ValueError("剪辑参数格式不正确") from exc
        stored = []
        for index, upload in enumerate(uploads):
            suffix = Path(upload.filename or '').suffix.lower()
            if suffix not in {'.mp4', '.mov', '.mkv', '.m4v', '.webm'}:
                raise ValueError('请选择 MP4、MOV、MKV、M4V 或 WebM 视频')
            destination = directory / f'{index:02d}{suffix}'
            temporary = destination.with_suffix(suffix + '.uploading')
            with temporary.open('wb') as handle:
                while chunk := await upload.read(1024 * 1024):
                    handle.write(chunk)
            if temporary.stat().st_size == 0:
                raise ValueError(f'上传的视频为空：{upload.filename}')
            temporary.replace(destination)
            stored.append({
                'source_id': f'source_{token}_{index}', 'file_name': upload.filename,
                'path': str(destination), 'sha256': _hash(destination), 'duration_ms': _probe_video(destination),
            })
        return get_auto_edit_store().create_project(
            title=title,
            sources=stored,
            settings=settings,
        )
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        _raise_domain(exc)
        raise
    finally:
        for upload in uploads:
            await upload.close()


@router.get("")
def list_auto_edit_projects(deleted: bool = False) -> dict[str, Any]:
    projects = get_auto_edit_store().list_projects(deleted=deleted)
    return {"projects": projects, "total": len(projects)}


@router.post('/{project_id}/trash')
def trash_auto_edit_project(project_id: str, request: RevisionRequest, restore: bool = False):
    try:
        return get_auto_edit_store().set_deleted(project_id, expected_revision=request.expected_revision, deleted=not restore)
    except Exception as exc:
        _raise_domain(exc)


@router.get("/{project_id}")
def get_auto_edit_project(project_id: str) -> dict[str, Any]:
    try:
        return get_auto_edit_store().get_project(project_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.patch("/{project_id}")
def update_auto_edit_project(project_id: str, request: UpdateProjectRequest) -> dict[str, Any]:
    try:
        return get_auto_edit_store().update_project(
            project_id, expected_revision=request.expected_revision,
            title=request.title, settings=request.settings.model_dump(),
        )
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/{project_id}/start", status_code=202)
def start_auto_edit_project(
    project_id: str, request: RevisionRequest, background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        project = get_auto_edit_store().enqueue(project_id, expected_revision=request.expected_revision)
    except Exception as exc:
        _raise_domain(exc)
        raise
    background_tasks.add_task(_run_queue)
    return project


@router.post("/{project_id}/retry", status_code=202)
def retry_auto_edit_project(
    project_id: str, request: RevisionRequest, background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    return start_auto_edit_project(project_id, request, background_tasks)


@router.post("/{project_id}/cancel")
def cancel_auto_edit_project(project_id: str, request: RevisionRequest) -> dict[str, Any]:
    try:
        return get_auto_edit_store().cancel(project_id, expected_revision=request.expected_revision)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/{project_id}/plan")
def get_auto_edit_plan(project_id: str) -> dict[str, Any]:
    project = get_auto_edit_project(project_id)
    return {
        "project_id": project_id,
        "status": project["status"],
        "analysis_summary": project.get("analysis_summary"),
        "selected_skill": project.get("selected_skill"),
        "plan": project.get("plan") or [],
    }
