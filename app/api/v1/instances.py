"""
Instances router — HTTP concerns only: parses/validates query/path/body
parameters, injects the DB session, obtains the acting user, calls the
service layer, returns its result. No SQLAlchemy usage and no business
logic here. Same pattern as bin_series.py.

File parsing (CSV via the stdlib `csv` module, XLSX via openpyxl) lives
here rather than in the service because turning an uploaded byte stream
into (headers, rows) is an HTTP/input concern — the service only sees
already-extracted Python lists and stays free of file-format knowledge.

ROUTE ORDER: the static paths (/stats, /export, /import, /import/template)
are declared BEFORE the "/{instanceId}" paths. FastAPI matches in
declaration order, so without this a request to /instances/stats would
otherwise be captured by /instances/{instanceId} and fail path validation
(stats is not an int >= 1).

page/pageSize are validated directly via FastAPI Query(ge=..., le=...)
(rejecting out-of-range values with 422) rather than the clamping
get_pagination_params dependency — same choice and reasoning as
bin_series.py.

Authentication is enforced at inclusion time for this entire router (see
app/api/v1/router.py's `dependencies=[Depends(get_current_user)]`).
Writes additionally depend on get_current_user directly to obtain the
acting user and pass `actor.id` to the service, so every write is
attributed in the shared revisions audit log.
"""
import csv
import io
from typing import List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.schemas.instance_import import (
    CreateImportJobResponse,
    ImportJobErrorsResponse,
    ImportJobProgressResponse,
)

from app.api.deps import get_db
from app.core.auth import CurrentUser, get_current_user
from app.core.exceptions import ValidationError
from app.schemas.common import PaginatedResponse
from app.schemas.instance import (
    CreateInstanceColumnRequest,
    CreateInstanceRequest,
    DeleteInstanceColumnRequest,
    DeleteInstanceColumnResponse,
    DeleteInstanceRequest,
    DeleteInstanceResponse,
    ImportInstanceResponse,
    InstanceColumnResponse,
    InstanceColumnsResponse,
    InstanceIssuersResponse,
    InstanceResponse,
    InstanceStatsResponse,
    LayoutColumnResponse,
    ReorderInstanceColumnsRequest,
    UpdateInstanceColumnRequest,
    UpdateInstanceRequest,
)
from app.services import instance_column_service, instance_import_service, instance_service

router = APIRouter()

SortField = Literal["name", "status", "id"]
SortOrder = Literal["asc", "desc"]
StatusFilter = Literal["Active", "Inactive"]

# Accepted upload extensions -> the parser branch to use.
_CSV_EXTENSIONS = (".csv",)
_XLSX_EXTENSIONS = (".xlsx",)


# ---------------------------------------------------------------------
# File parsing helpers (HTTP/input concern — kept out of the service)
# ---------------------------------------------------------------------
def _row_label(sheet: Optional[str], line: int) -> str:
    """A human-readable location for an import row used in error messages.
    For a multi-sheet workbook we include the sheet name so a validation
    error points at the exact sheet + row (e.g. "Zone3, row 12"); for a
    CSV / single-sheet file it's just "row 12". `line` is 1-based within
    the sheet, counting the header as row 1 (so data starts at row 2)."""
    return f"{sheet}, row {line}" if sheet else f"row {line}"


def _norm_header(value: object) -> str:
    """Normalize a header cell for comparison: trimmed, lowercased,
    inner whitespace collapsed. Matches the service's header matching so
    parser-side and service-side column resolution agree."""
    return " ".join(str(value or "").strip().lower().split())


def _parse_csv(
    content: bytes,
    expected_labels: List[str],
    required_labels: List[str],
) -> Tuple[List[str], List[List[object]], List[str], List[str]]:
    # utf-8-sig transparently strips a BOM that Excel often prepends to
    # CSV exports; falls back to plain utf-8 otherwise. A CSV is a single
    # sheet; its header is validated against the SAME real expected
    # columns as every xlsx sheet, and its rows re-aligned to the
    # canonical order — so behavior is identical whether the upload has
    # one sheet or many.
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    all_rows = [row for row in reader]
    if not all_rows:
        return list(expected_labels), [], [], []

    header_values = [c.strip() for c in all_rows[0]]
    col_map, error = _validate_sheet_header(
        None, header_values, expected_labels, required_labels
    )
    if error is not None:
        return list(expected_labels), [], [], [error]

    data_rows: List[List[object]] = []
    labels: List[str] = []
    # Enumerate over the ORIGINAL file lines so the label's row number
    # matches what the user sees in their editor (header = line 1).
    for line_no, row in enumerate(all_rows[1:], start=2):
        if any(str(c).strip() for c in row):
            aligned = [
                (row[src] if (src is not None and src < len(row)) else None)
                for src in col_map
            ]
            data_rows.append(aligned)
            labels.append(_row_label(None, line_no))
    return list(expected_labels), data_rows, labels, []


def _validate_sheet_header(
    sheet_name: Optional[str],
    header_values: List[object],
    expected_labels: List[str],
    required_labels: List[str],
) -> Tuple[Optional[List[int]], Optional[str]]:
    """Validate ONE sheet's (or a CSV's) header against the REAL expected
    columns — never against another sheet. Returns (col_map, error):

      - col_map[i] = the source column index in this header for the i-th
        expected column, or None if that expected column isn't present
        (only allowed for OPTIONAL columns). Used to re-align the sheet's
        rows to the canonical `expected_labels` order.
      - error = a human message if the header is invalid (a REQUIRED
        column missing, or an UNKNOWN/misspelled column present), else
        None.

    Because validation is against the system's own columns, a mistake in
    the FIRST sheet is flagged on the FIRST sheet — a correct sheet is
    never blamed for differing from a wrong one."""
    exp_norm = [_norm_header(label) for label in expected_labels]
    req_norm = {_norm_header(label) for label in required_labels}
    present = {}
    for idx, cell in enumerate(header_values):
        n = _norm_header(cell)
        if n:
            present[n] = idx

    # Only REQUIRED columns must be present. Any header cell that isn't a
    # known column (a stray/extra column, or a typo) is simply IGNORED —
    # not treated as an error — since columns are dynamic and an upload
    # may carry extra columns we don't manage. NOTE: a typo in a REQUIRED
    # column (e.g. ' Instance' misspelled) surfaces as that required
    # column being "missing" below, so a genuine mistake on a required
    # field is still caught.
    missing_required = [
        expected_labels[i]
        for i, n in enumerate(exp_norm)
        if n in req_norm and n not in present
    ]

    if missing_required:
        where = f"{sheet_name}: " if sheet_name else ""
        return None, (
            f"{where}missing required column(s): "
            + ", ".join(repr(m) for m in missing_required)
            + f". Required: {', '.join(required_labels)}."
        )

    # Build the alignment map to the canonical expected order. Expected
    # columns not present in this header map to None (optional ones are
    # left blank per row); unknown columns in the file are dropped.
    col_map = [present.get(n) for n in exp_norm]
    return col_map, None


def _parse_xlsx(
    content: bytes,
    expected_labels: List[str],
    required_labels: List[str],
) -> Tuple[List[str], List[List[object]], List[str], List[str]]:
    """Parse an .xlsx workbook into (headers, data_rows, row_labels,
    header_errors) using ONLY the FIRST non-empty worksheet — any
    remaining sheets are IGNORED entirely. Fully-empty leading sheets are
    skipped so "first sheet" means the first sheet with content.

      - If that sheet's header is missing a required column (or a required
        column is misspelled), it's reported in `header_errors` and NONE
        of its rows are emitted.
      - Its rows are re-aligned to the canonical `expected_labels` order,
        so the service maps columns by the returned `headers`.

    Every data row carries a label ("<SheetName>, row <n>"). Fully blank
    rows are dropped. The header row itself is never emitted as data."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)

    def _is_blank(values) -> bool:
        return not any(str(v).strip() for v in values if v is not None)

    data_rows: List[List[object]] = []
    row_labels: List[str] = []
    header_errors: List[str] = []

    for ws in wb.worksheets:
        sheet_iter = enumerate(ws.iter_rows(values_only=True), start=1)
        # This sheet's header = its first non-blank row.
        header_values = None
        for line, row in sheet_iter:
            values = list(row)
            if _is_blank(values):
                continue
            header_values = values
            break
        if header_values is None:
            continue  # empty sheet — keep looking for the first with content

        col_map, error = _validate_sheet_header(
            ws.title, header_values, expected_labels, required_labels
        )
        if error is not None:
            header_errors.append(error)
            break  # first non-empty sheet decided (bad header); ignore the rest

        # Emit this sheet's data rows, RE-ALIGNED to the canonical order.
        for line, row in sheet_iter:
            values = list(row)
            if _is_blank(values):
                continue
            aligned = [
                (values[src] if (src is not None and src < len(values)) else None)
                for src in col_map
            ]
            data_rows.append(aligned)
            row_labels.append(_row_label(ws.title, line))
        # Only the first non-empty sheet is imported — ignore the rest.
        break

    wb.close()
    return list(expected_labels), data_rows, row_labels, header_errors


def _parse_upload(
    file: UploadFile,
    content: bytes,
    expected_labels: List[str],
    required_labels: List[str],
) -> Tuple[List[str], List[List[object]], List[str], List[str]]:
    name = (file.filename or "").lower()
    if name.endswith(_CSV_EXTENSIONS):
        return _parse_csv(content, expected_labels, required_labels)
    if name.endswith(_XLSX_EXTENSIONS):
        return _parse_xlsx(content, expected_labels, required_labels)
    raise ValidationError(
        f"Unsupported file type: {file.filename!r}. Only .csv and .xlsx are accepted.",
        code="IMPORT_UNSUPPORTED_FILE_TYPE",
    )


# ---------------------------------------------------------------------
# Static (non-id) routes — MUST precede the /{instanceId} routes.
# ---------------------------------------------------------------------
@router.get("", response_model=PaginatedResponse[InstanceResponse])
def list_instances(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(
        None, min_length=1, description="Substring match across instance name and ticket number"
    ),
    status: Optional[StatusFilter] = Query(None, description="Exact status filter (Active/Inactive)"),
    sortBy: Optional[SortField] = Query(None, description="Field to sort by (default: id)"),
    sortOrder: SortOrder = Query("asc"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[InstanceResponse]:
    """Paginated, searchable (name or ticket number), filterable, sortable
    list of instances — feeds the Instance Management table."""
    return instance_service.list_instances(
        db,
        page=page,
        page_size=pageSize,
        search=search,
        status=status,
        sort_by=sortBy,
        sort_order=sortOrder,
    )


@router.get("/stats", response_model=InstanceStatsResponse)
def get_instance_stats(db: Session = Depends(get_db)) -> InstanceStatsResponse:
    """Header cards: total instances, active instances, issuers grouped."""
    return instance_service.get_stats(db)


@router.get("/export")
def export_instances(
    search: Optional[str] = Query(None, min_length=1, description="Same search filter as the list"),
    status: Optional[StatusFilter] = Query(None, description="Same status filter as the list"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Exports matching instances as a CSV. Columns are Instance, Ticket
    Number, Status, then every custom column's label in display order.
    Respects the same search/status filters as the list, so 'export what
    I'm viewing'."""
    headers, rows = instance_service.build_export(db, search=search, status=status)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="instances_export.csv"'},
    )


@router.get("/import/template")
def download_import_template(db: Session = Depends(get_db)) -> StreamingResponse:
    """Downloads a CSV template whose only row is the header: the exact
    set of accepted import columns — Instance, Ticket Number, Status, then
    every custom column's label — so the user always uploads the right
    headers. No example/data rows are included."""
    columns = instance_column_service.list_columns(db)
    headers = [
        instance_service.IMPORT_COLUMN_INSTANCE,
        instance_service.IMPORT_COLUMN_TICKET,
        instance_service.IMPORT_COLUMN_STATUS,
    ] + [c.label for c in columns]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="instances_import_template.csv"'},
    )


@router.post("/import", response_model=ImportInstanceResponse)
async def import_instances(
    file: UploadFile = File(
        ...,
        description=(
            "A .csv or .xlsx file. Columns are read by POSITION, not by "
            "header text (the header row can say anything and is skipped): "
            "column 1 = Instance name, column 2 = Ticket Number, "
            "column 3 (optional) = Status ('active'/'inactive', "
            "case-insensitive; blank or anything else defaults to Active)."
        ),
    ),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportInstanceResponse:
    """
    Bulk import instances from a CSV/XLSX — a single UPSERT-BY-NAME flow,
    no separate "update" vs "add" mode: existing names have their ticket
    number (and status, if column 3 differs) updated in place (same
    instance id preserved); unknown names are created. Columns are
    POSITION-based (1=Instance, 2=Ticket Number, 3=Status, 4+ ignored) —
    the header row's text is never interpreted, only skipped — see
    instance_service.import_instances. STRICT validation: every row must
    have at least 2 columns with a non-blank / non-NA Instance and Ticket
    Number, no in-file duplicate names. If ANY row fails, nothing is
    imported (422 with a per-row error list in error.details.errors).
    """
    content = await file.read()
    if not content:
        raise ValidationError("Uploaded file is empty.", code="IMPORT_EMPTY")

    # The expected columns come from the SYSTEM (built-ins + the custom
    # column definitions), so each sheet's header is validated against the
    # real columns — a mistake in any sheet (including the first) is
    # flagged on that sheet, not by comparing sheets to each other.
    expected_labels, required_labels = instance_service.expected_import_columns(db)
    headers, rows, row_labels, header_errors = _parse_upload(
        file, content, expected_labels, required_labels
    )
    return instance_service.import_instances(
        db,
        headers=headers,
        rows=rows,
        row_labels=row_labels,
        header_errors=header_errors,
        actor_user_id=actor.id,
    )


# ---------------------------------------------------------------------
# Background MULTI-FILE import (job-based) — MUST precede /{instanceId}.
#
# POST /import/jobs accepts many CSV/XLSX files, stages them, creates a
# background job, and returns immediately with a jobId (HTTP 202). The
# frontend then polls GET /import/jobs/{jobId} for live progress and
# GET /import/jobs/{jobId}/errors for the structured {file, sheet, row,
# messages[]} list. Processing is per-file atomic: one bad file fails
# alone while the others still commit. The original single-file
# synchronous POST /import above is unchanged.
# ---------------------------------------------------------------------
@router.post(
    "/import/jobs",
    response_model=CreateImportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_import_job(
    files: List[UploadFile] = File(
        ...,
        description="One or more .csv/.xlsx files to import. Processed in the background.",
    ),
    actor: CurrentUser = Depends(get_current_user),
) -> CreateImportJobResponse:
    """Submit a multi-file import. Reads each uploaded file into memory
    once (bounded by IMPORT_MAX_UPLOAD_BYTES), hands the bytes to the
    import service which stages them to disk and queues a background job,
    then returns the jobId without waiting for processing. Does NOT take a
    `db` dependency: the service manages its own committed transactions so
    the background worker can see the job immediately."""
    payloads = []
    for f in files:
        content = await f.read()
        payloads.append((f.filename or "", content))
    return instance_import_service.submit_import_job(
        file_payloads=payloads, actor_user_id=actor.id
    )


@router.get("/import/jobs/{jobId}", response_model=ImportJobProgressResponse)
def get_import_job(
    jobId: int = Path(..., ge=1),
    db: Session = Depends(get_db),
) -> ImportJobProgressResponse:
    """Live progress for an import job: overall row-based percentage plus
    the full files -> sheets tree with per-level counters and status.
    Poll this while status is 'queued'/'processing'."""
    return instance_import_service.get_progress(db, jobId)


@router.get("/import/jobs/{jobId}/errors", response_model=ImportJobErrorsResponse)
def get_import_job_errors(
    jobId: int = Path(..., ge=1),
    db: Session = Depends(get_db),
) -> ImportJobErrorsResponse:
    """Structured validation errors for an import job, each tagged with
    its file, sheet (null for CSV), and row (null for a header-level
    problem), so the UI can render file -> sheet -> row -> messages."""
    return instance_import_service.get_errors(db, jobId)


# ---------------------------------------------------------------------
# Custom column definition routes — MUST precede /{instanceId} (a path
# like /instances/columns must not be captured as instanceId).
# ---------------------------------------------------------------------
@router.get("/columns", response_model=InstanceColumnsResponse)
def get_instance_columns(db: Session = Depends(get_db)) -> InstanceColumnsResponse:
    """Both views of the Instance Management columns in one payload:

    - `definitions`: the user-defined custom column definitions only —
      drives the required-* markers, the create/edit form inputs, the
      Add Column duplicate-label check, and the expected upload headers.
    - `layout`: the full merged, ordered layout (built-in columns —
      Instance/Ticket Number/Issuers/Status/Updated By — and custom
      columns interleaved by their configured position). The frontend
      renders the table left-to-right from this (actions column pinned
      last, client-side).

    (Previously split across GET /columns and GET /columns/layout; merged
    so the table fetches its columns in a single round-trip.)"""
    return instance_column_service.get_columns_view(db)


@router.post("/columns", response_model=InstanceColumnResponse, status_code=status.HTTP_201_CREATED)
def create_instance_column(
    payload: CreateInstanceColumnRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstanceColumnResponse:
    """Adds a custom column and backfills every existing instance with its
    default value (or NA). 409 if the label is already used."""
    return instance_column_service.create_column(db, payload)


@router.patch("/columns/reorder", response_model=List[LayoutColumnResponse])
def reorder_instance_columns(
    payload: ReorderInstanceColumnsRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[LayoutColumnResponse]:
    """Sets the display order of the WHOLE Instance Management table
    (built-in + custom columns) from a single ordered list of column keys
    — the drag-and-drop reorder. `orderedKeys` is every column key in the
    new left-to-right order (see the layout endpoint). Returns the freshly
    resolved layout."""
    return instance_column_service.reorder_columns(db, ordered_keys=payload.orderedKeys)


@router.patch("/columns/{columnId}", response_model=InstanceColumnResponse)
def update_instance_column(
    payload: UpdateInstanceColumnRequest,
    columnId: int = Path(..., ge=1, description="Instance column id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstanceColumnResponse:
    """Partial update of a column definition — rename its label, toggle
    required, change default/options. The column key never changes."""
    return instance_column_service.update_column(db, column_id=columnId, payload=payload)


@router.delete("/columns/{columnId}", response_model=DeleteInstanceColumnResponse)
def delete_instance_column(
    payload: DeleteInstanceColumnRequest,
    columnId: int = Path(..., ge=1, description="Instance column id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteInstanceColumnResponse:
    """Deletes a custom column and strips its values from every instance.
    Records a deletion audit row (ticket + revised by + reviewer,
    attributed to the acting user) in instance_column_deletions."""
    deleted_id = instance_column_service.delete_column(
        db, column_id=columnId, payload=payload, actor_user_id=actor.id
    )
    return DeleteInstanceColumnResponse(id=deleted_id, deleted=True)


# ---------------------------------------------------------------------
# Id-scoped routes.
# ---------------------------------------------------------------------
@router.get("/{instanceId}", response_model=InstanceResponse)
def get_instance(
    instanceId: int = Path(..., ge=1, description="Instance id"),
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Single instance by id. 404 INSTANCE_NOT_FOUND if it doesn't exist."""
    return instance_service.get_instance(db, instance_id=instanceId)


@router.get("/{instanceId}/issuers", response_model=InstanceIssuersResponse)
def get_instance_issuers(
    instanceId: int = Path(..., ge=1, description="Instance id"),
    db: Session = Depends(get_db),
) -> InstanceIssuersResponse:
    """The issuers grouped under this instance, for the Create User
    screen's instance->issuer drill-down. Derived from the DISTINCT
    `issuer` values on the gift-card/wallet BIN records whose `instance`
    matches this instance's name (there is no stored instance->issuer
    link). 404 INSTANCE_NOT_FOUND if the instance doesn't exist; the
    issuer list may be empty if no BIN records reference it yet."""
    return instance_service.get_instance_issuers(db, instance_id=instanceId)


@router.post("", response_model=InstanceResponse, status_code=status.HTTP_201_CREATED)
def create_instance(
    payload: CreateInstanceRequest,
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Creates an instance and records a "create" audit revision
    attributed to the authenticated caller, in one transaction. 409
    INSTANCE_NAME_ALREADY_EXISTS if the name is taken."""
    return instance_service.create_instance(db, payload, actor_user_id=actor.id)


@router.put("/{instanceId}", response_model=InstanceResponse)
def update_instance(
    payload: UpdateInstanceRequest,
    instanceId: int = Path(..., ge=1, description="Instance id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstanceResponse:
    """Partial update (also the activate/deactivate control via `status`).
    Records an "update" audit revision (attributed to the authenticated
    caller) only if something actually changed."""
    return instance_service.update_instance(db, instance_id=instanceId, payload=payload, actor_user_id=actor.id)


@router.delete("/{instanceId}", response_model=DeleteInstanceResponse)
def delete_instance(
    payload: DeleteInstanceRequest,
    instanceId: int = Path(..., ge=1, description="Instance id"),
    actor: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteInstanceResponse:
    """Deletes an instance and records a deletion audit row (ticket +
    revised by + reviewer, attributed to the acting user) in
    instance_deletions."""
    return instance_service.delete_instance(
        db, instance_id=instanceId, payload=payload, actor_user_id=actor.id
    )
