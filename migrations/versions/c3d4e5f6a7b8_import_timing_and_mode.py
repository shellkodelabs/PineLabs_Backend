"""add worker_mode + per-file started_at for import timing

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-30 13:30:00.000000

Adds the two columns needed to report import LATENCY and attribute it to a
mode:

  - import_jobs.worker_mode  : "sequential" | "parallel" — which mode ran
    the job (captured at submit), so overall-duration comparisons stay
    attributable. Defaults to 'sequential'.
  - import_files.started_at  : when the worker PICKED UP the file (vs.
    created_at = when it was queued), so per-file duration is true
    processing time excluding queue wait.

Both are additive/nullable-or-defaulted, so existing rows are unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "import_jobs",
        sa.Column("worker_mode", sa.String(length=20), nullable=False, server_default=sa.text("'sequential'")),
    )
    op.add_column(
        "import_files",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_files", "started_at")
    op.drop_column("import_jobs", "worker_mode")
