from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from content_factory_contracts import ContractValidationError, validate_or_raise
from content_factory_media.ocr import ocr_available
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .s2 import _queue as media_queue
from .s3_analysis_method import AnalysisMethodError, load_installed_method, method_readiness
from .s3_gateway import GatewayError, GatewayResult, call_gateway, gateway_endpoint
from .s3_model_catalog import (
    DiscoveredModel,
    ModelCatalog,
    ModelDiscoveryError,
    discover_models,
    normalize_connection_url,
)
from .s3_observability import write_analysis_event
from .s3_queue import AnalysisTaskQueue
from .s3_reports import ReportConflictError, load_report, review_report, update_report
from .s3_skills import (
    SkillConflictError,
    SkillNotFoundError,
    ViralSkillStore,
    refresh_skill_candidates,
)
from .s3_settings import (
    GatewayApiMode,
    GatewayConfig,
    GatewayInputModality,
    GatewayModelCapacityReached,
    GatewayProvider,
    GatewayPurpose,
    GatewaySettingsError,
    GatewaySettingsStore,
    GatewayVerificationRequired,
    infer_provider_from_base_url,
    validate_base_url,
)

router = APIRouter(prefix="/s3", tags=["S3 deep analysis"])
logger = logging.getLogger(__name__)
# A connection must prove that the selected model can actually read an image,
# not merely accept an image-shaped request.  This deterministic 384 x 128 PNG
# contains the high-contrast token below; the prompt and JSON schema never
# contain the answer.  Keep the token visually unambiguous (no O/0 or I/1).
_VISION_PROBE_EXPECTED_TOKEN = "K7M2Q9"
_VISION_PROBE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAYAAAACAAQMAAAA4fixIAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAG"
    "UExURQAAAP///6XZn90AAAAJcEhZcwAADsMAAA7DAcdvqGQAAAKPSURBVFjD7dZdjtsgEAfwoa7KPqzqC0T1TZY9"
    "Vt/gaByFIyD1xQ/IdP5jOyabcZT0tVjafDj8AMMMs1RfvKiDDjrooIMO/lOwkD8B88BfRnwqNNREk9zhD5Zfo1PB"
    "QlMgMjtI6DyQQSurgkKuBYH/FiJmRdQ9mMm3gHiGhYHjH0gFmWoDuHOLpkwzHY/egmQ2YGqkiTtHHxgnYRgFxK"
    "EF3LlBUx4nnoBg70CkyuNEzEsBPHgDsskGg0ae6BhGBSyylDTw5LGkmT9w08osTEkDRSY622K44eeULQNsOH9zW"
    "QOzgDxuINmFgatgPlsFZFnsNO1gBPBVXmcd4D22YBHgT0CSgImOgafPaRGAJmcgIpxr8MUET3FCLjwGQW5SA8w"
    "BBgWs6UP1CgrvIe44HXyTLFkEfOwAjxUcuawAWoHhdvHDAPBePga4WYYD5B1waDwFkt2BjSowEkoHiDuIa2d3g"
    "K7gIiCMT4AMkC4DAKfpCpIOfiL4OI53sJDbQNbBL4B0gIJNFzDTdw045EOaAN4AZuyhgELvGvDI9AZwbm6hsdB"
    "FA5y6SAcG+c3ye8JpK8FX428F8BExyjm9g2ivoKrRihY8YQAC4BODwZ8VaPnAoXALsLI+Lw/AsIGZRgZYNPLpY"
    "wsABSCGg9+B9EwuvVOdnXrM4Lkx4Q2sSzpFOj3IsLINmGXTxkynR6VsUwMQjXFAeYmnwK+g8EymLIBPGqx3UI9"
    "7PCbVG3AtKCqQqLkCtJWSZU9LVkUlvAV4EClcalHk1fgKUHZ5t0/KLkffCjjXIjaApLCj3JkTYL8C1DAay5Ght"
    "yDdg0Q/8GTjPTi52PIeaP/NnF9N6+dAehXM/kWwvApurw466KCDDjro4F/BX3H7EhWW4EjsAAAAAElFTkSuQmCC"
)
_VISION_PROBE_CONTEXT_JSON = (
    '{"task":"只观察随请发送的图片。图片中央有一个由 6 个大写'
    '英文字母或数字组成的短码，请按从左到右顺序逐字读取并放入 '
    'image_token。不要根据文字提示猜测；看不清时返回空字符串。"}'
)
_runner_lock = threading.Lock()
_queue_lock = threading.Lock()
_queue_instances: dict[Path, AnalysisTaskQueue] = {}
_skill_store_lock = threading.Lock()
_skill_store_instances: dict[Path, ViralSkillStore] = {}
_worker_state_lock = threading.Lock()
_worker_wake = threading.Event()
_worker_stop = threading.Event()
_worker_thread: threading.Thread | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _runtime_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_RUNTIME_ROOT", _project_root() / "data" / "runtime")).resolve()


def _analysis_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_ANALYSIS_ROOT", _project_root() / "data" / "analysis")).resolve()


def _settings_store() -> GatewaySettingsStore:
    override = os.environ.get("CONTENT_FACTORY_S3_CONFIG_PATH")
    return GatewaySettingsStore(override) if override else GatewaySettingsStore()


def _queue() -> AnalysisTaskQueue:
    database_path = (_runtime_root() / "s3-tasks.sqlite3").resolve()
    with _queue_lock:
        queue = _queue_instances.get(database_path)
        if queue is None:
            queue = AnalysisTaskQueue(database_path)
            queue.recover_interrupted()
            _queue_instances[database_path] = queue
    return queue


def get_analysis_queue() -> AnalysisTaskQueue:
    """Return the recoverable S3 queue for downstream read-only orchestration."""

    return _queue()


def get_viral_skill_store() -> ViralSkillStore:
    database_path = (_analysis_root() / "skills" / "viral-skills.sqlite3").resolve()
    with _skill_store_lock:
        store = _skill_store_instances.get(database_path)
        if store is None:
            store = ViralSkillStore(database_path)
            _skill_store_instances[database_path] = store
    return store


def _refresh_skills_best_effort() -> None:
    try:
        refresh_skill_candidates(get_viral_skill_store(), _queue())
    except Exception as exc:
        logger.error("S3 Skill reconciliation failed after report mutation (%s)", type(exc).__name__)


def get_gateway_settings_store() -> GatewaySettingsStore:
    """Return the shared encrypted relay configuration used by S3 and S5."""

    return _settings_store()


def _run_queue_safely() -> None:
    with _runner_lock:
        config = _settings_store().load()
        if config is None:
            return
        try:
            config.for_purpose("analysis")
        except GatewaySettingsError:
            return
        _queue().run_pending(gateway_config=config, max_workers=2)


def _worker_loop() -> None:
    while not _worker_stop.is_set():
        _worker_wake.wait(timeout=2.0)
        _worker_wake.clear()
        if _worker_stop.is_set():
            break
        try:
            _run_queue_safely()
        except Exception as exc:
            # Individual task errors are persisted by the queue. A daemon-level
            # error must not terminate the only local worker; the next scan can
            # recover once configuration or storage is available again.
            logger.error("S3 analysis worker cycle failed (%s)", type(exc).__name__)
            try:
                write_analysis_event(
                    _analysis_root() / "_worker",
                    event="worker_cycle_failed",
                    job_id="analysis_worker",
                    trace_id=f"trace_{uuid4().hex}",
                    video_hash="0" * 64,
                    phase="worker",
                    completed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    error_type=type(exc).__name__,
                    error_code=getattr(exc, "diagnostic_code", None),
                    error_summary=str(exc),
                )
            except Exception as log_exc:
                logger.error("S3 worker diagnostic write failed (%s)", type(log_exc).__name__)
            _worker_stop.wait(timeout=1.0)
            continue


def start_analysis_worker() -> None:
    global _worker_thread
    with _worker_state_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            _worker_wake.set()
            return
        _worker_stop.clear()
        _queue().recover_interrupted()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            name="content-factory-analysis-worker",
            daemon=True,
        )
        _worker_thread.start()
        _worker_wake.set()


def stop_analysis_worker() -> None:
    global _worker_thread
    with _worker_state_lock:
        thread = _worker_thread
        _worker_stop.set()
        _worker_wake.set()
    if thread is not None:
        thread.join(timeout=5)
    with _worker_state_lock:
        if _worker_thread is thread:
            _worker_thread = None


def wake_analysis_worker() -> None:
    _worker_wake.set()


def _accepted_real_analysis(item: object) -> bool:
    if not isinstance(item, dict) or item.get("status") != "accepted":
        return False
    report_value = item.get("report")
    if not isinstance(report_value, str) or not report_value:
        return False
    report_path = Path(report_value)
    if not report_path.is_absolute():
        report_path = _project_root() / report_path
    try:
        report = load_report(report_path)
    except (OSError, ValueError):
        return False
    return report.get("fixture_data") is False and report.get("status") == "accepted"


def _real_analysis_progress() -> tuple[int, int]:
    path = _project_root() / "data" / "analysis-validation" / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        required = max(1, int(payload.get("required_count", 20)))
        accepted = sum(1 for item in payload.get("cases", []) if _accepted_real_analysis(item))
        return accepted, required
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0, 20


class GatewayModelSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    display_name: str
    provider: GatewayProvider
    base_url: str
    model: str
    api_mode: GatewayApiMode
    modalities: list[GatewayInputModality]
    purposes: list[GatewayPurpose]
    enabled: bool
    api_key_configured: bool


class GatewayModelSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    display_name: str = Field(min_length=1, max_length=60)
    provider: GatewayProvider | None = None
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=120)
    api_mode: GatewayApiMode
    modalities: list[GatewayInputModality] = Field(min_length=1, max_length=2)
    purposes: list[GatewayPurpose] = Field(min_length=1, max_length=4)
    enabled: bool = True
    # SecretStr prevents FastAPI/Pydantic validation details from echoing a
    # rejected credential. Identity/key changes are rejected by the PUT route
    # and must pass the strong visual probe on /gateway/connect.
    api_key: SecretStr | None = None


class GatewayRoutingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis: str | None
    script: str | None
    material: str | None
    video_review: str | None


class GatewayRoutingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    script: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    material: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    # Optional only for pre-2.1 clients. The store migrates it to the visual
    # analysis route and every 2.1 response always contains the explicit key.
    video_review: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,63}$")


class GatewaySettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["2.1.0"] = "2.1.0"
    base_url: str
    model: str
    api_mode: GatewayApiMode
    api_key_configured: bool
    default_model_id: str | None
    models: list[GatewayModelSettingsResponse]
    routing: GatewayRoutingResponse
    updated_at: str | None


class GatewaySettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Enforce 1..8 inside the route after SecretStr has masked every nested
    # credential. A container-level Pydantic max_length error includes the
    # original list and would otherwise expose all submitted keys.
    models: list[GatewayModelSettingsRequest] | None = None
    routing: GatewayRoutingRequest | None = None
    default_model_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    # Legacy single-model fields remain accepted so existing settings clients
    # and local test tools migrate without losing their encrypted secret.
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    api_mode: GatewayApiMode | None = None
    api_key: SecretStr | None = None


class GatewayDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1, max_length=500)
    # Validate length after SecretStr has masked the input; Pydantic's default
    # min/max error payload includes the rejected value verbatim.
    api_key: SecretStr | None = None
    saved_model_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,63}$")


class GatewayConnectRequest(GatewayDiscoveryRequest):
    upstream_model_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$",
    )
    manual_model_id: bool = False


class DiscoveredModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upstream_model_id: str
    display_name: str
    input_modalities: list[str] | None
    output_modalities: list[str] | None
    supports_structured_output: bool | None
    capability_source: Literal["provider_metadata", "unknown"]


class GatewayDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    provider: GatewayProvider
    normalized_base_url: str
    catalog_source: Literal["openai_models", "dashscope_models"]
    models: list[DiscoveredModelResponse]
    warnings: list[str]
    truncated: bool
    fetched_at: str


class GatewayConnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    connected_model_id: str
    latency_ms: int
    response_id: str | None
    settings: GatewaySettingsResponse


class GatewayTestResponse(BaseModel):
    status: Literal["ok"] = "ok"
    model_id: str
    model: str
    provider: GatewayProvider
    api_mode: GatewayApiMode
    endpoint_url: str
    tested_modalities: list[GatewayInputModality]
    latency_ms: int
    response_id: str | None


class MetricValuesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    play_count: int | None = Field(default=None, ge=0)
    follower_gain: int | None = Field(default=None, ge=0)
    three_second_retention: float | None = Field(default=None, ge=0, le=1)
    completion_rate: float | None = Field(default=None, ge=0, le=1)
    like_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)
    favorite_count: int | None = Field(default=None, ge=0)
    share_count: int | None = Field(default=None, ge=0)
    product_click_rate: float | None = Field(default=None, ge=0, le=1)
    transaction_count: int | None = Field(default=None, ge=0)
    conversion_rate: float | None = Field(default=None, ge=0, le=1)


class MetricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captured_at: datetime | None = None
    source_type: Literal["screenshot", "table", "manual", "authorized_api", "public"] = "manual"
    confidence: float = Field(default=1, ge=0, le=1)
    values: MetricValuesInput


class CommentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    source_label: str = Field(default="人工录入", min_length=1, max_length=100)
    like_count: int | None = Field(default=None, ge=0)
    is_author_reply: bool = False
    confidence: float = Field(default=1, ge=0, le=1)


class AnalysisCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_task_id: str = Field(pattern=r"^media_[a-f0-9]{32}$")
    model_purpose: Literal["analysis", "video_review"] = "analysis"
    metric_snapshots: list[MetricInput] = Field(default_factory=list, max_length=20)
    comments: list[CommentInput] = Field(default_factory=list, max_length=200)


class AnalysisTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str
    fixture_data: bool
    task_id: str
    media_task_id: str
    model_purpose: Literal["analysis", "video_review"]
    status: str
    progress: int
    current_step: str
    attempt_count: int
    max_attempts: int
    metric_count: int
    comment_count: int
    ocr_result_path: str | None
    result_path: str | None
    error: str | None
    created_at: str
    updated_at: str
    trace_id: str
    idempotency_key: str
    segment_total: int
    segment_completed: int
    current_segment: int | None
    last_completed_segment: int | None
    last_checkpoint_at: str | None
    next_retry_at: str | None
    recovering: bool
    cancel_requested: bool
    diagnostic_code: str | None
    retry_count: int


class AnalysisMethodStatus(BaseModel):
    id: Literal["huashu-douyin-script"]
    title: str
    status: Literal["ready", "not_installed", "invalid"]
    prompt_version: str
    dimensions: list[str]
    message: str


class S3ReadinessResponse(BaseModel):
    stage: Literal["S3"] = "S3"
    engineering_ready: bool
    ocr_ready: bool
    gateway_configured: bool
    queue_ready: bool = True
    max_cloud_workers: Literal[2] = 2
    pending_tasks: int
    running_tasks: int
    completed_tasks: int
    failed_tasks: int
    accepted_real_analyses: int
    required_real_analyses: int
    business_ready: bool
    pending_reason: str | None
    analysis_method: AnalysisMethodStatus


class QueueRunResponse(BaseModel):
    status: Literal["started"] = "started"


class ReportUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    changes: dict[str, Any]


class ReportReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    status: Literal["reviewed", "accepted"]
    reviewer: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class SkillCandidateApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_candidate_revision: int = Field(ge=1)
    expected_skill_revision: int | None = Field(default=None, ge=1)
    reviewer: str = Field(min_length=1, max_length=100)
    reuse_mode: Literal["reuse", "avoid"]
    name: str = Field(min_length=1, max_length=160)
    mechanism: str = Field(min_length=1, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)


class SkillStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    status: Literal["approved", "disabled"]
    reviewer: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)


def _public_settings() -> GatewaySettingsResponse:
    try:
        config = _settings_store().load()
    except GatewaySettingsError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if config is None:
        return GatewaySettingsResponse(
            base_url="", model="", api_mode="responses", api_key_configured=False,
            default_model_id=None, models=[],
            routing=GatewayRoutingResponse(analysis=None, script=None, material=None, video_review=None),
            updated_at=None,
        )
    return GatewaySettingsResponse(**config.public_dict())


def _gateway_error_detail(
    message: str,
    diagnostic_code: str,
    *,
    retryable: bool = False,
    upstream_status: int | None = None,
    endpoint_url: str | None = None,
) -> dict[str, object]:
    return {
        "message": message,
        "diagnostic_code": diagnostic_code,
        "retryable": retryable,
        "upstream_status": upstream_status,
        "endpoint_url": endpoint_url,
    }


def _raise_discovery_error(exc: ModelDiscoveryError) -> None:
    raise HTTPException(
        status_code=502,
        detail=_gateway_error_detail(
            str(exc),
            exc.diagnostic_code,
            retryable=exc.retryable,
            upstream_status=exc.upstream_status,
            endpoint_url=exc.endpoint_url,
        ),
    ) from exc


def _credential_origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlparse(value)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def _resolve_discovery_secret(request: GatewayDiscoveryRequest) -> tuple[str, str]:
    incoming = request.api_key.get_secret_value().strip() if request.api_key is not None else ""
    if incoming and request.saved_model_id:
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail(
                "API Key 和已保存模型只能选择一种密钥来源",
                "credential_input_invalid",
            ),
        )
    if incoming and not 8 <= len(incoming) <= 500:
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail(
                "API Key 未填写或长度异常",
                "credential_invalid",
            ),
        )
    try:
        normalized = normalize_connection_url(request.base_url)
    except GatewaySettingsError as exc:
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail(str(exc), "invalid_base_url"),
        ) from exc
    if incoming:
        return normalized, incoming
    if not request.saved_model_id:
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail("请输入 API Key", "credential_required"),
        )
    try:
        settings = _settings_store().load()
    except GatewaySettingsError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    profile = next(
        (
            item
            for item in settings.available_models()
            if item.model_id == request.saved_model_id
        ),
        None,
    ) if settings else None
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=_gateway_error_detail("找不到已保存的模型密钥", "saved_credential_not_found"),
        )
    if _credential_origin(profile.base_url) != _credential_origin(normalized):
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail(
                "接口主机已改变，请重新输入该服务商的 API Key",
                "credential_origin_changed",
            ),
        )
    return normalized, profile.api_key


def _selected_catalog_model(catalog: ModelCatalog, upstream_model_id: str) -> DiscoveredModel:
    selected = next(
        (item for item in catalog.models if item.upstream_model_id == upstream_model_id),
        None,
    )
    if selected is None:
        raise HTTPException(
            status_code=422,
            detail=_gateway_error_detail(
                "所选模型已不在服务商最新目录中，请重新获取模型列表",
                "model_not_in_catalog",
            ),
        )
    if selected.input_modalities is not None and "image" not in selected.input_modalities:
        raise HTTPException(
            status_code=422,
            detail=_gateway_error_detail(
                "所选模型的官方目录未声明图片输入能力，不能承担视频分析任务",
                "image_input_unsupported",
            ),
        )
    return selected


def _probe_modes(provider: GatewayProvider) -> tuple[GatewayApiMode, GatewayApiMode]:
    if provider == "openai":
        return "responses", "chat_completions"
    return "chat_completions", "responses"


def _visual_probe_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["image_token"],
        "properties": {
            # Avoid string constraint keywords that are unsupported by some
            # otherwise-compatible strict-schema providers. Exact length and
            # content are enforced locally with compare_digest below.
            "image_token": {"type": "string"},
        },
    }


def _require_visual_probe(result: GatewayResult) -> None:
    candidate = result.content.get("image_token")
    normalized = candidate.strip().upper() if isinstance(candidate, str) else ""
    if (
        not normalized.isascii()
        or not hmac.compare_digest(normalized, _VISION_PROBE_EXPECTED_TOKEN)
    ):
        raise GatewayError(
            "模型接口可连接，但未能准确读取验证图片，不能确认其图文能力",
            retryable=False,
            diagnostic_code="visual_probe_failed",
            endpoint_url=result.endpoint_url,
        )


def _probe_selected_model(
    catalog: ModelCatalog,
    selected: DiscoveredModel,
    api_key: str,
    *,
    allowed_modes: tuple[GatewayApiMode, ...] | None = None,
) -> tuple[GatewayConfig, int, str | None]:
    schema = _visual_probe_schema()
    started = time.perf_counter()
    last_error: GatewayError | None = None
    protocol_fallback_codes = {
        "endpoint_not_found",
        "request_incompatible",
        "structured_output_unsupported",
    }
    modes = _probe_modes(catalog.provider)
    if allowed_modes is not None:
        reusable = set(allowed_modes)
        modes = tuple(mode for mode in modes if mode in reusable)
    if not modes:
        raise GatewayModelCapacityReached(
            "已达到 8 个模型上限；当前连接不会复用现有已验证模型"
        )
    for index, mode in enumerate(modes):
        temporary = GatewayConfig(
            base_url=catalog.normalized_base_url,
            model=selected.upstream_model_id,
            api_mode=mode,
            api_key=api_key,
            updated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            model_id="model_probe",
            display_name=selected.display_name,
            modalities=("text", "image"),
            purposes=("analysis", "script", "material", "video_review"),
            provider=catalog.provider,
        )
        try:
            result = call_gateway(
                temporary,
                context_json=_VISION_PROBE_CONTEXT_JSON,
                keyframe_data_urls=[_VISION_PROBE_DATA_URL],
                output_schema=schema,
                timeout_seconds=60,
            )
        except GatewayError as exc:
            last_error = exc
            if index < len(modes) - 1 and exc.diagnostic_code in protocol_fallback_codes:
                continue
            raise
        _require_visual_probe(result)
        return temporary, round((time.perf_counter() - started) * 1000), result.response_id
    assert last_error is not None
    raise last_error


@router.get("/readiness", response_model=S3ReadinessResponse)
def get_readiness() -> S3ReadinessResponse:
    config = _settings_store().load()
    gateway_configured = config is not None and config.has_purpose("analysis")
    counts = _queue().counts()
    accepted, required = _real_analysis_progress()
    ocr_ready = ocr_available()
    business_ready = accepted >= required
    pending_parts = []
    if not gateway_configured:
        pending_parts.append("待配置中转站")
    if accepted < required:
        pending_parts.append(f"待完成 {required - accepted} 条真实分析验收")
    return S3ReadinessResponse(
        engineering_ready=ocr_ready,
        ocr_ready=ocr_ready,
        gateway_configured=gateway_configured,
        pending_tasks=counts["pending"] + counts["retry_wait"], running_tasks=counts["running"],
        completed_tasks=counts["completed"] + counts["succeeded"], failed_tasks=counts["failed"],
        accepted_real_analyses=accepted, required_real_analyses=required, business_ready=business_ready,
        pending_reason="；".join(pending_parts) or None,
        analysis_method=AnalysisMethodStatus(**method_readiness()),
    )


@router.get("/gateway", response_model=GatewaySettingsResponse)
def get_gateway_settings() -> GatewaySettingsResponse:
    return _public_settings()


@router.post("/gateway/discover", response_model=GatewayDiscoveryResponse)
def discover_gateway_models(request: GatewayDiscoveryRequest) -> GatewayDiscoveryResponse:
    normalized, api_key = _resolve_discovery_secret(request)
    try:
        catalog = discover_models(base_url=normalized, api_key=api_key)
    except GatewaySettingsError as exc:
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail(str(exc), "invalid_gateway_settings"),
        ) from exc
    except ModelDiscoveryError as exc:
        _raise_discovery_error(exc)
        raise
    return GatewayDiscoveryResponse(**catalog.public_dict())


@router.post("/gateway/connect", response_model=GatewayConnectResponse)
def connect_gateway_model(
    request: GatewayConnectRequest,
    background_tasks: BackgroundTasks,
) -> GatewayConnectResponse:
    normalized, api_key = _resolve_discovery_secret(request)
    try:
        catalog = discover_models(base_url=normalized, api_key=api_key)
    except GatewaySettingsError as exc:
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail(str(exc), "invalid_gateway_settings"),
        ) from exc
    except ModelDiscoveryError as exc:
        if request.manual_model_id and exc.diagnostic_code == "catalog_unsupported":
            catalog = ModelCatalog(
                provider=infer_provider_from_base_url(normalized),
                normalized_base_url=normalized,
                catalog_source="openai_models",
                models=(DiscoveredModel(
                    upstream_model_id=request.upstream_model_id,
                    display_name=request.upstream_model_id,
                ),),
                warnings=("该中转站未开放模型目录；模型能力以本次真实图文探针为准。",),
            )
        else:
            _raise_discovery_error(exc)
            raise
    catalog_match = next(
        (
            item
            for item in catalog.models
            if item.upstream_model_id == request.upstream_model_id
        ),
        None,
    )
    if catalog_match is None and request.manual_model_id and catalog.truncated:
        selected = DiscoveredModel(
            upstream_model_id=request.upstream_model_id,
            display_name=request.upstream_model_id,
        )
    else:
        selected = _selected_catalog_model(catalog, request.upstream_model_id)
    store = _settings_store()
    try:
        with store.reserve_verified_model_capacity(
            base_url=catalog.normalized_base_url,
            upstream_model_id=selected.upstream_model_id,
            provider=catalog.provider,
        ) as reusable_modes:
            verified, latency_ms, response_id = _probe_selected_model(
                catalog,
                selected,
                api_key,
                allowed_modes=reusable_modes,
            )
            saved = store.upsert_verified_model(
                base_url=verified.base_url,
                upstream_model_id=verified.model,
                # Catalog display labels may be longer than the persisted UI
                # label limit. Keep the exact upstream model ID untouched and
                # truncate only the human-facing name after the paid probe.
                display_name=(selected.display_name.strip() or selected.upstream_model_id)[:60],
                provider=verified.provider,
                api_mode=verified.api_mode,
                api_key=api_key,
            )
    except GatewayModelCapacityReached as exc:
        raise HTTPException(
            status_code=409,
            detail=_gateway_error_detail(
                str(exc),
                "gateway_model_capacity_reached",
            ),
        ) from exc
    except GatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=_gateway_error_detail(
                str(exc),
                exc.diagnostic_code or "gateway_probe_failed",
                retryable=exc.retryable,
                upstream_status=exc.status_code,
                endpoint_url=exc.endpoint_url,
            ),
        ) from exc
    except GatewaySettingsError as exc:
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail(str(exc), "invalid_gateway_settings"),
        ) from exc
    from .s5 import _run_queue_safely as run_script_queue

    background_tasks.add_task(run_script_queue)
    wake_analysis_worker()
    connected = saved.for_purpose("analysis")
    return GatewayConnectResponse(
        connected_model_id=connected.model_id,
        latency_ms=latency_ms,
        response_id=response_id,
        settings=GatewaySettingsResponse(**saved.public_dict()),
    )


@router.put("/gateway", response_model=GatewaySettingsResponse)
def put_gateway_settings(
    request: GatewaySettingsRequest,
    background_tasks: BackgroundTasks,
) -> GatewaySettingsResponse:
    if request.models is None or any(
        value is not None
        for value in (request.base_url, request.model, request.api_mode, request.api_key)
    ):
        raise HTTPException(
            status_code=409,
            detail=_gateway_error_detail(
                "新连接或连接参数变更必须通过‘获取模型 → 连接并保存’完成图文能力验证",
                "gateway_verification_required",
            ),
        )
    if not 1 <= len(request.models) <= 8:
        raise HTTPException(
            status_code=400,
            detail=_gateway_error_detail(
                "模型数量必须在 1 到 8 个之间",
                "invalid_model_count",
            ),
        )
    dumped = request.model_dump()
    raw_models = dumped.get("models")
    assert isinstance(raw_models, list)
    for parsed, raw in zip(request.models, raw_models, strict=True):
        raw["api_key"] = (
            parsed.api_key.get_secret_value()
            if parsed.api_key is not None
            else None
        )
    try:
        config = _settings_store().update_verified_metadata(
            models=raw_models,
            routing=dumped.get("routing"),
            default_model_id=request.default_model_id,
        )
    except GatewayVerificationRequired as exc:
        raise HTTPException(
            status_code=409,
            detail=_gateway_error_detail(
                str(exc),
                "gateway_verification_required",
            ),
        ) from exc
    except GatewaySettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if config.has_purpose("script"):
        # Import lazily because S5 consumes these settings and imports the S3
        # accessors above. Resolving the runner after both modules are loaded
        # also keeps tests and embedders free to replace the worker boundary.
        from .s5 import _run_queue_safely as run_script_queue

        background_tasks.add_task(run_script_queue)
    return GatewaySettingsResponse(**config.public_dict())


@router.delete("/gateway", status_code=204)
def delete_gateway_settings() -> None:
    _settings_store().clear()


@router.post("/gateway/test", response_model=GatewayTestResponse)
def test_gateway(
    model_id: str | None = None,
    purpose: GatewayPurpose | None = None,
) -> GatewayTestResponse:
    config = _settings_store().load()
    if config is None:
        raise HTTPException(status_code=409, detail="请先保存中转站设置")
    if model_id and purpose:
        raise HTTPException(status_code=400, detail="模型 ID 和任务用途只能选择一项进行测试")
    try:
        config = config.for_purpose(purpose) if purpose else config.for_model(model_id)
    except GatewaySettingsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    started = time.perf_counter()
    visual_test = "image" in config.modalities
    schema = _visual_probe_schema() if visual_test else {
        "type": "object", "additionalProperties": False, "required": ["ok"],
        "properties": {"ok": {"type": "boolean", "const": True}},
    }
    try:
        images = [_VISION_PROBE_DATA_URL] if visual_test else []
        result = call_gateway(
            config,
            context_json=(
                _VISION_PROBE_CONTEXT_JSON
                if visual_test
                else '{"task":"只返回 ok=true"}'
            ),
            keyframe_data_urls=images,
            output_schema=schema,
            timeout_seconds=60,
        )
        if visual_test:
            _require_visual_probe(result)
    except GatewayError as exc:
        endpoint_url = exc.endpoint_url or gateway_endpoint(config)
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "diagnostic_code": exc.diagnostic_code or "gateway_error",
                "endpoint_url": endpoint_url,
                "model": config.model,
                "api_mode": config.api_mode,
            },
        ) from exc
    if not visual_test and result.content.get("ok") is not True:
        raise HTTPException(status_code=502, detail="中转站测试没有返回约定结果")
    return GatewayTestResponse(
        model_id=config.model_id,
        model=config.model,
        provider=config.provider,
        api_mode=config.api_mode,
        endpoint_url=result.endpoint_url or gateway_endpoint(config),
        tested_modalities=list(config.modalities),
        latency_ms=round((time.perf_counter() - started) * 1000),
        response_id=result.response_id,
    )


@router.post("/analyses", response_model=AnalysisTaskResponse, status_code=202)
def create_analysis(request: AnalysisCreateRequest) -> AnalysisTaskResponse:
    if not ocr_available():
        raise HTTPException(status_code=503, detail="本地 OCR 尚未就绪")
    config = _settings_store().load()
    if config is None or not config.has_purpose(request.model_purpose):
        purpose_label = "视频审核" if request.model_purpose == "video_review" else "深度分析"
        raise HTTPException(status_code=409, detail=f"请先配置支持图文输入的{purpose_label}模型")
    media_task = media_queue().get(request.media_task_id)
    if media_task is None:
        raise HTTPException(status_code=404, detail="找不到对应的 S2 媒体任务")
    if media_task.status != "completed" or not media_task.result_path:
        raise HTTPException(status_code=409, detail="视频尚未完成 S2 本地处理")
    payload = request.model_dump(mode="json", exclude_none=False)
    payload.pop("media_task_id", None)
    model_purpose = payload.pop("model_purpose")
    payload["_gateway_purpose"] = model_purpose
    selected_model = config.for_purpose(model_purpose)
    payload["_gateway_model_snapshot"] = selected_model.execution_snapshot()
    if model_purpose == "analysis":
        try:
            analysis_method = load_installed_method()
        except AnalysisMethodError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if analysis_method is not None:
            payload["_analysis_method_snapshot"] = analysis_method
    queued = _queue().enqueue(
        media_task_id=media_task.task_id, media_result_path=media_task.result_path,
        workspace_path=_analysis_root(), input_payload=payload, fixture_data=media_task.fixture_data,
    )
    wake_analysis_worker()
    return AnalysisTaskResponse(**queued.to_dict())


@router.get("/tasks", response_model=list[AnalysisTaskResponse])
def list_tasks() -> list[AnalysisTaskResponse]:
    return [AnalysisTaskResponse(**task.to_dict()) for task in _queue().list()]


def _raise_skill_domain(exc: Exception) -> None:
    if isinstance(exc, SkillNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, SkillConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, ContractValidationError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.post("/skill-candidates/refresh")
def refresh_skill_library() -> dict[str, int]:
    return refresh_skill_candidates(get_viral_skill_store(), _queue())


@router.get("/skill-candidates")
def list_skill_candidates(
    active: bool | None = True,
    include_inactive_linked: bool = False,
) -> list[dict[str, Any]]:
    store = get_viral_skill_store()
    # First-open migration for reports that were accepted before the formal
    # Skill library existed.  Subsequent report mutations reconcile through
    # the report endpoints, and the explicit refresh route remains available.
    if not store.list_candidates(active=None):
        refresh_skill_candidates(store, _queue())
    return store.list_candidates(active=active, include_inactive_linked=include_inactive_linked)


@router.get("/skill-candidates/{candidate_id}")
def get_skill_candidate(candidate_id: str) -> dict[str, Any]:
    try:
        return get_viral_skill_store().get_candidate(candidate_id)
    except Exception as exc:
        _raise_skill_domain(exc)
        raise


@router.post("/skill-candidates/{candidate_id}/approve")
def approve_skill_candidate(candidate_id: str, request: SkillCandidateApprovalRequest) -> dict[str, Any]:
    try:
        store = get_viral_skill_store()
        saved = store.approve_candidate(candidate_id, **request.model_dump())
        return store.get_skill_view(saved["skill_id"])
    except Exception as exc:
        _raise_skill_domain(exc)
        raise


@router.get("/skills")
def list_viral_skills(
    status: Literal["approved", "disabled"] | None = None,
    reuse_mode: Literal["reuse", "avoid"] | None = None,
) -> list[dict[str, Any]]:
    return get_viral_skill_store().list_skill_views(status=status, reuse_mode=reuse_mode)


@router.get("/skills/{skill_id}")
def get_viral_skill(skill_id: str) -> dict[str, Any]:
    try:
        return get_viral_skill_store().get_skill_view(skill_id)
    except Exception as exc:
        _raise_skill_domain(exc)
        raise


@router.post("/skills/{skill_id}/status")
def set_viral_skill_status(skill_id: str, request: SkillStatusRequest) -> dict[str, Any]:
    try:
        store = get_viral_skill_store()
        saved = store.set_skill_status(skill_id, **request.model_dump())
        return store.get_skill_view(saved["skill_id"])
    except Exception as exc:
        _raise_skill_domain(exc)
        raise


def _task_or_404(task_id: str):
    task = _queue().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到这个分析任务")
    return task


@router.get("/tasks/{task_id}", response_model=AnalysisTaskResponse)
def get_task(task_id: str) -> AnalysisTaskResponse:
    return AnalysisTaskResponse(**_task_or_404(task_id).to_dict())


@router.post("/tasks/{task_id}/retry", response_model=AnalysisTaskResponse, status_code=202)
def retry_task(task_id: str) -> AnalysisTaskResponse:
    queued = _task_or_404(task_id)
    config = _settings_store().load()
    if config is None or not config.has_purpose(queued.model_purpose):
        raise HTTPException(status_code=409, detail="请先检查并保存当前任务的模型设置")
    try:
        task = _queue().retry(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    wake_analysis_worker()
    return AnalysisTaskResponse(**task.to_dict())


@router.post("/tasks/{task_id}/cancel", response_model=AnalysisTaskResponse)
def cancel_task(task_id: str) -> AnalysisTaskResponse:
    try:
        task = _queue().cancel(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    wake_analysis_worker()
    return AnalysisTaskResponse(**task.to_dict())


@router.get("/tasks/{task_id}/report")
def get_report(task_id: str) -> dict[str, Any]:
    task = _task_or_404(task_id)
    if task.status not in {"completed", "succeeded"} or not task.result_path:
        raise HTTPException(status_code=409, detail="分析报告尚未完成")
    try:
        return load_report(task.result_path)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/keyframes/{frame_id}", response_class=FileResponse)
def get_keyframe(task_id: str, frame_id: str) -> FileResponse:
    report = get_report(task_id)
    frame = next((item for item in report["keyframes"] if item.get("id") == frame_id), None)
    if frame is None:
        raise HTTPException(status_code=404, detail="找不到这个关键帧")
    path = Path(frame["local_path"]).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=404, detail="关键帧文件不存在")
    return FileResponse(path, media_type=f"image/{'jpeg' if path.suffix.lower() in {'.jpg', '.jpeg'} else path.suffix.lower()[1:]}")


@router.patch("/tasks/{task_id}/report")
def patch_report(task_id: str, request: ReportUpdateRequest) -> dict[str, Any]:
    task = _task_or_404(task_id)
    if not task.result_path:
        raise HTTPException(status_code=409, detail="分析报告尚未完成")
    try:
        updated = update_report(task.result_path, expected_revision=request.expected_revision, changes=request.changes)
        _refresh_skills_best_effort()
        return updated
    except ReportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, ContractValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/review")
def review(task_id: str, request: ReportReviewRequest) -> dict[str, Any]:
    task = _task_or_404(task_id)
    if not task.result_path:
        raise HTTPException(status_code=409, detail="分析报告尚未完成")
    try:
        reviewed = review_report(task.result_path, **request.model_dump())
        _refresh_skills_best_effort()
        return reviewed
    except ReportConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, ContractValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/queue/run", response_model=QueueRunResponse, status_code=202)
def run_queue() -> QueueRunResponse:
    config = _settings_store().load()
    if config is None or not config.has_purpose("analysis"):
        raise HTTPException(status_code=409, detail="请先配置支持图文输入的深度分析模型")
    wake_analysis_worker()
    return QueueRunResponse()
