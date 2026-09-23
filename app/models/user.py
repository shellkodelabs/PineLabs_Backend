"""
SQLAlchemy models for `users` and `user_sop_sheet_access`.

See backend design Part 3 §8. No `user_merchant_access` table: the Part 2
frontend analysis (CreateUserModal.jsx's toggleMerchant/toggleSheet logic)
confirmed the "grant whole merchant" checkbox is only a bulk-select
convenience over individual sheet grants, not a distinct access tier —
so merchant-level access is derived (DISTINCT merchant_id via a join
through this table), never stored separately.

No password/password_hash column and no provider-specific identity
columns — real authentication fields are added once the identity
provider and protocol are confirmed (see app/core/security.py).
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

ROLE_VALUES = ("Admin", "Support Lead", "Support Agent", "Auditor")
STATUS_VALUES = ("Active", "Inactive", "Invited")

_role_check_sql = "role IN ({})".format(", ".join(f"'{value}'" for value in ROLE_VALUES))
_status_check_sql = "status IN ({})".format(", ".join(f"'{value}'" for value in STATUS_VALUES))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_role_check_sql, name="ck_users_role"),
        CheckConstraint(_status_check_sql, name="ck_users_status"),
        Index("ix_users_role", "role"),
        Index("ix_users_status", "status"),
        # NOTE: the trigram search index across name/email/role
        # (ix_users_search, requires pg_trgm) is created directly in the
        # Alembic migration, same reasoning as Merchant/BinRecord.
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    mobile: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Invited'"))
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    sheet_access: Mapped[List["UserSopSheetAccess"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    revisions: Mapped[List["Revision"]] = relationship(back_populates="user")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<User id={self.id} email={self.email!r}>"


class UserSopSheetAccess(Base):
    """
    Grants one user visibility into one SOP sheet. Composite primary key
    (user_id, sheet_id) — no surrogate id column, matching Part 3 §8
    exactly. This table alone is the source of truth for both sheet-level
    AND (derived) merchant-level access; there is deliberately no
    user_merchant_access table.
    """

    __tablename__ = "user_sop_sheet_access"
    __table_args__ = (Index("ix_user_sop_sheet_access_sheet_id", "sheet_id"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sheet_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sop_sheets.id", ondelete="CASCADE"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped["User"] = relationship(back_populates="sheet_access")
    sheet: Mapped["SopSheet"] = relationship(back_populates="user_access")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<UserSopSheetAccess user_id={self.user_id} sheet_id={self.sheet_id}>"
