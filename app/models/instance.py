"""
SQLAlchemy model for `instances`.

An "instance" is the top-level entity that groups issuers (e.g. North
Zone, South Zone, Enterprise). Once created it appears in the Instance
Management listing and the SOP dashboard, and issuers/sheets are managed
under it. This is the backbone the frontend's `instances` concept
(src/data/sopData.js) always implied but which had no backend table —
`bin_records.instance_name` is currently only a loose, un-referenced
string (see app/models/bin_record.py's docstring). This model promotes
that concept to a first-class entity WITHOUT yet touching bin_records:
no FK is added there in this step, so nothing existing changes or needs
backfilling.

Columns mirror the Instance Management UI:
  - `name`        -> INSTANCE column
  - `status`      -> STATUS column (Active/Inactive; the
                     activate/deactivate toggle)
  - `ticket_number` -> the added Ticket Number field (optional)
  - `updated_by_user_id` + `updated_at` -> UPDATED BY column (who last
                     touched the instance, and when)

The UI's ISSUERS count is a DERIVED value (how many issuers are grouped
under the instance) — it is NOT a stored column here. It will be computed
once issuers are linked to instances; see app/services/instance_service.py.

`status` (Active/Inactive) supports the dashboard's activate/deactivate
concept at the instance level. Defaults to 'Active' on create.

`ticket_number` is an optional, non-unique reference ticket (e.g. an
INC/CR number) — nullable because not every instance has one.

`updated_by_user_id` is a NULLABLE FK to `users`, ON DELETE SET NULL —
same reasoning as bin_records.updated_by_user_id: an instance should not
disappear or become undeletable because the user who last touched it was
later removed; only the attribution clears.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

STATUS_VALUES = ("Active", "Inactive")

# Allowed data types for a user-defined custom column (see InstanceColumn).
# Drives per-cell validation on create/update/import.
COLUMN_TYPE_VALUES = ("text", "number", "date", "dropdown")

_status_check_sql = "status IN ({})".format(", ".join(f"'{value}'" for value in STATUS_VALUES))
_column_type_check_sql = "type IN ({})".format(", ".join(f"'{value}'" for value in COLUMN_TYPE_VALUES))


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (
        CheckConstraint(_status_check_sql, name="ck_instances_status"),
        Index("ix_instances_status", "status"),
        # NOTE: the trigram search index on `name` (ix_instances_name_trgm,
        # requires pg_trgm) is created directly in the Alembic migration,
        # same reasoning as Merchant/BinRecord/User — it is a
        # search-optimization index, not part of the logical schema, and
        # expression/opclass indexes are more reliably hand-authored in
        # the migration than reproduced by autogenerate.
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'Active'"))

    # Reference ticket (e.g. an INC/CR number). Nullable and non-unique:
    # not every instance necessarily has a ticket, and the same ticket may
    # relate to more than one instance.
    ticket_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # NOTE: Revised By / Reviewer are NOT stored on the instance row. They
    # are an EDIT/DELETE audit concern only — each edit is recorded in
    # `instance_edits` and each deletion in `instance_deletions`. Create
    # and import never capture them.

    # Nullable FK, ON DELETE SET NULL — see module docstring. Set to the
    # acting user on create and on every update, so the UI's "UPDATED BY"
    # column always reflects who last touched the instance.
    updated_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Values for the user-defined custom columns (see InstanceColumn),
    # stored as a JSONB document keyed by the column's `key`, e.g.
    # {"network": "Visa", "region": "NA"}. A dynamic-schema design (same
    # spirit as sop_rows.data) so adding/removing a column is a cheap row
    # operation on instance_columns + a JSONB update here — never an
    # ALTER TABLE. NOT NULL, defaults to an empty object so every row
    # always has a well-formed map.
    custom_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    updated_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[updated_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Instance id={self.id} name={self.name!r} status={self.status}>"


class InstanceColumn(Base):
    """
    A user-defined custom column shown on the Instance Management table
    (in addition to the fixed built-in columns Instance / Ticket Number /
    Status, which are NOT represented here and cannot be renamed/deleted).

    This is the column DEFINITION (its schema); the per-instance VALUES
    live in Instance.custom_fields JSONB keyed by `key`. Same
    definitions-table + JSONB-values pattern as the SOP domain
    (sop_columns + sop_rows.data).
    """

    __tablename__ = "instance_columns"
    __table_args__ = (
        CheckConstraint(_column_type_check_sql, name="ck_instance_columns_type"),
        Index("ix_instance_columns_sort_order", "sort_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # Stable machine key used as the JSONB key in Instance.custom_fields
    # and never changes even when the display label is renamed — so a
    # rename doesn't require rewriting every instance's data. Unique.
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    # The human-facing header text; renameable. Also the exact header a
    # CSV/Excel upload must use for this column.
    label: Mapped[str] = mapped_column(String(100), nullable=False)

    type: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'text'"))

    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    # Audit trail for the LAST change to this column definition (add or
    # rename) — the ticket this change relates to plus who revised and
    # reviewed it. Captured from the Add Column / Rename Column dialogs,
    # which make all three mandatory. Nullable at the DB level so legacy
    # rows (created before this was added) don't break.
    ticket_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    revised_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Value applied to existing instances when the column is added, and
    # the fallback for optional cells left blank on later writes. Stored
    # as text; coerced/validated per `type` by the service layer.
    default_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Allowed choices when type == 'dropdown' (JSON array of strings);
    # NULL/unused for other types.
    options: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Positioning: this custom column is placed immediately AFTER the
    # column whose key is `after_key`, within the single merged column
    # list that interleaves built-ins (name/ticketNumber/issuerCount/
    # status/updatedBy) and custom columns. `after_key` may reference a
    # built-in key OR another custom column's key. NULL/empty means "at
    # the very beginning" (before the Instance column). The effective
    # left-to-right order is resolved from these anchors by the service
    # (see instance_column_service.build_layout); `sort_order` is a
    # tiebreaker/insertion-time hint only.
    after_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Insertion-time ordering hint / tiebreaker when multiple custom
    # columns share the same after_key.
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<InstanceColumn id={self.id} key={self.key!r} type={self.type}>"


# The fixed built-in columns of the Instance Management table, in their
# DEFAULT left-to-right order. This is the single source of truth for
# which built-ins exist and their initial ordering; the migration seeds
# `instance_builtin_columns` from exactly this list, and the service uses
# it to backfill/repair any missing rows. Custom columns are positioned
# relative to these (or each other) via InstanceColumn.after_key. The
# trailing actions column is a pure UI concern (always pinned last) and
# is NOT part of this orderable set.
BUILTIN_COLUMN_DEFS = (
    {"key": "name", "label": "Instance"},
    {"key": "ticketNumber", "label": "Ticket Number"},
    {"key": "issuerCount", "label": "Issuers"},
    {"key": "status", "label": "Status"},
    {"key": "updatedBy", "label": "Updated By"},
)


class InstanceBuiltinColumn(Base):
    """
    The persisted display ORDER of a built-in Instance Management column
    (Instance / Ticket Number / Issuers / Status / Updated By).

    Built-in columns are not user-created and cannot be renamed or
    deleted, but they ARE now freely reorderable via drag-and-drop, so
    their left-to-right position must survive a reload. Rather than
    hardcode the order in the service, each built-in gets a row here
    holding its `sort_order`. The layout resolver reads these rows to seed
    the merged column order; custom columns then interleave around them by
    their `after_key` anchors.

    `key` is one of the fixed built-in keys (see BUILTIN_COLUMN_DEFS) and
    is the primary key — there is exactly one row per built-in. `label` is
    NOT stored here (built-ins are not renameable); it comes from
    BUILTIN_COLUMN_DEFS so the display text stays in one place.
    """

    __tablename__ = "instance_builtin_columns"
    __table_args__ = (Index("ix_instance_builtin_columns_sort_order", "sort_order"),)

    key: Mapped[str] = mapped_column(String(100), primary_key=True)

    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<InstanceBuiltinColumn key={self.key!r} sort_order={self.sort_order}>"


class InstanceColumnDeletion(Base):
    """
    Audit record of a DELETED custom column.

    When a custom column is removed, its definition row (instance_columns)
    is gone — so the add/rename audit fields that live on that row vanish
    with it. This table preserves the deletion event: which column was
    deleted (its key + label at the time), the ticket it related to, who
    revised and reviewed the change, who performed it, and when.

    This is a WRITE-ONLY audit trail for Instance Management, kept
    separate from the shared `revisions` log (Instance Management is
    intentionally decoupled from that). Rows are never updated or deleted
    by the app.

    `deleted_by_user_id` is a NULLABLE FK ON DELETE SET NULL — same
    reasoning as elsewhere: removing a user later must not break or erase
    an existing audit row; only the attribution clears.
    """

    __tablename__ = "instance_column_deletions"
    __table_args__ = (Index("ix_instance_column_deletions_deleted_at", text("deleted_at DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # The deleted column's stable key and human label, captured at delete
    # time (the definition row no longer exists to look them up).
    column_key: Mapped[str] = mapped_column(String(100), nullable=False)
    column_label: Mapped[str] = mapped_column(String(100), nullable=False)

    # Audit trail captured in the Delete Column dialog (all mandatory).
    ticket_number: Mapped[str] = mapped_column(String(100), nullable=False)
    revised_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)

    deleted_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    deleted_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[deleted_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<InstanceColumnDeletion id={self.id} column_key={self.column_key!r}>"


class InstanceDeletion(Base):
    """
    Audit record of a DELETED instance.

    When an instance is removed, its row (and the ticket/revised_by/
    reviewer stamped on it) is gone — so this table preserves the deletion
    event: which instance was deleted (its id + name at the time), the
    ticket it related to, who revised and reviewed the change, who
    performed it, and when. Write-only audit trail, kept separate from the
    shared `revisions` log (Instance Management is intentionally
    decoupled). Mirrors InstanceColumnDeletion.
    """

    __tablename__ = "instance_deletions"
    __table_args__ = (Index("ix_instance_deletions_deleted_at", text("deleted_at DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # The deleted instance's id and name, captured at delete time.
    instance_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    instance_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Audit trail captured in the Delete Instance dialog (all mandatory).
    ticket_number: Mapped[str] = mapped_column(String(100), nullable=False)
    revised_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)

    deleted_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    deleted_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[deleted_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<InstanceDeletion id={self.id} instance_id={self.instance_id} name={self.instance_name!r}>"


class InstanceEdit(Base):
    """
    Audit record of an EDIT to an instance.

    Every edit path — the Edit Instance form, an inline cell edit, and the
    Activate/Deactivate toggle — captures a ticket plus who revised and
    who reviewed the change. Rather than stamp those onto the instance row
    (which would only keep the LATEST), each edit is appended here as its
    own immutable row, giving a full edit history. Write-only audit trail,
    kept separate from the shared `revisions` log (Instance Management is
    intentionally decoupled). Mirrors InstanceDeletion.

    `edited_by_user_id` is a NULLABLE FK ON DELETE SET NULL so removing a
    user later clears attribution without breaking the audit row.
    """

    __tablename__ = "instance_edits"
    __table_args__ = (
        Index("ix_instance_edits_instance_id", "instance_id"),
        Index("ix_instance_edits_edited_at", text("edited_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # The edited instance's id and name (name captured at edit time).
    instance_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    instance_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Audit trail captured in the edit dialog (all mandatory).
    ticket_number: Mapped[str] = mapped_column(String(100), nullable=False)
    revised_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)

    edited_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    edited_by_user: Mapped[Optional["User"]] = relationship(foreign_keys=[edited_by_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<InstanceEdit id={self.id} instance_id={self.instance_id} name={self.instance_name!r}>"
