"""add revised_by + reviewer to instances

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-08 10:00:00.000000

Adds the audit-trail columns captured alongside the ticket number on
every Instance Management create/edit:

  - instances.revised_by : who revised the instance (free text name).
  - instances.reviewer   : who reviewed that change (free text name).

Both are additive and NULLABLE so existing rows and the bulk-import path
(which does not collect these) are unaffected. The API's create schema
makes them mandatory for interactive add/edit; the column stays nullable
at the DB level to avoid breaking legacy rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("instances", sa.Column("revised_by", sa.String(length=255), nullable=True))
    op.add_column("instances", sa.Column("reviewer", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("instances", "reviewer")
    op.drop_column("instances", "revised_by")
