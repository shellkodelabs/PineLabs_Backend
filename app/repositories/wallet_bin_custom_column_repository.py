"""
Repository for `wallet_bin_custom_columns` — SQLAlchemy queries only.
Structural mirror of app/repositories/gift_card_bin_custom_column_repository.py
(see that module's docstring for the full reasoning — no delete
method, no reorder method, confirmed by the same frontend inspection),
using WalletBinCustomColumn instead — deliberately NOT shared with the
Gift Card registry, per the confirmed architecture.
"""
from typing import List, Optional, Set

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.wallet_bin_custom_column import WalletBinCustomColumn


def list_all(session: Session) -> List[WalletBinCustomColumn]:
    """All Wallet custom columns, in display order (creation
    sequence)."""
    stmt = (
        select(WalletBinCustomColumn)
        .options(joinedload(WalletBinCustomColumn.updated_by_user))
        .order_by(WalletBinCustomColumn.display_order, WalletBinCustomColumn.id)
    )
    return list(session.execute(stmt).scalars().all())


def get_by_id(session: Session, column_id: int) -> Optional[WalletBinCustomColumn]:
    stmt = (
        select(WalletBinCustomColumn)
        .options(joinedload(WalletBinCustomColumn.updated_by_user))
        .where(WalletBinCustomColumn.id == column_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_key(session: Session, key: str) -> Optional[WalletBinCustomColumn]:
    stmt = select(WalletBinCustomColumn).where(func.lower(WalletBinCustomColumn.key) == key.strip().lower())
    return session.execute(stmt).scalar_one_or_none()


def get_by_name(session: Session, name: str, *, exclude_id: Optional[int] = None) -> Optional[WalletBinCustomColumn]:
    stmt = select(WalletBinCustomColumn).where(func.lower(WalletBinCustomColumn.name) == name.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(WalletBinCustomColumn.id != exclude_id)
    return session.execute(stmt).scalar_one_or_none()


def next_display_order(session: Session) -> int:
    max_order = session.scalar(select(func.max(WalletBinCustomColumn.display_order)))
    return (max_order or 0) + 1


def existing_keys(session: Session, keys: Set[str]) -> Set[str]:
    if not keys:
        return set()
    stmt = select(WalletBinCustomColumn.key).where(WalletBinCustomColumn.key.in_(keys))
    return set(session.execute(stmt).scalars().all())


def create(
    session: Session, *, key: str, name: str, display_order: int, updated_by_user_id: Optional[int]
) -> WalletBinCustomColumn:
    column = WalletBinCustomColumn(key=key, name=name, display_order=display_order, updated_by_user_id=updated_by_user_id)
    session.add(column)
    session.flush()
    return column


def rename(
    session: Session, column: WalletBinCustomColumn, *, name: str, updated_by_user_id: Optional[int]
) -> WalletBinCustomColumn:
    column.name = name
    column.updated_by_user_id = updated_by_user_id
    session.flush()
    return column
