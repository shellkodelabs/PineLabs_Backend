"""
SQLAlchemy model for `wallet_bin_records`.

New Excel-format Bin Series task — confirmed architecture: a completely
separate table for Wallet BIN records, independent of
`gift_card_bin_records` and of the old, untouched `bin_records`
(legacy, kept for Dashboard/seed compatibility — see
app/models/bin_record.py). No shared `bin_type` discriminator table.

Shape mirrors GiftCardBinRecord (see app/models/gift_card_bin_record.py
for the full field-by-field reasoning — `instance`/`merchant` as plain,
unrelated strings; `status`; `custom_fields`; `updated_by_user_id`; the
deferred cross-table uniqueness note) with one structural difference:
no `card_program_group_name`/`card_program_group_type`/`card_type` —
Wallet records use `wallet_program_name` and
`wallet_program_group_type` instead, per the client Excel format's
Wallet column set (Instance, Issuer, Merchant, Wallet Program Name,
BIN, Merchant Prefix, Wallet Program Group Type, Ticket Number).

`custom_fields` here is keyed by `wallet_bin_custom_columns.key` — an
INDEPENDENT registry from the Gift Card one (see
app/models/wallet_bin_custom_column.py), per the confirmed
architecture: two separate custom-column tables, not one shared
registry.
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


class WalletBinRecord(Base):
    __tablename__ = "wallet_bin_records"
    __table_args__ = (
        UniqueConstraint("bin_iin", "merchant_prefix", name="uq_wallet_bin_records_bin_prefix"),
        CheckConstraint("bin_iin ~ '^[0-9]{6}$'", name="ck_wallet_bin_records_bin_iin_format"),
        CheckConstraint("merchant_prefix ~ '^[0-9]{3}$'", name="ck_wallet_bin_records_merchant_prefix_format"),
        CheckConstraint(_status_check_sql, name="ck_wallet_bin_records_status"),
        Index("ix_wallet_bin_records_bin_iin", "bin_iin"),
        Index("ix_wallet_bin_records_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    instance: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant: Mapped[str] = mapped_column(String(255), nullable=False)
    wallet_program_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bin_iin: Mapped[str] = mapped_column(CHAR(6), nullable=False)
    merchant_prefix: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    wallet_program_group_type: Mapped[str] = mapped_column(String(255), nullable=False)
    ticket_number: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Active'"))

    # See module docstring — keyed by wallet_bin_custom_columns.key, an
    # independent registry from the Gift Card one.
    custom_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # No back_populates — same one-directional pattern as BinRecord.updated_by_user.
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[updated_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<WalletBinRecord id={self.id} bin_iin={self.bin_iin} merchant_prefix={self.merchant_prefix}>"
