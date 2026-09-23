"""
Merchant router — HTTP concerns only: parses/validates query and path
parameters, injects the DB session, calls the service layer, returns its
result. No SQLAlchemy usage and no business logic here — same pattern as
app/api/v1/bin_series.py.

page/pageSize validated directly via FastAPI Query(ge=..., le=...)
(rejects out-of-range values with 422) rather than the shared
app.api.deps.get_pagination_params dependency, which clamps instead of
rejecting — same reasoning as bin_series.py.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.merchant import CLASSIFICATION_VALUES
from app.schemas.common import PaginatedResponse
from app.schemas.merchant import MerchantResponse, MerchantSheetsResponse
from app.services import merchant_service

router = APIRouter()

SortField = Literal["name", "classification", "id"]
SortOrder = Literal["asc", "desc"]
# Built from the model's own constant (single source of truth) rather
# than re-typed here — invalid values fail FastAPI's own query
# validation with a 422 before this ever reaches the service/repository.
ClassificationFilter = Literal[CLASSIFICATION_VALUES]


@router.get("", response_model=PaginatedResponse[MerchantResponse])
def list_merchants(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(None, min_length=1, description="Substring match on merchant name"),
    classification: Optional[ClassificationFilter] = Query(None, description="Exact classification filter"),
    sortBy: Optional[SortField] = Query(None, description="Field to sort by (default: id)"),
    sortOrder: SortOrder = Query("asc"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[MerchantResponse]:
    """Paginated, searchable, filterable, sortable list of merchants."""
    return merchant_service.list_merchants(
        db,
        page=page,
        page_size=pageSize,
        search=search,
        classification=classification,
        sort_by=sortBy,
        sort_order=sortOrder,
    )


@router.get("/resolve", response_model=MerchantResponse)
def resolve_merchant(
    name: str = Query(..., min_length=1, description="Exact, case-insensitive merchant name"),
    db: Session = Depends(get_db),
) -> MerchantResponse:
    """Resolves the single merchant matching an exact (case-insensitive)
    name. Returns the project's standard 404 error envelope if none
    exists — never a partial match."""
    return merchant_service.resolve_merchant(db, name=name)


@router.get("/{merchantId}/sheets", response_model=MerchantSheetsResponse)
def get_merchant_sheets(
    merchantId: int = Path(..., ge=1, description="Merchant id"),
    db: Session = Depends(get_db),
) -> MerchantSheetsResponse:
    """Merchant-owned SOP sheets only — shared/default sheets
    (merchant_id IS NULL) are never included. Returns the project's
    standard 404 error envelope if the merchant doesn't exist."""
    return merchant_service.get_merchant_sheets(db, merchant_id=merchantId)
