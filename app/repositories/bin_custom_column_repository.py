"""
Repository for `bin_custom_columns` — SQLAlchemy queries only. No
business rules, no response-schema mapping, no HTTP concerns live here.
Same pattern as app/repositories/instance_repository.py (the closest
existing analog: a simple metadata entity with create + read, no delete).

Index usage: `uq_bin_custom_columns_key` backs both the exact-key lookup
(`get_by_key`) and the DB-level uniqueness guarantee. No dedicated index
for `list_all`'s ORDER BY display_order — this table is expected to stay
small (a handful of user-created columns), so a full-table sort needs no
index-backed acceleration, same reasoning already used for
instance_repository's unindexed `search`.
"""
from typing import List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.bin_custom_column import BinCustomColumn


def list_all(session: Session) -> List[BinCustomColumn]:
    """All custom columns, in display order — the frontend's own
    creation-sequence ordering (see app/models/bin_custom_column.py)."""
    stmt = (
        select(BinCustomColumn)
        .options(joinedload(BinCustomColumn.updated_by_user))
        .order_by(BinCustomColumn.display_order, BinCustomColumn.id)
    )
    return list(session.execute(stmt).scalars().all())


def get_by_id(session: Session, column_id: int) -> Optional[BinCustomColumn]:
    stmt = (
        select(BinCustomColumn)
        .options(joinedload(BinCustomColumn.updated_by_user))
        .where(BinCustomColumn.id == column_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_key(session: Session, key: str) -> Optional[BinCustomColumn]:
    """Exact, case-insensitive match — safe to return a single row
    because `key` is unique (uq_bin_custom_columns_key)."""
    stmt = select(BinCustomColumn).where(func.lower(BinCustomColumn.key) == key.strip().lower())
    return session.execute(stmt).scalar_one_or_none()


def get_by_name(session: Session, name: str, *, exclude_id: Optional[int] = None) -> Optional[BinCustomColumn]:
    """Exact, case-insensitive match on the DISPLAY name — used for the
    duplicate-name check on both create and rename (matches
    AddColumnModal.jsx's/validateHeader's own case-insensitive
    comparison). `name` has no DB-level unique constraint (only `key`
    does — see app/models/bin_custom_column.py), so this is the
    authoritative check; `exclude_id` lets a rename skip colliding with
    itself when the name is unchanged."""
    stmt = select(BinCustomColumn).where(func.lower(BinCustomColumn.name) == name.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(BinCustomColumn.id != exclude_id)
    return session.execute(stmt).scalar_one_or_none()


def next_display_order(session: Session) -> int:
    """New columns are always appended after every existing one — matches
    BinTable.jsx's addColumn(), which always inserts the new key at the
    end of the column list (before the fixed updatedBy/updatedAt trailing
    columns)."""
    max_order = session.scalar(select(func.max(BinCustomColumn.display_order)))
    return (max_order or 0) + 1


def existing_keys(session: Session, keys: Set[str]) -> Set[str]:
    """Given a set of candidate keys, returns the SUBSET that actually
    exist as registered custom columns — used by bin_service to validate
    a row write's customFields against the metadata registry without one
    query per key."""
    if not keys:
        return set()
    stmt = select(BinCustomColumn.key).where(BinCustomColumn.key.in_(keys))
    return set(session.execute(stmt).scalars().all())


def create(session: Session, *, key: str, name: str, display_order: int, updated_by_user_id: Optional[int]) -> BinCustomColumn:
    column = BinCustomColumn(key=key, name=name, display_order=display_order, updated_by_user_id=updated_by_user_id)
    session.add(column)
    session.flush()
    return column


def rename(session: Session, column: BinCustomColumn, *, name: str, updated_by_user_id: Optional[int]) -> BinCustomColumn:
    column.name = name
    column.updated_by_user_id = updated_by_user_id
    session.flush()
    return column
