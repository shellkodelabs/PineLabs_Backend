"""
Repository for `merchants` and merchant-owned `sop_sheets` — SQLAlchemy
queries only. No business rules, no response-schema mapping, no HTTP
concerns live here.

Index usage:
  - `search`'s exact `classification` filter uses `ix_merchants_classification`.
  - `search`'s free-text `search` filter is a substring ILIKE on `name`;
    the trigram index `ix_merchants_name_trgm` accelerates this.
  - `get_by_name` is an exact, case-insensitive match — also benefits
    from the trigram index for equality-style lookups, and is backed by
    the `uq_merchants_name` unique constraint (at most one row can match).
  - `list_sheets_for_merchant` filters `sop_sheets.merchant_id = :id`,
    which uses `ix_sop_sheets_merchant_id`. Because merchant_id is an
    exact integer comparison, rows with merchant_id IS NULL (the shared
    default sheets) are automatically excluded by SQL's NULL semantics
    — no extra "IS NOT NULL" clause is needed.
"""
from typing import List, Optional, Tuple

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.models.merchant import Merchant
from app.models.sop import SopSheet

SORT_FIELD_MAP = {
    "name": Merchant.name,
    "classification": Merchant.classification,
    "id": Merchant.id,
}


def get_by_id(session: Session, merchant_id: int) -> Optional[Merchant]:
    return session.get(Merchant, merchant_id)


def get_by_name(session: Session, name: str) -> Optional[Merchant]:
    """Exact, case-insensitive match only — never a partial match. Safe
    to return a single row because merchants.name is unique."""
    stmt = select(Merchant).where(func.lower(Merchant.name) == name.strip().lower())
    return session.execute(stmt).scalar_one_or_none()


def search(
    session: Session,
    *,
    search: Optional[str] = None,
    classification: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Merchant], int]:
    """Filters, sorts, and paginates merchants. Returns (items, total)."""
    stmt = select(Merchant)

    if search:
        stmt = stmt.where(Merchant.name.ilike(f"%{search.strip()}%"))
    if classification:
        stmt = stmt.where(Merchant.classification == classification)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, Merchant.id)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not Merchant.id:
        # Deterministic tiebreaker for stable pagination across pages.
        order_clauses.append(Merchant.id)
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0


def list_sheets_for_merchant(session: Session, merchant_id: int) -> List[SopSheet]:
    """Merchant-owned sheets ONLY (merchant_id = :id) — shared/default
    sheets (merchant_id IS NULL) are never included. Ordered by id, which
    matches the original frontend subsheet order (Part 6 imported each
    merchant's subsheets in their source array order)."""
    stmt = select(SopSheet).where(SopSheet.merchant_id == merchant_id).order_by(SopSheet.id)
    return list(session.execute(stmt).scalars().all())
