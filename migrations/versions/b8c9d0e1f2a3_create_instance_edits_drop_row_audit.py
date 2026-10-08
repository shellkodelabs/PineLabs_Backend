"""create instance_edits audit table; drop revised_by/reviewer from instances

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-08 14:00:00.000000

Revised By / Reviewer are no longer captured on CREATE or IMPORT, and are
no longer stored on the instance ROW. They are now an EDIT/DELETE audit
concern only:

  - every edit (edit form, inline cell edit, activate/deactivate toggle)
    is appended to the new `instance_edits` table, and
  - every deletion is already recorded in `instance_deletions`.

So this migration:
  1. creates `instance_edits` (write-only edit audit), and
  2. DROPS `instances.revised_by` and `instances.reviewer`.

`edited_by_user_id` is a NULLABLE FK ON DELETE SET NULL (clears
attribution without breaking the audit row).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instance_edits",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("instance_id", sa.BigInteger(), nullable=False),
        sa.Column("instance_name", sa.String(length=255), nullable=False),
        sa.Column("ticket_number", sa.String(length=100), nullable=False),
        sa.Column("revised_by", sa.String(length=255), nullable=False),
        sa.Column("reviewer", sa.String(length=255), nullable=False),
        sa.Column("edited_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["edited_by_user_id"],
            ["users.id"],
            name="fk_instance_edits_edited_by_user_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_instance_edits_instance_id", "instance_edits", ["instance_id"])
    op.execute("CREATE INDEX ix_instance_edits_edited_at ON instance_edits (edited_at DESC)")

    # Drop the now-unused row-level audit columns.
    op.drop_column("instances", "reviewer")
    op.drop_column("instances", "revised_by")


def downgrade() -> None:
    op.add_column("instances", sa.Column("revised_by", sa.String(length=255), nullable=True))
    op.add_column("instances", sa.Column("reviewer", sa.String(length=255), nullable=True))

    op.drop_index("ix_instance_edits_edited_at", table_name="instance_edits")
    op.drop_index("ix_instance_edits_instance_id", table_name="instance_edits")
    op.drop_table("instance_edits")
