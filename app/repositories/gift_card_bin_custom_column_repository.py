"""
Repository for `gift_card_bin_custom_columns` — SQLAlchemy queries
only. No business rules, no response-schema mapping, no HTTP concerns
live here. Structural mirror of the old, untouched
app/repositories/bin_custom_column_repository.py (see that module's
docstring for the full reasoning behind each method), using
GiftCardBinCustomColumn instead — deliberately NOT imported from or
combined with the old module, and NOT shared with
wallet_bin_custom_column_repository.py, per the confirmed architecture
(two fully independent registries).

No delete method: confirmed by inspection of the approved frontend
(BinTable.jsx still uses the same AddColumnModal.jsx/EditableHeader.jsx
pair as before, no delete-column UI anywhere) that Gift Card custom
columns have no delete capability, same as the old single-table
registry. No reorder/display_order-update method either — confirmed no
ordering UI exists; `display_order` is assigned once at creation via
`next_display_order()` and never changes after that.
"""
from typing import List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.gift_card_bin_custom_column import GiftCardBinCustomColumn


def list_all(session: Session) -> List[GiftCardBinCustomColumn]:
    """All Gift Card custom columns, in display order (creation
    sequence)."""
    stmt = (
        select(GiftCardBinCustomColumn)
        .options(joinedload(GiftCardBinCustomColumn.updated_by_user))
        .order_by(GiftCardBinCustomColumn.display_order, GiftCardBinCustomColumn.id)
    )
    return list(session.execute(stmt).scalars().all())


def get_by_id(session: Session, column_id: int) -> Optional[GiftCardBinCustomColumn]:
    stmt = (
        select(GiftCardBinCustomColumn)
        .options(joinedload(GiftCardBinCustomColumn.updated_by_user))
        .where(GiftCardBinCustomColumn.id == column_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_key(session: Session, key: str) -> Optional[GiftCardBinCustomColumn]:
    """Exact, case-insensitive match — safe to return a single row
    because `key` is unique (uq_gift_card_bin_custom_columns_key)."""
    stmt = select(GiftCardBinCustomColumn).where(func.lower(GiftCardBinCustomColumn.key) == key.strip().lower())
    return session.execute(stmt).scalar_one_or_none()


def get_by_name(session: Session, name: str, *, exclude_id: Optional[int] = None) -> Optional[GiftCardBinCustomColumn]:
    """Exact, case-insensitive match on the DISPLAY name — used for the
    duplicate-name check on both create and rename. `exclude_id` lets a
    rename skip colliding with itself when the name is unchanged."""
    stmt = select(GiftCardBinCustomColumn).where(func.lower(GiftCardBinCustomColumn.name) == name.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(GiftCardBinCustomColumn.id != exclude_id)
    return session.execute(stmt).scalar_one_or_none()


def next_display_order(session: Session) -> int:
    """New columns are always appended after every existing one."""
    max_order = session.scalar(select(func.max(GiftCardBinCustomColumn.display_order)))
    return (max_order or 0) + 1


def existing_keys(session: Session, keys: Set[str]) -> Set[str]:
    """Given a set of candidate keys, returns the SUBSET that actually
    exist as registered Gift Card custom columns — used by a later
    stage's service to validate a row write's customFields against this
    registry without one query per key."""
    if not keys:
        return set()
    stmt = select(GiftCardBinCustomColumn.key).where(GiftCardBinCustomColumn.key.in_(keys))
    return set(session.execute(stmt).scalars().all())


def create(
    session: Session, *, key: str, name: str, display_order: int, updated_by_user_id: Optional[int]
) -> GiftCardBinCustomColumn:
    column = GiftCardBinCustomColumn(key=key, name=name, display_order=display_order, updated_by_user_id=updated_by_user_id)
    session.add(column)
    session.flush()
    return column


def rename(
    session: Session, column: GiftCardBinCustomColumn, *, name: str, updated_by_user_id: Optional[int]
) -> GiftCardBinCustomColumn:
    column.name = name
    column.updated_by_user_id = updated_by_user_id
    session.flush()
    return column
