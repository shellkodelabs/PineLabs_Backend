"""add updated_by_user_id to bin_records

Revision ID: 47abf3f22664
Revises: 9dad23937653
Create Date: 2026-09-22 11:24:30.054116

Part 16 — BIN Series CRUD APIs. Adds `updated_by_user_id`, a nullable soft
FK to `users`, so the API can expose "updatedBy" for a BIN record without
needing an extra revisions lookup on every list/resolve read (see
app/services/bin_service.py and app/repositories/bin_repository.py).

Purely additive: NULL for all 611 pre-existing seeded records (and for
any record the new write APIs never touch) — no backfill, no data
rewrite, no change to any existing column, index, or constraint. In
particular, the `uq_bin_records_bin_prefix` unique constraint on
(bin_iin, merchant_prefix) is left completely untouched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '47abf3f22664'
down_revision: Union[str, None] = '9dad23937653'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bin_records", sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_bin_records_updated_by_user_id",
        "bin_records",
        "users",
        ["updated_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_bin_records_updated_by_user_id", "bin_records", type_="foreignkey")
    op.drop_column("bin_records", "updated_by_user_id")
