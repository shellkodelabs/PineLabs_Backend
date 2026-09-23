"""
Repository for `sop_sheets`, `sop_column_groups`, `sop_columns`, and
`sop_rows` — SQLAlchemy queries only. No business rules (shared-sheet
fallback logic, not-found decisions, response mapping) live here — that
belongs to app/services/sop_service.py.

Query design:
  - `get_merchant_sheet` filters `merchant_id = :id` (a concrete integer),
    which by SQL NULL semantics can never match a shared/default sheet
    (merchant_id IS NULL) — there is no fallback here by construction.
  - `get_shared_sheet` is the only function that queries
    `merchant_id IS NULL`, used exclusively by the common-escalation
    endpoint's service call.
  - `get_groups_for_sheet` / `get_columns_for_groups` are two queries
    (not N+1 per group) — columns for every group of a sheet are fetched
    in one `IN (...)` query, ordered by (group_id, sort_order) so the
    service layer can bucket them by group_id in a single pass. Both
    honor sort_order per the task's ordering requirement.
  - `list_rows_for_sheet`'s `search` performs a database-side substring
    match across the row's VALUES only (via `jsonb_each_text`, matching
    the frontend's own `Object.values(row).some(...)` semantics — keys
    are not searched). This is expressed as a raw SQL EXISTS fragment
    (bound, not string-interpolated) rather than a plain jsonb->text
    cast + ILIKE, specifically so JSON syntax characters and key names
    never produce a false match — a cast-based approach would match
    those too. The existing GIN index on `data` (jsonb_ops) is NOT used
    by this query (GIN accelerates containment/key-existence operators,
    not substring ILIKE) — acceptable given `ix_sop_rows_sheet_id`
    already narrows to one sheet's rows first, and sheets hold at most
    ~370 rows (Part 2 analysis), so a per-sheet scan is cheap regardless.
"""
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.sop import SopColumn, SopColumnGroup, SopRow, SopSheet


def get_merchant_sheet(session: Session, *, merchant_id: int, key: str) -> Optional[SopSheet]:
    """Merchant-owned sheet only. Returns None if the merchant has no
    sheet with this key — including when `key` happens to also be the
    shared default's key (e.g. "escalation"); this NEVER falls back to
    the shared sheet."""
    stmt = select(SopSheet).where(SopSheet.merchant_id == merchant_id, SopSheet.key == key)
    return session.execute(stmt).scalar_one_or_none()


def get_shared_sheet(session: Session, *, key: str) -> Optional[SopSheet]:
    """The shared/default sheet for this key (merchant_id IS NULL). Used
    only by the common-escalation endpoint."""
    stmt = select(SopSheet).where(SopSheet.merchant_id.is_(None), SopSheet.key == key)
    return session.execute(stmt).scalar_one_or_none()


def get_groups_for_sheet(session: Session, sheet_id: int) -> List[SopColumnGroup]:
    stmt = (
        select(SopColumnGroup)
        .where(SopColumnGroup.sheet_id == sheet_id)
        .order_by(SopColumnGroup.sort_order, SopColumnGroup.id)
    )
    return list(session.execute(stmt).scalars().all())


def get_columns_for_groups(session: Session, group_ids: Sequence[int]) -> List[SopColumn]:
    if not group_ids:
        return []
    stmt = (
        select(SopColumn)
        .where(SopColumn.group_id.in_(group_ids))
        .order_by(SopColumn.group_id, SopColumn.sort_order, SopColumn.id)
    )
    return list(session.execute(stmt).scalars().all())


def list_rows_for_sheet(
    session: Session,
    *,
    sheet_id: int,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[SopRow], int]:
    """Paginated rows for one sheet, id ascending by default. `search`
    (if given) is a case-insensitive substring match across the row's
    stored VALUES, evaluated entirely in PostgreSQL."""
    stmt = select(SopRow).where(SopRow.sheet_id == sheet_id)

    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            text(
                "EXISTS ("
                "SELECT 1 FROM jsonb_each_text(sop_rows.data) AS row_kv(key, value) "
                "WHERE row_kv.value ILIKE :row_search_term"
                ")"
            ).bindparams(row_search_term=term)
        )

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    stmt = stmt.order_by(SopRow.id).offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0
