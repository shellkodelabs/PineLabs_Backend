"""add instance_name to bin_records

Revision ID: 20febaabcb7b
Revises: 47abf3f22664
Create Date: 2026-09-22 12:21:14.525685

Part 17 — instanceName becomes mandatory for BIN write operations.

Adds `instance_name`, a plain nullable string column — NOT a foreign key.
No backend `instances` table exists anywhere in this codebase (the
frontend's `instances` concept in src/data/sopData.js has no backend
model at all), so there is nothing to reference; see
app/models/bin_record.py's module docstring for the full reasoning.

Deliberately NULLABLE at the database level: the 611 pre-existing BIN
records (and any other legacy row) have no instance information to
backfill from, and a NOT NULL constraint here would break the migration
against real data. "Mandatory" is enforced at the application layer
instead (app/services/bin_service.py) — required on create, and on
update whenever a real write would otherwise leave the record without a
non-blank instance_name. Purely additive: no existing column, index, or
constraint (including uq_bin_records_bin_prefix) is touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20febaabcb7b'
down_revision: Union[str, None] = '47abf3f22664'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bin_records", sa.Column("instance_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("bin_records", "instance_name")
