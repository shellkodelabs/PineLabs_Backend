"""
Wallet Bin Series router — structural mirror of
app/api/v1/bin_series_gift_card.py (see that module's docstring for the
full reasoning behind every convention below) wired to
app.services.wallet_bin_service instead. Mounted at "/bin-series/wallet"
(see app/api/v1/router.py) — no path collision with the Gift Card
router, the old bin_series.router, or the new shared router.
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.auth import CurrentUser, get_current_user
from app.schemas.bin_series_shared import BinBulkUploadResponse, BinRecordDeleteResponse, BinStatusUpdateRequest
from app.schemas.common import PaginatedResponse
from app.schemas.wallet_bin_custom_column import (
    CreateWalletCustomColumnRequest,
    RenameWalletCustomColumnRequest,
    WalletCustomColumnResponse,
)
from app.schemas.wallet_bin_series import (
    CreateWalletBinRecordRequest,
    UpdateWalletBinRecordRequest,
    WalletBinRecordResponse,
    WalletBinSeriesStatsResponse,
    WalletBulkUploadRequest,
)
from app.services import bin_series_upload_parser, wallet_bin_custom_column_service, wallet_bin_service

router = APIRouter()

SortField = Literal[
    "instance", "issuer", "merchant", "walletProgramName", "binIin", "merchantPrefix",
    "walletProgramGroupType", "ticketNumber", "status", "id",
]
SortOrder = Literal["asc", "desc"]
StatusFilter = Literal["Active", "Inactive"]


@router.get("/list", response_model=PaginatedResponse[WalletBinRecordResponse])
def list_wallet_bin_records(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(None, min_length=1, description="Substring match across every field, including custom column values"),
    status: Optional[StatusFilter] = Query(None, description="Exact status filter — Active or Inactive"),
    sortBy: Optional[SortField] = Query(None, description="Field to sort by (default: id)"),
    sortOrder: SortOrder = Query("asc"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[WalletBinRecordResponse]:
    """Paginated, searchable, filterable, sortable list of Wallet BIN records."""
    return wallet_bin_service.list_wallet_bin_records(
        db, page=page, page_size=pageSize, search=search, status=status, sort_by=sortBy, sort_order=sortOrder
    )


@router.post("/create", response_model=WalletBinRecordResponse, status_code=status.HTTP_201_CREATED)
def create_wallet_bin_record(
    payload: CreateWalletBinRecordRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WalletBinRecordResponse:
    """Creates a Wallet BIN record (always starts status='Active') and
    records a "create" audit revision. Checks cross-table (bin_iin,
    merchant_prefix) uniqueness against Gift Card records too."""
    return wallet_bin_service.create_wallet_bin_record(db, payload, actor_user_id=actor.id)


@router.put("/{id}/update", response_model=WalletBinRecordResponse)
def update_wallet_bin_record(
    payload: UpdateWalletBinRecordRequest,
    id: int = Path(..., ge=1, description="Wallet BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WalletBinRecordResponse:
    """Partial update. Never changes `status` (see PATCH /{id}/status)."""
    return wallet_bin_service.update_wallet_bin_record(db, record_id=id, payload=payload, actor_user_id=actor.id)


@router.delete("/{id}/delete", response_model=BinRecordDeleteResponse)
def delete_wallet_bin_record(
    id: int = Path(..., ge=1, description="Wallet BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BinRecordDeleteResponse:
    """Deletes a Wallet BIN record. Records a "delete" audit revision."""
    return wallet_bin_service.delete_wallet_bin_record(db, record_id=id, actor_user_id=actor.id)


@router.patch("/{id}/status", response_model=WalletBinRecordResponse)
def update_wallet_bin_status(
    payload: BinStatusUpdateRequest,
    id: int = Path(..., ge=1, description="Wallet BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WalletBinRecordResponse:
    """The ONLY endpoint that ever changes `status` — requires a
    mandatory, pattern-validated `ticketNumber`. Records an "update"
    audit revision (see app.services.wallet_bin_service's module
    docstring for the action_type reasoning)."""
    return wallet_bin_service.update_wallet_bin_status(db, record_id=id, payload=payload, actor_user_id=actor.id)


@router.post("/bulk-upload", response_model=BinBulkUploadResponse)
async def bulk_upload_wallet_bin_records(
    mode: Literal["updateExisting", "addNew"] = Form(..., description="addNew or updateExisting"),
    file: UploadFile = File(..., description="A .xlsx or .csv file of Wallet BIN rows (first row = headers)"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BinBulkUploadResponse:
    """Backs the frontend's "Import Data" upload flow for the Wallet
    tab — multipart/form-data: a `mode` form field plus a `file` field
    (see app.api.v1.bin_series_gift_card's identical endpoint for why
    `file` was chosen — no existing frontend upload call exists yet to
    match a field name against).

    The file is parsed server-side (app.services.bin_series_upload_parser)
    into the SAME row shape the JSON-body upload always used, then handed
    to the UNCHANGED business logic: mode="addNew" requires every
    non-custom field on each row; mode="updateExisting" matches rows by
    issuer name only, scoped to Wallet records. Partial success: one bad
    ROW never fails the whole batch — only a bad FILE is rejected
    outright."""
    content = await file.read()
    rows = bin_series_upload_parser.parse_wallet_upload_file(file.filename or "", content)
    payload = WalletBulkUploadRequest(mode=mode, rows=rows)
    return wallet_bin_service.bulk_upload_wallet_bin_records(db, payload, actor_user_id=actor.id)


@router.get("/export")
def export_wallet_bin_records(
    search: Optional[str] = Query(None, min_length=1, description="Same substring search as GET .../list"),
    status: Optional[StatusFilter] = Query(None, description="Exact status filter — Active or Inactive"),
    db: Session = Depends(get_db),
) -> Response:
    """Returns EVERY matching Wallet BIN record as a CSV document — no
    pagination. See app.services.wallet_bin_service's module docstring
    for the column set/order/header-label rules."""
    csv_text = wallet_bin_service.export_wallet_bin_records_csv(db, search=search, status=status)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bin-series-wallet.csv"'},
    )


@router.get("/statistics", response_model=WalletBinSeriesStatsResponse)
def get_wallet_bin_series_stats(db: Session = Depends(get_db)) -> WalletBinSeriesStatsResponse:
    """Total records / distinct issuers / distinct wallet program names
    across ALL wallet_bin_records — not paginated/filtered, always a
    whole-table aggregate."""
    return wallet_bin_service.get_wallet_bin_series_stats(db)


# =====================================================================
# Dynamic/Custom Columns — Wallet registry only, independent of the
# Gift Card one. No delete endpoint, no reorder endpoint.
# =====================================================================


@router.get("/custom-columns", response_model=List[WalletCustomColumnResponse])
def list_wallet_custom_columns(db: Session = Depends(get_db)) -> List[WalletCustomColumnResponse]:
    """All Wallet custom columns, in display order (creation sequence)."""
    return wallet_bin_custom_column_service.list_columns(db)


@router.post("/custom-columns/create", response_model=WalletCustomColumnResponse, status_code=status.HTTP_201_CREATED)
def create_wallet_custom_column(
    payload: CreateWalletCustomColumnRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WalletCustomColumnResponse:
    """Creates a new Wallet custom column and, if `defaultValue` is
    supplied, backfills it onto every EXISTING wallet_bin_records row in
    the same transaction. Records a "create" audit revision
    (entityType=wallet_bin_custom_column)."""
    return wallet_bin_custom_column_service.create_column(db, payload, actor_user_id=actor.id)


@router.patch("/custom-columns/{id}/rename", response_model=WalletCustomColumnResponse)
def rename_wallet_custom_column(
    payload: RenameWalletCustomColumnRequest,
    id: int = Path(..., ge=1, description="Wallet custom column id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WalletCustomColumnResponse:
    """Renames a Wallet custom column's display name only — its `key`
    (and therefore every stored custom_fields value) is untouched."""
    return wallet_bin_custom_column_service.rename_column(db, column_id=id, payload=payload, actor_user_id=actor.id)
