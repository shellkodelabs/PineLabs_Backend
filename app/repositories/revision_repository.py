"""
Repository for `revisions` — SQLAlchemy queries only. No business rules,
no response-schema mapping, no HTTP concerns live here.

Returns plain Row tuples (via an explicit column-list select), not ORM
`Revision` objects — deliberately, so the query can LEFT OUTER JOIN to
`users` and expose user_id/user_name/user_email as independently
NULL-able columns, matching exactly what the task's "user": null
requirement needs. Relying on the ORM's `Revision.user` relationship
instead would not give the same control over join type (and would
lazy-load per row rather than joining once).

Index usage:
  - `action_type`/`entity_type` exact filters use `ix_revisions_action_type`/
    `ix_revisions_entity_type`.
  - `user_id` exact filter uses `ix_revisions_user_id`.
  - Default sort (occurred_at DESC) uses `ix_revisions_occurred_at`.
  - `search` is an OR of ILIKE substring matches across target_label,
    change_description, and the joined user's name/email — the trigram
    index `ix_revisions_search` (target_label/change_description
    concatenation) partially accelerates this; the user name/email
    predicates aren't covered by any revisions-side index, but at the
    current data volume (10 seeded rows) this is a non-issue.

Version History task: `list_for_entity_types`/`count_for_entity_types`
back GET /api/v1/bin-series/version-history (renamed from /history by
the API Naming task) — an entity_type-scoped query
(`ix_revisions_entity_type` covers the `IN` filter) ordered by the same
`ix_revisions_occurred_at` index `search()`'s default sort already uses.
"""
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import Row, asc, desc, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.revision import Revision
from app.models.user import User

# Maps the API's sortBy values (per the task spec: occurred_at, action,
# entity, target — no "id", unlike other domains' repositories) to
# actual ORM columns.
SORT_FIELD_MAP = {
    "occurred_at": Revision.occurred_at,
    "action": Revision.action_type,
    "entity": Revision.entity_type,
    "target": Revision.target_label,
}


def search(
    session: Session,
    *,
    search: Optional[str] = None,
    action_type: Optional[str] = None,
    entity_type: Optional[str] = None,
    user_id: Optional[int] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[Sequence[Row], int]:
    """Filters, sorts, and paginates revisions. Returns (rows, total),
    where each row has attributes: id, occurred_at, action_type,
    entity_type, target_label, change_description, user_id, user_name,
    user_email (the last three all NULL together whenever the LEFT JOIN
    finds no matching user — never happens today given user_id's NOT
    NULL constraint, but the query is correct regardless)."""
    stmt = (
        select(
            Revision.id,
            Revision.occurred_at,
            Revision.action_type,
            Revision.entity_type,
            Revision.target_label,
            Revision.change_description,
            User.id.label("user_id"),
            User.name.label("user_name"),
            User.email.label("user_email"),
        )
        .select_from(Revision)
        .outerjoin(User, User.id == Revision.user_id)
    )

    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Revision.target_label.ilike(term),
                Revision.change_description.ilike(term),
                User.name.ilike(term),
                User.email.ilike(term),
            )
        )
    if action_type:
        stmt = stmt.where(Revision.action_type == action_type)
    if entity_type:
        stmt = stmt.where(Revision.entity_type == entity_type)
    if user_id is not None:
        stmt = stmt.where(Revision.user_id == user_id)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, Revision.occurred_at)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not Revision.id:
        # Deterministic tiebreaker — same direction as the primary sort,
        # per the task's requirement (revision id as tiebreaker).
        order_clauses.append(order_fn(Revision.id))
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = session.execute(stmt).all()

    return rows, total or 0


def list_for_entity_types(session: Session, *, entity_types: Sequence[str], limit: int) -> List[Revision]:
    """Backs GET /api/v1/bin-series/version-history (Version History
    task): newest-first (occurred_at DESC, id DESC tiebreaker — same tiebreaker
    convention as `search()` above) revisions whose entity_type is one
    of `entity_types` (bin_record, bin_custom_column). Returns full ORM
    `Revision` objects (unlike `search()`'s flattened Row tuples) because
    the caller needs `metadata_` — the before/after snapshot JSONB —
    which `search()`'s column-list SELECT deliberately omits (the
    generic revisions list has no use for it). `joinedload(Revision.user)`
    is a LEFT OUTER JOIN, avoiding one extra query per entry to resolve
    each entry's actor name."""
    stmt = (
        select(Revision)
        .options(joinedload(Revision.user))
        .where(Revision.entity_type.in_(entity_types))
        .order_by(Revision.occurred_at.desc(), Revision.id.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def count_for_entity_types(session: Session, *, entity_types: Sequence[str]) -> int:
    """Total matching revisions regardless of `limit` — informational
    only (there is no true pagination here, see
    app/services/bin_series_history_service.py's module docstring for
    why)."""
    stmt = select(func.count()).select_from(Revision).where(Revision.entity_type.in_(entity_types))
    return session.scalar(stmt) or 0
