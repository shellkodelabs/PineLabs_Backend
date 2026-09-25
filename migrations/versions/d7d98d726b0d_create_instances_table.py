"""create instances table

Revision ID: d7d98d726b0d
Revises: 20febaabcb7b
Create Date: 2026-09-23 13:10:49.539243

Bin Series gap-analysis API #1 — backs GET /api/v1/instances, which
supplies the "Select an instance" dropdown on the frontend's BIN Series
Add/Clone form.

Purely additive: creates one new table only. Does not touch
`bin_records` (including `instance_name`, still a plain string — no FK
relationship to this table is created here, that decision is explicitly
deferred) or any other existing table, column, index, or constraint.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7d98d726b0d'
down_revision: Union[str, None] = '20febaabcb7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instances",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'Active'")),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], name="fk_instances_updated_by_user_id", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("name", name="uq_instances_name"),
        sa.CheckConstraint("status IN ('Active', 'Inactive')", name="ck_instances_status"),
    )
    op.create_index("ix_instances_status", "instances", ["status"])


def downgrade() -> None:
    op.drop_table("instances")
