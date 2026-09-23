"""
Service layer for SOP. Orchestrates app.repositories.merchant_repository
and app.repositories.sop_repository, maps ORM objects to
app.schemas.sop response schemas, and raises the shared domain
exceptions for not-found cases — no SQLAlchemy usage, no HTTP-layer
concerns.

Shared-sheet handling: get_merchant_sheet()/list_merchant_sheet_rows()
NEVER fall back to the shared/default sheet — they only ever call
sop_repository.get_merchant_sheet(), which by construction cannot match
a merchant_id IS NULL row. get_common_escalation() is the only path that
calls sop_repository.get_shared_sheet(). This is a deliberate scoping
decision for this API surface (per the Part 9 task's explicit
instruction) — it does not implement the kind of "fall back to shared
escalation when a merchant lacks its own" behavior described for a
future cross-validate view in the backend design; that would be a
different endpoint's concern, not this one's.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.sop import SopSheet
from app.repositories import merchant_repository, sop_repository
from app.schemas.common import PaginatedResponse
from app.schemas.merchant import MerchantResponse
from app.schemas.sop import (
    CommonEscalationResponse,
    MerchantSheetDetailResponse,
    SopColumnGroupResponse,
    SopColumnResponse,
    SopRowResponse,
    SopSheetResponse,
)

COMMON_ESCALATION_KEY = "escalation"


def _merchant_to_response(merchant) -> MerchantResponse:
    return MerchantResponse(id=merchant.id, name=merchant.name, classification=merchant.classification)


def _row_to_response(row) -> SopRowResponse:
    return SopRowResponse(id=row.id, data=row.data)


def _build_sheet_response(db: Session, sheet: SopSheet) -> SopSheetResponse:
    """Assembles the full group/column tree for one sheet, in database
    sort_order, without hardcoding any group or column name."""
    groups = sop_repository.get_groups_for_sheet(db, sheet.id)
    columns = sop_repository.get_columns_for_groups(db, [g.id for g in groups])

    columns_by_group_id = {}
    for column in columns:
        columns_by_group_id.setdefault(column.group_id, []).append(column)

    return SopSheetResponse(
        id=sheet.id,
        key=sheet.key,
        name=sheet.name,
        groups=[
            SopColumnGroupResponse(
                id=group.id,
                label=group.label,
                sortOrder=group.sort_order,
                columns=[
                    SopColumnResponse(id=col.id, name=col.name, sortOrder=col.sort_order)
                    for col in columns_by_group_id.get(group.id, [])
                ],
            )
            for group in groups
        ],
    )


def _require_merchant(db: Session, merchant_id: int):
    merchant = merchant_repository.get_by_id(db, merchant_id)
    if merchant is None:
        raise NotFoundError(f"No merchant found for id={merchant_id}.", code="MERCHANT_NOT_FOUND")
    return merchant


def _require_merchant_sheet(db: Session, *, merchant_id: int, sheet_key: str) -> SopSheet:
    sheet = sop_repository.get_merchant_sheet(db, merchant_id=merchant_id, key=sheet_key)
    if sheet is None:
        raise NotFoundError(
            f"No SOP sheet with key={sheet_key!r} found for merchant id={merchant_id}.",
            code="SOP_SHEET_NOT_FOUND",
        )
    return sheet


def get_merchant_sheet(db: Session, *, merchant_id: int, sheet_key: str) -> MerchantSheetDetailResponse:
    merchant = _require_merchant(db, merchant_id)
    sheet = _require_merchant_sheet(db, merchant_id=merchant_id, sheet_key=sheet_key)

    return MerchantSheetDetailResponse(
        merchant=_merchant_to_response(merchant),
        sheet=_build_sheet_response(db, sheet),
    )


def list_merchant_sheet_rows(
    db: Session,
    *,
    merchant_id: int,
    sheet_key: str,
    page: int,
    page_size: int,
    search: Optional[str] = None,
) -> PaginatedResponse[SopRowResponse]:
    _require_merchant(db, merchant_id)
    sheet = _require_merchant_sheet(db, merchant_id=merchant_id, sheet_key=sheet_key)

    rows, total = sop_repository.list_rows_for_sheet(
        db, sheet_id=sheet.id, search=search, page=page, page_size=page_size
    )
    return PaginatedResponse[SopRowResponse](
        items=[_row_to_response(r) for r in rows],
        total=total,
        page=page,
        pageSize=page_size,
    )


def get_common_escalation(
    db: Session, *, page: int, page_size: int, search: Optional[str] = None
) -> CommonEscalationResponse:
    sheet = sop_repository.get_shared_sheet(db, key=COMMON_ESCALATION_KEY)
    if sheet is None:
        raise NotFoundError(
            f"No shared default SOP sheet found for key={COMMON_ESCALATION_KEY!r}.",
            code="SOP_SHEET_NOT_FOUND",
        )

    rows, total = sop_repository.list_rows_for_sheet(
        db, sheet_id=sheet.id, search=search, page=page, page_size=page_size
    )
    return CommonEscalationResponse(
        sheet=_build_sheet_response(db, sheet),
        rows=PaginatedResponse[SopRowResponse](
            items=[_row_to_response(r) for r in rows],
            total=total,
            page=page,
            pageSize=page_size,
        ),
    )
