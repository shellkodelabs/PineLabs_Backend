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
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.auth import CurrentUser, get_current_user
from app.schemas.bin_series import (
    BinRecordResponse,
    CreateBinRecordRequest,
    DeleteBinRecordResponse,
    UpdateBinRecordRequest,
)
from app.schemas.common import PaginatedResponse
from app.services import bin_service

router = APIRouter()

SortField = Literal["issuer", "cardProgramGroupName", "binIin", "merchantPrefix", "id"]
SortOrder = Literal["asc", "desc"]


@router.get("", response_model=PaginatedResponse[BinRecordResponse])
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


@router.get("/resolve", response_model=BinRecordResponse)
def resolve_bin_series(
    binIin: str = Query(..., pattern=r"^\d{6}$", description="Exactly 6 digits"),
    merchantPrefix: str = Query(..., pattern=r"^\d{3}$", description="Exactly 3 digits"),
    db: Session = Depends(get_db),
) -> BinRecordResponse:
    """Resolves the single BIN record matching an exact (binIin, merchantPrefix)
    pair. Returns the project's standard 404 error envelope if none exists —
    never a partial-match list."""
    return bin_service.resolve_bin(db, bin_iin=binIin, merchant_prefix=merchantPrefix)


@router.post("", response_model=BinRecordResponse, status_code=status.HTTP_201_CREATED)
def create_bin_record(
    payload: CreateBinRecordRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BinRecordResponse:
    """Creates a BIN record and records a "create" audit revision
    attributed to the authenticated caller, in one transaction."""
    return bin_service.create_bin_record(db, payload, actor_user_id=actor.id)


@router.put("/{binRecordId}", response_model=BinRecordResponse)
def update_bin_record(
    payload: UpdateBinRecordRequest,
    binRecordId: int = Path(..., ge=1, description="BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BinRecordResponse:
    """Partial update. Records an "update" audit revision (attributed to
    the authenticated caller) only if something actually changed."""
    return bin_service.update_bin_record(db, bin_record_id=binRecordId, payload=payload, actor_user_id=actor.id)


@router.delete("/{binRecordId}", response_model=DeleteBinRecordResponse)
def delete_bin_record(
    binRecordId: int = Path(..., ge=1, description="BIN record id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteBinRecordResponse:
    """Deletes a BIN record. Records a "delete" audit revision attributed
    to the authenticated caller, preserving the deleted record's id as
    the revision's entity_id."""
    return bin_service.delete_bin_record(db, bin_record_id=binRecordId, actor_user_id=actor.id)
