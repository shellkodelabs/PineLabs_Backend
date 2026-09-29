"""
SQLAlchemy model for `revisions` (audit log).

See backend design Part 3 §9. `entity_id` is intentionally NOT a foreign
key: a revision can describe a change to any one of several entity
tables (bin_record, merchant, sop_sheet, sop_row, user) — a polymorphic
reference — and PostgreSQL has no native polymorphic FK. Validating that
entity_id actually points at a real row of the given entity_type is a
service-layer concern (added in a later part), not a database constraint.
No five separate entity-specific FK columns either, per the design.

`bin_custom_column` (Dynamic/Custom Columns task) added to
ENTITY_TYPE_VALUES: a column create/rename is a metadata-level event
about the COLUMN itself, not about any specific bin_record row, so it
genuinely doesn't fit under the existing "bin_record" entity_type (which
always means a specific row). Row-level custom-FIELD-VALUE changes
(editing a custom cell) remain ordinary "bin_record" updates — only
column create/rename get this new entity_type. Requires a migration
(the CHECK constraint must be dropped and recreated with the expanded
value list — Postgres has no ALTER CHECK).

New Excel-format Bin Series task: `gift_card_bin_record`/
`wallet_bin_record`/`gift_card_bin_custom_column`/
`wallet_bin_custom_column` added, mirroring the `bin_record`/
`bin_custom_column` split above but for the two new, completely
independent Bin Series tables (see app/models/gift_card_bin_record.py,
app/models/wallet_bin_record.py, and their custom-column-registry
counterparts). `bin_record`/`bin_custom_column` are KEPT, unchanged —
the old `bin_records`/`bin_custom_columns` tables remain in place as
legacy data, and any historical revision rows already referencing them
must stay valid. Migration: migrations/versions/
b0892cfc6659_create_gift_card_and_wallet_bin_tables.py (drops and
recreates ck_revisions_entity_type with all six original values plus
these four new ones — Postgres has no ALTER CHECK).
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
ENTITY_TYPE_VALUES = (
    "bin_record",
    "merchant",
    "sop_sheet",
    "sop_row",
    "user",
    "bin_custom_column",
    "gift_card_bin_record",
    "wallet_bin_record",
    "gift_card_bin_custom_column",
    "wallet_bin_custom_column",
)

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
