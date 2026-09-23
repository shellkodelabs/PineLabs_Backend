"""
Revision History router (READ-ONLY) — HTTP concerns only: parses/
validates query parameters, injects the DB session, calls the service
layer, returns its result. No SQLAlchemy usage and no business logic
here — same pattern as every prior domain router.

No authentication dependency is wired up yet (explicitly out of scope).
No POST/write endpoint here — this part is read-only by design; audit
event creation from the other services is a later part.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.revision import ACTION_TYPE_VALUES, ENTITY_TYPE_VALUES
from app.schemas.common import PaginatedResponse
from app.schemas.revision import RevisionResponse
from app.services import revision_service

router = APIRouter()

SortField = Literal["occurred_at", "action", "entity", "target"]
SortOrder = Literal["asc", "desc"]
ActionTypeFilter = Literal[ACTION_TYPE_VALUES]
EntityTypeFilter = Literal[ENTITY_TYPE_VALUES]


@router.get("", response_model=PaginatedResponse[RevisionResponse])
def list_revisions(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(
        None,
        min_length=1,
        description="Substring match across target, change, and the associated user's name/email",
    ),
    actionType: Optional[ActionTypeFilter] = Query(None, description="Exact action type filter"),
    entityType: Optional[EntityTypeFilter] = Query(None, description="Exact entity type filter"),
    userId: Optional[int] = Query(None, ge=1, description="Exact revisions.user_id filter"),
    sortBy: Optional[SortField] = Query(None, description="Field to sort by (default: occurred_at)"),
    sortOrder: SortOrder = Query("desc", description="Default: desc"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[RevisionResponse]:
    """Paginated, searchable, filterable, sortable revision history.
    Filters combine with AND semantics. A userId with no matching
    revisions returns an empty page (total=0), not a 404 — this is a
    list filter, not a resource lookup."""
    return revision_service.list_revisions(
        db,
        page=page,
        page_size=pageSize,
        search=search,
        action_type=actionType,
        entity_type=entityType,
        user_id=userId,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
