"""
User Management router — HTTP concerns only: parses/validates
query/path/body parameters, injects the DB session, calls the service
layer, returns its result. No SQLAlchemy usage and no business logic
here — same pattern as bin_series.py/merchants.py/sop.py.

Authentication is enforced at inclusion time for this entire router
(see app/api/v1/router.py's `dependencies=[Depends(get_current_user)]`),
so every endpoint here already requires a valid authenticated caller
before its body ever runs.

ACTOR IDENTITY (Part 14 — supersedes Part 13's temporary `actorUserId`
query parameter, which has been REMOVED, not merely deprecated; it is no
longer read anywhere in this file, and passing it now has zero effect).
POST/PUT/DELETE obtain the acting user directly from
`Depends(get_current_user)` and pass `actor.id` to the service as
`actor_user_id` — the service layer's signature is unchanged from Part
13 (`actor_user_id: int`), so app/services/user_service.py and
app/services/audit_service.py required no changes at all for this
swap. There is no fallback to a default/query-supplied actor: if
authentication fails, the request never reaches these functions.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.auth import CurrentUser, get_current_user
from app.models.user import ROLE_VALUES, STATUS_VALUES
from app.schemas.common import PaginatedResponse
from app.schemas.user import (
    CreateUserRequest,
    DeleteUserResponse,
    ManageUserRequest,
    ManageUserResponse,
    UpdateUserRequest,
    UserAccessResponse,
    UserResponse,
)
from app.services import user_service

router = APIRouter()

SortField = Literal["name", "email", "role", "status", "id"]
SortOrder = Literal["asc", "desc"]
RoleFilter = Literal[ROLE_VALUES]
StatusFilter = Literal[STATUS_VALUES]


@router.get("", response_model=PaginatedResponse[UserResponse])
def list_users(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(None, min_length=1, description="Substring match across name, email, mobile"),
    role: Optional[RoleFilter] = Query(None, description="Exact role filter"),
    status_filter: Optional[StatusFilter] = Query(None, alias="status", description="Exact status filter"),
    sortBy: Optional[SortField] = Query(None, description="Field to sort by (default: id)"),
    sortOrder: SortOrder = Query("asc"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[UserResponse]:
    """Paginated, searchable, filterable, sortable list of users."""
    return user_service.list_users(
        db,
        page=page,
        page_size=pageSize,
        search=search,
        role=role,
        status=status_filter,
        sort_by=sortBy,
        sort_order=sortOrder,
    )


@router.post("/manage", response_model=ManageUserResponse)
def manage_user(
    payload: ManageUserRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ManageUserResponse:
    """Consolidated CRUD endpoint — one route handling create, update,
    and delete, selected by the request body's `operation` field. This is
    an alternative to the dedicated POST/PUT/DELETE verbs (which remain
    available); it delegates to the exact same service logic, so
    validation, SOP-access resolution, audit logging, and transaction
    semantics are identical.

    Body shapes:
      {"operation":"create","data":{...CreateUserRequest...}}
      {"operation":"update","userId":N,"data":{...UpdateUserRequest...}}
      {"operation":"delete","userId":N}

    MUST be declared before GET /{userId} so "manage" isn't captured as a
    user id."""
    return user_service.manage_user(db, payload, actor_user_id=actor.id)


@router.get("/{userId}", response_model=UserAccessResponse)
def get_user(
    userId: int = Path(..., ge=1, description="User id"),
    db: Session = Depends(get_db),
) -> UserAccessResponse:
    """One user plus their resolved SOP sheet access (empty dict if none)."""
    return user_service.get_user(db, user_id=userId)


@router.post("", response_model=UserAccessResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserAccessResponse:
    """Creates a user and any requested SOP sheet access grants, and
    records a "create" audit revision attributed to the authenticated
    caller, all in one transaction — if any access entry (or the
    revision itself) fails, nothing is created."""
    return user_service.create_user(db, payload, actor_user_id=actor.id)


@router.put("/{userId}", response_model=UserAccessResponse)
def update_user(
    payload: UpdateUserRequest,
    userId: int = Path(..., ge=1, description="User id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserAccessResponse:
    """Partial update. If `access` is supplied, it replaces the complete
    access state; if omitted, existing access is left untouched. Records
    an "update" audit revision (attributed to the authenticated caller)
    only if something actually changed."""
    return user_service.update_user(db, user_id=userId, payload=payload, actor_user_id=actor.id)


@router.delete("/{userId}", response_model=DeleteUserResponse)
def delete_user(
    userId: int = Path(..., ge=1, description="User id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteUserResponse:
    """Deletes a user. Their SOP sheet access is cascade-deleted by the
    existing FK. If the user is still referenced by revision history, the
    delete is refused (409) rather than silently cascading through that
    FK too — see app/services/user_service.py. Records a "delete" audit
    revision attributed to the authenticated caller (never the deleted
    user)."""
    return user_service.delete_user(db, user_id=userId, actor_user_id=actor.id)
