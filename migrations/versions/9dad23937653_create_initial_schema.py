"""create initial schema

Revision ID: 9dad23937653
Revises:
Create Date: 2026-09-16 22:06:05.705688

Creates all 9 tables from the backend design (Part 3) / SQLAlchemy models
(Part 5): merchants, bin_records, sop_sheets, sop_column_groups,
sop_columns, sop_rows, users, user_sop_sheet_access, revisions.

Hand-authored rather than produced by `alembic revision --autogenerate`:
autogenerate does not reliably emit CHECK constraints, partial/expression
indexes, or CREATE EXTENSION statements, all of which this schema needs.
Every constraint/index below was cross-checked against the SQLAlchemy
models in app/models/ to confirm autogenerate would not have silently
diverged from them.

Table creation order follows FK dependencies:
  merchants, users (no deps)
  -> bin_records, sop_sheets (depend on merchants)
  -> sop_column_groups (depends on sop_sheets)
  -> sop_columns (depends on sop_column_groups)
  -> sop_rows (depends on sop_sheets)
  -> user_sop_sheet_access (depends on users, sop_sheets)
  -> revisions (depends on users)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9dad23937653'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Required for every trigram (substring) search index created below.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------
    # merchants
    # ------------------------------------------------------------------
    op.create_table(
        "merchants",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("name", name="uq_merchants_name"),
        sa.CheckConstraint(
            "classification IN ('Digital Gift Card', 'Physical Gift Card', 'Corporate Gifting', 'Reward Card')",
            name="ck_merchants_classification",
        ),
    )
    op.create_index("ix_merchants_classification", "merchants", ["classification"])
    # Expression trigram index — substring search on name.
    op.execute(
        "CREATE INDEX ix_merchants_name_trgm ON merchants USING gin (name gin_trgm_ops)"
    )

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("mobile", sa.String(length=30), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'Invited'")),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint(
            "role IN ('Admin', 'Support Lead', 'Support Agent', 'Auditor')",
            name="ck_users_role",
        ),
        sa.CheckConstraint(
            "status IN ('Active', 'Inactive', 'Invited')",
            name="ck_users_status",
        ),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_status", "users", ["status"])
    op.execute(
        "CREATE INDEX ix_users_search ON users USING gin ((name || ' ' || email || ' ' || role) gin_trgm_ops)"
    )

    # ------------------------------------------------------------------
    # bin_records
    # ------------------------------------------------------------------
    op.create_table(
        "bin_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("card_program_group_name", sa.String(length=255), nullable=False),
        sa.Column("bin_iin", sa.CHAR(length=6), nullable=False),
        sa.Column("merchant_prefix", sa.CHAR(length=3), nullable=False),
        sa.Column("merchant_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], name="fk_bin_records_merchant_id", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("bin_iin", "merchant_prefix", name="uq_bin_records_bin_prefix"),
        sa.CheckConstraint("bin_iin ~ '^[0-9]{6}$'", name="ck_bin_records_bin_iin_format"),
        sa.CheckConstraint("merchant_prefix ~ '^[0-9]{3}$'", name="ck_bin_records_merchant_prefix_format"),
    )
    op.create_index("ix_bin_records_bin_iin", "bin_records", ["bin_iin"])
    op.execute(
        "CREATE INDEX ix_bin_records_search ON bin_records "
        "USING gin ((issuer || ' ' || card_program_group_name) gin_trgm_ops)"
    )

    # ------------------------------------------------------------------
    # sop_sheets
    # ------------------------------------------------------------------
    op.create_table(
        "sop_sheets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("merchant_id", sa.BigInteger(), nullable=True),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], name="fk_sop_sheets_merchant_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("merchant_id", "key", name="uq_sop_sheets_merchant_key"),
    )
    op.create_index("ix_sop_sheets_merchant_id", "sop_sheets", ["merchant_id"])
    # Partial unique index: at most one shared/default sheet per key
    # (merchant_id IS NULL) — this is how commonEscalation is represented.
    # No fake "Default Merchant" row.
    op.create_index(
        "uq_sop_sheets_shared_default_key",
        "sop_sheets",
        ["key"],
        unique=True,
        postgresql_where=sa.text("merchant_id IS NULL"),
    )

    # ------------------------------------------------------------------
    # sop_column_groups
    # ------------------------------------------------------------------
    op.create_table(
        "sop_column_groups",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("sheet_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sheet_id"], ["sop_sheets.id"], name="fk_sop_column_groups_sheet_id", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_sop_column_groups_sheet_id", "sop_column_groups", ["sheet_id"])

    # ------------------------------------------------------------------
    # sop_columns
    # ------------------------------------------------------------------
    op.create_table(
        "sop_columns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"], ["sop_column_groups.id"], name="fk_sop_columns_group_id", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_sop_columns_group_id", "sop_columns", ["group_id"])

    # ------------------------------------------------------------------
    # sop_rows
    # ------------------------------------------------------------------
    op.create_table(
        "sop_rows",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("sheet_id", sa.BigInteger(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["sheet_id"], ["sop_sheets.id"], name="fk_sop_rows_sheet_id", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_sop_rows_sheet_id", "sop_rows", ["sheet_id"])
    op.create_index("ix_sop_rows_data_gin", "sop_rows", ["data"], postgresql_using="gin")

    # ------------------------------------------------------------------
    # user_sop_sheet_access
    # ------------------------------------------------------------------
    op.create_table(
        "user_sop_sheet_access",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("sheet_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_sop_sheet_access_user_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sheet_id"], ["sop_sheets.id"], name="fk_user_sop_sheet_access_sheet_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "sheet_id", name="pk_user_sop_sheet_access"),
    )
    op.create_index("ix_user_sop_sheet_access_sheet_id", "user_sop_sheet_access", ["sheet_id"])

    # ------------------------------------------------------------------
    # revisions
    # ------------------------------------------------------------------
    op.create_table(
        "revisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("target_label", sa.String(length=255), nullable=False),
        sa.Column("change_description", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # No ON DELETE clause (defaults to NO ACTION) — a user with audit
        # history should not be silently deletable out from under it.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_revisions_user_id"),
        sa.CheckConstraint(
            "action_type IN ('create', 'update', 'delete', 'upload')",
            name="ck_revisions_action_type",
        ),
        sa.CheckConstraint(
            "entity_type IN ('bin_record', 'merchant', 'sop_sheet', 'sop_row', 'user')",
            name="ck_revisions_entity_type",
        ),
    )
    op.execute("CREATE INDEX ix_revisions_occurred_at ON revisions (occurred_at DESC)")
    op.create_index("ix_revisions_action_type", "revisions", ["action_type"])
    op.create_index("ix_revisions_entity_type", "revisions", ["entity_type"])
    op.create_index("ix_revisions_user_id", "revisions", ["user_id"])
    op.execute(
        "CREATE INDEX ix_revisions_search ON revisions "
        "USING gin ((target_label || ' ' || change_description) gin_trgm_ops)"
    )


def downgrade() -> None:
    # Reverse FK dependency order.
    op.drop_table("revisions")
    op.drop_table("user_sop_sheet_access")
    op.drop_table("sop_rows")
    op.drop_table("sop_columns")
    op.drop_table("sop_column_groups")
    op.drop_table("sop_sheets")
    op.drop_table("bin_records")
    op.drop_table("users")
    op.drop_table("merchants")
    # pg_trgm is intentionally left installed — it is a shared, cluster/DB
    # -level extension that other schemas/objects could depend on; dropping
    # it is not this migration's responsibility to reverse.
