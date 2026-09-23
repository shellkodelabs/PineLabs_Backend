"""
SOP router — HTTP concerns only. No SQLAlchemy usage and no business
logic here — same pattern as bin_series.py/merchants.py.

This module defines TWO separate APIRouter instances, both registered
in app/api/v1/router.py:

  - `merchant_sheets_router`: mounted under the SAME "/merchants" prefix
    as app.api.v1.merchants.router, adding the deeper
    "/{merchantId}/sheets/{sheetKey}" and ".../rows" paths alongside the
    existing "" (list), "/resolve", and "/{merchantId}/sheets" (list
    sheets) paths from Part 8. FastAPI/Starlette route matching is by
    exact path-segment structure, not prefix overlap, so these coexist
    without conflict: "/merchants/{merchantId}/sheets" (3 segments) and
    "/merchants/{merchantId}/sheets/{sheetKey}" (4 segments) can never
    match each other's requests. Registration order between the two
    routers therefore doesn't affect correctness either way.

  - `common_escalation_router`: mounted under "/sop", holding the one
    unrelated path "/escalation/common".

page/pageSize validated directly via FastAPI Query(ge=..., le=...)
(rejects out-of-range values with 422), same as every prior part.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.sop import CommonEscalationResponse, MerchantSheetDetailResponse, SopRowResponse
from app.services import sop_service

merchant_sheets_router = APIRouter()
common_escalation_router = APIRouter()


@merchant_sheets_router.get("/{merchantId}/sheets/{sheetKey}", response_model=MerchantSheetDetailResponse)
def get_merchant_sheet(
    merchantId: int = Path(..., ge=1, description="Merchant id"),
    sheetKey: str = Path(..., min_length=1, description="Sheet key, e.g. 'block'"),
    db: Session = Depends(get_db),
) -> MerchantSheetDetailResponse:
    """Full structure (groups/columns, in sort_order) for one
    merchant-owned SOP sheet. 404 MERCHANT_NOT_FOUND if the merchant
    doesn't exist; 404 SOP_SHEET_NOT_FOUND if it exists but has no sheet
    with this key. Never returns the shared/default sheet."""
    return sop_service.get_merchant_sheet(db, merchant_id=merchantId, sheet_key=sheetKey)


@merchant_sheets_router.get("/{merchantId}/sheets/{sheetKey}/rows", response_model=PaginatedResponse[SopRowResponse])
def list_merchant_sheet_rows(
    merchantId: int = Path(..., ge=1, description="Merchant id"),
    sheetKey: str = Path(..., min_length=1, description="Sheet key, e.g. 'block'"),
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(None, min_length=1, description="Substring match across the row's stored values"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[SopRowResponse]:
    """Paginated rows for one merchant-owned SOP sheet, id ascending by
    default. `data` is returned exactly as stored (dynamic JSONB, no
    fixed schema)."""
    return sop_service.list_merchant_sheet_rows(
        db, merchant_id=merchantId, sheet_key=sheetKey, page=page, page_size=pageSize, search=search
    )


@common_escalation_router.get("/escalation/common", response_model=CommonEscalationResponse)
def get_common_escalation(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(None, min_length=1, description="Substring match across the row's stored values"),
    db: Session = Depends(get_db),
) -> CommonEscalationResponse:
    """The shared/default escalation sheet (merchant_id IS NULL,
    key='escalation') — never a merchant-specific escalation sheet such
    as ICICI's own."""
    return sop_service.get_common_escalation(db, page=page, page_size=pageSize, search=search)
