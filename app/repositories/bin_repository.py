"""
Repository for `bin_records` — SQLAlchemy queries only. No business
rules, no response-schema mapping, no HTTP concerns live here.

Index usage:
  - `get_by_bin_and_prefix` filters on the exact (bin_iin, merchant_prefix)
    pair, which is exactly the column set behind `uq_bin_records_bin_prefix`
    — a fast, unique lookup.
  - `search`'s free-text `search` filter is an OR of ILIKE substring
    matches across all four searchable fields, per the task spec. Only
    the issuer/card_program_group_name pair is covered by the existing
    trigram index (`ix_bin_records_search`, an expression index over
    their concatenation) — bin_iin/merchant_prefix substring search
    falls back to a sequential scan. At the current data volume (~611
    rows) this is a non-issue; `ix_bin_records_bin_iin` still helps the
    common case of an exact/prefix bin_iin match. No new index was
    added — schema changes are out of scope for this part.
  - `search`'s exact `issuer`/`card_program_group_name` filters use
    case-insensitive equality (not substring), so they don't depend on
    the trigram index either way.

Part 16 (Create/Update/Delete): every read here (`search`,
`get_by_bin_and_prefix`, `get_by_id`) eager-loads `updated_by_user` via
`joinedload` — a single LEFT OUTER JOIN — rather than letting
bin_service touch the relationship lazily. This mirrors why
revision_repository.search() hand-builds its own outer join instead of
relying on `Revision.user`: without it, rendering a page of up to 200
BIN records for GET /api/v1/bin-series would issue up to 200 extra
per-row queries to resolve each one's `updatedBy` name (classic N+1).
"""
from typing import List, Optional, Tuple

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.bin_record import BinRecord

# Maps the API's camelCase sortBy values to actual ORM columns. Keeping
# this in the repository (not the service) since it's purely a
# column-mapping concern — no business rule depends on it.
SORT_FIELD_MAP = {
    "issuer": BinRecord.issuer,
    "cardProgramGroupName": BinRecord.card_program_group_name,
    "binIin": BinRecord.bin_iin,
    "merchantPrefix": BinRecord.merchant_prefix,
    "id": BinRecord.id,
}


def get_by_bin_and_prefix(session: Session, *, bin_iin: str, merchant_prefix: str) -> Optional[BinRecord]:
    """Exact match only — (bin_iin, merchant_prefix) is unique, so this
    returns at most one record. Never returns a partial-match list."""
    stmt = (
        select(BinRecord)
        .options(joinedload(BinRecord.updated_by_user))
        .where(
            BinRecord.bin_iin == bin_iin,
            BinRecord.merchant_prefix == merchant_prefix,
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_id(session: Session, bin_record_id: int) -> Optional[BinRecord]:
    stmt = (
        select(BinRecord)
        .options(joinedload(BinRecord.updated_by_user))
        .where(BinRecord.id == bin_record_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def create(
    session: Session,
    *,
    issuer: str,
    card_program_group_name: str,
    bin_iin: str,
    merchant_prefix: str,
    merchant_id: Optional[int],
    instance_name: str,
    updated_by_user_id: Optional[int],
) -> BinRecord:
    record = BinRecord(
        issuer=issuer,
        card_program_group_name=card_program_group_name,
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        merchant_id=merchant_id,
        instance_name=instance_name,
        updated_by_user_id=updated_by_user_id,
    )
    session.add(record)
    session.flush()
    return record


def update_fields(session: Session, record: BinRecord, **fields) -> BinRecord:
    for key, value in fields.items():
        setattr(record, key, value)
    session.flush()
    return record


def delete(session: Session, record: BinRecord) -> None:
    session.delete(record)
    session.flush()


def search(
    session: Session,
    *,
    search: Optional[str] = None,
    issuer: Optional[str] = None,
    card_program_group_name: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[BinRecord], int]:
    """Filters, sorts, and paginates bin_records. Returns (items, total)."""
    stmt = select(BinRecord).options(joinedload(BinRecord.updated_by_user))

    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                BinRecord.issuer.ilike(term),
                BinRecord.card_program_group_name.ilike(term),
                BinRecord.bin_iin.ilike(term),
                BinRecord.merchant_prefix.ilike(term),
            )
        )
    if issuer:
        stmt = stmt.where(func.lower(BinRecord.issuer) == issuer.strip().lower())
    if card_program_group_name:
        stmt = stmt.where(func.lower(BinRecord.card_program_group_name) == card_program_group_name.strip().lower())

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, BinRecord.id)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not BinRecord.id:
        # Deterministic tiebreaker — without this, pagination across
        # pages is not guaranteed stable when sorting by a non-unique
        # field (e.g. issuer, which repeats across many records).
        order_clauses.append(BinRecord.id)
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0
