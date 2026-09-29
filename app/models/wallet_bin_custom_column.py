"""
SQLAlchemy model for `wallet_bin_custom_columns` — the persisted
metadata registry for Wallet Bin Series dynamic/custom columns.

Independent of `gift_card_bin_custom_columns` (see
app/models/gift_card_bin_custom_column.py — full reasoning there) and
of the old, untouched `bin_custom_columns` (legacy). No relationship
between this table and the Gift Card registry at all — two entirely
separate tables, per the confirmed architecture.

Row-level custom-field VALUES live in `wallet_bin_records.custom_fields`
(JSONB), keyed by THIS table's `key` column — see
app/models/wallet_bin_record.py. This table is metadata-only.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class WalletBinCustomColumn(Base):
    __tablename__ = "wallet_bin_custom_columns"
    __table_args__ = (
        UniqueConstraint("key", name="uq_wallet_bin_custom_columns_key"),
        UniqueConstraint("name", name="uq_wallet_bin_custom_columns_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    updated_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[updated_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<WalletBinCustomColumn id={self.id} key={self.key!r} name={self.name!r}>"
