"""
Service layer for Revision History (read-only). Orchestrates
app.repositories.revision_repository and maps its row tuples to
app.schemas.revision.RevisionResponse — no SQLAlchemy usage, no
HTTP-layer concerns.

This is deliberately the only function in this module: list_revisions().
No create/update/delete — automatic audit-event creation from the other
services (user/bin/merchant/sop) is explicitly out of scope for this
part (a later part), and this module does not touch them.
"""
from typing import Optional

from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.repositories import revision_repository
from app.schemas.common import PaginatedResponse
from app.schemas.revision import RevisionResponse, RevisionUserResponse


def _row_to_response(row: Row) -> RevisionResponse:
    """Maps one repository row to a RevisionResponse. `user` is None
    whenever the row's user_id is None — i.e. whenever the LEFT OUTER
    JOIN found no matching user. Under the current schema
    (revisions.user_id NOT NULL), this can never actually happen via a
    real row; see app/schemas/revision.py's module docstring. The
    null-safe branch below is exercised directly by a dedicated unit
    test (tests/integration/test_revisions_api.py) rather than a DB
    round-trip, since no such row can be inserted."""
    user = None
    if row.user_id is not None:
        user = RevisionUserResponse(id=row.user_id, name=row.user_name, email=row.user_email)

    return RevisionResponse(
        id=row.id,
        timestamp=row.occurred_at,
        user=user,
        action=row.action_type,
        entity=row.entity_type,
        target=row.target_label,
        change=row.change_description,
    )


def list_revisions(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    action_type: Optional[str] = None,
    entity_type: Optional[str] = None,
    user_id: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
) -> PaginatedResponse[RevisionResponse]:
    rows, total = revision_repository.search(
        db,
        search=search,
        action_type=action_type,
        entity_type=entity_type,
        user_id=user_id,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse[RevisionResponse](
        items=[_row_to_response(r) for r in rows], total=total, page=page, pageSize=page_size
    )
