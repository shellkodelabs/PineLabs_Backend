"""allow completed_with_errors on import_files (sheet-level atomicity)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-30 12:00:00.000000

Import atomicity moved from per-FILE to per-SHEET: a file's clean sheets
import while its bad sheets are skipped, so a file can now finish in the
"completed_with_errors" state (previously a file was all-or-nothing and
only ever completed/failed). This widens the import_files status CHECK to
include that value. import_sheets stays restricted to the atomic set
(a sheet is only ever completed or failed), so its CHECK is unchanged.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD = "status IN ('queued', 'processing', 'completed', 'failed')"
_NEW = "status IN ('queued', 'processing', 'completed', 'failed', 'completed_with_errors')"


def upgrade() -> None:
    op.drop_constraint("ck_import_files_status", "import_files", type_="check")
    op.create_check_constraint("ck_import_files_status", "import_files", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_import_files_status", "import_files", type_="check")
    op.create_check_constraint("ck_import_files_status", "import_files", _OLD)
