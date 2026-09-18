"""S8 publication registration, metric feedback, and explainable learning API."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Literal

from content_factory_contracts import ContractValidationError
from content_factory_media.ocr import OcrEngineError, ocr_available
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .s6 import _fixtures_allowed
from .s7 import get_edit_store
from .s7_store import EditNotFoundError
from .s8_imports import parse_csv_candidates, recognize_metric_image, sha256_bytes
from .s8_learning import build_learning_report
from .s8_store import FeedbackConflictError, FeedbackNotFoundError, FeedbackStore

router = APIRouter(prefix="/s8", tags=["S8 publishing feedback"])
_stores: dict[Path, FeedbackStore] = {}
_store_lock = threading.Lock()
MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _data_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_S8_DATA_DIR", _project_root() / "data" / "s8")).resolve()


def get_feedback_store() -> FeedbackStore:
    root = _data_root()
    with _store_lock:
        store = _stores.get(root)
        if store is None:
            store = FeedbackStore(root)
            _stores[root] = store
        return store


def _raise_domain(exc: Exception) -> None:
    if isinstance(exc, (FeedbackNotFoundError, EditNotFoundError, LookupError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, FeedbackConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, ContractValidationError, OcrEngineError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _visible_publication(publication_id: str) -> dict[str, Any]:
    publication = get_feedback_store().get_publication(publication_id)
    if publication["fixture_data"] and not _fixtures_allowed():
        raise FeedbackNotFoundError("找不到这个发布记录")
    return publication


def _visible_draft(draft_id: str) -> dict[str, Any]:
    draft = get_feedback_store().get_import_draft(draft_id)
    if draft["fixture_data"] and not _fixtures_allowed():
        raise FeedbackNotFoundError("找不到这个导入草稿")
    return draft


def _eligible_output_rows() -> list[dict[str, Any]]:
    store = get_edit_store()
    registered = {item["output_id"] for item in get_feedback_store().list_publications(include_fixtures=_fixtures_allowed())}
    rows: list[dict[str, Any]] = []
    for output in store.list_outputs(include_fixtures=_fixtures_allowed()):
        if output["status"] != "approved" or output["output_id"] in registered:
            continue
        try:
            project = store.get_project(output["project_id"])
        except Exception:
            continue
        variant = next((item for item in project["variants"] if item["id"] == output["variant_id"]), None)
        if (
            project["revision"] != output["project_revision"] or project["status"] != "approved"
            or not variant or variant.get("latest_output_id") != output["output_id"]
        ):
            continue
        rows.append({
            "output_id": output["output_id"], "project_id": output["project_id"],
            "project_revision": output["project_revision"], "variant_id": output["variant_id"],
            "variant_name": variant["name"], "script_id": project["script_id"],
            "script_version_id": variant["script_version_id"], "product_id": project["product_id"],
            "filename": output["media"]["filename"], "duration_ms": output["media"]["duration_ms"],
            "fixture_data": output["fixture_data"], "approved_at": output["review"]["reviewed_at"],
        })
    return rows


class CreatePublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_id: str = Field(min_length=6, max_length=120)
    work_url: str = Field(min_length=12, max_length=500)
    work_id: str = Field(pattern=r"^[A-Za-z0-9_-]{6,40}$")
    account_label: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    published_at: str
    actor: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=1000)


class UpdatePublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=100)
    action: Literal["corrected", "archived", "restored"]
    work_url: str | None = Field(default=None, min_length=12, max_length=500)
    work_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{6,40}$")
    account_label: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    published_at: str | None = None
    note: str = Field(default="", max_length=1000)


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captured_at: str
    source: Literal["manual", "authorized_api"] = "manual"
    confidence: Literal["confirmed", "estimated"] = "confirmed"
    confirmed_by: str = Field(min_length=1, max_length=100)
    metrics: dict[str, int | float] = Field(min_length=1)
    is_correction: bool = False
    correction_reason: str | None = Field(default=None, max_length=500)


class ConfirmBusinessReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    reviewed_by: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=1000)


class ConfirmImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed_by: str = Field(min_length=1, max_length=100)
    candidates: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class RejectImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=500)


class GenerateReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=6, max_length=120)
    primary_metric: Literal["views", "retention_3s", "completion_rate", "engagement_per_1000", "product_ctr", "conversion_rate", "orders", "gmv_cents"] = "product_ctr"
    target_window_minutes: int = Field(default=1440, ge=1, le=5256000)


@router.get("/readiness")
def readiness() -> dict[str, Any]:
    counts = get_feedback_store().counts()
    eligible = len(_eligible_output_rows())
    closed = counts["closed_real_loops"]
    return {
        "stage": "S8", "engineering_ready": True, "ocr_ready": ocr_available(),
        "eligible_outputs": eligible, "publication_count": counts["publications"],
        "pending_imports": counts["pending_imports"], "snapshot_count": counts["snapshots"],
        "report_count": counts["reports"], "closed_real_loops": closed, "required_real_loops": 1,
        "business_ready": closed >= 1,
        "pending_reason": None if closed else "待用真实抖音作品保存两个不同时刻的已确认指标快照，并由业务人员确认复盘闭环",
        "publishing_mode": "manual_registration", "learning_mode": "local_explainable_rules",
    }


@router.get("/eligible-outputs")
def eligible_outputs() -> list[dict[str, Any]]:
    return _eligible_output_rows()


@router.post("/publications", status_code=201)
def create_publication(request: CreatePublicationRequest) -> dict[str, Any]:
    try:
        output = get_edit_store().get_output(request.output_id)
        if output["fixture_data"] and not _fixtures_allowed():
            raise EditNotFoundError("找不到这个成片")
        project = get_edit_store().get_project(output["project_id"])
        publication, duplicate = get_feedback_store().create_publication(
            output=output, project=project, **request.model_dump(exclude={"output_id"}),
        )
        return {"publication": publication, "duplicate": duplicate}
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/publications")
def publications(product_id: str | None = None) -> list[dict[str, Any]]:
    return get_feedback_store().list_publications(include_fixtures=_fixtures_allowed(), product_id=product_id)


@router.get("/publications/{publication_id}")
def publication(publication_id: str) -> dict[str, Any]:
    try:
        return _visible_publication(publication_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.patch("/publications/{publication_id}")
def update_publication(publication_id: str, request: UpdatePublicationRequest) -> dict[str, Any]:
    try:
        _visible_publication(publication_id)
        return get_feedback_store().update_publication(publication_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/publications/{publication_id}/metric-snapshots", status_code=201)
def create_snapshot(publication_id: str, request: SnapshotRequest) -> dict[str, Any]:
    try:
        _visible_publication(publication_id)
        return get_feedback_store().add_snapshot(publication_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/publications/{publication_id}/confirm-business-review")
def confirm_business_review(publication_id: str, request: ConfirmBusinessReviewRequest) -> dict[str, Any]:
    try:
        _visible_publication(publication_id)
        return get_feedback_store().confirm_business_review(publication_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/metric-snapshots")
def metric_snapshots(publication_id: str | None = None) -> list[dict[str, Any]]:
    if publication_id:
        try:
            _visible_publication(publication_id)
        except Exception as exc:
            _raise_domain(exc)
    return get_feedback_store().list_snapshots(publication_id=publication_id, include_fixtures=_fixtures_allowed())


@router.post("/imports/csv", status_code=201)
async def import_csv(
    created_by: str = Form(min_length=1, max_length=100), data: UploadFile = File(),
) -> dict[str, Any]:
    try:
        content = await data.read(MAX_CSV_BYTES + 1)
        if len(content) > MAX_CSV_BYTES:
            raise ValueError("CSV 不能超过 5 MB")
        raw_text, candidates = parse_csv_candidates(content)
        records = [_visible_publication(item["publication_id"]) for item in candidates]
        fixture_values = {bool(item["fixture_data"]) for item in records}
        if len(fixture_values) != 1:
            raise ValueError("同一批 CSV 不能混用样例和正式作品")
        draft, duplicate = get_feedback_store().create_import_draft(
            import_kind="csv", fixture_data=fixture_values.pop(), source_name=data.filename or "metrics.csv",
            source_sha256=sha256_bytes(content), raw_text=raw_text, candidates=candidates, errors=[], created_by=created_by,
        )
        return {"draft": draft, "duplicate": duplicate}
    except Exception as exc:
        _raise_domain(exc)
        raise
    finally:
        await data.close()


@router.post("/imports/ocr", status_code=201)
async def import_ocr(
    publication_id: str = Form(min_length=6, max_length=120), captured_at: str = Form(),
    created_by: str = Form(min_length=1, max_length=100), image: UploadFile = File(),
) -> dict[str, Any]:
    path: Path | None = None
    try:
        publication_row = _visible_publication(publication_id)
        suffix = Path(image.filename or "").suffix.lower()
        if suffix not in IMAGE_TYPES:
            raise ValueError("截图仅支持 PNG、JPG、JPEG、WEBP")
        content = await image.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError("截图不能超过 20 MB")
        digest = sha256_bytes(content)
        path = (get_feedback_store().import_root / f"{digest}{suffix}").resolve()
        if not path.is_relative_to(get_feedback_store().import_root):
            raise ValueError("截图保存位置不安全")
        if not path.exists():
            path.write_bytes(content)
        raw_text, candidates = recognize_metric_image(path, publication_id=publication_id, captured_at=captured_at)
        draft, duplicate = get_feedback_store().create_import_draft(
            import_kind="ocr", fixture_data=bool(publication_row["fixture_data"]), source_name=image.filename or "metrics.png",
            source_sha256=digest, raw_text=raw_text, candidates=candidates, errors=[], created_by=created_by,
        )
        return {"draft": draft, "duplicate": duplicate}
    except Exception as exc:
        _raise_domain(exc)
        raise
    finally:
        await image.close()


@router.get("/imports")
def imports() -> list[dict[str, Any]]:
    return get_feedback_store().list_import_drafts(include_fixtures=_fixtures_allowed())


@router.post("/imports/{draft_id}/confirm")
def confirm_import(draft_id: str, request: ConfirmImportRequest) -> dict[str, Any]:
    try:
        _visible_draft(draft_id)
        return get_feedback_store().confirm_import(draft_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/imports/{draft_id}/reject")
def reject_import(draft_id: str, request: RejectImportRequest) -> dict[str, Any]:
    try:
        _visible_draft(draft_id)
        return get_feedback_store().reject_import(draft_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/learning-reports", status_code=201)
def generate_learning_report(request: GenerateReportRequest) -> dict[str, Any]:
    try:
        publications = get_feedback_store().list_publications(include_fixtures=_fixtures_allowed(), product_id=request.product_id)
        snapshots = get_feedback_store().list_snapshots(include_fixtures=_fixtures_allowed())
        report = build_learning_report(publications=publications, snapshots=snapshots, **request.model_dump())
        return get_feedback_store().save_report(report)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/learning-reports")
def learning_reports(product_id: str | None = None) -> list[dict[str, Any]]:
    return get_feedback_store().list_reports(include_fixtures=_fixtures_allowed(), product_id=product_id)
