"""
SQLAlchemy models for the background Instance Management import job.

A single upload of one-or-more CSV/XLSX files becomes one ImportJob. The
job fans out into one ImportFile per uploaded file, and each file fans
out into one ImportSheet per worksheet (a CSV is a single sheet). This
three-level tree is what lets the frontend show exactly which FILE, which
SHEET, and which ROW an error came from, plus live per-file/per-sheet
progress while the worker runs.

WHY A DB-BACKED JOB (not just an in-memory dict): progress must survive
being read by any request (polling GET /instances/import/{jobId}) and the
job outlives the original HTTP request that created it — the request
returns immediately with a job id while a background worker processes the
files. Persisting the tree in Postgres is the simplest store that both
the worker (writer) and the poll endpoint (reader) can share, with no new
infrastructure (no Redis/Celery) — matching this codebase's current
"plain FastAPI + SQLAlchemy" footprint.

ATOMICITY MODEL (documented, deliberate): each SHEET is atomic. Every
sheet is validated independently; a sheet with any header/row error is
SKIPPED whole (nothing written for it) while the CLEAN sheets in the same
file still import. So a file may end "completed" (all sheets clean),
"completed_with_errors" (some sheets skipped), or "failed" (every sheet
skipped); the other files in the job are always independent. A skipped
sheet still has ALL its problems collected and reported (every bad row,
every bad column) so the user can fix them in one pass — but we store
only the compact error entries, never the sheet's raw rows, so a file
with many sheets stays cheap to track. See
app/services/instance_import_service.py.

Status columns follow the same CHECK-constraint-from-a-value-tuple
pattern as Instance.status / User.status (no DB enum type), so adding a
state later is a value-tuple edit + a CHECK-recreate migration.
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Lifecycle states shared by a job, a file, and a sheet. Not every state
# is meaningful at every level, but one vocabulary keeps the frontend
# rendering logic uniform:
#   queued     -> accepted, not started
#   processing -> currently being worked on
#   completed  -> finished with zero errors (all rows written)
#   failed     -> finished but rejected (validation errors; nothing
#                 written at that file's/sheet's level)
#   completed_with_errors -> job-level only: the job finished but at least
#                 one file failed while others succeeded
IMPORT_STATUS_VALUES = (
    "queued",
    "processing",
    "completed",
    "failed",
    "completed_with_errors",
)

_job_status_check_sql = "status IN ({})".format(
    ", ".join(f"'{value}'" for value in IMPORT_STATUS_VALUES)
)
# A FILE can be "completed_with_errors" (sheet-level atomicity: some of
# its sheets imported, some were skipped). A SHEET is atomic — it is only
# ever fully completed or fully failed — so it never carries that state.
_file_status_check_sql = "status IN ({})".format(
    ", ".join(f"'{value}'" for value in IMPORT_STATUS_VALUES)
)
_sheet_status_values = tuple(v for v in IMPORT_STATUS_VALUES if v != "completed_with_errors")
_sheet_status_check_sql = "status IN ({})".format(
    ", ".join(f"'{value}'" for value in _sheet_status_values)
)


class ImportJob(Base):
    """One multi-file import submission. Holds the roll-up counters the
    progress endpoint reads. Counters are maintained by the worker as it
    processes each file/sheet, so a poll can compute overall progress
    (row-based) without walking every child row."""

    __tablename__ = "import_jobs"
    __table_args__ = (
        CheckConstraint(_job_status_check_sql, name="ck_import_jobs_status"),
        Index("ix_import_jobs_status", "status"),
        Index("ix_import_jobs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'queued'"))

    # How this job was processed. Always "sequential" today (files are
    # processed one at a time — see instance_import_service._run_job). The
    # column is retained so the UI can label the run and so a future
    # parallel mode, if reintroduced, stays attributable without a schema
    # change.
    worker_mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'sequential'"))

    # Roll-up counters. total_* are known (or discovered during PARSING)
    # up front where possible; processed_* advance as the worker runs.
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed_files: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_sheets: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed_sheets: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # Who submitted the job. Nullable FK, ON DELETE SET NULL — same
    # attribution reasoning as Instance.updated_by_user_id: deleting a
    # user should not delete their import history, only clear the link.
    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    files: Mapped[List["ImportFile"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ImportFile.id"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<ImportJob id={self.id} status={self.status} files={self.total_files}>"


class ImportFile(Base):
    """One uploaded file within a job. `staged_path` is where the raw
    bytes were written on submit so the worker can read them back off disk
    (keeping request memory low). Per-file counters mirror the job's."""

    __tablename__ = "import_files"
    __table_args__ = (
        CheckConstraint(_file_status_check_sql, name="ck_import_files_status"),
        Index("ix_import_files_job_id", "import_job_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    import_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False
    )

    # 0-based position in the submitted files[] array — lets the frontend
    # keep upload order stable regardless of processing order.
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # 'csv' | 'xlsx' (derived from extension at submit).
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # Absolute path of the staged copy on disk while the job runs; cleared
    # (and the file deleted) once the file is fully processed / on cleanup.
    staged_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'queued'"))

    total_sheets: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed_sheets: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Set when the worker PICKS UP this file (not when it was queued), so
    # per-file duration (started_at -> completed_at) is true processing
    # time and excludes any time spent waiting behind other files.
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["ImportJob"] = relationship(back_populates="files")
    sheets: Mapped[List["ImportSheet"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="ImportSheet.id"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<ImportFile id={self.id} name={self.file_name!r} status={self.status}>"


class ImportSheet(Base):
    """One worksheet within a file (a CSV yields exactly one). Holds the
    structured errors for that sheet as a JSONB array so the errors
    endpoint can return {file, sheet, row, messages[]} without a separate
    errors table — the volume per sheet is bounded by validation and the
    tree is always read whole."""

    __tablename__ = "import_sheets"
    __table_args__ = (
        CheckConstraint(_sheet_status_check_sql, name="ck_import_sheets_status"),
        Index("ix_import_sheets_file_id", "import_file_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    import_file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("import_files.id", ondelete="CASCADE"), nullable=False
    )

    # NULL for a CSV (single, unnamed sheet); the worksheet title for XLSX.
    sheet_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'queued'"))

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # Structured per-row errors for this sheet:
    #   [{"row": 12, "messages": ["'Ticket Number' is missing", ...]}, ...]
    # Empty list when the sheet is clean. Header-level problems are stored
    # with row=null.
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    file: Mapped["ImportFile"] = relationship(back_populates="sheets")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<ImportSheet id={self.id} name={self.sheet_name!r} status={self.status}>"
