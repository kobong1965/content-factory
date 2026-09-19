from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import logging
import threading
from typing import Literal, Mapping

from content_factory_contracts import read_gold_set_readiness
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from . import __version__
from .edit_batches import router as edit_batches_router
from .subtitle_editor import router as subtitle_editor_router
from .auto_edit import recover_auto_edit_queue, router as auto_edit_router
from .local_exports import router as local_exports_router
from .library_management import router as library_management_router
from .software_updates import router as software_updates_router, update_gate
from .s2 import _run_queue_safely as run_s2_recovered_queue, router as s2_router
from .s3 import router as s3_router, start_analysis_worker, stop_analysis_worker
from .s4 import router as s4_router
from .s5 import _run_queue_safely as run_s5_recovered_queue, router as s5_router
from .s6 import _run_queue as run_s6_recovered_queue, router as s6_router
from .s7 import _run_queue as run_s7_recovered_queue, router as s7_router
from .s8 import router as s8_router


logger = logging.getLogger(__name__)
_RECOVERY_RUNNERS = (
    ("s2", run_s2_recovered_queue),
    ("s5", run_s5_recovered_queue),
    ("s6", run_s6_recovered_queue),
    ("s7", run_s7_recovered_queue),
    ("auto-edit", recover_auto_edit_queue),
)


def _run_recovery(stage: str, runner) -> None:
    try:
        runner()
    except Exception:
        logger.exception("Recovered %s queue could not resume", stage)


def start_recovered_queue_workers() -> list[threading.Thread]:
    """Wake each one-shot local queue consumer after interrupted state is recovered."""
    workers: list[threading.Thread] = []
    for stage, runner in _RECOVERY_RUNNERS:
        worker = threading.Thread(
            target=lambda current_stage=stage, current_runner=runner: _run_recovery(current_stage, current_runner),
            name=f"recover-{stage}",
            daemon=True,
        )
        worker.start()
        workers.append(worker)
    return workers


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: Literal["api"] = "api"
    status: Literal["ok"] = "ok"
    version: str
    instance_id: str
    data_profile_id: str
    build_id: str


class S1ReadinessResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: Literal["S1"] = "S1"
    schema_version: str
    schema_count: int
    engineering_ready: bool
    business_ready: bool
    accepted_videos: int
    required_videos: int
    accepted_products: int
    required_products: int
    pending_reason: str | None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_analysis_worker()
    start_recovered_queue_workers()
    try:
        yield
    finally:
        stop_analysis_worker()


app = FastAPI(
    title="爆款内容工厂 API",
    summary="抖音男装短视频分析与脚本生产业务服务",
    version=__version__,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def safe_request_validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return useful validation locations without reflecting request values."""

    # Pydantic's default error dictionaries contain `input` and sometimes
    # value-bearing `ctx`. Request bodies may include third-party API keys, so
    # keep only the stable fields needed by the desktop client.
    detail = [
        {
            "loc": list(error.get("loc", ())),
            "msg": str(error.get("msg") or "请求参数格式不正确"),
            "type": str(error.get("type") or "value_error"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.include_router(s2_router)
app.include_router(s3_router)
app.include_router(s4_router)
app.include_router(s5_router)
app.include_router(s6_router)
app.include_router(s7_router)
app.include_router(auto_edit_router)
app.include_router(edit_batches_router)
app.include_router(subtitle_editor_router)
app.include_router(local_exports_router)
app.include_router(library_management_router)
app.include_router(software_updates_router)
app.middleware('http')(update_gate)
app.include_router(s8_router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def get_health() -> HealthResponse:
    """Return a stable readiness response for local orchestration."""

    return HealthResponse(
        version=__version__,
        instance_id=project_instance_id(),
        data_profile_id=data_profile_id(),
        build_id=_PROJECT_BUILD_ID,
    )


def project_instance_id(project_root: Path | None = None) -> str:
    """Identify this checkout without exposing its local filesystem path."""

    root = project_root or Path(__file__).resolve().parents[4]
    normalized = os.path.normcase(str(root.resolve())).rstrip("\\/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def data_profile_id(
    project_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Fingerprint all persistent roots so launchers never reuse a test profile."""

    root = (project_root or Path(__file__).resolve().parents[4]).resolve()
    env = os.environ if environment is None else environment
    local_app_data = Path(env.get("LOCALAPPDATA", root / "data" / "runtime"))
    defaults = (
        ("CONTENT_FACTORY_RUNTIME_ROOT", root / "data" / "runtime"),
        ("CONTENT_FACTORY_MEDIA_ROOT", root / "data" / "media"),
        ("CONTENT_FACTORY_ANALYSIS_ROOT", root / "data" / "analysis"),
        ("CONTENT_FACTORY_S3_CONFIG_PATH", local_app_data / "爆款内容工厂" / "gateway-config.json"),
        ("CONTENT_FACTORY_S4_DATA_DIR", root / "data" / "s4"),
        ("CONTENT_FACTORY_S5_DATA_DIR", root / "data" / "s5"),
        ("CONTENT_FACTORY_S6_DATA_DIR", root / "data" / "s6"),
        ("CONTENT_FACTORY_S7_DATA_DIR", root / "data" / "s7"),
        ("CONTENT_FACTORY_S8_DATA_DIR", root / "data" / "s8"),
    )
    normalized_paths = [
        os.path.normcase(str(Path(env.get(name, default)).expanduser().resolve())).rstrip("\\/")
        for name, default in defaults
    ]
    payload = "\n".join(normalized_paths).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def project_build_id(project_root: Path | None = None) -> str:
    """Return the desktop release identity captured by the API at import time."""

    root = (project_root or Path(__file__).resolve().parents[4]).resolve()
    payload = json.loads((root / "package.json").read_text(encoding="utf-8"))
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("The project package version is missing")
    return f"content-factory-{version.strip()}"


_PROJECT_BUILD_ID = project_build_id()


@app.get("/s1/readiness", response_model=S1ReadinessResponse, tags=["system"])
def get_s1_readiness() -> S1ReadinessResponse:
    """Report engineering readiness separately from real gold-set readiness."""

    project_root = Path(__file__).resolve().parents[4]
    readiness = read_gold_set_readiness(project_root / "data" / "gold-set" / "manifest.json")
    return S1ReadinessResponse(**readiness.to_dict())
