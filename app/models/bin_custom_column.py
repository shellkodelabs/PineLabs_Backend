"""
SQLAlchemy model for `bin_custom_columns` — the persisted metadata
registry for Bin Series dynamic/custom columns (BinTable.jsx's "Add
Column" feature).

See app/schemas/bin_series.py's module docstring for the full frontend
inspection findings this design is based on. The two load-bearing facts
from that inspection:

  1. A column's data KEY is set once at creation (the trimmed, initial
     name) and is IMMUTABLE thereafter — renaming only ever changes a
     separate display `name`, never the key. `key` is therefore a plain
     unique string, not a synthetic slug; `name` is the mutable label.
  2. No type system, no delete capability, no explicit ordering field —
     order is pure creation sequence. This model deliberately has none
     of those either.

`custom_fields` values themselves live in `bin_records.custom_fields`
(JSONB), keyed by THIS table's `key` column — see app/models/bin_record.py.
This table is metadata-only: it says which custom columns exist and
their display name/order, never the row-level values.
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


class BinCustomColumn(Base):
    __tablename__ = "bin_custom_columns"
    __table_args__ = (
        UniqueConstraint("key", name="uq_bin_custom_columns_key"),
        # Defense-in-depth backstop for the RENAME race specifically
        # (key's own uniqueness can't catch a name-only collision, since
        # key never changes after creation) — same "app-level
        # case-insensitive check + DB-level exact-case constraint as
        # backup" pattern already used for merchants.name/users.email.
        UniqueConstraint("name", name="uq_bin_custom_columns_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # Immutable once created — see module docstring. Exact-string unique
    # (not case-folded at rest); case-insensitive duplicate checks are an
    # application-layer concern (app/services/bin_custom_column_service.py),
    # same convention as merchants.name / instances.name elsewhere in
    # this codebase.
    key: Mapped[str] = mapped_column(String(255), nullable=False)

    # The mutable display label — starts identical to `key`, may diverge
    # after a rename. This is what the frontend calls "name".
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Creation-sequence order (no reordering capability — frontend has
    # none). Assigned as max(display_order)+1 at creation time.
    display_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Same pattern as BinRecord.updated_by_user_id / Instance.updated_by_user_id:
    # nullable soft FK, ON DELETE SET NULL, set from the authenticated
    # actor on both create AND rename (no separate created_by field —
    # matches the established project convention exactly).
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[updated_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<BinCustomColumn id={self.id} key={self.key!r} name={self.name!r}>"
