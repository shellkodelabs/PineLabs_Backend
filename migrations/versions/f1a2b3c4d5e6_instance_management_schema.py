"""instance management schema (consolidated)

Revision ID: f1a2b3c4d5e6
Revises: 20febaabcb7b
Create Date: 2026-09-29 10:00:00.000000

CONSOLIDATED Instance Management schema — a single migration that replaces
the four incremental ones authored during development:

  b2c3d4e5f6a7  add instances table
  c7d8e9f0a1b2  add instance custom columns (+ instances.custom_fields)
  d8e9f0a1b2c3  add after_key to instance_columns
  e9f0a1b2c3d4  add instance builtin column order (+ seed)

Those four have been removed and replaced by this one so the migration
history is a single clean step for the whole feature. It chains onto the
same parent the first of them did (20febaabcb7b), and its `upgrade`
produces the exact final state the four together produced. Existing
databases that already ran the four are stamped directly to this revision
(the objects already exist; nothing is re-run).

Objects created:
  - `instances` table: unique name, Active/Inactive status (CHECK),
    optional ticket_number, nullable updated_by_user_id FK
    (ON DELETE SET NULL), timestamps, a JSONB `custom_fields` map
    (NOT NULL default '{}') holding the user-defined column values, plus
    a pg_trgm substring-search index on `name`.
  - `instance_columns` table: the user-defined custom column DEFINITIONS
    (key, label, type CHECK, required, default_value, options JSONB,
    after_key positioning anchor, sort_order, timestamps).
  - `instance_builtin_columns` table: the persisted display ORDER of the
    built-in columns (key PK, sort_order), seeded with the five built-ins
    in their default left-to-right order.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "20febaabcb7b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The built-in column keys in their default left-to-right order. Kept in
# lockstep with app.models.instance.BUILTIN_COLUMN_DEFS.
_BUILTIN_KEYS_IN_ORDER = ("name", "ticketNumber", "issuerCount", "status", "updatedBy")


def upgrade() -> None:
    # --- instances (incl. custom_fields) ------------------------------
    op.create_table(
        "instances",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'Active'")),
        sa.Column("ticket_number", sa.String(length=100), nullable=True),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "custom_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], name="fk_instances_updated_by_user_id", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("name", name="uq_instances_name"),
        sa.CheckConstraint(
            "status IN ('Active', 'Inactive')",
            name="ck_instances_status",
        ),
    )
    op.create_index("ix_instances_status", "instances", ["status"])
    op.execute("CREATE INDEX ix_instances_name_trgm ON instances USING gin (name gin_trgm_ops)")

    # --- instance_columns (custom column definitions, incl. after_key) -
    op.create_table(
        "instance_columns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False, server_default=sa.text("'text'")),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("default_value", sa.Text(), nullable=True),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_key", sa.String(length=100), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("key", name="uq_instance_columns_key"),
        sa.CheckConstraint(
            "type IN ('text', 'number', 'date', 'dropdown')",
            name="ck_instance_columns_type",
        ),
    )
    op.create_index("ix_instance_columns_sort_order", "instance_columns", ["sort_order"])

    # --- instance_builtin_columns (persisted built-in order) + seed ----
    builtin_columns = op.create_table(
        "instance_builtin_columns",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "ix_instance_builtin_columns_sort_order", "instance_builtin_columns", ["sort_order"]
    )
    op.bulk_insert(
        builtin_columns,
        [{"key": key, "sort_order": index} for index, key in enumerate(_BUILTIN_KEYS_IN_ORDER)],
    )


def downgrade() -> None:
    op.drop_index("ix_instance_builtin_columns_sort_order", table_name="instance_builtin_columns")
    op.drop_table("instance_builtin_columns")

    op.drop_index("ix_instance_columns_sort_order", table_name="instance_columns")
    op.drop_table("instance_columns")

    op.drop_index("ix_instances_name_trgm", table_name="instances")
    op.drop_index("ix_instances_status", table_name="instances")
    op.drop_table("instances")
