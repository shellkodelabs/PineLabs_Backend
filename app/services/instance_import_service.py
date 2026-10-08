"""
Orchestration for the background multi-file Instance Management import.

RESPONSIBILITIES
  - submit_import_job(): runs on the REQUEST thread. Validates the upload
    (count/size/extensions), stages each file's bytes to disk, creates the
    import_jobs/import_files tree, and kicks off the background worker.
    Returns immediately with the job id (the request does NOT wait for
    processing).
  - _run_job(): runs on a BACKGROUND thread. Processes the job's files
    SEQUENTIALLY (one at a time); each file is parsed into sheets and
    written sheet-by-sheet (per-sheet atomicity), with progress counters
    advancing per sheet/row so the poll endpoint reflects live status.
  - get_progress() / get_errors(): run on the REQUEST thread for the poll
    endpoints; read the job tree via the repository and map to schemas.

WHY THREADS (not Celery/asyncio): this codebase has no task queue and
uses SYNC SQLAlchemy (psycopg2). A bounded ThreadPoolExecutor with each
unit opening its own SessionLocal is the lightest thing that gives true
background execution + shared, pollable state (the DB). If the process
restarts mid-job, in-flight jobs are left in 'processing'; a future
sweep could mark stale jobs failed — out of scope here, noted for later.

ATOMICITY: per file (see module docstrings of instance_import.py and
instance_service.validate_file_sheets). One bad row fails only its file;
the job continues and finishes as 'completed' (no errors), 'failed' (all
files failed), or 'completed_with_errors' (some failed, some succeeded).
"""
import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import List, Optional

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.exceptions import NotFoundError, ValidationError
from app.repositories import instance_import_repository as repo
from app.schemas.instance_import import (
    CreateImportJobResponse,
    ImportErrorItem,
    ImportFileProgress,
    ImportJobErrorsResponse,
    ImportJobProgressResponse,
    ImportSheetProgress,
)
from app.services import instance_import_parser as parser
from app.services import instance_service


# A small pool that runs each JOB off the request thread (so submit
# returns immediately). This is background execution only — within a job
# the files are processed sequentially by _run_job. max_workers bounds how
# many concurrent JOBS (different uploads) can run at once, not files
# within a job.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="instance-import")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# =====================================================================
# Submit (request thread)
# =====================================================================
def submit_import_job(
    *,
    file_payloads: List[tuple],
    actor_user_id: int,
) -> CreateImportJobResponse:
    """
    `file_payloads` is a list of (filename, content_bytes). Validates the
    batch, stages each file to disk, creates the job tree in its own
    committed transaction, then launches the background worker and returns
    the job id. Raises ValidationError for a rejected upload (empty, too
    many files, too large, unsupported type) — nothing is staged/created
    in that case.
    """
    settings = get_settings()

    if not file_payloads:
        raise ValidationError("No files were uploaded.", code="IMPORT_NO_FILES")
    # IMPORT_MAX_FILES <= 0 means NO per-request file-count limit.
    if settings.IMPORT_MAX_FILES > 0 and len(file_payloads) > settings.IMPORT_MAX_FILES:
        raise ValidationError(
            f"Too many files: {len(file_payloads)} (max {settings.IMPORT_MAX_FILES}).",
            code="IMPORT_TOO_MANY_FILES",
        )

    total_bytes = sum(len(c) for _n, c in file_payloads)
    if total_bytes > settings.IMPORT_MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"Upload too large: {total_bytes} bytes (max {settings.IMPORT_MAX_UPLOAD_BYTES}).",
            code="IMPORT_UPLOAD_TOO_LARGE",
        )

    # Validate every file up front (type + non-empty) BEFORE staging or
    # creating any job row, so a bad file rejects the whole submit cleanly.
    for filename, content in file_payloads:
        if not content:
            raise ValidationError(f"File {filename!r} is empty.", code="IMPORT_EMPTY")
        parser.file_type_for(filename)  # raises IMPORT_UNSUPPORTED_FILE_TYPE

    stage_dir = _job_stage_dir(settings)
    os.makedirs(stage_dir, exist_ok=True)

    # Create the job + file rows in one committed transaction so the
    # worker (a separate session) can see them immediately.
    db = SessionLocal()
    try:
        job = repo.create_job(
            db,
            created_by_user_id=actor_user_id,
            total_files=len(file_payloads),
            worker_mode="sequential",
        )
        for position, (filename, content) in enumerate(file_payloads):
            staged_path = os.path.join(stage_dir, f"job{job.id}_f{position}_{uuid.uuid4().hex}")
            with open(staged_path, "wb") as fh:
                fh.write(content)
            repo.create_file(
                db,
                import_job_id=job.id,
                position=position,
                file_name=filename,
                file_type=parser.file_type_for(filename),
                staged_path=staged_path,
            )
        db.commit()
        job_id = job.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # Launch the worker (fire-and-forget). Errors inside are captured and
    # written to the job row, never bubbled to this request.
    _executor.submit(_run_job, job_id, actor_user_id)

    return CreateImportJobResponse(jobId=job_id, status="queued", totalFiles=len(file_payloads))


def _job_stage_dir(settings) -> str:
    base = settings.IMPORT_TEMP_DIR.strip() or os.path.join(tempfile.gettempdir(), "pinelabs_instance_import")
    return base


# =====================================================================
# Worker (background thread)
# =====================================================================
def _run_job(job_id: int, actor_user_id: int) -> None:
    """Top-level worker entry for one job. Opens its own session, marks
    the job processing, then processes each file SEQUENTIALLY (one at a
    time, in submission order) and finalizes the job status.

    Sequential is the only mode: it gives the cleanest "finish one file,
    clear it, move to the next" progress, the lowest memory/DB load, and
    avoids any cross-file write contention. (A parallel mode was
    prototyped and removed; if throughput ever demands it, reintroduce a
    bounded pool here — the per-file logic in _process_file is already
    self-contained and thread-safe with its own session.)"""
    db = SessionLocal()
    try:
        job = repo.get_job(db, job_id)
        if job is None:
            return
        repo.update_fields(db, job, status="processing", started_at=_now())
        db.commit()

        files = repo.list_files_for_job(db, job_id)
        file_ids = [f.id for f in files]
    except Exception:
        db.rollback()
        _safe_mark_job_failed(job_id)
        db.close()
        return
    finally:
        db.close()

    # One file at a time, in order. A per-file crash is recorded inside
    # _process_file and never aborts the remaining files.
    for fid in file_ids:
        try:
            _process_file(job_id, fid, actor_user_id)
        except Exception:
            pass

    _finalize_job(job_id)


def _process_file(job_id: int, file_id: int, actor_user_id: int) -> None:
    """Process ONE file, SHEET BY SHEET, committing after each sheet so
    progress ticks up live (a 10-sheet file visibly moves 10% -> 20% ->
    ... -> 100% as each sheet lands). Steps:

      1. Parse the file into sheets; validate the whole file once (fast,
         no writes) so cross-sheet duplicate detection still holds.
      2. For EACH sheet, in order: mark it processing, WRITE it if clean
         (its own savepoint via instance_service.write_one_sheet), mark it
         completed/failed, then bump the file's processed counters AND the
         job's roll-up — committing after each sheet. That commit is what
         the poll endpoint sees, so the overall + current-file bars animate
         in real time.
      3. When the file is done, set its terminal status and DELETE its
         staged temp file immediately (clearing staged_path), so storage
         is freed the moment a file finishes rather than at job end.

    Its own session; a per-sheet failure rolls back only that sheet."""
    db = SessionLocal()
    try:
        file = repo.get_file(db, file_id)
        if file is None:
            return
        # started_at = when the worker actually picks this file up, so
        # per-file duration excludes queue wait (matters in sequential
        # mode where later files sit queued while earlier ones run).
        repo.update_fields(db, file, status="processing", started_at=_now())
        db.commit()

        # Read staged bytes back off disk.
        content = b""
        if file.staged_path and os.path.exists(file.staged_path):
            with open(file.staged_path, "rb") as fh:
                content = fh.read()

        expected_labels, required_labels = instance_service.expected_import_columns(db)

        try:
            sheets = parser.parse_bytes(file.file_name, content, expected_labels, required_labels)
        except ValidationError as exc:
            # Unsupported type / parse failure -> whole file failed with a
            # single header-level error on a synthetic sheet row.
            _record_file_parse_failure(db, file, exc.message)
            db.commit()
            _bump_job_rollup(db, job_id)
            _cleanup_staged(db, file)
            return

        # Persist a sheet row per parsed sheet (so the tree shows every
        # sheet immediately, all 'queued'), and set the file's totals so
        # the current-file bar has a denominator from the first poll.
        sheet_rows = []
        total_rows = 0
        for ps in sheets:
            sheet_row = repo.create_sheet(
                db, import_file_id=file.id, sheet_name=ps.sheet_name, total_rows=len(ps.rows)
            )
            sheet_rows.append((sheet_row, ps))
            total_rows += len(ps.rows)
        repo.update_fields(db, file, total_sheets=len(sheets), total_rows=total_rows)
        db.commit()
        _bump_job_rollup(db, job_id)

        # Validate the whole file once (no writes). This preserves
        # file-wide duplicate-name detection while letting us WRITE each
        # sheet independently below.
        result = instance_service.validate_file_sheets(db, sheets=sheets, expected_labels=expected_labels)

        file_created = 0
        file_updated = 0
        file_errors = 0
        processed_sheets = 0
        processed_rows = 0

        # Per-sheet: write (if clean) + mark + commit + roll up. This is
        # the loop that makes progress incremental.
        for sv, (sheet_row, _ps) in zip(result.sheets, sheet_rows):
            repo.update_fields(db, sheet_row, status="processing")
            db.commit()

            # Record this sheet's structured errors (empty for a clean
            # sheet). A sheet with any error is skipped whole.
            if sv.errors:
                repo.update_fields(
                    db, sheet_row, error_count=len(sv.errors), errors=sv.errors
                )

            # Write only clean sheets; sets sv.created / sv.updated.
            instance_service.write_one_sheet(db, sv=sv, actor_user_id=actor_user_id)

            sheet_status = "failed" if sv.errors else "completed"
            repo.update_fields(
                db,
                sheet_row,
                status=sheet_status,
                processed_rows=sv.total_rows,
                created_rows=sv.created,
                updated_rows=sv.updated,
                completed_at=_now(),
            )

            # Advance the FILE's live counters as this sheet finishes.
            processed_sheets += 1
            processed_rows += sv.total_rows
            file_created += sv.created
            file_updated += sv.updated
            file_errors += len(sv.errors)
            repo.update_fields(
                db,
                file,
                processed_sheets=processed_sheets,
                processed_rows=processed_rows,
                created_rows=file_created,
                updated_rows=file_updated,
                error_count=file_errors,
            )
            db.commit()

            # Roll the job counters up so the OVERALL bar advances too,
            # not just the current file's.
            _bump_job_rollup(db, job_id)

        # File terminal status from its sheets (sheet-level atomicity).
        failed_sheets = sum(1 for sv in result.sheets if sv.errors)
        if failed_sheets == 0:
            file_status = "completed"
        elif failed_sheets == len(result.sheets):
            file_status = "failed"
        else:
            file_status = "completed_with_errors"

        repo.update_fields(db, file, status=file_status, completed_at=_now())
        db.commit()

        # Free the staged temp file the moment THIS file is done.
        _cleanup_staged(db, file)
        _bump_job_rollup(db, job_id)
    except Exception:  # pragma: no cover - defensive
        db.rollback()
        try:
            file = repo.get_file(db, file_id)
            if file is not None:
                repo.update_fields(
                    db, file, status="failed", error_count=file.error_count or 1, completed_at=_now()
                )
                db.commit()
                _cleanup_staged(db, file)
                _bump_job_rollup(db, job_id)
        except Exception:
            db.rollback()
    finally:
        db.close()


def _bump_job_rollup(db, job_id: int) -> None:
    """Recompute the job's roll-up counters from its files and commit, so
    the poll endpoint's OVERALL progress reflects work as it happens (not
    only at job end). Does NOT set a terminal job status — that's
    _finalize_job's job once every file is done. Best-effort: a failure
    here never derails file processing."""
    try:
        files = repo.list_files_for_job(db, job_id)
        job = repo.get_job(db, job_id)
        if job is None:
            return
        processed_files = sum(
            1 for f in files if f.status in ("completed", "failed", "completed_with_errors")
        )
        repo.update_fields(
            db,
            job,
            total_sheets=sum(f.total_sheets for f in files),
            processed_sheets=sum(f.processed_sheets for f in files),
            total_rows=sum(f.total_rows for f in files),
            processed_rows=sum(f.processed_rows for f in files),
            created_rows=sum(f.created_rows for f in files),
            updated_rows=sum(f.updated_rows for f in files),
            failed_rows=sum(f.error_count for f in files),
            processed_files=processed_files,
        )
        db.commit()
    except Exception:
        db.rollback()


def _record_file_parse_failure(db, file, message: str) -> None:
    sheet_row = repo.create_sheet(db, import_file_id=file.id, sheet_name=None, total_rows=0)
    repo.update_fields(
        db,
        sheet_row,
        status="failed",
        error_count=1,
        errors=[{"row": None, "messages": [message]}],
    )
    repo.update_fields(
        db,
        file,
        status="failed",
        total_sheets=1,
        processed_sheets=1,
        error_count=1,
        completed_at=_now(),
    )


def _cleanup_staged(db, file) -> None:
    """Delete the staged file from disk the moment its file is done, and
    clear staged_path in the DB so it's provably released (storage doesn't
    linger until job end). Best-effort on the unlink; the DB clear still
    runs so we don't point at a path we may have removed."""
    try:
        if file.staged_path and os.path.exists(file.staged_path):
            os.remove(file.staged_path)
    except OSError:
        pass
    try:
        repo.update_fields(db, file, staged_path=None)
        db.commit()
    except Exception:
        db.rollback()


def _finalize_job(job_id: int) -> None:
    """Recompute the job's roll-up counters from its files and set the
    terminal status: completed (no failed files), failed (all failed), or
    completed_with_errors (mixed)."""
    db = SessionLocal()
    try:
        files = repo.list_files_for_job(db, job_id)
        job = repo.get_job(db, job_id)
        if job is None:
            return

        total_sheets = sum(f.total_sheets for f in files)
        processed_sheets = sum(f.processed_sheets for f in files)
        total_rows = sum(f.total_rows for f in files)
        processed_rows = sum(f.processed_rows for f in files)
        created_rows = sum(f.created_rows for f in files)
        updated_rows = sum(f.updated_rows for f in files)
        failed_rows = sum(f.error_count for f in files)
        # With sheet-level atomicity a file is:
        #   completed              -> no errors at all
        #   completed_with_errors  -> some sheets imported, some skipped
        #   failed                 -> every sheet skipped (nothing imported)
        fully_failed = sum(1 for f in files if f.status == "failed")
        any_errors = sum(1 for f in files if f.status in ("failed", "completed_with_errors"))
        processed_files = sum(
            1 for f in files if f.status in ("completed", "failed", "completed_with_errors")
        )

        if any_errors == 0:
            status = "completed"
        elif fully_failed == len(files):
            # Every file imported nothing.
            status = "failed"
        else:
            status = "completed_with_errors"

        repo.update_fields(
            db,
            job,
            status=status,
            processed_files=processed_files,
            total_sheets=total_sheets,
            processed_sheets=processed_sheets,
            total_rows=total_rows,
            processed_rows=processed_rows,
            created_rows=created_rows,
            updated_rows=updated_rows,
            failed_rows=failed_rows,
            completed_at=_now(),
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _safe_mark_job_failed(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = repo.get_job(db, job_id)
        if job is not None:
            repo.update_fields(db, job, status="failed", completed_at=_now())
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# =====================================================================
# Progress + errors (request thread)
# =====================================================================
def _pct(done: int, total: int) -> int:
    if total <= 0:
        return 100 if done > 0 else 0
    return min(100, int(round(done / total * 100)))


def _elapsed_ms(started, completed, terminal: bool):
    """Milliseconds between `started` and either `completed` (once the
    unit is terminal) or NOW (while it's still running), so the UI can
    show a live-ticking timer that then freezes at the final latency.
    Returns None if it hasn't started yet."""
    if started is None:
        return None
    end = completed if (terminal and completed is not None) else _now()
    delta = end - started
    return max(0, int(delta.total_seconds() * 1000))


_FILE_TERMINAL = {"completed", "failed", "completed_with_errors"}


def get_progress(db, job_id: int) -> ImportJobProgressResponse:
    job = repo.get_job_with_tree(db, job_id)
    if job is None:
        raise NotFoundError(f"No import job found for id={job_id}.", code="IMPORT_JOB_NOT_FOUND")

    file_items: List[ImportFileProgress] = []
    for f in job.files:
        sheet_items = [
            ImportSheetProgress(
                id=s.id,
                sheetName=s.sheet_name,
                status=s.status,
                progress=_pct(s.processed_rows, s.total_rows),
                totalRows=s.total_rows,
                processedRows=s.processed_rows,
                createdRows=s.created_rows,
                updatedRows=s.updated_rows,
                errorCount=s.error_count,
            )
            for s in f.sheets
        ]
        file_items.append(
            ImportFileProgress(
                id=f.id,
                fileName=f.file_name,
                fileType=f.file_type,
                status=f.status,
                progress=_pct(f.processed_rows, f.total_rows),
                totalSheets=f.total_sheets,
                processedSheets=f.processed_sheets,
                totalRows=f.total_rows,
                processedRows=f.processed_rows,
                createdRows=f.created_rows,
                updatedRows=f.updated_rows,
                errorCount=f.error_count,
                startedAt=f.started_at,
                completedAt=f.completed_at,
                elapsedMs=_elapsed_ms(f.started_at, f.completed_at, f.status in _FILE_TERMINAL),
                sheets=sheet_items,
            )
        )

    job_terminal = job.status in _FILE_TERMINAL

    return ImportJobProgressResponse(
        jobId=job.id,
        status=job.status,
        progress=_pct(job.processed_rows, job.total_rows),
        workerMode=job.worker_mode,
        totalFiles=job.total_files,
        processedFiles=job.processed_files,
        totalSheets=job.total_sheets,
        processedSheets=job.processed_sheets,
        totalRows=job.total_rows,
        processedRows=job.processed_rows,
        createdRows=job.created_rows,
        updatedRows=job.updated_rows,
        failedRows=job.failed_rows,
        createdAt=job.created_at,
        startedAt=job.started_at,
        completedAt=job.completed_at,
        elapsedMs=_elapsed_ms(job.started_at, job.completed_at, job_terminal),
        files=file_items,
    )


def get_errors(db, job_id: int) -> ImportJobErrorsResponse:
    job = repo.get_job_with_tree(db, job_id)
    if job is None:
        raise NotFoundError(f"No import job found for id={job_id}.", code="IMPORT_JOB_NOT_FOUND")

    items: List[ImportErrorItem] = []
    for f in job.files:
        for s in f.sheets:
            for entry in (s.errors or []):
                items.append(
                    ImportErrorItem(
                        file=f.file_name,
                        sheet=s.sheet_name,
                        row=entry.get("row"),
                        messages=entry.get("messages", []),
                    )
                )

    return ImportJobErrorsResponse(
        jobId=job.id,
        status=job.status,
        totalErrors=len(items),
        errors=items,
    )
