"""
Repository for `instances` — SQLAlchemy queries only. No business rules,
no response-schema mapping, no HTTP concerns live here. Same pattern as
app/repositories/merchant_repository.py (the closest existing analog:
a simple, mostly-static lookup entity with a list endpoint).

Index usage:
  - `search`'s exact `status` filter uses `ix_instances_status`.
  - `search`'s free-text `search` filter is a substring ILIKE on `name`
    — no trigram index (unlike Merchant/BinRecord/User/Revision): this
    table is expected to stay small (a handful to a few dozen rows), so
    a plain ILIKE needs no index-backed acceleration; adding pg_trgm
    infrastructure for it would be premature per this task's "do not add
    unnecessary fields/complexity" instruction.
  - `updated_by_user` is eager-loaded via `joinedload` (one LEFT OUTER
    JOIN) for the same N+1-avoidance reason as BinRecord's
    `updated_by_user` (see app/repositories/bin_repository.py).
"""
from typing import List, Optional, Tuple

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session, joinedload

from app.models.instance import Instance

SORT_FIELD_MAP = {
    "name": Instance.name,
    "status": Instance.status,
    "id": Instance.id,
}


def search(
    session: Session,
    *,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Instance], int]:
    """Filters, sorts, and paginates instances. Returns (items, total).

    Default sort is by NAME (not id, unlike every other list endpoint in
    this project) — an explicit, deliberate choice for this endpoint per
    the task's instruction ("preferably by instance name"), since the
    primary consumer is a dropdown where alphabetical order is the
    useful default.
    """
    stmt = select(Instance).options(joinedload(Instance.updated_by_user))

    if search:
        stmt = stmt.where(Instance.name.ilike(f"%{search.strip()}%"))
    if status:
        stmt = stmt.where(Instance.status == status)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, Instance.name)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not Instance.id:
        # Deterministic tiebreaker — without this, pagination across
        # pages is not guaranteed stable when sorting by a non-unique
        # field.
        order_clauses.append(Instance.id)
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0
