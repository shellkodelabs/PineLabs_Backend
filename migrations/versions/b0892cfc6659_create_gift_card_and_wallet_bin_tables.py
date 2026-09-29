"""create gift card and wallet bin tables

Revision ID: b0892cfc6659
Revises: e6dc40b5767e
Create Date: 2026-09-28 14:46:02.075025

New Excel-format Bin Series task, Stage 1 (migration only) — confirmed
architecture: two completely separate record tables (no shared bin_type
discriminator table), per client Excel format:

  gift_card_bin_records: instance, issuer, merchant,
    card_program_group_name, bin_iin, merchant_prefix,
    card_program_group_type, card_type, ticket_number, status,
    custom_fields, updated_by_user_id, created_at, updated_at.

  wallet_bin_records: same shape, minus card_program_group_name/
    card_program_group_type/card_type, plus wallet_program_name/
    wallet_program_group_type.

Both are brand-new tables with zero legacy rows, so — unlike
bin_records.instance_name, which had to be nullable-in-DB +
app-enforced to avoid breaking 611 pre-existing rows with no value to
backfill — every business field here is a true NOT NULL column; there
is no backward-compatibility burden to split the enforcement for.

gift_card_bin_custom_columns / wallet_bin_custom_columns: independent
per-type mirrors of the existing bin_custom_columns metadata-registry
shape (id, key[immutable/unique], name[mutable/unique], display_order,
updated_by_user_id, created_at, updated_at) — confirmed decision: two
separate registries, not one shared one. Each new record table's own
`custom_fields` JSONB holds that type's row-level values, keyed by its
OWN registry's `key` (gift_card_bin_records.custom_fields is keyed by
gift_card_bin_custom_columns.key; wallet is independent).

CROSS-TABLE (bin_iin, merchant_prefix) UNIQUENESS: confirmed decision —
NOT enforced here. A plain UNIQUE constraint cannot span two
independent tables, and no reservation-table or other DB-level
mechanism is being introduced for it now (explicitly deferred). Each
table gets its own UNIQUE(bin_iin, merchant_prefix) covering only
within-type duplicates; cross-type uniqueness becomes an
application-level check in a later stage.

revisions.entity_type CHECK constraint: expanded (dropped and
recreated — Postgres has no ALTER CHECK, same mechanism the Dynamic
Columns task used) to ADD four new values — gift_card_bin_record,
wallet_bin_record, gift_card_bin_custom_column,
wallet_bin_custom_column — for the new tables' audit events. Every
previously allowed value (bin_record, merchant, sop_sheet, sop_row,
user, bin_custom_column) is kept, unchanged, so historical revision
rows referencing the old (untouched, still-legacy) bin_records/
bin_custom_columns tables remain valid.

Does NOT touch: bin_records, bin_custom_columns, instances, merchants,
sop_*, users, or any of their existing columns/constraints/indexes.
Nothing is migrated from the 611 legacy bin_records rows — confirmed
decision: they lack the data (merchant, ticketNumber,
cardProgramGroupType, cardType, status) to safely populate the new
mandatory fields, so no data migration is attempted.

Indexes: `ix_<table>_bin_iin` on each new record table, mirroring the
existing `ix_bin_records_bin_iin` — the shared lookup/bulk-lookup
endpoint (a later stage) will query both tables by bin_iin, same access
pattern the old single-table lookup already relied on that index for.
`ix_<table>_status` on each, mirroring `ix_instances_status`, for the
Active/Inactive view tabs. No trigram search index — not part of this
stage's requested scope, and unlike the old bin_records table, nothing
here yet requires a substring-search access path.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b0892cfc6659'
down_revision: Union[str, None] = 'e6dc40b5767e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_ENTITY_TYPES = "'bin_record', 'merchant', 'sop_sheet', 'sop_row', 'user', 'bin_custom_column'"
_NEW_ENTITY_TYPES = (
    "'gift_card_bin_record', 'wallet_bin_record', "
    "'gift_card_bin_custom_column', 'wallet_bin_custom_column'"
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # gift_card_bin_records
    # ------------------------------------------------------------------
    op.create_table(
        "gift_card_bin_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("instance", sa.String(length=255), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("merchant", sa.String(length=255), nullable=False),
        sa.Column("card_program_group_name", sa.String(length=255), nullable=False),
        sa.Column("bin_iin", sa.CHAR(length=6), nullable=False),
        sa.Column("merchant_prefix", sa.CHAR(length=3), nullable=False),
        sa.Column("card_program_group_type", sa.String(length=255), nullable=False),
        sa.Column("card_type", sa.String(length=255), nullable=False),
        sa.Column("ticket_number", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'Active'")),
        sa.Column(
            "custom_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_gift_card_bin_records_updated_by_user_id", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("bin_iin", "merchant_prefix", name="uq_gift_card_bin_records_bin_prefix"),
        sa.CheckConstraint("bin_iin ~ '^[0-9]{6}$'", name="ck_gift_card_bin_records_bin_iin_format"),
        sa.CheckConstraint("merchant_prefix ~ '^[0-9]{3}$'", name="ck_gift_card_bin_records_merchant_prefix_format"),
        sa.CheckConstraint("status IN ('Active', 'Inactive')", name="ck_gift_card_bin_records_status"),
    )
    op.create_index("ix_gift_card_bin_records_bin_iin", "gift_card_bin_records", ["bin_iin"])
    op.create_index("ix_gift_card_bin_records_status", "gift_card_bin_records", ["status"])

    # ------------------------------------------------------------------
    # wallet_bin_records
    # ------------------------------------------------------------------
    op.create_table(
        "wallet_bin_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("instance", sa.String(length=255), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("merchant", sa.String(length=255), nullable=False),
        sa.Column("wallet_program_name", sa.String(length=255), nullable=False),
        sa.Column("bin_iin", sa.CHAR(length=6), nullable=False),
        sa.Column("merchant_prefix", sa.CHAR(length=3), nullable=False),
        sa.Column("wallet_program_group_type", sa.String(length=255), nullable=False),
        sa.Column("ticket_number", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'Active'")),
        sa.Column(
            "custom_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_wallet_bin_records_updated_by_user_id", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("bin_iin", "merchant_prefix", name="uq_wallet_bin_records_bin_prefix"),
        sa.CheckConstraint("bin_iin ~ '^[0-9]{6}$'", name="ck_wallet_bin_records_bin_iin_format"),
        sa.CheckConstraint("merchant_prefix ~ '^[0-9]{3}$'", name="ck_wallet_bin_records_merchant_prefix_format"),
        sa.CheckConstraint("status IN ('Active', 'Inactive')", name="ck_wallet_bin_records_status"),
    )
    op.create_index("ix_wallet_bin_records_bin_iin", "wallet_bin_records", ["bin_iin"])
    op.create_index("ix_wallet_bin_records_status", "wallet_bin_records", ["status"])

    # ------------------------------------------------------------------
    # gift_card_bin_custom_columns (independent per-type registry)
    # ------------------------------------------------------------------
    op.create_table(
        "gift_card_bin_custom_columns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_gift_card_bin_custom_columns_updated_by_user_id", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("key", name="uq_gift_card_bin_custom_columns_key"),
        sa.UniqueConstraint("name", name="uq_gift_card_bin_custom_columns_name"),
    )

    # ------------------------------------------------------------------
    # wallet_bin_custom_columns (independent per-type registry)
    # ------------------------------------------------------------------
    op.create_table(
        "wallet_bin_custom_columns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_wallet_bin_custom_columns_updated_by_user_id", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("key", name="uq_wallet_bin_custom_columns_key"),
        sa.UniqueConstraint("name", name="uq_wallet_bin_custom_columns_name"),
    )

    # ------------------------------------------------------------------
    # revisions.entity_type — additive expansion only, every existing
    # value is kept so historical rows referencing them stay valid.
    # ------------------------------------------------------------------
    op.drop_constraint("ck_revisions_entity_type", "revisions", type_="check")
    op.create_check_constraint(
        "ck_revisions_entity_type",
        "revisions",
        f"entity_type IN ({_OLD_ENTITY_TYPES}, {_NEW_ENTITY_TYPES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_revisions_entity_type", "revisions", type_="check")
    op.create_check_constraint(
        "ck_revisions_entity_type",
        "revisions",
        f"entity_type IN ({_OLD_ENTITY_TYPES})",
    )

    op.drop_table("wallet_bin_custom_columns")
    op.drop_table("gift_card_bin_custom_columns")
    op.drop_table("wallet_bin_records")
    op.drop_table("gift_card_bin_records")
