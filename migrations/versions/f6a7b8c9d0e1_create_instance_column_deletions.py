"""create instance_column_deletions audit table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-08 12:00:00.000000

A write-only audit trail of DELETED custom columns. When a custom column
is removed, its definition row (and the add/rename audit on it) is gone,
so the deletion event is preserved here instead: the deleted column's
key/label, the ticket it related to, who revised and reviewed it, who
performed the delete, and when.

`deleted_by_user_id` is a NULLABLE FK ON DELETE SET NULL so removing a
user later clears attribution without breaking the audit row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instance_column_deletions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("column_key", sa.String(length=100), nullable=False),
        sa.Column("column_label", sa.String(length=100), nullable=False),
        sa.Column("ticket_number", sa.String(length=100), nullable=False),
        sa.Column("revised_by", sa.String(length=255), nullable=False),
        sa.Column("reviewer", sa.String(length=255), nullable=False),
        sa.Column("deleted_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["deleted_by_user_id"],
            ["users.id"],
            name="fk_instance_column_deletions_deleted_by_user_id",
            ondelete="SET NULL",
        ),
    )
    op.execute(
        "CREATE INDEX ix_instance_column_deletions_deleted_at "
        "ON instance_column_deletions (deleted_at DESC)"
    )


def downgrade() -> None:
    op.drop_index("ix_instance_column_deletions_deleted_at", table_name="instance_column_deletions")
    op.drop_table("instance_column_deletions")
