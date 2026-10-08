"""add ticket_number + revised_by + reviewer to instance_columns

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-08 11:00:00.000000

Adds the audit-trail columns captured when a custom column is added or
renamed (the Add Column / Rename Column dialogs make all three
mandatory):

  - instance_columns.ticket_number : the ticket this column change relates to.
  - instance_columns.revised_by    : who revised the column.
  - instance_columns.reviewer      : who reviewed the change.

All NULLABLE so column rows created before this migration are unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("instance_columns", sa.Column("ticket_number", sa.String(length=100), nullable=True))
    op.add_column("instance_columns", sa.Column("revised_by", sa.String(length=255), nullable=True))
    op.add_column("instance_columns", sa.Column("reviewer", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("instance_columns", "reviewer")
    op.drop_column("instance_columns", "revised_by")
    op.drop_column("instance_columns", "ticket_number")
