"""S5 template adaptation, script generation, editing, and review API."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from content_factory_contracts import ContractValidationError
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .s3 import get_analysis_queue, get_gateway_settings_store, get_viral_skill_store
from .s3_settings import GatewayConfig, GatewaySettingsError
from .s4 import get_product_store
from .s4_store import ProductNotFoundError
from .s5_production_policy import build_production_policy, resolve_viral_evidence
from .s5_generation import ScriptGenerationError
from .s5_queue import ScriptTaskQueue, ScriptTaskRecord
from .s5_scripts import (
    ScriptConflictError,
    ScriptNotFoundError,
    frozen_inputs,
    get_script,
    list_revisions,
    list_scripts,
    review_script,
    script_counts,
    skill_usage_counts,
    update_script,
)
from .s5_sources import (
    inspect_templates,
    list_script_products,
    list_templates,
    resolve_analysis,
    resolve_product,
    resolve_template,
    script_product_eligibility,
)

router = APIRouter(prefix="/s5", tags=["S5 script studio"])
_queue_instances: dict[Path, ScriptTaskQueue] = {}
_queue_lock = threading.Lock()
_runner_lock = threading.Lock()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _data_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_S5_DATA_DIR", _project_root() / "data" / "s5")).resolve()


def get_script_queue() -> ScriptTaskQueue:
    root = _data_root()
    database = root / "script-tasks.sqlite3"
    with _queue_lock:
        queue = _queue_instances.get(database)
        if queue is None:
            queue = ScriptTaskQueue(database)
            queue.recover_interrupted()
            _queue_instances[database] = queue
        return queue


def _run_queue_safely() -> None:
    with _runner_lock:
        queue = get_script_queue()
        config = _script_gateway()
        if config is not None:
            queue.run_pending(
                gateway_config=config,
                max_workers=2,
                preflight=_validate_frozen_generation_sources,
            )


def _load_frozen_task_input(task: ScriptTaskRecord) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(task.input_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScriptGenerationError("脚本任务的冻结输入已损坏，请重新建立生成任务") from exc
    if not isinstance(payload, Mapping):
        raise ScriptGenerationError("脚本任务的冻结输入格式无效，请重新建立生成任务")
    return payload


def _validate_frozen_generation_sources(
    _task: ScriptTaskRecord,
    payload: Mapping[str, Any],
) -> None:
    """Ensure a queued frozen Skill still has permission and current evidence."""

    frozen_skill = payload.get("skill")
    # Pending tasks created before the formal Skill workflow did not freeze a
    # Skill. Keep those historical tasks recoverable; every new API request is
    # already required to select one.
    if frozen_skill is None:
        return
    if not isinstance(frozen_skill, Mapping):
        raise ScriptGenerationError("任务冻结的爆点 Skill 格式无效，请重新建立任务")
    skill_id = frozen_skill.get("skill_id")
    skill_revision = frozen_skill.get("revision")
    if (
        not isinstance(skill_id, str)
        or not isinstance(skill_revision, int)
        or frozen_skill.get("status") != "approved"
        or frozen_skill.get("reuse_mode") != "reuse"
    ):
        raise ScriptGenerationError("任务冻结的爆点 Skill 未批准复用，请重新选择")
    try:
        _template, current_analysis, current_pattern, current_skill = resolve_template(
            get_analysis_queue(), get_viral_skill_store(), skill_id,
        )
    except (LookupError, ValueError) as exc:
        raise ScriptGenerationError(
            f"任务冻结的爆点 Skill 已停用或来源发生变化：{exc}；请重新选择后建立任务"
        ) from exc
    if current_skill.get("revision") != skill_revision:
        raise ScriptGenerationError("任务冻结的爆点 Skill 版本已发生变化，请重新选择后建立任务")
    frozen_analysis = payload.get("analysis")
    frozen_pattern = payload.get("pattern")
    if (
        not isinstance(frozen_analysis, Mapping)
        or not isinstance(frozen_pattern, Mapping)
        or frozen_analysis.get("analysis_id") != current_analysis.get("analysis_id")
        or frozen_analysis.get("revision") != current_analysis.get("revision")
        or frozen_pattern.get("id") != current_pattern.get("id")
    ):
        raise ScriptGenerationError("任务冻结的 Skill 来源分析已变化，请重新选择后建立任务")


def _script_gateway() -> GatewayConfig | None:
    config = get_gateway_settings_store().load()
    if config is None:
        return None
    try:
        return config.for_purpose("script")
    except GatewaySettingsError:
        return None


class CreateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str | None = Field(default=None, pattern=r"^skill_[a-f0-9]{32}$")
    template_id: str | None = Field(default=None, pattern=r"^(?:skill|template)_[a-f0-9]{32}$")
    product_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    content_goal: Literal["seeding", "conversion", "review", "brand"]
    target_audience: str = Field(min_length=1, max_length=500)
    version_count: int = Field(ge=3, le=5)

    @model_validator(mode="after")
    def require_one_skill(self) -> "CreateGenerationRequest":
        selected = self.skill_id or self.template_id
        if selected is None:
            raise ValueError("请选择一个已批准的爆点 Skill")
        if self.skill_id and self.template_id and self.skill_id != self.template_id:
            raise ValueError("爆点 Skill 选择不一致，请刷新后重试")
        return self


class ScriptUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=100)
    content_goal: Literal["seeding", "conversion", "review", "brand"]
    target_audience: str = Field(min_length=1, max_length=500)
    selected_version_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    versions: list[dict[str, Any]] = Field(min_length=3, max_length=5)
    shooting_order: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    material_checklist: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class ScriptReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    status: Literal["approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)


class S5ReadinessResponse(BaseModel):
    stage: Literal["S5"] = "S5"
    engineering_ready: bool = True
    gateway_configured: bool
    available_templates: int
    available_products: int
    pending_tasks: int
    running_tasks: int
    failed_tasks: int
    pending_review_scripts: int
    approved_scripts: int
    accepted_real_scripts: int
    required_real_scripts: Literal[1] = 1
    business_ready: bool
    pending_reason: str | None


def _raise_domain(exc: Exception) -> None:
    if isinstance(exc, (ScriptNotFoundError, ProductNotFoundError, LookupError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ScriptConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, ContractValidationError, ScriptGenerationError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.get("/readiness", response_model=S5ReadinessResponse)
def get_readiness() -> S5ReadinessResponse:
    gateway = _script_gateway() is not None
    template_inventory = inspect_templates(
        get_analysis_queue(), get_viral_skill_store(), usage_counts=skill_usage_counts(get_script_queue()),
    )
    templates = template_inventory["templates"]
    products = list_script_products(get_product_store())
    tasks = get_script_queue().counts()
    scripts = script_counts(get_script_queue())
    accepted = scripts["accepted_real"]
    reasons: list[str] = []
    if not gateway:
        reasons.append("待配置中转站")
    if not templates:
        blocked_reasons = list(dict.fromkeys(
            str(item["reason"])
            for item in template_inventory["blocked"]
            if item.get("reason")
        ))
        reasons.append(
            "爆点 Skill 暂不可用：" + "；".join(blocked_reasons[:3])
            if blocked_reasons
            else "待在 S3 批准至少一个可复用爆点 Skill"
        )
    if not products:
        reasons.append("待准备至少一款含已确认卖点事实的商品")
    if accepted < 1:
        reasons.append("待人工批准 1 个真实脚本包")
    return S5ReadinessResponse(
        gateway_configured=gateway, available_templates=len(templates), available_products=len(products),
        pending_tasks=tasks["pending"] + tasks["retry_wait"], running_tasks=tasks["running"],
        failed_tasks=tasks["failed"], pending_review_scripts=scripts["pending"] + scripts["draft"],
        approved_scripts=scripts["approved"], accepted_real_scripts=accepted, business_ready=accepted >= 1,
        pending_reason="；".join(reasons) or None,
    )


@router.get("/skills")
@router.get("/templates")
def get_templates() -> list[dict[str, Any]]:
    return list_templates(
        get_analysis_queue(), get_viral_skill_store(), usage_counts=skill_usage_counts(get_script_queue()),
    )


@router.get("/products")
def get_products() -> list[dict[str, Any]]:
    return list_script_products(get_product_store())


@router.get('/skills/{skill_id}/sources/{video_id}/media')
def get_skill_media(skill_id: str, video_id: str):
    from .creator_media import skill_media_file
    from .s2 import _queue, _media_root
    try:
        path = skill_media_file(get_analysis_queue(), get_viral_skill_store(), _queue(), skill_id, video_id, _media_root())
        return FileResponse(path, media_type='video/webm' if path.suffix == '.webm' else 'video/mp4')
    except (ValueError, LookupError, OSError, KeyError) as exc:
        raise HTTPException(status_code=404, detail='来源视频暂不可播放，请检查对应原素材') from exc


@router.get('/skills/{skill_id}/sources/{video_id}/cover')
def get_skill_cover(skill_id: str, video_id: str):
    from .creator_media import skill_media_file
    from .s2 import _queue, _media_root
    try:
        path = skill_media_file(get_analysis_queue(), get_viral_skill_store(), _queue(), skill_id, video_id, _media_root(), cover=True)
        return FileResponse(path)
    except (ValueError, LookupError, OSError, KeyError) as exc:
        raise HTTPException(status_code=404, detail='对应原素材封面暂不可用') from exc


@router.post("/generations", status_code=202)
def create_generation(request: CreateGenerationRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    config = _script_gateway()
    if config is None:
        raise HTTPException(status_code=409, detail="请先在模型设置里配置可用于脚本生成的文本模型")
    try:
        selected_skill_id = request.skill_id or request.template_id
        if selected_skill_id is None:
            raise ValueError("请选择一个已批准的爆点 Skill")
        _template, analysis, pattern, skill = resolve_template(
            get_analysis_queue(), get_viral_skill_store(), selected_skill_id,
        )
        product = resolve_product(get_product_store(), request.product_id)
        production_policy = build_production_policy(product, script_product_eligibility(product))
        payload = {
            "product": product,
            "analysis": analysis,
            "pattern": pattern,
            "skill": skill,
            "request": request.model_dump(mode="json", exclude={"skill_id", "template_id", "product_id"}),
            "production_policy": production_policy,
            "_gateway_model_snapshot": config.execution_snapshot(),
        }
        from .product_workspace import read_workspace
        workspace = read_workspace(get_product_store(), product['product_id'])
        payload['product_workspace'] = workspace
        payload['selected_product_assets'] = [s for s in product['sources'] if s.get('asset_id') in workspace['selected_asset_ids']]
        task = get_script_queue().enqueue(workspace_path=_data_root(), input_payload=payload)
    except Exception as exc:
        _raise_domain(exc)
        raise
    background_tasks.add_task(_run_queue_safely)
    return task.to_dict()


@router.get("/tasks")
def get_tasks() -> list[dict[str, Any]]:
    return [task.to_dict() for task in get_script_queue().list()]


def _task_or_404(task_id: str):
    task = get_script_queue().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到这个脚本任务")
    return task


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    return _task_or_404(task_id).to_dict()


@router.post("/tasks/{task_id}/retry", status_code=202)
def retry_task(task_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if _script_gateway() is None:
        raise HTTPException(status_code=409, detail="请先检查并保存脚本生成模型设置")
    try:
        queue = get_script_queue()
        current = queue.get(task_id)
        if current is None:
            raise LookupError("找不到这个脚本任务")
        # Validate before changing the row back to pending. When a Skill was
        # disabled or its evidence changed, the failed task and its diagnostic
        # history remain intact for audit instead of being silently requeued.
        if current.status == "failed":
            _validate_frozen_generation_sources(current, _load_frozen_task_input(current))
        task = queue.retry(task_id)
    except Exception as exc:
        _raise_domain(exc)
        raise
    background_tasks.add_task(_run_queue_safely)
    return task.to_dict()


@router.post("/queue/run", status_code=202)
def run_queue(background_tasks: BackgroundTasks) -> dict[str, str]:
    if _script_gateway() is None:
        raise HTTPException(status_code=409, detail="请先配置脚本生成模型")
    background_tasks.add_task(_run_queue_safely)
    return {"status": "started"}


@router.get("/scripts")
def get_scripts() -> list[dict[str, Any]]:
    return list_scripts(get_script_queue())


@router.get("/scripts/{script_id}")
def get_script_detail(script_id: str) -> dict[str, Any]:
    try:
        return get_script(get_script_queue(), script_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/scripts/{script_id}/context")
def get_script_context(script_id: str) -> dict[str, Any]:
    try:
        task = get_script_queue().completed_for_script(script_id)
        if task is None:
            raise StopIteration
        frozen = frozen_inputs(task.result_path or "")
        viral_evidence = resolve_viral_evidence(frozen["analysis"], frozen["pattern"])
        return {
            "product": frozen["product"], "pattern": frozen["pattern"],
            "skill": frozen.get("skill"),
            "product_workspace": frozen.get("product_workspace"),
            "selected_product_assets": frozen.get("selected_product_assets", []),
            "production_policy": frozen.get("production_policy"),
            "viral_evidence": viral_evidence,
            "analysis": {
                "analysis_id": frozen["analysis"]["analysis_id"],
                "revision": frozen["analysis"]["revision"],
                "summary": frozen["analysis"]["summary"],
                "evidence": list(viral_evidence["by_id"].values()),
            },
        }
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="找不到这个脚本") from exc


@router.put("/scripts/{script_id}")
def put_script(script_id: str, request: ScriptUpdateRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    expected_revision = payload.pop("expected_revision")
    actor = payload.pop("actor")
    try:
        return update_script(
            get_script_queue(), script_id, expected_revision=expected_revision, changes=payload, actor=actor,
        )
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/scripts/{script_id}/review")
def post_script_review(script_id: str, request: ScriptReviewRequest) -> dict[str, Any]:
    try:
        script = get_script(get_script_queue(), script_id)
        try:
            product = get_product_store().get(script["product_id"])
        except ProductNotFoundError:
            product = None
        analysis = resolve_analysis(get_analysis_queue(), script["source_analysis_id"])
        current_skill = None
        if script.get("skill_id"):
            try:
                current_skill = get_viral_skill_store().get_skill_view(script["skill_id"])
            except LookupError:
                current_skill = None
        return review_script(
            get_script_queue(), script_id, **request.model_dump(),
            current_product=product, current_analysis=analysis, current_skill=current_skill,
        )
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/scripts/{script_id}/versions")
def get_script_versions(script_id: str) -> list[dict[str, Any]]:
    try:
        return list_revisions(get_script_queue(), script_id)
    except Exception as exc:
        _raise_domain(exc)
        raise
