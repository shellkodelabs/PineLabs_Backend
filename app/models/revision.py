"""
SQLAlchemy model for `revisions` (audit log).

See backend design Part 3 §9. `entity_id` is intentionally NOT a foreign
key: a revision can describe a change to any one of several entity
tables (bin_record, merchant, sop_sheet, sop_row, user) — a polymorphic
reference — and PostgreSQL has no native polymorphic FK. Validating that
entity_id actually points at a real row of the given entity_type is a
service-layer concern (added in a later part), not a database constraint.
No five separate entity-specific FK columns either, per the design.
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
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

ACTION_TYPE_VALUES = ("create", "update", "delete", "upload")
ENTITY_TYPE_VALUES = ("bin_record", "merchant", "sop_sheet", "sop_row", "user")

_action_type_check_sql = "action_type IN ({})".format(", ".join(f"'{value}'" for value in ACTION_TYPE_VALUES))
_entity_type_check_sql = "entity_type IN ({})".format(", ".join(f"'{value}'" for value in ENTITY_TYPE_VALUES))


class Revision(Base):
    __tablename__ = "revisions"
    __table_args__ = (
        CheckConstraint(_action_type_check_sql, name="ck_revisions_action_type"),
        CheckConstraint(_entity_type_check_sql, name="ck_revisions_entity_type"),
        Index("ix_revisions_occurred_at", text("occurred_at DESC")),
        Index("ix_revisions_action_type", "action_type"),
        Index("ix_revisions_entity_type", "entity_type"),
        Index("ix_revisions_user_id", "user_id"),
        # NOTE: the trigram search index across target_label +
        # change_description (ix_revisions_search, requires pg_trgm) is
        # created directly in the Alembic migration, same reasoning as
        # Merchant/BinRecord/User.
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # No ON DELETE clause specified in the design (unlike sop_sheets.merchant_id
    # or bin_records.merchant_id) — defaults to Postgres's NO ACTION, which
    # is the right behavior for an audit trail: a user with revision history
    # should not be silently deletable out from under that history.
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)

    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_label: Mapped[str] = mapped_column(String(255), nullable=False)
    change_description: Mapped[str] = mapped_column(Text, nullable=False)

    # Mapped attribute is `metadata_` because `metadata` is reserved on
    # SQLAlchemy declarative classes (Base.metadata). The actual DB column
    # is still named `metadata`, per the design.
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    user: Mapped["User"] = relationship(back_populates="revisions")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Revision id={self.id} action_type={self.action_type} entity_type={self.entity_type}>"
