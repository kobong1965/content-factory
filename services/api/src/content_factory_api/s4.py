"""S4 product knowledge and shooting-constraint API."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Literal

from content_factory_contracts import ContractValidationError
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .s3 import get_gateway_settings_store
from .s3_gateway import GatewayError
from .s3_settings import GatewaySettingsError
from .s4_suggestions import suggest_visible_facts
from .s4_products import script_product_eligibility
from .s4_store import (
    ProductAssetError,
    ProductConflictError,
    ProductNotFoundError,
    ProductStore,
)

router = APIRouter(prefix="/s4", tags=["S4 product knowledge"])
_stores: dict[Path, ProductStore] = {}
_stores_lock = threading.Lock()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _data_root() -> Path:
    return Path(os.environ.get("CONTENT_FACTORY_S4_DATA_DIR", _project_root() / "data" / "s4")).resolve()


def _store() -> ProductStore:
    root = _data_root()
    with _stores_lock:
        store = _stores.get(root)
        if store is None:
            store = ProductStore(root)
            _stores[root] = store
        return store


def get_product_store() -> ProductStore:
    """Return the canonical S4 product repository for downstream stages."""

    return _store()


class CreateProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=100)


class ProductWorkspaceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    notes: str = Field(default='', max_length=20000)
    selected_asset_ids: list[str] = Field(default_factory=list, max_length=500)
    primary_asset_id: str | None = None


class SheetProductRow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=200)
    sku: str = Field(default='',max_length=64)
    notes: str = Field(default='',max_length=20000)


class SheetImportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rows: list[SheetProductRow] = Field(min_length=1,max_length=200)


@router.post('/product-sheet/preview')
def preview_product_sheet(upload: UploadFile = File(), sheet_name: str | None = Form(default=None)):
    from .product_sheet import preview_sheet
    try:
        return preview_sheet(upload.file.read(8*1024*1024+1),upload.filename or '',sheet_name)
    except Exception as exc:
        _raise_domain(exc)
    finally:
        upload.file.close()


@router.post('/product-sheet/import')
def import_product_sheet(request: SheetImportInput):
    from .product_sheet import import_rows
    try:
        return import_rows(_store(),[r.model_dump() for r in request.rows])
    except Exception as exc:
        _raise_domain(exc)


@router.get('/products/{product_id}/workspace')
def get_product_workspace(product_id: str):
    from .product_workspace import read_workspace
    try:
        return read_workspace(_store(), product_id)
    except Exception as exc:
        _raise_domain(exc)


@router.put('/products/{product_id}/workspace')
def put_product_workspace(product_id: str, request: ProductWorkspaceInput):
    from .product_workspace import save_workspace
    try:
        return save_workspace(_store(), product_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)


class ProductSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    kind: Literal["detail_page", "image", "video", "document"]
    label: str = Field(min_length=1, max_length=200)
    source_ref: str = Field(min_length=1, max_length=2000)
    asset_id: str | None = None
    mime_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    managed: bool


class ProductFactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    field: Literal["fabric", "fit", "size", "color", "price", "activity", "feature", "care", "other"]
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    unit: str | None = Field(default=None, max_length=50)
    source_type: Literal["official_detail_page", "supplier_document", "manual_confirmed", "media_evidence"]
    source_id: str | None = None
    source_ref: str = Field(min_length=1, max_length=1000)
    confirmed_by: str = Field(min_length=1, max_length=100)
    confirmed_at: str


class ShootingConstraintsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: list[str] = Field(default_factory=list, max_length=30)
    locations: list[str] = Field(default_factory=list, max_length=30)
    equipment: list[str] = Field(default_factory=list, max_length=30)
    lights: list[str] = Field(default_factory=list, max_length=30)
    daily_available_minutes: int = Field(ge=0, le=1440)
    budget_cny: float = Field(ge=0, le=10000000)
    reusable_assets_allowed: bool
    brand_tone: str = Field(max_length=1000)
    required_disclosures: list[str] = Field(default_factory=list, max_length=50)
    max_duration_ms: int = Field(ge=0, le=3600000)
    confirmed_by: str | None = Field(default=None, max_length=100)
    confirmed_at: str | None = None


class UpdateProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    sources: list[ProductSourceInput] = Field(default_factory=list, max_length=100)
    facts: list[ProductFactInput] = Field(default_factory=list, max_length=200)
    selling_point_fact_ids: list[str] = Field(default_factory=list, max_length=100)
    forbidden_expressions: list[str] = Field(default_factory=list, max_length=200)
    unprovable_claims: list[str] = Field(default_factory=list, max_length=200)
    brand_boundary_confirmed_by: str | None = Field(default=None, max_length=100)
    brand_boundary_confirmed_at: str | None = None
    shooting_constraints: ShootingConstraintsInput


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    status: Literal["draft", "active", "archived"]
    actor: str = Field(min_length=1, max_length=100)


class S4ReadinessResponse(BaseModel):
    stage: Literal["S4"] = "S4"
    engineering_ready: bool = True
    contract_version: Literal["1.1.0"] = "1.1.0"
    total_products: int
    draft_products: int
    active_products: int
    archived_products: int
    script_eligible_products: int
    accepted_real_products: int
    required_real_products: Literal[3] = 3
    business_ready: bool
    pending_reason: str | None


def _raise_domain(exc: Exception) -> None:
    if isinstance(exc, ProductNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ProductConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, GatewayError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, GatewaySettingsError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ProductAssetError, ValueError, ContractValidationError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _suggestion_gateway():
    settings = get_gateway_settings_store().load()
    if settings is None:
        raise GatewaySettingsError("请先给素材识别分配一个支持图片输入的模型")
    return settings.for_purpose("material")


@router.get("/readiness", response_model=S4ReadinessResponse)
def get_readiness() -> S4ReadinessResponse:
    counts = _store().counts()
    script_eligible = sum(
        script_product_eligibility(_store().get(item["product_id"]))["eligible"]
        for item in _store().list()
    )
    accepted = counts["active"]
    business_ready = accepted >= 3
    return S4ReadinessResponse(
        total_products=counts["total"], draft_products=counts["draft"], active_products=counts["active"],
        archived_products=counts["archived"], script_eligible_products=script_eligible,
        accepted_real_products=accepted, business_ready=business_ready,
        pending_reason=None if business_ready else f"待补 {3 - accepted} 款真实且完整的商品资料",
    )


@router.get("/products")
def list_products(
    query: str = Query(default="", max_length=200),
    status: Literal["draft", "active", "archived"] | None = None,
) -> list[dict[str, Any]]:
    return _store().list(query=query, status=status)


@router.post("/products", status_code=201)
def create_product(request: CreateProductRequest) -> dict[str, Any]:
    try:
        return _store().create(**request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/products/{product_id}")
def get_product(product_id: str) -> dict[str, Any]:
    try:
        return _store().get(product_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.put("/products/{product_id}")
def update_product(product_id: str, request: UpdateProductRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    expected_revision = payload.pop("expected_revision")
    actor = payload.pop("actor")
    try:
        return _store().update(product_id, expected_revision=expected_revision, changes=payload, actor=actor)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/products/{product_id}/status")
def set_product_status(product_id: str, request: StatusRequest) -> dict[str, Any]:
    try:
        return _store().set_status(product_id, **request.model_dump())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/products/{product_id}/versions")
def list_product_versions(product_id: str) -> list[dict[str, Any]]:
    try:
        return _store().versions(product_id)
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.get("/products/{product_id}/script-eligibility")
def get_product_script_eligibility(product_id: str) -> dict[str, Any]:
    try:
        return script_product_eligibility(_store().get(product_id))
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/products/{product_id}/suggestions")
def suggest_product_facts(product_id: str) -> dict[str, Any]:
    """Inspect managed product images and return unsaved fact proposals."""

    try:
        profile = _store().get(product_id)
        image_sources: list[tuple[dict[str, Any], Path]] = []
        for raw in reversed(profile.get("sources", [])):
            if not isinstance(raw, dict) or raw.get("managed") is not True or raw.get("kind") != "image":
                continue
            asset_id = raw.get("asset_id")
            if not isinstance(asset_id, str):
                continue
            path, media_type, _ = _store().asset(product_id, asset_id)
            if media_type not in {"image/jpeg", "image/png", "image/webp"}:
                continue
            image_sources.append((raw, path))
            if len(image_sources) == 8:
                break
        return suggest_visible_facts(profile, image_sources, _suggestion_gateway())
    except Exception as exc:
        _raise_domain(exc)
        raise


@router.post("/products/{product_id}/assets")
def upload_product_asset(
    product_id: str,
    expected_revision: int = Form(ge=1),
    actor: str = Form(min_length=1, max_length=100),
    upload: UploadFile = File(),
) -> dict[str, Any]:
    try:
        profile, source, duplicate = _store().add_asset(
            product_id, expected_revision=expected_revision, actor=actor,
            file_name=upload.filename or "", stream=upload.file,
        )
        return {"profile": profile, "source": source, "duplicate": duplicate}
    except Exception as exc:
        _raise_domain(exc)
        raise
    finally:
        upload.file.close()


@router.get("/products/{product_id}/assets/{asset_id}", response_class=FileResponse)
def get_product_asset(product_id: str, asset_id: str) -> FileResponse:
    try:
        path, media_type, file_name = _store().asset(product_id, asset_id)
    except Exception as exc:
        _raise_domain(exc)
        raise
    return FileResponse(path, media_type=media_type, filename=file_name)
