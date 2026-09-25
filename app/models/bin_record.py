"""
SQLAlchemy model for `bin_records`.

See backend design Part 3 §4 and §10. `merchant_id` is a NULLABLE, soft
link to `merchants` — the BIN Series <-> Merchant relationship is real in
intent (the frontend's "Jump to SOP" flow assumes shared issuer names)
but not confirmed as a mandatory 1:1 business invariant, so it is not
required here.

`updated_by_user_id` (Part 16) is a NULLABLE FK to `users`, set by
app/services/bin_service.py from the authenticated actor on every
create/update — never accepted from a request payload (see
app/schemas/bin_series.py). ON DELETE SET NULL, same reasoning as
`merchant_id`: a BIN record should not disappear or become undeletable
just because the user who last touched it was later removed; only the
attribution itself clears. NULL for the 611 pre-existing seeded records
and for any record never touched by the new write APIs — this is what
makes the column additive/backward-compatible rather than a backfill
requirement.

`instance_name` (Part 17): a plain, NULLABLE string — NOT a foreign key.
The frontend's `instances` concept (src/data/sopData.js — zone-style
groupings like "North Zone") has no backend table or model anywhere in
this codebase (confirmed by inspection before this column was added: no
`instances`/`Instance` model exists), so there is nothing to reference.
Nullable at the DB level so the 611 pre-existing (and any other legacy)
records are preserved unmodified with no backfill; app.services.bin_service
enforces it as MANDATORY (non-blank) on create, and on update whenever a
real write would leave the record without one — see that module's
docstring for the exact rule. This split (nullable schema + mandatory
application rule) is deliberate: a NOT NULL constraint here would break
every existing row with no real instance data to backfill from.

`custom_fields` (Dynamic/Custom Columns task): JSONB, NOT NULL DEFAULT
'{}'. Holds Bin Series dynamic-column VALUES for this row, keyed by
`bin_custom_columns.key` (see app/models/bin_custom_column.py — that
table is the metadata registry; this column is only the per-row values).
Values are always plain strings, matching the frontend's own model (no
type system, no nested structures — BinTable.jsx's EditableCell only
ever commits a trimmed string). NOT NULL with a '{}' default (unlike
instance_name's NULL-based backward-compatibility split above) because
an empty JSON object is a completely natural "no custom values yet"
representation that needs no special-casing anywhere it's read.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class BinRecord(Base):
    __tablename__ = "bin_records"
    __table_args__ = (
        UniqueConstraint("bin_iin", "merchant_prefix", name="uq_bin_records_bin_prefix"),
        CheckConstraint("bin_iin ~ '^[0-9]{6}$'", name="ck_bin_records_bin_iin_format"),
        CheckConstraint("merchant_prefix ~ '^[0-9]{3}$'", name="ck_bin_records_merchant_prefix_format"),
        Index("ix_bin_records_bin_iin", "bin_iin"),
        # NOTE: the trigram search index across issuer + card_program_group_name
        # (ix_bin_records_search, requires pg_trgm) is an expression index
        # over the concatenation of both columns — created directly in the
        # Alembic migration, not declared here (same reasoning as Merchant).
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    card_program_group_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bin_iin: Mapped[str] = mapped_column(CHAR(6), nullable=False)
    merchant_prefix: Mapped[str] = mapped_column(CHAR(3), nullable=False)

    # Nullable soft FK. ON DELETE SET NULL (not CASCADE): a BIN record
    # should not disappear just because its loosely-linked merchant row
    # was removed — only the (optional) link itself should clear.
    merchant_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("merchants.id", ondelete="SET NULL"), nullable=True
    )

    # See module docstring — plain string, no FK (no backend instances
    # table exists). Nullable at the DB level; mandatory-ness is an
    # application-layer rule enforced in app/services/bin_service.py.
    instance_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # See module docstring — Dynamic/Custom Columns task.
    custom_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # See module docstring. No back_populates — User has no need for a
    # reverse "bin records I last touched" collection (same one-directional
    # pattern as Merchant.sop_sheets).
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    merchant: Mapped[Optional["Merchant"]] = relationship(back_populates="bin_records")
    updated_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[updated_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<BinRecord id={self.id} bin_iin={self.bin_iin} merchant_prefix={self.merchant_prefix}>"
