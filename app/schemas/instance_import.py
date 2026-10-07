"""
Pydantic schemas for the background Instance Management import.

Three response shapes back the three endpoints:
  - CreateImportJobResponse : returned immediately by POST /instances/import
    (the request does NOT wait for processing) — carries the jobId the
    frontend then polls.
  - ImportJobProgressResponse : returned by GET /instances/import/{jobId} —
    the full job -> files -> sheets tree with roll-up counters and a
    derived overall `progress` percentage, so the UI can render both the
    overall bar and per-file/per-sheet detail from a single poll.
  - ImportJobErrorsResponse : returned by GET /instances/import/{jobId}/errors
    — a flat list of {file, sheet, row, messages[]} so the UI can show
    exactly which file/sheet/row failed (unambiguous across many files).

Field names are camelCase to match the frontend contract and the rest of
the schemas package (no alias generator is used anywhere in this codebase).
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------
# POST /instances/import  (submit -> returns immediately)
# ---------------------------------------------------------------------
class CreateImportJobResponse(BaseModel):
    """Acknowledgement that the upload was accepted and a background job
    was queued. Processing happens asynchronously — poll `jobId` for
    progress and results."""

    jobId: int
    status: str = Field(description="Lifecycle state, 'queued' immediately after submit.")
    totalFiles: int


# ---------------------------------------------------------------------
# GET /instances/import/{jobId}  (progress polling)
# ---------------------------------------------------------------------
class ImportSheetProgress(BaseModel):
    id: int
    sheetName: Optional[str] = Field(
        None, description="Worksheet title for XLSX; null for a single-sheet CSV."
    )
    status: str
    progress: int = Field(description="0-100, row-based (processedRows/totalRows).")
    totalRows: int
    processedRows: int
    createdRows: int
    updatedRows: int
    errorCount: int


class ImportFileProgress(BaseModel):
    id: int
    fileName: str
    fileType: str
    status: str
    progress: int = Field(description="0-100, row-based across the file's sheets.")
    totalSheets: int
    processedSheets: int
    totalRows: int
    processedRows: int
    createdRows: int
    updatedRows: int
    errorCount: int
    # Per-file timing. startedAt is when the worker picked the file up;
    # elapsedMs is (completedAt - startedAt) once done, else the live
    # (now - startedAt) so the UI can tick a running timer. null until the
    # file starts.
    startedAt: Optional[datetime] = None
    completedAt: Optional[datetime] = None
    elapsedMs: Optional[int] = None
    sheets: List[ImportSheetProgress] = Field(default_factory=list)


class ImportJobProgressResponse(BaseModel):
    """Full job tree + roll-up counters. `progress` is row-based (see
    instance_import_service._job_progress) so a small file finishing
    doesn't jump the bar the way file-count progress would."""

    jobId: int
    status: str
    progress: int = Field(description="0-100 overall, row-based (processedRows/totalRows).")
    workerMode: str = Field("sequential", description="Which mode ran the job: 'sequential' | 'parallel'.")

    totalFiles: int
    processedFiles: int
    totalSheets: int
    processedSheets: int
    totalRows: int
    processedRows: int
    createdRows: int
    updatedRows: int
    failedRows: int

    createdAt: datetime
    startedAt: Optional[datetime] = None
    completedAt: Optional[datetime] = None
    # Overall wall-clock duration of the run. While running it's the live
    # (now - startedAt); once terminal it's (completedAt - startedAt) — the
    # authoritative latency figure to compare sequential vs parallel.
    elapsedMs: Optional[int] = None

    files: List[ImportFileProgress] = Field(default_factory=list)


# ---------------------------------------------------------------------
# GET /instances/import/{jobId}/errors
# ---------------------------------------------------------------------
class ImportErrorItem(BaseModel):
    """One row's (or one header's) validation problem, tagged with its
    exact location so the UI can render file -> sheet -> row -> messages.
    `row` is null for a header-level problem (e.g. a missing required
    column)."""

    file: str
    sheet: Optional[str] = None
    row: Optional[int] = None
    messages: List[str]


class ImportJobErrorsResponse(BaseModel):
    jobId: int
    status: str
    totalErrors: int
    errors: List[ImportErrorItem] = Field(default_factory=list)
