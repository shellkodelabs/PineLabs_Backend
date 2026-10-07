"""
Repository for the import-job tree (import_jobs / import_files /
import_sheets) — SQLAlchemy queries only, no business rules and no
response-schema mapping (same shape as instance_repository).

Two kinds of callers use this:
  - the request thread (create the job + files on submit; read the tree
    for the progress/errors endpoints), and
  - the background worker threads (advance status/counters and record
    sheet errors as they process).

Every function takes an explicit `session` so the worker can pass its own
per-thread SessionLocal — this repository never opens or commits a
session itself.
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.instance_import import ImportFile, ImportJob, ImportSheet


# --- Creation -------------------------------------------------------
def create_job(
    session: Session,
    *,
    created_by_user_id: Optional[int],
    total_files: int,
    worker_mode: str = "sequential",
) -> ImportJob:
    job = ImportJob(
        status="queued",
        total_files=total_files,
        created_by_user_id=created_by_user_id,
        worker_mode=worker_mode,
    )
    session.add(job)
    session.flush()
    return job


def create_file(
    session: Session,
    *,
    import_job_id: int,
    position: int,
    file_name: str,
    file_type: str,
    staged_path: Optional[str],
) -> ImportFile:
    file = ImportFile(
        import_job_id=import_job_id,
        position=position,
        file_name=file_name,
        file_type=file_type,
        staged_path=staged_path,
        status="queued",
    )
    session.add(file)
    session.flush()
    return file


def create_sheet(
    session: Session,
    *,
    import_file_id: int,
    sheet_name: Optional[str],
    total_rows: int,
) -> ImportSheet:
    sheet = ImportSheet(
        import_file_id=import_file_id,
        sheet_name=sheet_name,
        total_rows=total_rows,
        status="queued",
    )
    session.add(sheet)
    session.flush()
    return sheet


# --- Reads ----------------------------------------------------------
def get_job(session: Session, job_id: int) -> Optional[ImportJob]:
    """Bare job row (no children eager-loaded) — for counter updates."""
    return session.get(ImportJob, job_id)


def get_job_with_tree(session: Session, job_id: int) -> Optional[ImportJob]:
    """Job with files and their sheets eager-loaded in two extra queries
    (selectinload), so the progress/errors endpoints render the whole
    tree without N+1 per file/sheet."""
    stmt = (
        select(ImportJob)
        .options(selectinload(ImportJob.files).selectinload(ImportFile.sheets))
        .where(ImportJob.id == job_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_file(session: Session, file_id: int) -> Optional[ImportFile]:
    return session.get(ImportFile, file_id)


def list_files_for_job(session: Session, job_id: int) -> List[ImportFile]:
    stmt = select(ImportFile).where(ImportFile.import_job_id == job_id).order_by(ImportFile.position, ImportFile.id)
    return list(session.execute(stmt).scalars().all())


def list_sheets_for_file(session: Session, file_id: int) -> List[ImportSheet]:
    stmt = select(ImportSheet).where(ImportSheet.import_file_id == file_id).order_by(ImportSheet.id)
    return list(session.execute(stmt).scalars().all())


# --- Updates (worker) ----------------------------------------------
def update_fields(session: Session, obj, **fields) -> None:
    """Generic setattr+flush for a job/file/sheet row. Kept generic (like
    instance_repository.update_fields) since the worker touches many
    different counter/status columns across the three levels."""
    for key, value in fields.items():
        setattr(obj, key, value)
    session.flush()
