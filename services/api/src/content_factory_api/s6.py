"""S6 material library, recognition, script matching, and reshoot API."""

from __future__ import annotations

import copy
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from content_factory_contracts import ContractValidationError
from content_factory_media.pipeline import MediaPipeline
from content_factory_media.tools import SUPPORTED_VIDEO_EXTENSIONS, MediaToolError, find_tool, probe_media
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .s2 import _model_path
from .s3 import get_gateway_settings_store
from .s3_gateway import GatewayError
from .s3_settings import GatewayConfig, GatewaySettingsError
from .s4 import get_product_store
from .s5 import get_script_queue
from .s5_scripts import ScriptNotFoundError, get_script, list_scripts
from .s6_matching import build_shooting_task, confirm_suggestion
from .s6_materials import build_material_profile, load_media_result_for_material, material_resource
from .s6_queue import MaterialImportQueue, MaterialImportRecord
from .s6_recognition import mark_recognition_failed, recognize_material
from .s6_store import MaterialConflictError, MaterialNotFoundError, MaterialStore, now_iso

router = APIRouter(prefix="/s6", tags=["S6 material center"])
_stores: dict[Path, MaterialStore] = {}
_queues: dict[Path, MaterialImportQueue] = {}
_instance_lock = threading.RLock()


def _material_gateway(model_id: str | None = None) -> GatewayConfig | None:
    try:
        config = get_gateway_settings_store().load()
    except (GatewaySettingsError, OSError):
        # Material ingestion is local-first.  A damaged or temporarily
        # inaccessible cloud-model setting must not block archiving the video.
        return None
    if config is None:
        return None
    try:
        return config.for_model_purpose(model_id, "material") if model_id else config.for_purpose("material")
    except GatewaySettingsError:
        return None
_runner_lock = threading.Lock()
_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024 * 1024


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _data_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_S6_DATA_DIR", _project_root() / "data" / "s6")).resolve()


def get_material_store() -> MaterialStore:
    database = _data_root() / "materials.sqlite3"
    with _instance_lock:
        store = _stores.get(database)
        if store is None:
            store = MaterialStore(database)
            _stores[database] = store
        return store


def get_material_queue() -> MaterialImportQueue:
    database = _data_root() / "material-imports.sqlite3"
    with _instance_lock:
        queue = _queues.get(database)
        if queue is None:
            queue = MaterialImportQueue(database)
            queue.recover_interrupted(_reconcile_interrupted_import)
            _queues[database] = queue
        return queue


def _fixtures_allowed() -> bool:
    return os.environ.get("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS") == "1"


def _approved_script(script_id: str, *, include_fixtures: bool = False) -> dict[str, Any]:
    script = get_script(get_script_queue(), script_id)
    if script.get("review", {}).get("status") != "approved":
        raise ValueError("只有已批准脚本才能进入素材匹配")
    if script.get("fixture_data") and not include_fixtures:
        raise ValueError("工程样例脚本不能混入正式素材")
    return script


def _failed_recognition_from_snapshot(
    profile: dict[str, Any], snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Give a recovered local material an explicit terminal recognition state."""

    updated = copy.deepcopy(profile)
    updated["revision"] += 1
    updated["updated_at"] = now_iso()
    snapshot = snapshot or {}
    base_url = str(snapshot.get("base_url") or "")
    provider = urlparse(base_url).hostname or str(snapshot.get("provider") or "") or None
    model_id = str(snapshot.get("model_id") or "") or None
    updated["processing"] = {
        **updated["processing"],
        "recognition_status": "failed",
        "provider": provider,
        "model_profile_id": model_id,
        "model": str(snapshot.get("model") or "") or None,
        "recognized_at": None,
        "original_video_uploaded": False,
    }
    return updated


def _reconcile_interrupted_import(task: MaterialImportRecord) -> str | None:
    """Finish a committed S6 material without repeating the local media pipeline."""

    expected_media_task_id = f"media_{task.task_id.removeprefix('material_task_')}"
    store = get_material_store()
    matches = [
        item for item in store.list()
        if item.get("processing", {}).get("media_task_id") == expected_media_task_id
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("同一导入任务对应多个素材")
    stored = matches[0]
    if (
        stored.get("product_id") != task.product_id
        or stored.get("source_script_id") != task.source_script_id
        or bool(stored.get("fixture_data")) != task.fixture_data
    ):
        raise ValueError("已落库素材与导入任务不一致")

    inputs = task.input()
    raw_snapshot = inputs.get("_gateway_model_snapshot")
    snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else None
    status = stored["processing"]["recognition_status"]
    try:
        media_result = load_media_result_for_material(
            store.media_result_path(stored["material_id"]),
            allowed_root=Path(task.workspace_path) / "media",
        )
        if (
            media_result.get("task_id") != expected_media_task_id
            or bool(media_result.get("fixture_data")) != task.fixture_data
            or media_result.get("source", {}).get("sha256") != stored.get("file", {}).get("sha256")
        ):
            raise ValueError("已落库素材的本地处理结果与导入任务不一致")
    except Exception:
        if status == "pending":
            terminal = _failed_recognition_from_snapshot(stored, snapshot)
            store.replace_after_recognition(terminal, actor="中断恢复")
        raise
    if status != "pending":
        return str(stored["material_id"])

    pinned_model_id = snapshot.get("model_id") if snapshot is not None else None
    config = _material_gateway(pinned_model_id if isinstance(pinned_model_id, str) else None)
    if snapshot is None or config is None or not config.matches_snapshot(snapshot):
        terminal = _failed_recognition_from_snapshot(stored, snapshot)
        store.replace_after_recognition(terminal, actor="中断恢复")
        return str(stored["material_id"])
    try:
        recognized = recognize_material(stored, media_result, config)
    except Exception:
        recognized = mark_recognition_failed(stored, config)
    store.replace_after_recognition(recognized, actor="中断恢复")
    return str(stored["material_id"])


def _process_import(task: MaterialImportRecord, progress) -> str:
    inputs = task.input()
    snapshot = inputs.pop("_gateway_model_snapshot", None)
    pinned_model_id = snapshot.get("model_id") if isinstance(snapshot, dict) else None
    config = _material_gateway(pinned_model_id if isinstance(pinned_model_id, str) else None)
    if isinstance(snapshot, dict) and (config is None or not config.matches_snapshot(snapshot)):
        raise GatewaySettingsError("素材任务绑定的模型配置已变更，请明确重新导入后再识别")
    media_task_id = f"media_{task.task_id.removeprefix('material_task_')}"
    result_path = MediaPipeline(asr_model_path=_model_path()).process(
        task.source_path, Path(task.workspace_path) / "media", media_task_id,
        fixture_data=task.fixture_data, original_name=task.source_name, progress=progress,
    )
    profile = build_material_profile(
        result_path, product=inputs["product"], source_script_id=task.source_script_id,
        archive=inputs["archive"], recognition_configured=config is not None,
        capture_role=inputs.get("capture_role"),
    )
    store = get_material_store()
    stored, duplicate = store.create(
        profile, media_result_path=result_path, actor=inputs["archive"]["imported_by"],
    )
    recognition_status = stored["processing"]["recognition_status"]
    if duplicate and recognition_status != "pending":
        return stored["material_id"]
    if recognition_status == "pending" and config is None:
        raise GatewaySettingsError("素材识别尚未完成，但原任务绑定的模型配置不可用")
    if config is not None:
        progress("recognize", 97)
        recognition_result_path = store.media_result_path(stored["material_id"]) if duplicate else result_path
        media_result = load_media_result_for_material(
            recognition_result_path, allowed_root=Path(task.workspace_path) / "media",
        )
        try:
            recognized = recognize_material(stored, media_result, config)
        except Exception:
            failed = mark_recognition_failed(stored, config)
            store.replace_after_recognition(failed)
        else:
            store.replace_after_recognition(recognized)
    return stored["material_id"]


def _run_queue() -> None:
    with _runner_lock:
        queue = get_material_queue()
        queue.run_pending(_process_import, max_workers=2)
        upload_root = (_data_root() / "uploads").resolve()
        for task in queue.list():
            source = Path(task.source_path).resolve()
            if task.status == "completed" and source.is_relative_to(upload_root):
                source.unlink(missing_ok=True)


def _raise_domain(exc: Exception) -> None:
    if isinstance(exc, (MaterialNotFoundError, ScriptNotFoundError, LookupError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, MaterialConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, GatewayError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, ContractValidationError, MediaToolError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _material_product(profile: dict[str, Any]) -> dict[str, Any]:
    fact_by_id = {
        item.get("id"): item.get("value")
        for item in profile.get("facts", [])
        if isinstance(item, dict)
    }
    constraints = profile.get("shooting_constraints", {})
    return {
        "product_id": profile["product_id"],
        "revision": profile["revision"],
        "sku": profile["sku"],
        "name": profile["name"],
        "selling_points": [
            fact_by_id[fact_id]
            for fact_id in profile.get("selling_point_fact_ids", [])
            if fact_by_id.get(fact_id)
        ],
        "max_duration_ms": int(constraints.get("max_duration_ms") or 0),
        "status": profile["status"],
    }


def _resolve_material_product(product_id: str, *, include_fixtures: bool = False) -> dict[str, Any]:
    profile = get_product_store().get(product_id)
    if profile.get("status") == "archived":
        raise ValueError("已归档商品不能继续导入新素材")
    if bool(profile.get("fixture_data")) != include_fixtures:
        raise ValueError("工程样例商品不能混入正式素材")
    return profile


def _product_options(include_fixtures: bool = False) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for summary in get_product_store().list():
        try:
            profile = _resolve_material_product(summary["product_id"], include_fixtures=include_fixtures)
        except (LookupError, ValueError):
            continue
        options.append(_material_product(profile))
    return options


def _script_options(include_fixtures: bool = False) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    product_map = {item["product_id"]: item for item in _product_options(include_fixtures)}
    for summary in list_scripts(get_script_queue()):
        if summary["review_status"] != "approved":
            continue
        try:
            script = _approved_script(summary["script_id"], include_fixtures=include_fixtures)
        except (ValueError, ScriptNotFoundError):
            continue
        product = product_map.get(script["product_id"])
        if product is None:
            continue
        versions = [item for item in script.get("versions", []) if isinstance(item, dict)]
        selected_version_id = script.get("selected_version_id") or (versions[0].get("id") if versions else None)
        selected_version = next((item for item in versions if item.get("id") == selected_version_id), None)
        if selected_version is None:
            continue
        options.append({
            "script_id": script["script_id"], "product_id": script["product_id"], "product_name": product["name"],
            "product_sku": product["sku"], "revision": script["revision"], "version_count": len(script["versions"]),
            "selected_version_id": selected_version["id"], "selected_version_name": selected_version["name"],
            "selected_version_duration_ms": selected_version["duration_ms"],
        })
    return options


class MaterialUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=100)
    archive: dict[str, Any]
    clips: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class MatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_shot_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    material_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    clip_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    actor: str = Field(min_length=1, max_length=100)


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(min_length=1, max_length=100)


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
    whisper = _model_path().is_file()
    store_counts = get_material_store().counts()
    queue_counts = get_material_queue().counts()
    tasks = [build_shooting_task(get_material_store(), _approved_script(item["script_id"])) for item in _script_options()]
    ready = [item for item in tasks if item["status"] == "ready_for_edit"]
    real_ready = [item for item in ready if not item["fixture_data"]]
    return {
        "stage": "S6", "engineering_ready": ffmpeg and ffprobe and whisper,
        "ffmpeg_ready": ffmpeg, "ffprobe_ready": ffprobe, "whisper_ready": whisper,
        "gateway_configured": _material_gateway() is not None,
        "material_count": store_counts["materials"], "clip_count": store_counts["clips"],
        "pending_imports": queue_counts["pending"] + queue_counts["retry_wait"] + queue_counts["running"],
        "failed_imports": queue_counts["failed"],
        "pending_shoot_tasks": sum(item["status"] != "ready_for_edit" for item in tasks),
        "ready_for_edit_tasks": len(ready), "accepted_real_ready_tasks": len(real_ready), "required_real_ready_tasks": 1,
        "business_ready": len(real_ready) >= 1,
        "pending_reason": None if real_ready else "待完成 1 份真实批准脚本的真实素材匹配",
    }


@router.get("/products")
def products() -> list[dict[str, Any]]:
    return _product_options(include_fixtures=_fixtures_allowed())


@router.get("/scripts")
def scripts() -> list[dict[str, Any]]:
    return _script_options(include_fixtures=_fixtures_allowed())


@router.post("/imports", status_code=202)
async def import_material(
    background_tasks: BackgroundTasks,
    product_id: str = Form(min_length=3, max_length=64),
    source_script_id: str | None = Form(default=None, max_length=64),
    model_name: str = Form(min_length=1, max_length=500),
    scene: str = Form(min_length=1, max_length=500),
    shot_date: date = Form(),
    batch: str = Form(min_length=1, max_length=500),
    imported_by: str = Form(min_length=1, max_length=100),
    note: str = Form(default="", max_length=2000),
    capture_role: Literal["host_take", "detail", "standard"] | None = Form(default=None),
    video: UploadFile = File(),
    fixture_header: str | None = Header(default=None, alias="X-Content-Factory-Fixture"),
) -> dict[str, Any]:
    fixture_data = _fixtures_allowed() and fixture_header == "true"
    try:
        product = _resolve_material_product(product_id, include_fixtures=fixture_data)
        if source_script_id:
            script = _approved_script(source_script_id, include_fixtures=fixture_data)
            if script["product_id"] != product_id or bool(script["fixture_data"]) != fixture_data:
                raise ValueError("所选脚本与商品或数据环境不一致")
    except Exception as exc:
        _raise_domain(exc)
        raise
    original_name = Path(video.filename or "").name
    extension = Path(original_name).suffix.lower()
    if not original_name or extension not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="请选择支持的视频文件")
    upload_root = _data_root() / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    incoming = upload_root / f"{uuid4().hex}{extension}.uploading"
    stored = incoming.with_suffix("")
    total = 0
    try:
        with incoming.open("wb") as handle:
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > _UPLOAD_LIMIT_BYTES:
                    raise HTTPException(status_code=413, detail="单个视频不能超过 4 GB")
                handle.write(chunk)
        os.replace(incoming, stored)
        probe_media(stored)
        input_payload: dict[str, Any] = {"product": product, "archive": {
            "model_name": model_name, "scene": scene, "shot_date": shot_date.isoformat(), "batch": batch,
            "imported_by": imported_by, "note": note,
        }}
        if capture_role is not None:
            input_payload["capture_role"] = capture_role
        recognition_config = _material_gateway()
        if recognition_config is not None:
            input_payload["_gateway_model_snapshot"] = recognition_config.execution_snapshot()
        task = get_material_queue().enqueue(
            stored, _data_root(), fixture_data=fixture_data, source_name=original_name,
            product_id=product_id, source_script_id=source_script_id,
            input_payload=input_payload,
        )
    except HTTPException:
        incoming.unlink(missing_ok=True)
        stored.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, MediaToolError) as exc:
        incoming.unlink(missing_ok=True)
        stored.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await video.close()
    background_tasks.add_task(_run_queue)
    return task.to_dict()


@router.get("/imports")
def imports() -> list[dict[str, Any]]:
    return [item.to_dict() for item in get_material_queue().list()]


@router.post("/imports/{task_id}/retry", status_code=202)
def retry_import(task_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        task = get_material_queue().retry(task_id)
    except Exception as exc:
        _raise_domain(exc)
        raise
    background_tasks.add_task(_run_queue)
    return task.to_dict()


@router.get("/materials")
def materials(
    product_id: str | None = None, query: str = Query(default="", max_length=200),
    purpose: Literal["hook", "proof", "detail", "comfort", "cta", "transition", "broll"] | None = None,
) -> list[dict[str, Any]]:
    product_map = {item["product_id"]: item for item in _product_options(include_fixtures=_fixtures_allowed())}
    summaries: list[dict[str, Any]] = []
    for item in get_material_store().list(product_id=product_id, query=query, purpose=purpose):
        if item.get("fixture_data") and not _fixtures_allowed():
            continue
        product = product_map.get(item["product_id"], {})
        summaries.append({
            "material_id": item["material_id"], "product_id": item["product_id"], "product_name": product.get("name", "已归档商品"),
            "product_sku": product.get("sku", "-"), "revision": item["revision"], "original_name": item["file"]["original_name"],
            "model_name": item["archive"]["model_name"], "scene": item["archive"]["scene"], "shot_date": item["archive"]["shot_date"],
            "batch": item["archive"]["batch"], "clip_count": len(item["clips"]),
            "reusable_clip_count": sum(bool(clip["reusable"]) for clip in item["clips"]),
            "recognition_status": item["processing"]["recognition_status"], "updated_at": item["updated_at"],
        })
    return summaries


@router.get("/materials/{material_id}")
def material(material_id: str) -> dict[str, Any]:
    try:
        return get_material_store().get(material_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.put("/materials/{material_id}")
def update_material(material_id: str, request: MaterialUpdateRequest) -> dict[str, Any]:
    try:
        return get_material_store().update(material_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/materials/{material_id}/versions")
def material_versions(material_id: str) -> list[dict[str, Any]]:
    try:
        return get_material_store().revisions(material_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/materials/{material_id}/recognize")
def recognize(material_id: str) -> dict[str, Any]:
    config = _material_gateway()
    if config is None:
        raise HTTPException(status_code=409, detail="请先在模型设置中配置支持图文输入的素材识别模型")
    try:
        store = get_material_store()
        profile = store.get(material_id)
        media_result = load_media_result_for_material(store.media_result_path(material_id), allowed_root=_data_root() / "media")
        try:
            updated = recognize_material(profile, media_result, config)
        except Exception:
            store.replace_after_recognition(mark_recognition_failed(profile, config))
            raise
        return store.replace_after_recognition(updated)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/materials/{material_id}/proxy", response_class=FileResponse)
def proxy(material_id: str) -> FileResponse:
    return _resource_response(material_id)


@router.get("/materials/{material_id}/clips/{clip_id}/keyframe", response_class=FileResponse)
def keyframe(material_id: str, clip_id: str) -> FileResponse:
    return _resource_response(material_id, clip_id)


def _resource_response(material_id: str, clip_id: str | None = None) -> FileResponse:
    try:
        store = get_material_store()
        profile = store.get(material_id)
        result = load_media_result_for_material(store.media_result_path(material_id), allowed_root=_data_root() / "media")
        path, media_type = material_resource(profile, result, clip_id=clip_id)
        return FileResponse(path, media_type=media_type)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/materials/{material_id}/usage")
def usage(material_id: str, clip_id: str | None = None) -> list[dict[str, Any]]:
    get_material_store().get(material_id)
    return get_material_store().usage(material_id, clip_id)


@router.get("/shooting-tasks")
def shooting_tasks() -> list[dict[str, Any]]:
    return [build_shooting_task(get_material_store(), _approved_script(item["script_id"], include_fixtures=_fixtures_allowed())) for item in _script_options(include_fixtures=_fixtures_allowed())]


@router.get("/shooting-tasks/{script_id}")
def shooting_task(script_id: str) -> dict[str, Any]:
    try:
        return build_shooting_task(get_material_store(), _approved_script(script_id, include_fixtures=_fixtures_allowed()))
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/shooting-tasks/{script_id}/matches")
def match(script_id: str, request: MatchRequest) -> dict[str, Any]:
    try:
        script = _approved_script(script_id, include_fixtures=_fixtures_allowed())
        return confirm_suggestion(get_material_store(), script, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.delete("/shooting-tasks/{script_id}/matches/{script_shot_id}")
def release(script_id: str, script_shot_id: str, request: ReleaseRequest) -> dict[str, Any]:
    try:
        script = _approved_script(script_id, include_fixtures=_fixtures_allowed())
        get_material_store().release_match(script_id=script_id, script_shot_id=script_shot_id, actor=request.actor)
        return build_shooting_task(get_material_store(), script)
    except Exception as exc:
        _raise_domain(exc)
        raise
