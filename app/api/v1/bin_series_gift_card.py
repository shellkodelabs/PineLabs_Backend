"""
Gift Card Bin Series router — HTTP concerns only: parses/validates
query/path/body parameters, injects the DB session, calls
app.services.gift_card_bin_service, returns its result. No SQLAlchemy
usage and no business logic here — structural mirror of the old,
untouched app/api/v1/bin_series.py (see that module's docstring for the
base conventions reused verbatim below: page/pageSize validated directly
via Query(..., ge=..., le=...) rather than app.api.deps.get_pagination_params
— which CLAMPS instead of rejecting — actor obtained via
Depends(get_current_user) and passed as actor_user_id, no SQLAlchemy/
business logic in the router layer).

Mounted at "/bin-series/gift-card" (see app/api/v1/router.py) — does not
collide with the old bin_series.router's paths (all mounted directly
under "/bin-series", e.g. "/bin-series/list") or with the new shared
router's combined "/bin-series/lookup" etc. — every route below is
under a distinct "/bin-series/gift-card/..." prefix.

New Excel-format Bin Series task, Stage 6 (routers only). Route paths
follow this stage's explicit instruction (list/create/{id}/update/
{id}/delete/{id}/status/bulk-upload/export/statistics, plus
custom-columns/list/create/{id}/rename) rather than the old router's
exact sub-path spelling — a deliberate, task-directed naming for this
new architecture, not an inconsistency.

STATUS CODES: POST /create and POST /custom-columns/create return 201
(matches the old router's own convention for creation endpoints);
DELETE /{id}/delete returns 200 with a BinRecordDeleteResponse body,
not 204 — same convention as the old router (the frontend's delete flow
expects a JSON body back, not an empty response). Every AppError
subclass raised by the service layer (NotFoundError/ConflictError/
ValidationError) propagates to the project's global error handler (see
app/core/error_handlers.py, registered in app/main.py) — no per-route
try/except here, same as every other router in this project.
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.auth import CurrentUser, get_current_user
from app.schemas.bin_series_shared import BinBulkUploadResponse, BinRecordDeleteResponse, BinStatusUpdateRequest
from app.schemas.common import PaginatedResponse
from app.schemas.gift_card_bin_custom_column import (
    CreateGiftCardCustomColumnRequest,
    GiftCardCustomColumnResponse,
    RenameGiftCardCustomColumnRequest,
)
from app.schemas.gift_card_bin_series import (
    CreateGiftCardBinRecordRequest,
    GiftCardBinRecordResponse,
    GiftCardBinSeriesStatsResponse,
    GiftCardBulkUploadRequest,
    UpdateGiftCardBinRecordRequest,
)
from app.services import bin_series_upload_parser, gift_card_bin_custom_column_service, gift_card_bin_service

router = APIRouter()

SortField = Literal[
    "instance", "issuer", "merchant", "cardProgramGroupName", "binIin", "merchantPrefix",
    "cardProgramGroupType", "cardType", "ticketNumber", "status", "id",
]
SortOrder = Literal["asc", "desc"]
StatusFilter = Literal["Active", "Inactive"]


@router.get("/list", response_model=PaginatedResponse[GiftCardBinRecordResponse])
def list_gift_card_bin_records(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(None, min_length=1, description="Substring match across every field, including custom column values"),
    status: Optional[StatusFilter] = Query(None, description="Exact status filter — Active or Inactive"),
    sortBy: Optional[SortField] = Query(None, description="Field to sort by (default: id)"),
    sortOrder: SortOrder = Query("asc"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[GiftCardBinRecordResponse]:
    """Paginated, searchable, filterable, sortable list of Gift Card BIN records."""
    return gift_card_bin_service.list_gift_card_bin_records(
        db, page=page, page_size=pageSize, search=search, status=status, sort_by=sortBy, sort_order=sortOrder
    )


@router.post("/create", response_model=GiftCardBinRecordResponse, status_code=status.HTTP_201_CREATED)
def create_gift_card_bin_record(
    payload: CreateGiftCardBinRecordRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GiftCardBinRecordResponse:
    """Creates a Gift Card BIN record (always starts status='Active') and
    records a "create" audit revision attributed to the authenticated
    caller, in one transaction. Checks cross-table (bin_iin,
    merchant_prefix) uniqueness against Wallet records too."""
    return gift_card_bin_service.create_gift_card_bin_record(db, payload, actor_user_id=actor.id)


@router.put("/{id}/update", response_model=GiftCardBinRecordResponse)
def update_gift_card_bin_record(
    payload: UpdateGiftCardBinRecordRequest,
    id: int = Path(..., ge=1, description="Gift Card BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GiftCardBinRecordResponse:
    """Partial update. Never changes `status` (see PATCH /{id}/status).
    Records an "update" audit revision only if something actually
    changed."""
    return gift_card_bin_service.update_gift_card_bin_record(db, record_id=id, payload=payload, actor_user_id=actor.id)


@router.delete("/{id}/delete", response_model=BinRecordDeleteResponse)
def delete_gift_card_bin_record(
    id: int = Path(..., ge=1, description="Gift Card BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BinRecordDeleteResponse:
    """Deletes a Gift Card BIN record. Records a "delete" audit revision
    attributed to the authenticated caller."""
    return gift_card_bin_service.delete_gift_card_bin_record(db, record_id=id, actor_user_id=actor.id)


@router.patch("/{id}/status", response_model=GiftCardBinRecordResponse)
def update_gift_card_bin_status(
    payload: BinStatusUpdateRequest,
    id: int = Path(..., ge=1, description="Gift Card BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GiftCardBinRecordResponse:
    """The ONLY endpoint that ever changes `status` — requires a
    mandatory, pattern-validated `ticketNumber` (see
    app.schemas.bin_series_shared.BinStatusUpdateRequest), matching
    StatusConfirmModal.jsx's unconditional TicketField requirement for
    this flow. Records an "update" audit revision (see
    app.services.gift_card_bin_service's module docstring for why status
    changes reuse action_type="update" rather than a nonexistent
    "status_change" value)."""
    return gift_card_bin_service.update_gift_card_bin_status(db, record_id=id, payload=payload, actor_user_id=actor.id)


@router.post("/bulk-upload", response_model=BinBulkUploadResponse)
async def bulk_upload_gift_card_bin_records(
    mode: Literal["updateExisting", "addNew"] = Form(..., description="addNew or updateExisting"),
    file: UploadFile = File(..., description="A .xlsx or .csv file of Gift Card BIN rows (first row = headers)"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BinBulkUploadResponse:
    """Backs the frontend's "Import Data" upload flow for the Gift Card
    tab — multipart/form-data: a `mode` form field plus a `file` field
    (the actual uploaded .xlsx/.csv). No existing frontend upload call
    exists yet to match a field name against (confirmed by inspection —
    see app.services.bin_series_upload_parser's module docstring), so
    `file` was chosen as the FastAPI-conventional name for a single-file
    upload; align the frontend's eventual FormData key to this.

    The file is parsed server-side (app.services.bin_series_upload_parser)
    into the SAME row shape the JSON-body upload always used, then handed
    to the UNCHANGED business logic: mode="addNew" requires every
    non-custom field on each row; mode="updateExisting" matches rows by
    issuer name only, scoped to Gift Card records (see
    app.services.gift_card_bin_service's module docstring for the full
    matching-key reasoning). Partial success: one bad ROW never fails
    the whole batch — only a bad FILE (unreadable, empty, no detectable
    headers, too many rows) is rejected outright, before any row is
    even attempted."""
    content = await file.read()
    rows = bin_series_upload_parser.parse_gift_card_upload_file(file.filename or "", content)
    payload = GiftCardBulkUploadRequest(mode=mode, rows=rows)
    return gift_card_bin_service.bulk_upload_gift_card_bin_records(db, payload, actor_user_id=actor.id)


@router.get("/export")
def export_gift_card_bin_records(
    search: Optional[str] = Query(None, min_length=1, description="Same substring search as GET .../list"),
    status: Optional[StatusFilter] = Query(None, description="Exact status filter — Active or Inactive"),
    db: Session = Depends(get_db),
) -> Response:
    """Returns EVERY matching Gift Card BIN record as a CSV document —
    no pagination, matches the old export button's "full dataset"
    behavior. See app.services.gift_card_bin_service's module docstring
    for the column set/order/header-label rules."""
    csv_text = gift_card_bin_service.export_gift_card_bin_records_csv(db, search=search, status=status)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bin-series-gift-card.csv"'},
    )


@router.get("/statistics", response_model=GiftCardBinSeriesStatsResponse)
def get_gift_card_bin_series_stats(db: Session = Depends(get_db)) -> GiftCardBinSeriesStatsResponse:
    """Total records / distinct issuers / distinct card program names
    across ALL gift_card_bin_records — not paginated/filtered, always a
    whole-table aggregate."""
    return gift_card_bin_service.get_gift_card_bin_series_stats(db)


# =====================================================================
# Dynamic/Custom Columns — Gift Card registry only, independent of the
# Wallet one. No delete endpoint, no reorder endpoint — same reasoning
# as the old bin_series.py router's custom-columns section.
# =====================================================================


@router.get("/custom-columns", response_model=List[GiftCardCustomColumnResponse])
def list_gift_card_custom_columns(db: Session = Depends(get_db)) -> List[GiftCardCustomColumnResponse]:
    """All Gift Card custom columns, in display order (creation sequence)."""
    return gift_card_bin_custom_column_service.list_columns(db)


@router.post("/custom-columns/create", response_model=GiftCardCustomColumnResponse, status_code=status.HTTP_201_CREATED)
def create_gift_card_custom_column(
    payload: CreateGiftCardCustomColumnRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GiftCardCustomColumnResponse:
    """Creates a new Gift Card custom column and, if `defaultValue` is
    supplied, backfills it onto every EXISTING gift_card_bin_records row
    in the same transaction. Records a "create" audit revision
    (entityType=gift_card_bin_custom_column)."""
    return gift_card_bin_custom_column_service.create_column(db, payload, actor_user_id=actor.id)


@router.patch("/custom-columns/{id}/rename", response_model=GiftCardCustomColumnResponse)
def rename_gift_card_custom_column(
    payload: RenameGiftCardCustomColumnRequest,
    id: int = Path(..., ge=1, description="Gift Card custom column id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GiftCardCustomColumnResponse:
    """Renames a Gift Card custom column's display name only — its `key`
    (and therefore every stored custom_fields value) is untouched."""
    return gift_card_bin_custom_column_service.rename_column(db, column_id=id, payload=payload, actor_user_id=actor.id)
