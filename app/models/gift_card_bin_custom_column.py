"""
SQLAlchemy model for `gift_card_bin_custom_columns` — the persisted
metadata registry for Gift Card Bin Series dynamic/custom columns.

Independent of `wallet_bin_custom_columns` (see
app/models/wallet_bin_custom_column.py) and of the old, untouched
`bin_custom_columns` (legacy — see app/models/bin_custom_column.py).
Per the confirmed architecture, there is no relationship between the
two new per-type registries at all — they are entirely separate tables
with no shared row ever referencing the other.

Exact structural mirror of the existing `bin_custom_columns` model —
same two load-bearing facts drive this design (see
app/models/bin_custom_column.py's module docstring for the full
reasoning): `key` is set once at creation (the trimmed initial name)
and is immutable thereafter; `name` is the separate, mutable display
label; no type system, no delete capability, no explicit reordering —
order is pure creation sequence via `display_order`.

Row-level custom-field VALUES live in `gift_card_bin_records.custom_fields`
(JSONB), keyed by THIS table's `key` column — see
app/models/gift_card_bin_record.py. This table is metadata-only.
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


class GiftCardBinCustomColumn(Base):
    __tablename__ = "gift_card_bin_custom_columns"
    __table_args__ = (
        UniqueConstraint("key", name="uq_gift_card_bin_custom_columns_key"),
        # Defense-in-depth backstop for the RENAME race specifically
        # (key's own uniqueness can't catch a name-only collision, since
        # key never changes after creation) — same pattern as
        # bin_custom_columns.name / merchants.name / instances.name.
        UniqueConstraint("name", name="uq_gift_card_bin_custom_columns_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # Immutable once created — see module docstring. Exact-string unique
    # (not case-folded at rest); case-insensitive duplicate checks are
    # an application-layer concern (a later stage's service).
    key: Mapped[str] = mapped_column(String(255), nullable=False)

    # The mutable display label — starts identical to `key`, may
    # diverge after a rename.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Creation-sequence order (no reordering capability — frontend has
    # none). Assigned as max(display_order)+1 at creation time.
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Same pattern as GiftCardBinRecord.updated_by_user_id: nullable
    # soft FK, ON DELETE SET NULL, set from the authenticated actor on
    # both create AND rename.
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[updated_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<GiftCardBinCustomColumn id={self.id} key={self.key!r} name={self.name!r}>"
