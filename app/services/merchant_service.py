"""
Service layer for Merchants. Orchestrates
app.repositories.merchant_repository, maps ORM objects to
app.schemas.merchant response schemas, and raises the shared domain
exceptions for not-found cases — no SQLAlchemy usage, no HTTP-layer
concerns.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.merchant import Merchant
from app.models.sop import SopSheet
from app.repositories import merchant_repository
from app.schemas.common import PaginatedResponse
from app.schemas.merchant import MerchantResponse, MerchantSheetResponse, MerchantSheetsResponse


def _to_response(merchant: Merchant) -> MerchantResponse:
    return MerchantResponse(id=merchant.id, name=merchant.name, classification=merchant.classification)


def _sheet_to_response(sheet: SopSheet) -> MerchantSheetResponse:
    return MerchantSheetResponse(id=sheet.id, key=sheet.key, name=sheet.name)


def list_merchants(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    classification: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
) -> PaginatedResponse[MerchantResponse]:
    merchants, total = merchant_repository.search(
        db,
        search=search,
        classification=classification,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse[MerchantResponse](
        items=[_to_response(m) for m in merchants],
        total=total,
        page=page,
        pageSize=page_size,
    )


def resolve_merchant(db: Session, *, name: str) -> MerchantResponse:
    merchant = merchant_repository.get_by_name(db, name=name)
    if merchant is None:
        raise NotFoundError(f"No merchant found for name={name!r}.", code="MERCHANT_NOT_FOUND")
    return _to_response(merchant)


def get_merchant_sheets(db: Session, *, merchant_id: int) -> MerchantSheetsResponse:
    merchant = merchant_repository.get_by_id(db, merchant_id)
    if merchant is None:
        raise NotFoundError(f"No merchant found for id={merchant_id}.", code="MERCHANT_NOT_FOUND")

    sheets = merchant_repository.list_sheets_for_merchant(db, merchant_id)
    return MerchantSheetsResponse(
        merchant=_to_response(merchant),
        sheets=[_sheet_to_response(s) for s in sheets],
    )
