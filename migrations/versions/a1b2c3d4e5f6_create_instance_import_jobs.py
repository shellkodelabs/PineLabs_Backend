"""create instance import job tables

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-30 10:00:00.000000

Creates the three tables backing the background Instance Management import
job (see app/models/instance_import.py):

  - `import_jobs`   : one multi-file upload; roll-up progress counters and
                      lifecycle status; nullable created_by_user_id FK
                      (ON DELETE SET NULL).
  - `import_files`  : one uploaded file per job (ON DELETE CASCADE from the
                      job); per-file counters, staged_path, status.
  - `import_sheets` : one worksheet per file (ON DELETE CASCADE from the
                      file); per-sheet counters and a JSONB `errors` array
                      of {row, messages[]} entries.

Status columns are String + CHECK constraint (no DB enum type), matching
the instances/users convention. Purely additive — chains onto the
consolidated instance-management schema (f1a2b3c4d5e6), which owns the
`instances`/`users` tables these reference.

NOTE: the migration tree has a second head (e6dc40b5767e, the bin custom
columns branch off 20febaabcb7b). This revision chains onto the instance
head; if a single linear head is desired, author a separate `alembic
merge` revision to join e6dc40b5767e and a1b2c3d4e5f6.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_JOB_STATUS_CHECK = (
    "status IN ('queued', 'processing', 'completed', 'failed', 'completed_with_errors')"
)
_CHILD_STATUS_CHECK = "status IN ('queued', 'processing', 'completed', 'failed')"


def upgrade() -> None:
    # --- import_jobs --------------------------------------------------
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("total_files", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed_files", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_sheets", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed_sheets", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_import_jobs_created_by_user_id", ondelete="SET NULL"
        ),
        sa.CheckConstraint(_JOB_STATUS_CHECK, name="ck_import_jobs_status"),
    )
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"])
    op.create_index("ix_import_jobs_created_at", "import_jobs", ["created_at"])

    # --- import_files -------------------------------------------------
    op.create_table(
        "import_files",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("import_job_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("file_type", sa.String(length=16), nullable=False),
        sa.Column("staged_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("total_sheets", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed_sheets", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["import_job_id"], ["import_jobs.id"], name="fk_import_files_import_job_id", ondelete="CASCADE"
        ),
        sa.CheckConstraint(_CHILD_STATUS_CHECK, name="ck_import_files_status"),
    )
    op.create_index("ix_import_files_job_id", "import_files", ["import_job_id"])

    # --- import_sheets ------------------------------------------------
    op.create_table(
        "import_sheets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("import_file_id", sa.BigInteger(), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_rows", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["import_file_id"], ["import_files.id"], name="fk_import_sheets_import_file_id", ondelete="CASCADE"
        ),
        sa.CheckConstraint(_CHILD_STATUS_CHECK, name="ck_import_sheets_status"),
    )
    op.create_index("ix_import_sheets_file_id", "import_sheets", ["import_file_id"])


def downgrade() -> None:
    op.drop_index("ix_import_sheets_file_id", table_name="import_sheets")
    op.drop_table("import_sheets")

    op.drop_index("ix_import_files_job_id", table_name="import_files")
    op.drop_table("import_files")

    op.drop_index("ix_import_jobs_created_at", table_name="import_jobs")
    op.drop_index("ix_import_jobs_status", table_name="import_jobs")
    op.drop_table("import_jobs")
