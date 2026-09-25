"""
BIN Series router — HTTP concerns only: parses/validates query/path/body
parameters, injects the DB session, calls the service layer, returns its
result. No SQLAlchemy usage and no business logic here.

Note on page/pageSize: app.api.deps.get_pagination_params CLAMPS
out-of-range values instead of rejecting them, which is the wrong
behavior for this endpoint — invalid pagination values here are
expected to fail with a 422 (see the "invalid page/pageSize" test
case), not be silently corrected. So this router validates page/pageSize
directly via FastAPI's Query(..., ge=..., le=...) instead of using that
shared dependency.

Part 16 (Create/Update/Delete): authentication is enforced at inclusion
time for this entire router (see app/api/v1/router.py's
`dependencies=[Depends(get_current_user)]`). POST/PUT/DELETE additionally
depend on `get_current_user` directly to obtain the acting user and pass
`actor.id` to the service — same pattern as app/api/v1/users.py. There is
no actorUserId parameter and no fallback: if authentication fails, the
request never reaches these functions.

API Naming task: every route below was renamed to make its purpose
readable directly from the URL, per the task's explicit mapping — the
prefix ("/bin-series", set in app/api/v1/router.py) is unchanged, only
the sub-paths are. This was a pure rename: no request/response schema,
business logic, auth, or frontend behavior changed. Old -> new:
  GET    ""                   -> GET    /list
  GET    /resolve              -> GET    /lookup
  POST   /resolve-batch        -> POST   /bulk-lookup
  POST   ""                   -> POST   /create
  PUT    /{binRecordId}        -> PUT    /{binRecordId}/update
  DELETE /{binRecordId}        -> DELETE /{binRecordId}/delete
  GET    /stats                -> GET    /statistics
  GET    /export               -> GET    /export (unchanged — already clear)
  GET    /history               -> GET    /version-history
  GET    /columns              -> GET    /custom-columns
  POST   /columns              -> POST   /custom-columns/create
  PATCH  /columns/{columnId}   -> PATCH  /custom-columns/{columnId}/rename
  POST   /bulk-upload           -> POST   /bulk-upload (unchanged — already clear)
No path conflicts: every route is either a distinct static literal or
the sole route of its (method, static-prefix) shape, so registration
order doesn't matter here — see the naming task's completion report for
the explicit conflict check.
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.auth import CurrentUser, get_current_user
from app.schemas.bin_custom_column import CreateCustomColumnRequest, CustomColumnResponse, RenameCustomColumnRequest
from app.schemas.bin_series import (
    BinRecordResponse,
    BinSeriesStatsResponse,
    BulkUploadRequest,
    BulkUploadResponse,
    CreateBinRecordRequest,
    DeleteBinRecordResponse,
    ResolveBatchRequest,
    ResolveBatchResponse,
    UpdateBinRecordRequest,
)
from app.schemas.bin_series_history import BinSeriesHistoryResponse
from app.schemas.common import PaginatedResponse
from app.services import bin_custom_column_service, bin_series_history_service, bin_service

router = APIRouter()

SortField = Literal["issuer", "cardProgramGroupName", "binIin", "merchantPrefix", "id"]
SortOrder = Literal["asc", "desc"]


@router.get("/list", response_model=PaginatedResponse[BinRecordResponse])
def list_bin_series(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(
        None, min_length=1, description="Substring match across issuer, cardProgramGroupName, binIin, merchantPrefix"
    ),
    issuer: Optional[str] = Query(None, min_length=1, description="Exact, case-insensitive issuer filter"),
    cardProgramGroupName: Optional[str] = Query(
        None, min_length=1, description="Exact, case-insensitive card program group name filter"
    ),
    sortBy: Optional[SortField] = Query(None, description="Field to sort by (default: id)"),
    sortOrder: SortOrder = Query("asc"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[BinRecordResponse]:
    """Paginated, searchable, filterable, sortable list of BIN records."""
    return bin_service.list_bin_records(
        db,
        page=page,
        page_size=pageSize,
        search=search,
        issuer=issuer,
        card_program_group_name=cardProgramGroupName,
        sort_by=sortBy,
        sort_order=sortOrder,
    )


@router.get("/lookup", response_model=BinRecordResponse)
def resolve_bin_series(
    binIin: str = Query(..., pattern=r"^\d{6}$", description="Exactly 6 digits"),
    merchantPrefix: str = Query(..., pattern=r"^\d{3}$", description="Exactly 3 digits"),
    db: Session = Depends(get_db),
) -> BinRecordResponse:
    """Resolves the single BIN record matching an exact (binIin, merchantPrefix)
    pair. Returns the project's standard 404 error envelope if none exists —
    never a partial-match list."""
    return bin_service.resolve_bin(db, bin_iin=binIin, merchant_prefix=merchantPrefix)


@router.get("/export")
def export_bin_series(
    search: Optional[str] = Query(
        None, min_length=1, description="Same substring search as GET /api/v1/bin-series/list (issuer, cardProgramGroupName, binIin, merchantPrefix, instanceName, custom-field values)"
    ),
    issuer: Optional[str] = Query(None, min_length=1, description="Exact, case-insensitive issuer filter"),
    cardProgramGroupName: Optional[str] = Query(
        None, min_length=1, description="Exact, case-insensitive card program group name filter"
    ),
    db: Session = Depends(get_db),
) -> Response:
    """Backs the frontend's Export button (BinTable.jsx's exportSheet()).
    Returns EVERY matching BIN record as a CSV document — confirmed by
    frontend inspection that Export downloads the full dataset, never
    just the current page (no pagination params here, by design). The
    query parameters mirror GET /list's search/issuer/cardProgramGroupName
    exactly (same underlying repository filter, reused not duplicated)
    for forward integration, even though today's approved frontend sends
    none of them — see app/services/bin_service.py's module docstring
    for the full column-set/order/header-label reasoning."""
    csv_text = bin_service.export_bin_records_csv(
        db, search=search, issuer=issuer, card_program_group_name=cardProgramGroupName
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bin-series.csv"'},
    )


@router.get("/version-history", response_model=BinSeriesHistoryResponse)
def get_bin_series_history(
    limit: int = Query(
        5, ge=1, le=200, description="Max entries to return, newest first (default 5 — matches the frontend's 'last 5 changes' Version History panel)"
    ),
    db: Session = Depends(get_db),
) -> BinSeriesHistoryResponse:
    """Backs BinTable.jsx's Version History button (VersionHistoryModal.jsx
    / ChangePreviewModal.jsx). GLOBAL to the whole Bin Series table, not
    scoped to one record — confirmed by frontend inspection: there is no
    per-row history trigger anywhere (RowActionsMenu.jsx has no history
    action), just one table-wide button. Every entry already carries its
    full before/after snapshot when one was captured (see
    app/services/bin_series_history_service.py), so the frontend's
    click-to-preview needs no second request — there is deliberately no
    separate detail endpoint."""
    return bin_series_history_service.list_bin_series_history(db, limit=limit)


@router.get("/statistics", response_model=BinSeriesStatsResponse)
def get_bin_series_stats(db: Session = Depends(get_db)) -> BinSeriesStatsResponse:
    """Total records / distinct issuers / distinct card programs across
    ALL bin_records — backs the frontend's StatCards on the BIN Series
    page (Total Records, Issuers, Card Programs), today computed
    client-side from the full local mock array. Not paginated/filtered:
    always a whole-table aggregate, computed in the database."""
    return bin_service.get_bin_series_stats(db)


@router.post("/bulk-lookup", response_model=ResolveBatchResponse)
def resolve_bin_series_batch(
    payload: ResolveBatchRequest,
    db: Session = Depends(get_db),
) -> ResolveBatchResponse:
    """Bulk/multi-card resolve — backs BulkLookupModal.jsx (opened from
    both BinResolver.jsx and BinTable.jsx). Returns exactly one result
    per input card, in input order, including duplicates — never drops
    or errors on an individual unmatched/malformed card, only an
    explicit matched=false. No actor/audit — read-only, same as GET
    /lookup. See app/services/bin_service.py for the matching rule,
    which is a documented superset of GET /lookup's own rule (adds a
    "BIN uniquely identifies one record" shortcut when the prefix is
    partial/absent)."""
    return bin_service.resolve_bin_batch(db, payload.cards)


@router.post("/create", response_model=BinRecordResponse, status_code=status.HTTP_201_CREATED)
def create_bin_record(
    payload: CreateBinRecordRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BinRecordResponse:
    """Creates a BIN record and records a "create" audit revision
    attributed to the authenticated caller, in one transaction."""
    return bin_service.create_bin_record(db, payload, actor_user_id=actor.id)


@router.post("/bulk-upload", response_model=BulkUploadResponse)
def bulk_upload_bin_records(
    payload: BulkUploadRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkUploadResponse:
    """Backs the frontend's "Import Data" upload flow (BinTable ->
    UploadSheetModal.jsx): mode="addNew" inserts every row as a new BIN
    record; mode="updateExisting" matches rows by (binIin, merchantPrefix)
    when available, falling back to an unambiguous issuer-name match
    otherwise — see app/schemas/bin_series.py for the full matching-key
    reasoning — and only overwrites non-blank fields, skipping (never
    creating) rows with no match. Partial success: one bad row never
    fails the whole batch — see the response's failedRows/skippedRows."""
    return bin_service.bulk_upload_bin_records(db, payload, actor_user_id=actor.id)


@router.put("/{binRecordId}/update", response_model=BinRecordResponse)
def update_bin_record(
    payload: UpdateBinRecordRequest,
    binRecordId: int = Path(..., ge=1, description="BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BinRecordResponse:
    """Partial update. Records an "update" audit revision (attributed to
    the authenticated caller) only if something actually changed."""
    return bin_service.update_bin_record(db, bin_record_id=binRecordId, payload=payload, actor_user_id=actor.id)


@router.delete("/{binRecordId}/delete", response_model=DeleteBinRecordResponse)
def delete_bin_record(
    binRecordId: int = Path(..., ge=1, description="BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteBinRecordResponse:
    """Deletes a BIN record. Records a "delete" audit revision attributed
    to the authenticated caller, preserving the deleted record's id as
    the revision's entity_id."""
    return bin_service.delete_bin_record(db, bin_record_id=binRecordId, actor_user_id=actor.id)


# =====================================================================
# Dynamic/Custom Columns — backs BinTable.jsx's "Add Column" button and
# EditableHeader.jsx's rename. No delete endpoint: the frontend has no
# delete-column capability at all (confirmed by inspection — see
# app/models/bin_custom_column.py). No reorder endpoint either: no
# ordering UI exists in the frontend, order is pure creation sequence.
# =====================================================================


@router.get("/custom-columns", response_model=List[CustomColumnResponse])
def list_custom_columns(db: Session = Depends(get_db)) -> List[CustomColumnResponse]:
    """All Bin Series custom columns, in display order (creation
    sequence — matches BinTable.jsx's own column ordering, which always
    appends a new column after every existing one)."""
    return bin_custom_column_service.list_columns(db)


@router.post("/custom-columns/create", response_model=CustomColumnResponse, status_code=status.HTTP_201_CREATED)
def create_custom_column(
    payload: CreateCustomColumnRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomColumnResponse:
    """Creates a new custom column and, if `defaultValue` is supplied,
    backfills it onto every EXISTING bin_records row in the same
    transaction — matches AddColumnModal.jsx's "applied to all N
    existing records at once" behavior exactly. Records a "create" audit
    revision (entityType=bin_custom_column) attributed to the
    authenticated caller."""
    return bin_custom_column_service.create_column(db, payload, actor_user_id=actor.id)


@router.patch("/custom-columns/{columnId}/rename", response_model=CustomColumnResponse)
def rename_custom_column(
    payload: RenameCustomColumnRequest,
    columnId: int = Path(..., ge=1, description="Custom column id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomColumnResponse:
    """Renames a custom column's display name only — the column's `key`
    (and therefore every bin_records.custom_fields value stored under
    it) is completely untouched, matching BinTable.jsx's renameColumn()
    exactly. Records an "update" audit revision (entityType=
    bin_custom_column) only if the name actually changed."""
    return bin_custom_column_service.rename_column(db, column_id=columnId, payload=payload, actor_user_id=actor.id)
