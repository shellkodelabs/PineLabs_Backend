"""add bin custom columns

Revision ID: e6dc40b5767e
Revises: d7d98d726b0d
Create Date: 2026-09-24 00:39:18.826542

Dynamic/Custom Columns task — backs BinTable.jsx's "Add Column" feature.

Additive:
  - New table `bin_custom_columns` (metadata registry: id, key [immutable,
    unique], name [mutable display label], display_order, updated_by_user_id,
    created_at, updated_at).
  - `bin_records.custom_fields JSONB NOT NULL DEFAULT '{}'` — per-row
    custom-column values, keyed by bin_custom_columns.key. Existing 611
    seeded rows get '{}' via the server_default, no backfill needed, no
    row rewrite required.
  - `revisions.entity_type` CHECK constraint expanded to also allow
    'bin_custom_column' (column create/rename audit events) — dropped
    and recreated since PostgreSQL has no ALTER CHECK; every previously
    allowed value stays allowed, only one new value is added.

Does not touch bin_records' existing columns/constraints/indexes
(uq_bin_records_bin_prefix, instance_name, updated_by_user_id, etc.) or
any other existing table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e6dc40b5767e'
down_revision: Union[str, None] = 'd7d98d726b0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bin_custom_columns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], name="fk_bin_custom_columns_updated_by_user_id", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("key", name="uq_bin_custom_columns_key"),
        sa.UniqueConstraint("name", name="uq_bin_custom_columns_name"),
    )

    op.add_column(
        "bin_records",
        sa.Column("custom_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.drop_constraint("ck_revisions_entity_type", "revisions", type_="check")
    op.create_check_constraint(
        "ck_revisions_entity_type",
        "revisions",
        "entity_type IN ('bin_record', 'merchant', 'sop_sheet', 'sop_row', 'user', 'bin_custom_column')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_revisions_entity_type", "revisions", type_="check")
    op.create_check_constraint(
        "ck_revisions_entity_type",
        "revisions",
        "entity_type IN ('bin_record', 'merchant', 'sop_sheet', 'sop_row', 'user')",
    )

    op.drop_column("bin_records", "custom_fields")

    op.drop_table("bin_custom_columns")
