"""
SQLAlchemy models for the SOP domain: SopSheet, SopColumnGroup,
SopColumn, SopRow.

See backend design Part 3 §6-§7 (Option A). Row data is JSONB — there is
no SopCell table and no fixed per-classification tables (block_sop,
activation_sop, poc_sop, ...). This directly reflects the confirmed
frontend finding that columns vary per merchant, even for the same
subsheet key (e.g. 5 distinct "Block" column schemas across 510
merchants — Part 2 §3).
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SopSheet(Base):
    """
    One merchant's subsheet (Block, Activation, POC, Escalation Matrix, ...).

    merchant_id = NULL represents a SHARED/DEFAULT sheet — this is how the
    frontend's `commonEscalation` fallback (used when a merchant has no
    escalation sheet of its own) is represented. There is no fake
    "Default Merchant" row; the partial unique index below guarantees at
    most one shared sheet per `key`.
    """

    __tablename__ = "sop_sheets"
    __table_args__ = (
        # A merchant cannot have two sheets with the same key. NULLs in a
        # standard UNIQUE constraint don't conflict with each other in
        # Postgres, so this constraint only actively enforces uniqueness
        # for rows where merchant_id IS NOT NULL — the shared-default case
        # is handled separately by the partial index below.
        UniqueConstraint("merchant_id", "key", name="uq_sop_sheets_merchant_key"),
        # Only one shared/default sheet may exist per key (merchant_id IS NULL).
        Index(
            "uq_sop_sheets_shared_default_key",
            "key",
            unique=True,
            postgresql_where=text("merchant_id IS NULL"),
        ),
        Index("ix_sop_sheets_merchant_id", "merchant_id"),
        # No separate index on `key` alone: every confirmed query pattern
        # looks up (merchant_id, key) together, or merchant_id alone — the
        # composite unique constraint's implicit index already covers the
        # former, and ix_sop_sheets_merchant_id covers the latter.
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    merchant_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    merchant: Mapped[Optional["Merchant"]] = relationship(back_populates="sop_sheets")
    column_groups: Mapped[List["SopColumnGroup"]] = relationship(
        back_populates="sheet",
        cascade="all, delete-orphan",
        order_by="SopColumnGroup.sort_order",
    )
    rows: Mapped[List["SopRow"]] = relationship(back_populates="sheet", cascade="all, delete-orphan")
    user_access: Mapped[List["UserSopSheetAccess"]] = relationship(back_populates="sheet")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<SopSheet id={self.id} merchant_id={self.merchant_id} key={self.key!r}>"


class SopColumnGroup(Base):
    """The two-tier header grouping for a sheet, e.g. 'Prerequisites'."""

    __tablename__ = "sop_column_groups"
    __table_args__ = (Index("ix_sop_column_groups_sheet_id", "sheet_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sheet_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sop_sheets.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    sheet: Mapped["SopSheet"] = relationship(back_populates="column_groups")
    columns: Mapped[List["SopColumn"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="SopColumn.sort_order",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<SopColumnGroup id={self.id} sheet_id={self.sheet_id} label={self.label!r}>"


class SopColumn(Base):
    """
    One column within a group. Column names are NOT globally unique —
    they only have meaning within their own sheet/group (different
    merchants' Block sheets use different column names for similar
    concepts, e.g. "Card Status" vs "Card Type").
    """

    __tablename__ = "sop_columns"
    __table_args__ = (Index("ix_sop_columns_group_id", "group_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sop_column_groups.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    group: Mapped["SopColumnGroup"] = relationship(back_populates="columns")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<SopColumn id={self.id} group_id={self.group_id} name={self.name!r}>"


class SopRow(Base):
    """
    One data row — a decision-table entry or a POC contact — stored as a
    JSONB document shaped exactly like the frontend's existing row objects:
    {"Column Name": "Value", "Another Column": "Value"}.

    No SopCell table, no per-classification fixed tables. This is the
    final decision from the backend design (Part 3 §6, Option A),
    justified there by: matching current frontend row shape exactly,
    variable columns per merchant, no confirmed cross-merchant
    column-based query requirement, and simpler import/export from Excel.
    """

    __tablename__ = "sop_rows"
    __table_args__ = (
        Index("ix_sop_rows_sheet_id", "sheet_id"),
        # Default GIN opclass (jsonb_ops) — supports both containment
        # (@>) and key-existence (?) queries. No per-column expression
        # indexes: SOP columns vary by merchant, so there is no single
        # column to justify indexing ahead of the others.
        Index("ix_sop_rows_data_gin", "data", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sheet_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sop_sheets.id", ondelete="CASCADE"), nullable=False
    )
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    sheet: Mapped["SopSheet"] = relationship(back_populates="rows")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<SopRow id={self.id} sheet_id={self.sheet_id}>"
