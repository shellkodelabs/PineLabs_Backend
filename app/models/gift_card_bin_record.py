"""
SQLAlchemy model for `gift_card_bin_records`.

New Excel-format Bin Series task — confirmed architecture: a completely
separate table for Gift Card BIN records, independent of
`wallet_bin_records` and of the old, untouched `bin_records` (legacy,
kept for Dashboard/seed compatibility — see app/models/bin_record.py).
No shared `bin_type` discriminator table.

Every business field is a true NOT NULL column (`instance`, `issuer`,
`merchant`, `card_program_group_name`, `card_program_group_type`,
`card_type`, `ticket_number`) — unlike `bin_records.instance_name`,
which had to be nullable-in-DB + application-enforced to avoid breaking
611 pre-existing rows with no value to backfill, this is a brand-new
table with zero legacy rows, so there is no backward-compatibility
reason to split the enforcement the old way.

`instance` and `merchant` are plain strings, matching the frontend and
the client Excel format exactly:
  - `instance` is NOT a foreign key (same reasoning as the old
    `bin_records.instance_name` — no backend `instances` FK relationship
    was established for that field either, and this task does not
    introduce one).
  - `merchant` is NOT connected to the existing `merchants` table/
    `Merchant` model in any way (explicit instruction) — it is an
    unrelated, independently-typed free-text field, not a replacement
    for a `merchant_id` FK (this table has no such FK at all).

`status` follows the exact same convention as `app/models/instance.py`
(STATUS_VALUES tuple + a computed CHECK constraint + a dedicated index)
rather than reusing that module's tuple directly — kept local so this
model has no import-time coupling to the Instance Management module,
which is explicitly out of scope for this task.

`custom_fields` (JSONB, NOT NULL DEFAULT '{}') holds this type's
dynamic-column VALUES, keyed by `gift_card_bin_custom_columns.key` (see
app/models/gift_card_bin_custom_column.py — an INDEPENDENT registry
from the Wallet one, per the confirmed architecture: two separate
custom-column tables, not one shared registry).

`updated_by_user_id`: same nullable soft-FK / ON DELETE SET NULL
pattern as every other "who last touched this row" column in this
project (BinRecord, Instance, BinCustomColumn) — set server-side from
the authenticated actor, never accepted from a request payload.

Cross-table (bin_iin, merchant_prefix) uniqueness spanning this table
and `wallet_bin_records` is NOT enforced here (a plain UNIQUE
constraint cannot span two independent tables) — this table only gets
its own within-type UNIQUE(bin_iin, merchant_prefix), per the Stage 1
migration. Cross-table uniqueness is deferred to an application-level
check in a later stage, per the confirmed architecture.
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

STATUS_VALUES = ("Active", "Inactive")
_status_check_sql = "status IN ({})".format(", ".join(f"'{value}'" for value in STATUS_VALUES))


class GiftCardBinRecord(Base):
    __tablename__ = "gift_card_bin_records"
    __table_args__ = (
        UniqueConstraint("bin_iin", "merchant_prefix", name="uq_gift_card_bin_records_bin_prefix"),
        CheckConstraint("bin_iin ~ '^[0-9]{6}$'", name="ck_gift_card_bin_records_bin_iin_format"),
        CheckConstraint("merchant_prefix ~ '^[0-9]{3}$'", name="ck_gift_card_bin_records_merchant_prefix_format"),
        CheckConstraint(_status_check_sql, name="ck_gift_card_bin_records_status"),
        Index("ix_gift_card_bin_records_bin_iin", "bin_iin"),
        Index("ix_gift_card_bin_records_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    instance: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant: Mapped[str] = mapped_column(String(255), nullable=False)
    card_program_group_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bin_iin: Mapped[str] = mapped_column(CHAR(6), nullable=False)
    merchant_prefix: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    card_program_group_type: Mapped[str] = mapped_column(String(255), nullable=False)
    card_type: Mapped[str] = mapped_column(String(255), nullable=False)
    ticket_number: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Active'"))

    # See module docstring — keyed by gift_card_bin_custom_columns.key,
    # an independent registry from the Wallet one.
    custom_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # See module docstring. No back_populates — User has no need for a
    # reverse "gift card bin records I last touched" collection (same
    # one-directional pattern as BinRecord.updated_by_user).
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[updated_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<GiftCardBinRecord id={self.id} bin_iin={self.bin_iin} merchant_prefix={self.merchant_prefix}>"
