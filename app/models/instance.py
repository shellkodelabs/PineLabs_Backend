"""
SQLAlchemy model for `instances`.

API #1 of the Bin Series gap analysis: backs GET /api/v1/instances, which
supplies the "Select an instance" dropdown on the frontend's BIN Series
Add/Clone form (src/components/dashboard/AddRowModal.jsx via BinTable.jsx,
options sourced from src/data/sopData.js's `instances` array).

Deliberately minimal, per the task's explicit scope: id, name,
description, status, updated_by_user_id, created_at, updated_at. NO
issuer/merchant relationship (frontend's `issuerIds` array) — the current
Bin Series requirement only needs the instance list itself, and
`bin_records.instance_name` stays a plain string (Part 17), not a FK
into this table; that decision is explicitly deferred, not made here.

Same conventions as app/models/merchant.py (closest existing analog: a
simple, mostly-static lookup entity) and app/models/user.py (for the
status CHECK-constraint pattern and the updated_by_user_id/relationship
pattern already established on BinRecord).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

STATUS_VALUES = ("Active", "Inactive")
_status_check_sql = "status IN ({})".format(", ".join(f"'{value}'" for value in STATUS_VALUES))


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (
        UniqueConstraint("name", name="uq_instances_name"),
        CheckConstraint(_status_check_sql, name="ck_instances_status"),
        Index("ix_instances_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Active'"))

    # Same pattern as BinRecord.updated_by_user_id (Part 16): nullable soft
    # FK, ON DELETE SET NULL so a deleted user never blocks/cascades an
    # instance row. Set server-side from the authenticated actor — never
    # accepted from a request payload.
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[updated_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Instance id={self.id} name={self.name!r} status={self.status!r}>"
