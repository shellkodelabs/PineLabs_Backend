"""
Service layer for Instance Management. Orchestrates
app.repositories.instance_repository, maps ORM objects to
app.schemas.instance.InstanceResponse, and raises the shared domain
exceptions (app.core.exceptions) for not-found/conflict cases — no
SQLAlchemy usage, no HTTP-layer concerns. Same shape as bin_service.

TRANSACTION STRATEGY: create/update/delete each wrap their DB-mutating
work in ONE `with db.begin_nested():` block (a SAVEPOINT), so a failure
rolls the whole business write back cleanly.

NOTE — NO AUDIT/REVISION WRITES: this service deliberately does NOT write
to the shared `revisions` audit log. That audit system is owned/reworked
by another developer, and coupling Instance Management to it created
merge conflicts; Instance Management is intentionally decoupled from it
for now. `updated_by_user_id` (the acting user) is still stamped on the
row so the UI's "UPDATED BY" column works. Audit logging can be re-added
here once the revisions logic is settled upstream.

ACTOR HANDLING: the authenticated `CurrentUser` (app/core/auth.py) is
resolved from a real users row by the auth boundary; `actor.id` is used
directly as the instance's `updated_by_user_id` (which drives the UI's
"UPDATED BY" column). Never accepted from a request payload.

ISSUER COUNT: the UI's ISSUERS column is a DERIVED count of issuers
grouped under the instance. Issuers are not yet linked to instances in
the backend, so `issuerCount` is reported as 0 for now — see
_ISSUER_COUNT_PLACEHOLDER below. Wire this to a real COUNT once the
issuer<->instance relationship exists.
"""
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.instance import Instance
from app.repositories import instance_column_repository, instance_repository
from app.schemas.common import PaginatedResponse
from app.schemas.instance import (
    CreateInstanceRequest,
    DeleteInstanceRequest,
    DeleteInstanceResponse,
    ImportInstanceResponse,
    InstanceResponse,
    InstanceStatsResponse,
    UpdateInstanceRequest,
)
from app.services import instance_column_service

# (request field, model attribute) — the fields an update may change on
# the instance ROW. Revised By / Reviewer are NOT here: they are not
# stored on the row, only in the instance_edits audit table.
_UPDATABLE_FIELDS = [
    ("name", "name"),
    ("status", "status"),
    # NOTE: like every other field here, an OMITTED ticketNumber leaves
    # the value unchanged. Because the update loop skips values that are
    # None, this partial-update convention means it can be SET/changed
    # but not cleared back to null through this endpoint.
    ("ticketNumber", "ticket_number"),
]

_DEFAULT_STATUS = "Active"

# Until issuers are linked to instances, the UI's ISSUERS count has no
# real source — reported as 0 rather than faked. Replace with a real
# COUNT(issuers WHERE instance_id = ...) once that relationship exists.
_ISSUER_COUNT_PLACEHOLDER = 0


def _to_response(instance: Instance) -> InstanceResponse:
    return InstanceResponse(
        id=instance.id,
        name=instance.name,
        issuerCount=_ISSUER_COUNT_PLACEHOLDER,
        status=instance.status,
        ticketNumber=instance.ticket_number,
        customFields=instance.custom_fields or {},
        updatedBy=instance.updated_by_user.name if instance.updated_by_user else None,
        updatedAt=instance.updated_at,
    )


def _require_instance(db: Session, instance_id: int) -> Instance:
    instance = instance_repository.get_by_id(db, instance_id)
    if instance is None:
        raise NotFoundError(f"No instance found for id={instance_id}.", code="INSTANCE_NOT_FOUND")
    return instance


def _check_name_available(db: Session, *, name: str, exclude_id: Optional[int] = None) -> None:
    existing = instance_repository.get_by_name(db, name)
    if existing is not None and existing.id != exclude_id:
        raise ConflictError(
            f"An instance already exists with name={name!r}.",
            code="INSTANCE_NAME_ALREADY_EXISTS",
        )


def list_instances(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
) -> PaginatedResponse[InstanceResponse]:
    instances, total = instance_repository.search(
        db,
        search=search,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse[InstanceResponse](
        items=[_to_response(i) for i in instances],
        total=total,
        page=page,
        pageSize=page_size,
    )


def get_instance(db: Session, *, instance_id: int) -> InstanceResponse:
    return _to_response(_require_instance(db, instance_id))


def create_instance(db: Session, payload: CreateInstanceRequest, *, actor_user_id: int) -> InstanceResponse:
    name = payload.name.strip()
    _check_name_available(db, name=name)
    status = payload.status or _DEFAULT_STATUS

    # Validate + coerce the custom column values against the current
    # column definitions (required enforced; skipped optional -> default
    # or NA). partial=False so every defined column is resolved.
    custom_fields = instance_column_service.coerce_and_validate_custom_fields(
        db, incoming=payload.customFields, partial=False
    )

    try:
        with db.begin_nested():
            instance = instance_repository.create(
                db,
                name=name,
                status=status,
                ticket_number=payload.ticketNumber,  # already normalized (blank -> None) by the schema
                updated_by_user_id=actor_user_id,
                custom_fields=custom_fields,
            )
    except IntegrityError as exc:
        # Defense in depth beyond the upfront name check (e.g. a concurrent
        # request inserting the same name in between).
        raise ConflictError(
            f"An instance already exists with name={name!r}.",
            code="INSTANCE_NAME_ALREADY_EXISTS",
        ) from exc

    return _to_response(instance)


def update_instance(
    db: Session, *, instance_id: int, payload: UpdateInstanceRequest, actor_user_id: int
) -> InstanceResponse:
    instance = _require_instance(db, instance_id)

    # If the name is changing, ensure the new name isn't taken by another
    # instance.
    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name.lower() != instance.name.lower():
            _check_name_available(db, name=new_name, exclude_id=instance.id)

    # Compute what would actually change WITHOUT writing yet — so a no-op
    # update touches nothing (no updated_at bump). Same approach as
    # bin_service.update_bin_record.
    fields_to_set = {}
    for payload_field, model_attr in _UPDATABLE_FIELDS:
        value = getattr(payload, payload_field)
        if value is None:
            continue
        if payload_field == "name":
            value = value.strip()
        old_value = getattr(instance, model_attr)
        if value != old_value:
            fields_to_set[model_attr] = value

    # Custom fields: merge the supplied values into the existing map
    # (partial=True — untouched columns keep their value). Only write if
    # the resulting map actually differs.
    if payload.customFields is not None:
        merged = instance_column_service.coerce_and_validate_custom_fields(
            db, incoming=payload.customFields, existing=instance.custom_fields or {}, partial=True
        )
        if merged != (instance.custom_fields or {}):
            fields_to_set["custom_fields"] = merged

    if not fields_to_set:
        return _to_response(instance)

    # A real edit is happening. Every edit path (edit form, inline cell
    # edit, activate/deactivate) must supply the audit trail: a ticket
    # plus who revised and who reviewed the change. These are recorded in
    # the instance_edits audit table — NOT stored on the instance row.
    missing_audit = []
    if not (payload.ticketNumber and payload.ticketNumber.strip()):
        missing_audit.append("ticketNumber")
    if not (payload.revisedBy and payload.revisedBy.strip()):
        missing_audit.append("revisedBy")
    if not (payload.reviewer and payload.reviewer.strip()):
        missing_audit.append("reviewer")
    if missing_audit:
        raise ValidationError(
            f"An edit requires: {', '.join(missing_audit)}.",
            code="INSTANCE_EDIT_AUDIT_REQUIRED",
            details={"missing": missing_audit},
        )

    # Attribute the row's "UPDATED BY" to the acting user.
    fields_to_set["updated_by_user_id"] = actor_user_id

    try:
        with db.begin_nested():
            instance_repository.update_fields(db, instance, **fields_to_set)
            # Append the immutable edit-audit row (ticket + revised by +
            # reviewer), in the same savepoint as the edit itself.
            instance_repository.create_edit(
                db,
                instance_id=instance.id,
                instance_name=instance.name,
                ticket_number=payload.ticketNumber.strip(),
                revised_by=payload.revisedBy.strip(),
                reviewer=payload.reviewer.strip(),
                edited_by_user_id=actor_user_id,
            )
    except IntegrityError as exc:
        raise ConflictError(
            f"An instance already exists with name={instance.name!r}.",
            code="INSTANCE_NAME_ALREADY_EXISTS",
        ) from exc

    return _to_response(instance)


def delete_instance(
    db: Session,
    *,
    instance_id: int,
    payload: DeleteInstanceRequest,
    actor_user_id: int,
) -> DeleteInstanceResponse:
    instance = _require_instance(db, instance_id)
    # Captured before deletion — the ORM instance may be expired/unusable
    # for attribute access once its row is gone.
    deleted_id = instance.id
    deleted_name = instance.name

    with db.begin_nested():
        # Record the deletion audit FIRST (captures id/name before the row
        # is gone), then delete. One savepoint, so audit + removal commit
        # together or not at all.
        instance_repository.create_deletion(
            db,
            instance_id=deleted_id,
            instance_name=deleted_name,
            ticket_number=payload.ticketNumber,
            revised_by=payload.revisedBy,
            reviewer=payload.reviewer,
            deleted_by_user_id=actor_user_id,
        )
        instance_repository.delete(db, instance)

    return DeleteInstanceResponse(id=deleted_id, deleted=True)


# =====================================================================
# Stats
# =====================================================================
def get_stats(db: Session) -> InstanceStatsResponse:
    """Header cards: total instances, active instances, and total issuers
    grouped. issuersGrouped is 0 until issuers are linked to instances
    (same placeholder reasoning as issuerCount)."""
    return InstanceStatsResponse(
        totalInstances=instance_repository.count_all(db),
        activeInstances=instance_repository.count_by_status(db, "Active"),
        issuersGrouped=_ISSUER_COUNT_PLACEHOLDER,
    )


# =====================================================================
# Export
# =====================================================================
def build_export(
    db: Session, *, search: Optional[str] = None, status: Optional[str] = None
) -> Tuple[List[str], List[List[str]]]:
    """Returns (headers, rows) for the export file: the fixed columns
    (Instance, Ticket Number, Status) followed by every custom column's
    label in display order. Each row's cells align to those headers, with
    custom values pulled from the instance's custom_fields (missing ->
    blank). The header labels double as the accepted import headers."""
    columns = instance_column_repository.list_all(db)
    headers = [IMPORT_COLUMN_INSTANCE, IMPORT_COLUMN_TICKET, IMPORT_COLUMN_STATUS] + [c.label for c in columns]

    instances = instance_repository.list_for_export(db, search=search, status=status)
    rows: List[List[str]] = []
    for inst in instances:
        cf = inst.custom_fields or {}
        row = [inst.name, inst.ticket_number or "", inst.status]
        row.extend("" if cf.get(c.key) is None else str(cf.get(c.key)) for c in columns)
        rows.append(row)
    return headers, rows


# =====================================================================
# Import — single unified upsert-by-name flow.
#
# There is no "mode": for every row in the uploaded file, the instance
# is looked up by name — if it already exists, its ticket number (and
# status, if the Status column says otherwise) is UPDATED IN PLACE (same
# id, no deletion); if it doesn't exist, a new instance is CREATED. This
# is what "one sheet handles both update and add" means in practice —
# the decision is made per-row from what's already in the database, not
# from a tab the user picks beforehand.
#
# COLUMN MATCHING IS BY HEADER NAME (not position): the file's header
# row must name the columns "Instance", "Ticket Number" and (optionally)
# "Status". Matching is case-insensitive and whitespace-insensitive but
# NOT synonym-based — a header must actually be one of these names. The
# Instance and Ticket Number columns are REQUIRED (the file is rejected
# if either is absent); the Status column is OPTIONAL. Any other columns
# present in the file are ignored.
# =====================================================================
IMPORT_COLUMN_INSTANCE = "Instance"
IMPORT_COLUMN_TICKET = "Ticket Number"
IMPORT_COLUMN_STATUS = "Status"
# Import does NOT capture Revised By / Reviewer (those are an edit/delete
# audit concern only). Instance and Ticket Number are REQUIRED; Status
# is optional.
IMPORT_REQUIRED_COLUMNS = (IMPORT_COLUMN_INSTANCE, IMPORT_COLUMN_TICKET)

def _is_missing(value: object) -> bool:
    """True ONLY when the cell is empty or whitespace-only. The sole
    import checkpoint for a required column is 'not empty' — any typed
    text is accepted and stored VERBATIM, including NA-style placeholders
    (NA / na / null / none / n/a / -). This applies to EVERY column
    (Instance, Ticket Number, Revised By, Reviewer, and custom columns);
    we deliberately do NOT treat 'NA' as missing anymore."""
    return value is None or not str(value).strip()


# Backwards-compatible alias — some call sites referred to this name.
_is_blank_only = _is_missing


def _parse_import_status(value: object) -> str:
    """The Status column is optional and permissive: the cell is
    lowercased before checking — 'active'/'inactive' (any surrounding
    whitespace/case) map to Active/Inactive; a BLANK cell, a MISSING
    Status column, or any unrelated text (typos, other words, numbers)
    all default to Active rather than being rejected as an error."""
    text = str(value or "").strip().lower()
    if text == "inactive":
        return "Inactive"
    return "Active"


def _normalize_header(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def expected_import_columns(db: Session) -> Tuple[List[str], List[str]]:
    """The columns an import file is expected to have, used to validate
    EACH sheet's header independently (not sheet-to-sheet). Returns
    (all_expected_labels, required_labels):

      - all_expected_labels: Instance, Ticket Number, Status, then every
        custom column's label — the full set of headers a sheet may
        legitimately contain, in a canonical order.
      - required_labels: the ones that MUST be present — Instance, Ticket
        Number, and any REQUIRED custom column. (Status is optional.)

    This is the single source of truth for "what a valid header looks
    like", so a multi-sheet workbook validates each sheet against the
    real system columns rather than against whatever the first sheet
    happened to contain (which may itself be wrong)."""
    custom_columns = instance_column_repository.list_all(db)
    all_labels = [IMPORT_COLUMN_INSTANCE, IMPORT_COLUMN_TICKET, IMPORT_COLUMN_STATUS]
    all_labels += [c.label for c in custom_columns]
    required = [IMPORT_COLUMN_INSTANCE, IMPORT_COLUMN_TICKET]
    required += [c.label for c in custom_columns if c.required]
    return all_labels, required


def _find_header(headers: List[str], column: str) -> Optional[int]:
    """Index of the header cell matching `column` by name (case- and
    whitespace-insensitive; no synonyms), or None if absent."""
    target = _normalize_header(column)
    for idx, h in enumerate(headers):
        if _normalize_header(h) == target:
            return idx
    return None


def import_instances(
    db: Session,
    *,
    headers: List[str],
    rows: List[List[object]],
    row_labels: Optional[List[str]] = None,
    header_errors: Optional[List[str]] = None,
    actor_user_id: int,
) -> ImportInstanceResponse:
    """
    Bulk import from a parsed spreadsheet (headers + rows already
    extracted from CSV/XLSX by the router). STRICT + ALL-OR-NOTHING,
    single UPSERT-BY-NAME flow, columns matched BY HEADER NAME:

      1. The header row must contain "Instance" and "Ticket Number"
         columns (case/whitespace-insensitive, no synonyms); the file is
         rejected outright if either is missing. A "Status" column is
         optional. Any other columns are ignored.
      2. Every data row must have a non-blank, non-NA Instance AND Ticket
         Number — otherwise ALL problems are collected and raised
         together, each tagged with its LOCATION. `row_labels[i]` gives
         that location (e.g. "Zone3, row 12" for a multi-sheet workbook,
         or "row 12" for a CSV); if not supplied we fall back to a plain
         "row N" derived from position (header = row 1).
      3. No duplicate Instance names within the file.
      4. Status, if the column is present, is lowercased and checked:
         'inactive' -> Inactive, anything else (including blank) ->
         Active. Never an error.
      5. Per row: if an instance with that name already exists, its
         ticket_number (and status, if changed) is UPDATED IN PLACE
         (same id preserved, so any future linked issuers stay intact).
         Otherwise a new instance is CREATED.
      6. If ANY validation fails, nothing is written (the whole thing is
         one savepoint that rolls back on the raised ValidationError).
    """
    instance_col = _find_header(headers, IMPORT_COLUMN_INSTANCE)
    ticket_col = _find_header(headers, IMPORT_COLUMN_TICKET)
    status_col = _find_header(headers, IMPORT_COLUMN_STATUS)  # optional

    missing = []
    if instance_col is None:
        missing.append(IMPORT_COLUMN_INSTANCE)
    if ticket_col is None:
        missing.append(IMPORT_COLUMN_TICKET)
    if missing:
        raise ValidationError(
            f"Import file is missing required column(s): {', '.join(missing)}. "
            f"Expected columns: {', '.join(IMPORT_REQUIRED_COLUMNS)} (Status optional).",
            code="IMPORT_MISSING_COLUMNS",
            details={"missingColumns": missing, "expectedColumns": list(IMPORT_REQUIRED_COLUMNS)},
        )

    # Locate each custom column by its label in the header row. A REQUIRED
    # custom column whose header is absent from the file is rejected up
    # front (same treatment as a missing built-in required column);
    # optional custom columns simply default to their default/NA per row.
    custom_columns = instance_column_repository.list_all(db)
    custom_col_index = {}  # column.key -> header index (present columns only)
    missing_required_custom = []
    for col in custom_columns:
        idx = _find_header(headers, col.label)
        if idx is not None:
            custom_col_index[col.key] = idx
        elif col.required:
            missing_required_custom.append(col.label)
    if missing_required_custom:
        raise ValidationError(
            f"Import file is missing required column(s): {', '.join(missing_required_custom)}.",
            code="IMPORT_MISSING_COLUMNS",
            details={"missingColumns": missing_required_custom},
        )

    # Per-sheet header mismatches detected by the parser (e.g. a sheet
    # whose header misspells a column). These are collected alongside the
    # row-level problems below and reported together, all-or-nothing —
    # nothing is imported if any sheet's header is wrong.
    errors: List[str] = list(header_errors or [])

    if not rows and not errors:
        raise ValidationError(
            "Import file has no data rows.",
            code="IMPORT_EMPTY",
        )

    # (location_label, name, ticket, status, raw_custom_by_key)
    parsed: List[Tuple[str, str, str, str, dict]] = []
    seen_names: dict = {}  # lower(name) -> first occurrence's location label

    for offset, raw in enumerate(rows):
        # Location label for this row's error messages: the parser-supplied
        # "Sheet, row N" (multi-sheet workbook) or "row N" (CSV). Fall back
        # to a plain "row N" (header = row 1) if labels weren't provided.
        loc = (
            row_labels[offset]
            if row_labels is not None and offset < len(row_labels)
            else f"row {offset + 2}"
        )
        # Capitalized for the sentence start, e.g. "Row 12: ..." / "Zone3,
        # row 12: ...".
        loc_prefix = loc[0].upper() + loc[1:] if loc else loc

        name_cell = raw[instance_col] if instance_col < len(raw) else None
        ticket_cell = raw[ticket_col] if ticket_col < len(raw) else None
        status_cell = raw[status_col] if status_col is not None and status_col < len(raw) else None

        row_ok = True
        if _is_missing(name_cell):
            errors.append(f"{loc_prefix}: '{IMPORT_COLUMN_INSTANCE}' must not be empty.")
            row_ok = False
        if _is_missing(ticket_cell):
            errors.append(f"{loc_prefix}: '{IMPORT_COLUMN_TICKET}' must not be empty.")
            row_ok = False
        if not row_ok:
            continue

        name = str(name_cell).strip()
        ticket = str(ticket_cell).strip()
        status = _parse_import_status(status_cell)

        # Raw custom values for this row, keyed by column key — ONLY the
        # custom columns whose header is present in the file. Validated
        # below via the shared helper (partial=True): it resolves exactly
        # these columns, enforcing required/type and applying default/NA
        # for a present-but-blank optional cell, while leaving custom
        # columns NOT in the file untouched (so an existing instance keeps
        # those values on update; a new instance gets them backfilled/
        # defaulted separately at create via the create path).
        raw_custom = {}
        for key, idx in custom_col_index.items():
            raw_custom[key] = raw[idx] if idx < len(raw) else None

        # Note: a REQUIRED custom column's header is guaranteed present
        # (checked above), so it IS in raw_custom and required-ness is
        # enforced per row here.
        try:
            coerced_custom = instance_column_service.coerce_and_validate_custom_fields(
                db, incoming=raw_custom, existing={}, partial=True
            )
        except ValidationError as exc:
            errors.append(f"{loc_prefix}: {exc.message}")
            continue

        dup_loc = seen_names.get(name.lower())
        if dup_loc is not None:
            errors.append(
                f"{loc_prefix}: duplicate {IMPORT_COLUMN_INSTANCE} {name!r} "
                f"(already appears at {dup_loc})."
            )
            continue
        seen_names[name.lower()] = loc
        parsed.append((loc, name, ticket, status, coerced_custom))

    if errors:
        raise ValidationError(
            f"Import rejected: {len(errors)} problem(s) found. No records were imported.",
            code="IMPORT_VALIDATION_FAILED",
            details={"errors": errors},
        )

    # All validation passed — write everything in ONE savepoint so a
    # late failure still leaves the table untouched. Each row is an
    # UPSERT: update in place if the name exists, create otherwise.
    #
    # BATCHED for large imports: resolve every existing row in a SINGLE
    # query (list_by_names) instead of a get_by_name per row, and let the
    # ORM flush all inserts/updates once at the end of the savepoint
    # rather than per row. This turns thousands of round-trips into a
    # handful, so a multi-thousand-row file imports in ~a second instead
    # of timing out.
    created = 0
    updated = 0
    with db.begin_nested():
        existing_by_name = {
            inst.name.strip().lower(): inst
            for inst in instance_repository.list_by_names(db, [p[1] for p in parsed])
        }
        new_instances: List[Instance] = []
        for _loc, name, ticket, status, coerced_custom in parsed:
            instance = existing_by_name.get(name.strip().lower())
            if instance is None:
                # New instance: fill EVERY custom column — the ones from
                # the file (coerced_custom) plus any absent optional
                # columns defaulted to their default/NA — so the new row
                # is complete, same as a manual create.
                full_custom = instance_column_service.coerce_and_validate_custom_fields(
                    db, incoming=coerced_custom, partial=False
                )
                new_instances.append(
                    Instance(
                        name=name,
                        status=status,
                        ticket_number=ticket,
                        updated_by_user_id=actor_user_id,
                        custom_fields=full_custom,
                    )
                )
                created += 1
            else:
                changes = False
                if instance.ticket_number != ticket:
                    instance.ticket_number = ticket
                    changes = True
                if instance.status != status:
                    instance.status = status
                    changes = True
                # Merge custom values over the existing map (only the
                # columns present in the file were resolved).
                merged_custom = {**(instance.custom_fields or {}), **coerced_custom}
                if merged_custom != (instance.custom_fields or {}):
                    instance.custom_fields = merged_custom
                    changes = True
                if changes:
                    instance.updated_by_user_id = actor_user_id
                updated += 1

        if new_instances:
            db.add_all(new_instances)
        db.flush()  # one flush for all inserts + dirty updates

    return ImportInstanceResponse(created=created, updated=updated, total=len(parsed))


# =====================================================================
# File-level import for the BACKGROUND multi-file job
# =====================================================================
# The synchronous import_instances() above validates+writes ONE flat
# (headers, rows) payload all-or-nothing. The background job needs a
# variant that:
#   - works over MANY sheets belonging to one file at once,
#   - returns STRUCTURED per-sheet/per-row errors (not pre-joined
#     strings) so the API can report {file, sheet, row, messages[]},
#   - is ATOMIC PER FILE (all the file's sheets validate first; if any
#     problem exists nothing from the file is written, but other files
#     in the job are unaffected).
# It deliberately reuses the SAME building blocks as import_instances
# (expected_import_columns, _find_header, _is_missing, _parse_import_status,
# coerce_and_validate_custom_fields, list_by_names) so validation rules
# stay identical to the single-file path.


class SheetValidation:
    """Per-sheet validation outcome for the background worker. `errors`
    is a list of {"row": Optional[int], "messages": [str, ...]} — row is
    None for a header-level problem. `parsed` holds the accepted rows as
    (name, ticket, status, coerced_custom) ready to write.

    A sheet is written IFF `ok` (no errors at all). When it has any error
    the whole sheet is SKIPPED (sheet-level atomicity), but every problem
    across the sheet is still collected here so the user sees the complete
    list to fix — we keep only these compact error entries, never the
    sheet's raw rows, so a file with many sheets stays cheap to track."""

    def __init__(self, sheet_name):
        self.sheet_name = sheet_name
        self.errors: List[dict] = []
        self.parsed: List[tuple] = []
        self.total_rows: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


class FileImportResult:
    """Outcome of processing one file across all its sheets. With
    sheet-level atomicity a file may partially import: the CLEAN sheets
    are written and the sheets with errors are skipped."""

    def __init__(self):
        self.created = 0
        self.updated = 0
        self.sheets: List[SheetValidation] = []

    @property
    def error_count(self) -> int:
        return sum(len(s.errors) for s in self.sheets)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    @property
    def has_clean_sheet(self) -> bool:
        """At least one sheet has no errors and has rows to write."""
        return any(s.ok and s.parsed for s in self.sheets)


def validate_file_sheets(
    db: Session,
    *,
    sheets,
    expected_labels: List[str],
) -> FileImportResult:
    """Validate every sheet of ONE file, collecting structured errors,
    WITHOUT writing anything. `sheets` is a list of objects exposing
    .sheet_name, .headers, .rows, .row_numbers, .header_errors (the
    ParsedSheet dataclass from instance_import_parser). Duplicate-name
    detection spans the WHOLE file (a name may not repeat across the
    file's sheets), matching the single-file rule applied per upload."""
    custom_columns = instance_column_repository.list_all(db)

    result = FileImportResult()
    # lower(name) -> "sheet/row" location of first occurrence, for a
    # file-wide duplicate check.
    seen_names: dict = {}

    for sheet in sheets:
        sv = SheetValidation(sheet.sheet_name)
        sv.total_rows = len(sheet.rows)

        # Header-level problems from the parser (e.g. missing required
        # column) — recorded with row=None; no rows will exist for it.
        for msg in sheet.header_errors:
            sv.errors.append({"row": None, "messages": [msg]})

        headers = sheet.headers
        instance_col = _find_header(headers, IMPORT_COLUMN_INSTANCE)
        ticket_col = _find_header(headers, IMPORT_COLUMN_TICKET)
        status_col = _find_header(headers, IMPORT_COLUMN_STATUS)

        # Resolve custom column header positions (present columns only);
        # a REQUIRED custom column absent from the header is a per-sheet
        # header error.
        custom_col_index = {}
        for col in custom_columns:
            idx = _find_header(headers, col.label)
            if idx is not None:
                custom_col_index[col.key] = idx
            elif col.required:
                sv.errors.append(
                    {"row": None, "messages": [f"missing required column {col.label!r}."]}
                )

        # If the built-in required columns aren't resolvable (shouldn't
        # happen once the parser aligned to canonical order, but guard
        # anyway), record and skip row processing for this sheet.
        if instance_col is None or ticket_col is None:
            missing = []
            if instance_col is None:
                missing.append(IMPORT_COLUMN_INSTANCE)
            if ticket_col is None:
                missing.append(IMPORT_COLUMN_TICKET)
            sv.errors.append(
                {"row": None, "messages": [f"missing required column(s): {', '.join(missing)}."]}
            )
            result.sheets.append(sv)
            continue

        # Duplicate-name detection is now PER SHEET (each sheet is its own
        # atomic unit; a name repeated across two different sheets is no
        # longer a cross-sheet conflict because the sheets import
        # independently). lower(name) -> row number of first occurrence.
        seen_in_sheet: dict = {}

        for offset, raw in enumerate(sheet.rows):
            row_no = sheet.row_numbers[offset] if offset < len(sheet.row_numbers) else offset + 2
            # Collect EVERY problem for this row (not just the first), so
            # the user can fix all of them in one pass: a missing Instance,
            # a missing Ticket, and each bad custom column are reported
            # together.
            messages: List[str] = []

            name_cell = raw[instance_col] if instance_col < len(raw) else None
            ticket_cell = raw[ticket_col] if ticket_col < len(raw) else None
            status_cell = raw[status_col] if status_col is not None and status_col < len(raw) else None

            name_missing = _is_missing(name_cell)
            if name_missing:
                messages.append(f"'{IMPORT_COLUMN_INSTANCE}' must not be empty.")
            if _is_missing(ticket_cell):
                messages.append(f"'{IMPORT_COLUMN_TICKET}' must not be empty.")

            # Validate custom columns present in this sheet's header,
            # collecting ALL their messages (required-missing, datatype
            # mismatch, invalid dropdown option) — see
            # instance_column_service.coerce_row_custom_fields.
            raw_custom = {}
            for key, idx in custom_col_index.items():
                raw_custom[key] = raw[idx] if idx < len(raw) else None
            coerced_custom, custom_errors = instance_column_service.coerce_row_custom_fields(
                db, incoming=raw_custom, columns=custom_columns
            )
            messages.extend(custom_errors)

            # Duplicate name within this sheet — only meaningful when the
            # name itself is present.
            if not name_missing:
                name = str(name_cell).strip()
                dup_row = seen_in_sheet.get(name.lower())
                if dup_row is not None:
                    messages.append(
                        f"duplicate {IMPORT_COLUMN_INSTANCE} {name!r} "
                        f"(already appears at row {dup_row})."
                    )
                else:
                    seen_in_sheet[name.lower()] = row_no

            if messages:
                sv.errors.append({"row": row_no, "messages": messages})
                continue

            # Row is fully valid — stage it for the write.
            name = str(name_cell).strip()
            ticket = str(ticket_cell).strip()
            status = _parse_import_status(status_cell)
            sv.parsed.append((name, ticket, status, coerced_custom))

        result.sheets.append(sv)

    return result


def write_file_rows(
    db: Session,
    *,
    result: FileImportResult,
    actor_user_id: int,
) -> None:
    """Write the accepted rows of every CLEAN sheet — SHEET-LEVEL
    atomicity. Each clean sheet gets its OWN savepoint, so a sheet that
    somehow fails at flush time rolls back alone without discarding the
    other sheets that already committed. Sheets with validation errors
    are skipped entirely (they have no `parsed` rows). Per-sheet
    created/updated counts are recorded on each SheetValidation (as
    .created/.updated) for the progress UI, and rolled up onto
    result.created/result.updated.

    Rows across a sheet are resolved with a single list_by_names query and
    flushed once (the same batching as the single-file path), so a large
    clean sheet still imports in one round-trip."""
    for sv in result.sheets:
        write_one_sheet(db, sv=sv, actor_user_id=actor_user_id)
        result.created += sv.created
        result.updated += sv.updated


def write_one_sheet(
    db: Session,
    *,
    sv: "SheetValidation",
    actor_user_id: int,
) -> None:
    """Write a SINGLE validated sheet's accepted rows in its OWN savepoint
    and set sv.created / sv.updated. A skipped sheet (has errors or no
    accepted rows) writes nothing and reports zero counts. Extracted from
    write_file_rows so the background worker can write-and-commit one sheet
    at a time — enabling live, per-sheet progress (the UI ticks up as each
    sheet lands) and immediate roll-up, rather than one write at the end of
    the whole file."""
    sv.created = 0
    sv.updated = 0
    if not sv.ok or not sv.parsed:
        return  # skipped sheet — nothing written

    with db.begin_nested():
        existing_by_name = {
            inst.name.strip().lower(): inst
            for inst in instance_repository.list_by_names(db, [p[0] for p in sv.parsed])
        }
        new_instances: List[Instance] = []
        for name, ticket, status, coerced_custom in sv.parsed:
            instance = existing_by_name.get(name.strip().lower())
            if instance is None:
                full_custom = instance_column_service.coerce_and_validate_custom_fields(
                    db, incoming=coerced_custom, partial=False
                )
                new_instances.append(
                    Instance(
                        name=name,
                        status=status,
                        ticket_number=ticket,
                        updated_by_user_id=actor_user_id,
                        custom_fields=full_custom,
                    )
                )
                sv.created += 1
            else:
                changes = False
                if instance.ticket_number != ticket:
                    instance.ticket_number = ticket
                    changes = True
                if instance.status != status:
                    instance.status = status
                    changes = True
                merged_custom = {**(instance.custom_fields or {}), **coerced_custom}
                if merged_custom != (instance.custom_fields or {}):
                    instance.custom_fields = merged_custom
                    changes = True
                if changes:
                    instance.updated_by_user_id = actor_user_id
                sv.updated += 1

        if new_instances:
            db.add_all(new_instances)
        db.flush()
