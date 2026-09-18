"""S7 automatic editing, local rendering, and video-review API."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from content_factory_contracts import ContractValidationError
from content_factory_media.tools import MediaToolError, find_tool, write_json_atomic
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .s6 import _approved_script, _fixtures_allowed, _script_options, get_material_store
from .s6_matching import build_shooting_task
from .s6_store import MaterialConflictError, MaterialNotFoundError
from .s7_projects import build_edit_project
from .s7_queue import RenderQueue, RenderTaskRecord
from .s7_renderer import render_project
from .s7_store import EditConflictError, EditNotFoundError, EditStore

router = APIRouter(prefix="/s7", tags=["S7 editing studio"])
_stores: dict[Path, EditStore] = {}
_queues: dict[Path, RenderQueue] = {}
_instance_lock = threading.RLock()
_runner_lock = threading.Lock()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _data_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_S7_DATA_DIR", _project_root() / "data" / "s7")).resolve()


def _s6_media_root() -> Path:
    default = _project_root() / "data" / "s6" / "media"
    return Path(os.environ.get("CONTENT_FACTORY_S6_DATA_DIR", default.parent)).resolve() / "media"


def get_edit_store() -> EditStore:
    root = _data_root()
    with _instance_lock:
        store = _stores.get(root)
        if store is None:
            store = EditStore(root)
            _stores[root] = store
        return store


def get_render_queue() -> RenderQueue:
    database = _data_root() / "render-queue.sqlite3"
    with _instance_lock:
        queue = _queues.get(database)
        if queue is None:
            queue = RenderQueue(database)
            queue.recover_interrupted(_reconcile_interrupted_render)
            _queues[database] = queue
        return queue


def _raise_domain(exc: Exception) -> None:
    if isinstance(exc, (EditNotFoundError, MaterialNotFoundError, LookupError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (EditConflictError, MaterialConflictError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, ContractValidationError, MediaToolError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _visible_project(project_id: str) -> dict[str, Any]:
    project = get_edit_store().get_project(project_id)
    if project.get("fixture_data") and not _fixtures_allowed():
        raise EditNotFoundError("找不到这个剪辑工程")
    return project


def _visible_output(output_id: str) -> dict[str, Any]:
    output = get_edit_store().get_output(output_id)
    if output.get("fixture_data") and not _fixtures_allowed():
        raise EditNotFoundError("找不到这个成片")
    return output


def _reconcile_interrupted_render(task: RenderTaskRecord) -> str | None:
    """Return a fully registered output for this task, validating every resource."""

    store = get_edit_store()
    matches = [
        item for item in store.list_outputs(project_id=task.project_id, include_fixtures=True)
        if item.get("render", {}).get("task_id") == task.task_id
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("同一渲染任务对应多个成片")
    output = matches[0]
    try:
        if (
            output.get("project_id") != task.project_id
            or output.get("project_revision") != task.project_revision
            or output.get("variant_id") != task.variant_id
            or bool(output.get("fixture_data")) != task.fixture_data
        ):
            raise ValueError("已登记成片与渲染任务不一致")
        for key, resource_ref in output["resources"].items():
            if key != "jianying_experimental":
                store.resource(output["output_id"], resource_ref)
    except Exception:
        store.discard_incomplete_output(
            output["output_id"], reason="中断恢复时成片血缘或资源校验未通过",
        )
        return None
    return str(output["output_id"])


def _process_render(task: RenderTaskRecord, progress) -> str:
    project = task.snapshot()
    return render_project(
        task, project, material_store=get_material_store(), edit_store=get_edit_store(),
        s6_media_root=_s6_media_root(), progress=progress,
    )


def _run_queue() -> None:
    with _runner_lock:
        get_render_queue().run_pending(_process_render, max_workers=2)


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    actor: str = Field(min_length=1, max_length=100)


class SaveProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=100)
    settings: dict[str, Any]
    variants: list[dict[str, Any]] = Field(min_length=1, max_length=5)


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    variant_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)


@router.get("/readiness")
def readiness() -> dict[str, Any]:
    try:
        find_tool("ffmpeg")
        ffmpeg = True
    except MediaToolError:
        ffmpeg = False
    try:
        find_tool("ffprobe")
        ffprobe = True
    except MediaToolError:
        ffprobe = False
    store = get_edit_store()
    queue_counts = get_render_queue().counts()
    counts = store.counts()
    approved_real = sum(
        item["status"] == "approved" and not item["fixture_data"]
        for item in store.list_outputs(include_fixtures=True)
    )
    eligible = 0
    for item in _script_options(include_fixtures=_fixtures_allowed()):
        try:
            script = _approved_script(item["script_id"], include_fixtures=_fixtures_allowed())
            eligible += build_shooting_task(get_material_store(), script)["status"] == "ready_for_edit"
        except Exception:
            continue
    return {
        "stage": "S7", "engineering_ready": ffmpeg and ffprobe,
        "ffmpeg_ready": ffmpeg, "ffprobe_ready": ffprobe, "max_parallel_renders": 2,
        "eligible_scripts": eligible, "project_count": counts["projects"],
        "pending_renders": queue_counts["pending"] + queue_counts["running"] + queue_counts["retry_wait"],
        "failed_renders": queue_counts["failed"], "outputs_waiting_review": counts["video_review"],
        "accepted_real_outputs": approved_real, "required_real_outputs": 1,
        "business_ready": approved_real >= 1,
        "pending_reason": None if approved_real else "待用真实批准脚本和真实素材渲染并通过 1 条成片",
        "jianying_mode": "experimental_handoff_only",
    }


@router.get("/eligible-scripts")
def eligible_scripts() -> list[dict[str, Any]]:
    existing = {item["script_id"]: item["project_id"] for item in get_edit_store().list_projects(include_fixtures=_fixtures_allowed())}
    options: list[dict[str, Any]] = []
    for item in _script_options(include_fixtures=_fixtures_allowed()):
        try:
            script = _approved_script(item["script_id"], include_fixtures=_fixtures_allowed())
            task = build_shooting_task(get_material_store(), script)
        except Exception:
            continue
        options.append({
            **item, "fixture_data": bool(script["fixture_data"]), "shooting_status": task["status"],
            "missing_count": task["missing_count"], "project_id": existing.get(script["script_id"]),
        })
    return options


@router.post("/projects", status_code=201)
def create_project(request: CreateProjectRequest) -> dict[str, Any]:
    try:
        script = _approved_script(request.script_id, include_fixtures=_fixtures_allowed())
        shooting_task = build_shooting_task(get_material_store(), script)
        project = build_edit_project(script, shooting_task, get_material_store())
        stored, duplicate = get_edit_store().create_project(project, actor=request.actor)
        return {"project": stored, "duplicate": duplicate}
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/projects")
def projects() -> list[dict[str, Any]]:
    return get_edit_store().list_projects(include_fixtures=_fixtures_allowed())


@router.get("/projects/{project_id}")
def project(project_id: str) -> dict[str, Any]:
    try:
        return _visible_project(project_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.put("/projects/{project_id}")
def save_project(project_id: str, request: SaveProjectRequest) -> dict[str, Any]:
    try:
        current = _visible_project(project_id)
        script = _approved_script(current["script_id"], include_fixtures=bool(current["fixture_data"]))
        materials = {
            clip["material_id"]: get_material_store().get(clip["material_id"])
            for variant in request.variants for clip in variant.get("clips", [])
        }
        related: dict[str, Any] = {"script": script, "materials": materials}
        bgm_asset_id = request.settings.get("bgm_asset_id")
        if bgm_asset_id:
            related["audio_asset"] = get_edit_store().get_audio(str(bgm_asset_id))
        return get_edit_store().save_project(
            project_id, expected_revision=request.expected_revision, actor=request.actor,
            settings=request.settings, variants=request.variants,
            related=related,
        )
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/projects/{project_id}/versions")
def project_versions(project_id: str) -> list[dict[str, Any]]:
    try:
        _visible_project(project_id)
        return get_edit_store().project_revisions(project_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/projects/{project_id}/render", status_code=202)
def start_render(project_id: str, request: RenderRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    workspace: Path | None = None
    try:
        project = _visible_project(project_id)
        if project["revision"] != request.expected_revision:
            raise EditConflictError("剪辑工程已变更，请刷新后再渲染")
        token = uuid4().hex
        workspace = (_data_root() / "tasks" / token).resolve()
        if not workspace.is_relative_to(_data_root()):
            raise ValueError("渲染工作区不安全")
        workspace.mkdir(parents=True, exist_ok=False)
        snapshot_path = workspace / "project-snapshot.json"
        write_json_atomic(snapshot_path, project)
        task = get_render_queue().enqueue(
            project, request.variant_id, snapshot_path=snapshot_path, workspace_path=workspace,
        )
    except Exception as exc:
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
        _raise_domain(exc)
        raise
    background_tasks.add_task(_run_queue)
    return task.to_dict()


@router.get("/render-tasks")
def render_tasks() -> list[dict[str, Any]]:
    return [item.to_dict() for item in get_render_queue().list() if _fixtures_allowed() or not item.fixture_data]


@router.get("/render-tasks/{task_id}")
def render_task(task_id: str) -> dict[str, Any]:
    task = get_render_queue().get(task_id)
    if task is None or (task.fixture_data and not _fixtures_allowed()):
        raise HTTPException(status_code=404, detail="找不到这个渲染任务")
    return task.to_dict()


@router.post("/render-tasks/{task_id}/retry", status_code=202)
def retry_render(task_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        current = get_render_queue().get(task_id)
        if current is None or (current.fixture_data and not _fixtures_allowed()):
            raise EditNotFoundError("找不到这个渲染任务")
        task = get_render_queue().retry(task_id)
    except Exception as exc:
        _raise_domain(exc)
        raise
    background_tasks.add_task(_run_queue)
    return task.to_dict()


@router.get("/outputs")
def outputs(project_id: str | None = None) -> list[dict[str, Any]]:
    return get_edit_store().list_outputs(project_id=project_id, include_fixtures=_fixtures_allowed())


@router.get("/outputs/{output_id}")
def output(output_id: str) -> dict[str, Any]:
    try:
        return _visible_output(output_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/outputs/{output_id}/review")
def review_output(output_id: str, request: ReviewRequest) -> dict[str, Any]:
    try:
        _visible_output(output_id)
        return get_edit_store().review_output(output_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/outputs/{output_id}/resources/{resource_ref}", response_class=FileResponse)
def output_resource(output_id: str, resource_ref: str, download: bool = Query(default=False)) -> FileResponse:
    try:
        _visible_output(output_id)
        path, media_type, name = get_edit_store().resource(output_id, resource_ref)
        return FileResponse(path, media_type=media_type, filename=name if download else None)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/audio")
def audio_assets() -> list[dict[str, Any]]:
    return get_edit_store().list_audio(include_fixtures=_fixtures_allowed())


@router.post("/audio", status_code=201)
async def upload_audio(
    kind: Literal["bgm", "sound_effect"] = Form(), name: str = Form(min_length=1, max_length=500),
    license_note: str = Form(min_length=2, max_length=1000), imported_by: str = Form(min_length=1, max_length=500),
    audio: UploadFile = File(), fixture_header: str | None = Header(default=None, alias="X-Content-Factory-Fixture"),
) -> dict[str, Any]:
    fixture_data = _fixtures_allowed() and fixture_header == "true"
    try:
        asset, duplicate = get_edit_store().add_audio(
            kind=kind, name=name, original_name=audio.filename or "", license_note=license_note,
            imported_by=imported_by, fixture_data=fixture_data, stream=audio.file,
        )
        return {"asset": asset, "duplicate": duplicate}
    except Exception as exc:
        _raise_domain(exc)
        raise
    finally:
        await audio.close()
