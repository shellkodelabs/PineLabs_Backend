"""
Service layer for BIN Series. Orchestrates app.repositories.bin_repository
(plus merchant_repository for the merchantId existence check), maps ORM
objects to app.schemas.bin_series.BinRecordResponse, and raises the
shared domain exceptions (app.core.exceptions) for not-found/conflict/
validation cases — no SQLAlchemy usage, no HTTP-layer concerns.

Part 16 (Create/Update/Delete) TRANSACTION STRATEGY: same pattern as
app/services/user_service.py — create_bin_record()/update_bin_record()/
delete_bin_record() each wrap their DB-mutating work, including the
audit_service.create_revision() call, in ONE `with db.begin_nested():`
block (a real SAVEPOINT). If anything inside raises, everything written
inside that block — the business write AND the revision — rolls back
together. The audit call is the LAST statement in each block for the
same reason as user_service: if IT is what fails, the business write
made just before it in the same request is rolled back too.

ACTOR HANDLING: the authenticated `CurrentUser` (see app/core/auth.py)
is already resolved from a real row in the local `users` table by the
authentication boundary — unlike Part 13's original actorUserId query
parameter, there is no separate "does this actor id exist" re-check
here; `actor.id` is used directly as both the revision's actor and the
new `updated_by_user_id` FK value. Never accepted from a request
payload — see app/schemas/bin_series.py, neither CreateBinRecordRequest
nor UpdateBinRecordRequest has an updatedBy/actor field at all.

Part 17 — `instanceName` mandatory:
  - CREATE: CreateBinRecordRequest.instanceName is a required, validated
    (non-blank) field — Pydantic rejects missing/null/empty/whitespace
    before this module ever runs (see app/schemas/bin_series.py).
  - UPDATE: `instanceName` follows the same partial-update convention as
    every other field (omitted = unchanged) EXCEPT that an explicit
    `null` is rejected — `_reject_explicit_null_instance_name` below
    detects that case via `payload.model_fields_set`, since a schema-level
    Optional[str] validator alone can't tell "omitted" apart from
    "explicitly sent as null" (both parse to Python None). On top of
    that, per the task's requirement that instanceName be mandatory for
    "NEW/UPDATED records": whenever an update would otherwise write ANY
    real change, the record must end up with a non-blank instance_name —
    enforced by `_check_resulting_instance_name` after the normal
    field-diff computation, using whatever instance_name would result
    from this request (freshly supplied, or already on the record).
    A pure no-op update (nothing actually changes) is exempt — see
    update_bin_record()'s existing "nothing to write" early return,
    unchanged from Part 16.

Bulk upload (POST /api/v1/bin-series/bulk-upload) — Bin Series priority
task. Backs the frontend's BinTable "Import Data" button
(UploadSheetModal.jsx), which offers two modes: update rows already on
file, or add brand-new ones. `bulk_upload_bin_records()` dispatches to
`_bulk_add_new()`/`_bulk_update_existing()` accordingly.

  MATCHING KEY (see app/schemas/bin_series.py's module docstring for the
  full reasoning): HYBRID, not a single-key decision.
    1. (bin_iin, merchant_prefix) when a row supplies both — the real
       DB-unique key. Primary, authoritative.
    2. Issuer name, ONLY when a row has no usable (bin_iin,
       merchant_prefix) pair, and ONLY when it matches EXACTLY ONE
       existing record — two or more matches fails that row as
       "ambiguous" rather than silently picking one. This exists
       because the frontend's own updateExisting() (BinTable.jsx)
       currently matches by issuer alone, and a real "just update one
       field" sheet may genuinely omit bin_iin/merchant_prefix — but
       issuer is provably non-unique, so it can never be trusted alone.

  PARTIAL FAILURE (deliberate choice, not all-or-nothing): a bad row
  must not sink the whole batch. Each row gets its OWN
  `db.begin_nested()` savepoint (business write + its own "upload"
  audit revision, atomic with each other exactly like the single-row
  APIs), so one row's failure rolls back only that row and processing
  continues. This matches how the approved frontend already behaves —
  UploadSheetModal tolerates per-file parse errors and updateExisting()
  silently skips non-matching rows rather than aborting anything — so a
  "tell me what happened" response fits the approved UX better than an
  all-or-nothing 422. Every row's outcome is exactly one of: created,
  updated, skipped (updateExisting only — no matching row, or nothing
  actually changed), or failed (validation problem, ambiguous match, or
  a write conflict) — reported back, never silently dropped.

  DUPLICATE ROWS: for addNew, an in-batch (bin_iin, merchant_prefix)
  duplicate fails the SECOND occurrence (the first still succeeds) —
  same rule as a duplicate against the existing DB. For updateExisting,
  if two rows resolve to the same existing record, they are applied in
  order (later rows' values win); this is a deliberate, documented
  choice, not an accident.

  instanceName: no per-row value exists in the frontend's upload payload
  today (UploadSheetModal.jsx collects none), so this endpoint takes ONE
  batch-level `instanceName` — required on every new row (addNew), and
  used only to backfill a matched row that doesn't already have one
  (updateExisting) — same "mandatory for NEW/UPDATED records" rule as
  the single-row APIs (Part 17), never overwriting an existing value.

Bulk/multi-card resolve (POST /api/v1/bin-series/bulk-lookup, renamed
from /resolve-batch by the API Naming task) — Bin Series priority task.
Backs BulkLookupModal.jsx (opened from both
BinResolver.jsx and BinTable.jsx).

  MATCHING RULE — a documented SUPERSET of resolve_bin()'s rule, not a
  second inconsistent algorithm: for each card, extract up to 9 digits
  (bin_iin = first 6, prefix = next up to 3). If exactly 3 prefix digits
  were extracted, require an exact (bin_iin, merchant_prefix) match —
  same as resolve_bin(). If FEWER than 3 prefix digits were extracted
  (partial or absent), additionally try a "BIN uniquely identifies
  exactly one record" shortcut: if bin_iin matches exactly one existing
  record, resolve to it anyway; if it matches zero or 2+, no match — NEVER
  guesses among multiple candidates. This exact rule (including the
  shortcut) is what BulkLookupModal.jsx's own local resolve() already
  implements; reusing app.repositories.bin_repository's conventions
  (batch dict-of-lists lookup, same shape as get_by_issuer_batch) rather
  than reinventing a new query style.

  EFFICIENCY: ONE batch query for every card's bin_iin
  (get_by_bin_iin_batch), never one query per card.

  ORDER / DUPLICATES: `results` has exactly one entry per input card, in
  the exact input order, including literal duplicates — never
  deduplicated, never dropped. A card that can't even yield 6 digits
  still gets an explicit `matched: false` result, never an error.

Dynamic/Custom Columns task — `customFields` on create/update:
  `_check_custom_fields_exist` validates every key in a request's
  `customFields` against the bin_custom_columns metadata registry
  (app/repositories/bin_custom_column_repository.py) — an unknown key is
  a 422, never silently accepted; that would let this endpoint create
  ad-hoc, unregistered "columns" bypassing POST /bin-series/custom-columns/create
  entirely, which is exactly the single-source-of-truth violation the
  registry exists to prevent. On UPDATE, `customFields` MERGES into the
  existing dict (each supplied key overwrites that key only; every other
  existing key is left untouched) — matches BinTable.jsx's saveCell(),
  which always touches exactly one key. Column create/rename events
  themselves are NOT audited here — see
  app/services/bin_custom_column_service.py; only the ROW-level
  custom-field value diff is folded into this module's existing
  "bin_record" update change_description/metadata, alongside every other
  field.

Export task — GET /api/v1/bin-series/export:
  `export_bin_records_csv` backs the frontend's Export button
  (BinTable.jsx's exportSheet() -> utils/csv.js's serializeCsv/
  downloadCsv). Read-only, no audit revision — export was already a
  pure client-side download with no backend involvement, and nothing in
  this task's requirements calls for treating a read as auditable.

  COLUMNS/ORDER (from frontend inspection): Issuer, Card Program Group
  Name, BIN / IIN Code, Merchant Prefix, then every custom column (by
  its current display `name`, in display_order — reusing the same
  metadata registry POST/GET /bin-series/custom-columns already uses),
  then Updated By, Updated At last. This is exactly BinTable.jsx's own
  `exportColumns` order: `Object.keys(row)` for a mock row is always
  issuer/cardProgramGroupName/binIin/merchantPrefix first (insertion
  order from data/binSeries.js), any custom columns addColumn() has
  since inserted (always appended before updatedBy/updatedAt, never
  after), then updatedBy/updatedAt last (addColumn() explicitly pulls
  them out and re-appends them after every custom-column insert).
  `merchantId` and `instanceName` are deliberately NOT exported: the
  frontend's local mock row objects never had those fields to begin
  with (data/binSeries.js's seed rows only ever have
  issuer/cardProgramGroupName/binIin/merchantPrefix + the audit pair),
  so they never appear in `Object.keys(row)` and never reach the
  exported CSV — matching the frontend's actual behavior exactly rather
  than adding fields the approved UI doesn't surface. Flagged as an
  openQuestionForProduct in docs/bin-series-api-contract.json, same as
  the pre-existing instanceName/merchantId mismatch Part 17 already
  flagged.

  HEADER LABELS: base columns use FIXED labels (Issuer, Card Program
  Group Name, BIN / IIN Code, Merchant Prefix, Updated By, Updated At)
  matching BinTable.jsx's `baseColumnLabels` exactly — NOT whatever a
  given browser session may have locally renamed a header to, since
  base-column renames are confirmed frontend-only/never persisted (see
  the Dynamic/Custom Columns task's Step 8 finding) — the backend has no
  way to know about a rename it was never told about. Custom-column
  headers use each column's current, PERSISTED `name` (renaming a
  custom column IS a real backend write, unlike a base column), so a
  rename is correctly reflected on the next export.

  DATASET: `export_all()` (app/repositories/bin_repository.py) takes the
  exact same optional filters as the list endpoint's `search()` — reused,
  not reimplemented — but NEVER paginates: confirmed by inspection that
  exportSheet() downloads `data` (the full local dataset), not `rows`
  (the search-filtered view actually shown on screen) and not the
  current page — the approved frontend's Export button ignores the
  search box entirely. The query parameters this endpoint accepts exist
  for forward integration (so export CAN be scoped once a real search
  box drives it), not because today's frontend sends any today; calling
  it with no parameters exactly reproduces the button's current
  behavior.

  CSV FORMAT: matches utils/csv.js's own `serializeCsv`/`csvEscape`
  exactly — comma-delimited, CRLF row separator, a value is quoted (with
  internal quotes doubled) only if it contains a comma/quote/newline —
  which is precisely Python's built-in `csv` module's default
  (QUOTE_MINIMAL) dialect, so no hand-rolled escaping was written here.
  No UTF-8 BOM (the frontend's Blob doesn't add one either). Header row
  is always present, even for zero matching rows.

Version History task — before/after snapshots:
  `_snapshot()` is the single place that turns a BinRecord into a
  complete, JSON-serializable dict (issuer/cardProgramGroupName/binIin/
  merchantPrefix/merchantId/instanceName/customFields — deliberately NOT
  updatedBy/updatedAt, see its own docstring) for storage inside a
  revision's `metadata_.before`/`metadata_.after`. Every bin_record
  mutation path (create, update, delete, and BOTH bulk-upload row paths)
  now adds `before`/`after` keys to the SAME `audit_service.
  create_revision()` call it already made — no new revision-creation
  call sites, no new audit system, purely additive metadata keys
  alongside whatever each call already stored. The rule is always
  "snapshot BEFORE mutating, mutate, snapshot AFTER, build the revision
  metadata from both" — for create, before=None (nothing existed yet);
  for delete, after=None (nothing exists anymore); for update (single-row
  and the bulk-upload update path alike), both are full row states,
  captured immediately before/after the same `bin_repository.
  update_fields()` call that already existed. See
  app/services/bin_series_history_service.py for how these are surfaced
  via GET /api/v1/bin-series/version-history — this module never reads
  its own revisions back, it only writes richer ones.

  Bulk upload's audit calls (`_bulk_add_new`/`_bulk_update_existing`)
  gained ONLY these two new metadata keys each — their request/response
  contract, matching logic, and partial-failure semantics are completely
  unchanged (Bulk Upload remains frozen); this is the "smallest safe
  change to the existing audit path" the Version History task explicitly
  authorized.
"""
import csv
import io
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.bin_record import BinRecord
from app.repositories import bin_custom_column_repository, bin_repository, merchant_repository
from app.schemas.bin_series import (
    BinRecordResponse,
    BinSeriesStatsResponse,
    BulkUploadRequest,
    BulkUploadResponse,
    BulkUploadRowNote,
    BulkUploadRowRequest,
    CreateBinRecordRequest,
    DeleteBinRecordResponse,
    ResolveBatchResponse,
    ResolveBatchResultItem,
    UpdateBinRecordRequest,
)
from app.schemas.common import PaginatedResponse
from app.services import audit_service

_BIN_IIN_PATTERN = re.compile(r"^\d{6}$")
_MERCHANT_PREFIX_PATTERN = re.compile(r"^\d{3}$")

# (request field, model attribute, human-readable label) — in the fixed
# order they should appear in a change_description when several change
# at once, same convention as user_service._UPDATABLE_FIELD_LABELS.
_UPDATABLE_FIELDS = [
    ("issuer", "issuer", "Issuer"),
    ("cardProgramGroupName", "card_program_group_name", "Card Program Group Name"),
    ("binIin", "bin_iin", "BIN / IIN"),
    ("merchantPrefix", "merchant_prefix", "Merchant Prefix"),
    ("merchantId", "merchant_id", "Merchant Id"),
    ("instanceName", "instance_name", "Instance Name"),
]

# model_attr -> human label, reused by the bulk-upload path for the same
# change-description style as the single-row update (_describe_field_changes).
_FIELD_LABELS_BY_ATTR = {model_attr: label for _, model_attr, label in _UPDATABLE_FIELDS}

# Row-count cap for a single bulk-upload request is enforced in the
# schema itself (BulkUploadRequest.rows: max_length=1000) — no separate
# check needed here.


def _snapshot(record: BinRecord) -> dict:
    """A complete, JSON-serializable point-in-time copy of a BIN record's
    business fields — used ONLY as a revision's before/after history
    snapshot (app/services/bin_service.py's Version History integration),
    never returned directly as an API response. Deliberately excludes
    updatedBy/updatedAt: those describe who/when last touched the CURRENT
    row in general, not anything specific to the historical moment this
    snapshot captures (an "after" snapshot's updatedAt would just equal
    "now", which is already the revision's own occurredAt). Must be
    called BEFORE any mutation that would invalidate it — since
    `custom_fields` is a mutable dict, this returns a shallow COPY (`dict(...)`)
    so a later `record.custom_fields = {...}` reassignment elsewhere
    can't retroactively change an already-captured "before" snapshot."""
    return {
        "id": record.id,
        "issuer": record.issuer,
        "cardProgramGroupName": record.card_program_group_name,
        "binIin": record.bin_iin,
        "merchantPrefix": record.merchant_prefix,
        "merchantId": record.merchant_id,
        "instanceName": record.instance_name,
        "customFields": dict(record.custom_fields or {}),
    }


def _to_response(record: BinRecord) -> BinRecordResponse:
    return BinRecordResponse(
        id=record.id,
        issuer=record.issuer,
        cardProgramGroupName=record.card_program_group_name,
        binIin=record.bin_iin,
        merchantPrefix=record.merchant_prefix,
        merchantId=record.merchant_id,
        instanceName=record.instance_name,
        customFields=record.custom_fields or {},
        updatedBy=record.updated_by_user.name if record.updated_by_user else None,
        updatedAt=record.updated_at,
    )


def _check_custom_fields_exist(db: Session, custom_fields: Optional[Dict[str, str]]) -> None:
    if not custom_fields:
        return
    known_keys = bin_custom_column_repository.existing_keys(db, set(custom_fields.keys()))
    unknown_keys = set(custom_fields.keys()) - known_keys
    if unknown_keys:
        raise ValidationError(
            f"Unknown custom column key(s): {', '.join(sorted(unknown_keys))}.",
            code="BIN_CUSTOM_COLUMN_NOT_FOUND",
        )


def _describe_field_changes(field_changes: List[Tuple[str, object, object]]) -> str:
    """Same convention as user_service._describe_field_changes: e.g.
    "Issuer changed from Aurora Retail to Aurora Retail Ltd; merchant
    prefix changed from 001 to 004" (first clause capitalized,
    subsequent clauses lowercased, joined with "; ")."""
    clauses = []
    for label, old, new in field_changes:
        clause = f"{label} changed from {old} to {new}"
        if clauses:
            clause = clause[0].lower() + clause[1:]
        clauses.append(clause)
    return "; ".join(clauses)


def _require_bin_record(db: Session, bin_record_id: int) -> BinRecord:
    record = bin_repository.get_by_id(db, bin_record_id)
    if record is None:
        raise NotFoundError(f"No BIN record found for id={bin_record_id}.", code="BIN_RECORD_NOT_FOUND")
    return record


def _check_natural_key_available(
    db: Session, *, bin_iin: str, merchant_prefix: str, exclude_id: Optional[int] = None
) -> None:
    existing = bin_repository.get_by_bin_and_prefix(db, bin_iin=bin_iin, merchant_prefix=merchant_prefix)
    if existing is not None and existing.id != exclude_id:
        raise ConflictError(
            f"A BIN record already exists for binIin={bin_iin!r} and merchantPrefix={merchant_prefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        )


def _check_merchant_exists(db: Session, merchant_id: Optional[int]) -> None:
    if merchant_id is None:
        return
    if merchant_repository.get_by_id(db, merchant_id) is None:
        raise ValidationError(f"No merchant found for merchantId={merchant_id}.", code="BIN_MERCHANT_NOT_FOUND")


def _target_label(record: BinRecord) -> str:
    return f"{record.issuer} — {record.bin_iin}{record.merchant_prefix}"


def _reject_explicit_null_instance_name(payload: UpdateBinRecordRequest) -> None:
    """`instanceName` is mandatory, so a client explicitly sending
    `"instanceName": null` must be rejected — but omitting the key
    entirely must NOT be (that's ordinary partial-update omission,
    leaving the existing value untouched). Both cases parse to
    `payload.instanceName is None`, so the schema-level validator
    (app/schemas/bin_series.py) can't distinguish them; `model_fields_set`
    (which field KEYS were actually present in the request body) can."""
    if "instanceName" in payload.model_fields_set and payload.instanceName is None:
        raise ValidationError("instanceName must not be null.", code="BIN_INSTANCE_NAME_REQUIRED")


def _check_resulting_instance_name(record: BinRecord, fields_to_set: dict) -> None:
    """Per the task's requirement that instanceName be mandatory for
    "NEW/UPDATED records": if this update is about to write any real
    change, the record must end up with a non-blank instance_name —
    either freshly supplied in this same request, or already present on
    the record from before. Only reachable once `fields_to_set` is known
    to be non-empty (a genuine no-op update is exempt, same as every
    other field)."""
    resulting_instance_name = fields_to_set.get("instance_name", record.instance_name)
    if not resulting_instance_name:
        raise ValidationError(
            "instanceName is required and cannot be blank when updating a BIN record "
            "that does not already have one.",
            code="BIN_INSTANCE_NAME_REQUIRED",
        )


def list_bin_records(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    issuer: Optional[str] = None,
    card_program_group_name: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
) -> PaginatedResponse[BinRecordResponse]:
    records, total = bin_repository.search(
        db,
        search=search,
        issuer=issuer,
        card_program_group_name=card_program_group_name,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse[BinRecordResponse](
        items=[_to_response(r) for r in records],
        total=total,
        page=page,
        pageSize=page_size,
    )


# Fixed base-column export headers — matches BinTable.jsx's
# baseColumnLabels exactly (see this module's docstring for why these
# are hardcoded rather than pulled from anything client-supplied: base
# column renames are frontend-only and never reach the backend).
_EXPORT_BASE_COLUMNS: List[Tuple[str, str]] = [
    ("issuer", "Issuer"),
    ("cardProgramGroupName", "Card Program Group Name"),
    ("binIin", "BIN / IIN Code"),
    ("merchantPrefix", "Merchant Prefix"),
]


def export_bin_records_csv(
    db: Session,
    *,
    search: Optional[str] = None,
    issuer: Optional[str] = None,
    card_program_group_name: Optional[str] = None,
) -> str:
    """Returns the full CSV document as a string — see this module's
    docstring for the column set/order, header-label, and escaping
    rules this replicates from the approved frontend."""
    records = bin_repository.export_all(
        db, search=search, issuer=issuer, card_program_group_name=card_program_group_name
    )
    custom_columns = bin_custom_column_repository.list_all(db)

    header = (
        [label for _, label in _EXPORT_BASE_COLUMNS]
        + [column.name for column in custom_columns]
        + ["Updated By", "Updated At"]
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for record in records:
        row = [
            record.issuer,
            record.card_program_group_name,
            record.bin_iin,
            record.merchant_prefix,
        ]
        row.extend((record.custom_fields or {}).get(column.key, "") for column in custom_columns)
        row.append(record.updated_by_user.name if record.updated_by_user else "")
        row.append(record.updated_at.isoformat() if record.updated_at else "")
        writer.writerow(row)

    return buffer.getvalue()


def resolve_bin(db: Session, *, bin_iin: str, merchant_prefix: str) -> BinRecordResponse:
    record = bin_repository.get_by_bin_and_prefix(db, bin_iin=bin_iin, merchant_prefix=merchant_prefix)
    if record is None:
        raise NotFoundError(
            f"No BIN record found for binIin={bin_iin!r} and merchantPrefix={merchant_prefix!r}.",
            code="BIN_RECORD_NOT_FOUND",
        )
    return _to_response(record)


def create_bin_record(db: Session, payload: CreateBinRecordRequest, *, actor_user_id: int) -> BinRecordResponse:
    _check_natural_key_available(db, bin_iin=payload.binIin, merchant_prefix=payload.merchantPrefix)
    _check_merchant_exists(db, payload.merchantId)
    _check_custom_fields_exist(db, payload.customFields)

    try:
        with db.begin_nested():
            record = bin_repository.create(
                db,
                issuer=payload.issuer.strip(),
                card_program_group_name=payload.cardProgramGroupName.strip(),
                bin_iin=payload.binIin,
                merchant_prefix=payload.merchantPrefix,
                merchant_id=payload.merchantId,
                instance_name=payload.instanceName,  # already stripped/validated non-blank by the schema
                updated_by_user_id=actor_user_id,
                custom_fields=payload.customFields,
            )
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="create",
                entity_type="bin_record",
                entity_id=record.id,
                target_label=_target_label(record),
                change_description="BIN record created",
                metadata={
                    "binIin": record.bin_iin,
                    "merchantPrefix": record.merchant_prefix,
                    "merchantId": record.merchant_id,
                    "instanceName": record.instance_name,
                    "customFields": record.custom_fields or None,
                    # Version History task: before=null (nothing existed
                    # yet), after=the complete created state — see
                    # _snapshot()'s docstring.
                    "before": None,
                    "after": _snapshot(record),
                },
            )
    except IntegrityError as exc:
        # Defense in depth beyond the upfront uniqueness check above (e.g.
        # a concurrent request inserting the same (binIin, merchantPrefix)
        # in between).
        raise ConflictError(
            f"A BIN record already exists for binIin={payload.binIin!r} and merchantPrefix={payload.merchantPrefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        ) from exc

    return _to_response(record)


def update_bin_record(
    db: Session, *, bin_record_id: int, payload: UpdateBinRecordRequest, actor_user_id: int
) -> BinRecordResponse:
    record = _require_bin_record(db, bin_record_id)

    new_bin_iin = payload.binIin if payload.binIin is not None else record.bin_iin
    new_merchant_prefix = payload.merchantPrefix if payload.merchantPrefix is not None else record.merchant_prefix
    if (new_bin_iin, new_merchant_prefix) != (record.bin_iin, record.merchant_prefix):
        _check_natural_key_available(
            db, bin_iin=new_bin_iin, merchant_prefix=new_merchant_prefix, exclude_id=record.id
        )

    if payload.merchantId is not None:
        _check_merchant_exists(db, payload.merchantId)

    _reject_explicit_null_instance_name(payload)
    _check_custom_fields_exist(db, payload.customFields)

    # --- Compute what would actually change, WITHOUT writing anything
    # yet, so we can (a) skip the whole operation (and any revision, and
    # touching updated_by_user_id/updated_at) if nothing would really
    # change, and (b) build an accurate change_description from real
    # before/after values. Same approach as user_service.update_user().
    field_changes: List[Tuple[str, object, object]] = []
    fields_to_set = {}
    for payload_field, model_attr, label in _UPDATABLE_FIELDS:
        value = getattr(payload, payload_field)
        if value is not None:
            old_value = getattr(record, model_attr)
            if value != old_value:
                field_changes.append((label, old_value, value))
                fields_to_set[model_attr] = value
            # else: client resent the current value — not a real change.

    # customFields MERGES into the existing dict — only the supplied
    # keys change, every other existing key is preserved untouched
    # (matches BinTable.jsx's saveCell(), which always touches exactly
    # one key at a time). Already validated to reference only existing
    # columns, above.
    if payload.customFields:
        merged_custom_fields = dict(record.custom_fields or {})
        for key, value in payload.customFields.items():
            old_value = merged_custom_fields.get(key)
            if value != old_value:
                field_changes.append((f"Custom Field: {key}", old_value, value))
                merged_custom_fields[key] = value
        if merged_custom_fields != (record.custom_fields or {}):
            fields_to_set["custom_fields"] = merged_custom_fields

    if not fields_to_set:
        return _to_response(record)

    # A real write is about to happen — the record must end up with a
    # non-blank instance_name (Part 17), regardless of whether THIS
    # request is what supplies it.
    _check_resulting_instance_name(record, fields_to_set)

    change_description = _describe_field_changes(field_changes)
    fields_to_set["updated_by_user_id"] = actor_user_id

    # Version History task: BEFORE must be captured before the mutation
    # happens — `_snapshot()` copies out `record`'s current field values
    # (including a dict-copy of custom_fields) into a plain dict immune
    # to the setattr() calls update_fields() is about to make on this
    # SAME ORM object.
    before_snapshot = _snapshot(record)

    try:
        with db.begin_nested():
            bin_repository.update_fields(db, record, **fields_to_set)
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="update",
                entity_type="bin_record",
                entity_id=record.id,
                target_label=_target_label(record),
                change_description=change_description,
                metadata={
                    "fieldsChanged": {label: {"from": old, "to": new} for label, old, new in field_changes},
                    "before": before_snapshot,
                    "after": _snapshot(record),
                },
            )
    except IntegrityError as exc:
        raise ConflictError(
            f"A BIN record already exists for binIin={new_bin_iin!r} and merchantPrefix={new_merchant_prefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        ) from exc

    return _to_response(record)


def delete_bin_record(db: Session, *, bin_record_id: int, actor_user_id: int) -> DeleteBinRecordResponse:
    record = _require_bin_record(db, bin_record_id)

    # Captured before deletion — revisions.entity_id is a soft reference
    # (not a FK, see app/models/revision.py), so it safely outlives the
    # row it describes, but the ORM instance itself may be
    # expired/unusable for attribute access once its row is gone.
    deleted_id = record.id
    deleted_target_label = _target_label(record)
    # Version History task: the deleted row's complete state, captured
    # BEFORE bin_repository.delete() — this is the ONLY place that state
    # will ever exist again (the row itself is gone). See _snapshot()'s
    # docstring.
    before_snapshot = _snapshot(record)

    with db.begin_nested():
        bin_repository.delete(db, record)
        # revision.user_id = the ACTOR performing the delete — entity_id
        # (not a FK) safely carries the deleted record's former id.
        audit_service.create_revision(
            db,
            actor_user_id=actor_user_id,
            action_type="delete",
            entity_type="bin_record",
            entity_id=deleted_id,
            target_label=deleted_target_label,
            change_description="BIN record deleted",
            metadata={"before": before_snapshot, "after": None},
        )

    return DeleteBinRecordResponse(id=deleted_id, deleted=True)


def _note(row_index: int, bin_iin: Optional[str], merchant_prefix: Optional[str], reason: str) -> BulkUploadRowNote:
    return BulkUploadRowNote(rowIndex=row_index, binIin=bin_iin, merchantPrefix=merchant_prefix, reason=reason)


def _bulk_add_new(
    db: Session, rows: List[BulkUploadRowRequest], *, instance_name: str, actor_user_id: int
) -> Tuple[int, List[BulkUploadRowNote]]:
    created = 0
    failed: List[BulkUploadRowNote] = []
    seen_in_batch = set()

    candidate_pairs = [(r.binIin, r.merchantPrefix) for r in rows if r.binIin and r.merchantPrefix]
    existing = bin_repository.get_by_bin_and_prefix_batch(db, candidate_pairs)

    for index, row in enumerate(rows):
        issuer = (row.issuer or "").strip()
        card_program_group_name = (row.cardProgramGroupName or "").strip()
        bin_iin = row.binIin
        merchant_prefix = row.merchantPrefix

        if not issuer or not card_program_group_name or not bin_iin or not merchant_prefix:
            failed.append(_note(
                index, bin_iin, merchant_prefix,
                "issuer, cardProgramGroupName, binIin, and merchantPrefix are all required to add a new row.",
            ))
            continue
        if not _BIN_IIN_PATTERN.match(bin_iin):
            failed.append(_note(index, bin_iin, merchant_prefix, "binIin must be exactly 6 digits."))
            continue
        if not _MERCHANT_PREFIX_PATTERN.match(merchant_prefix):
            failed.append(_note(index, bin_iin, merchant_prefix, "merchantPrefix must be exactly 3 digits."))
            continue

        key = (bin_iin, merchant_prefix)
        if key in existing or key in seen_in_batch:
            failed.append(_note(
                index, bin_iin, merchant_prefix,
                "A BIN record already exists for this binIin and merchantPrefix.",
            ))
            continue
        if row.merchantId is not None and merchant_repository.get_by_id(db, row.merchantId) is None:
            failed.append(_note(index, bin_iin, merchant_prefix, f"No merchant found for merchantId={row.merchantId}."))
            continue

        try:
            with db.begin_nested():
                record = bin_repository.create(
                    db,
                    issuer=issuer,
                    card_program_group_name=card_program_group_name,
                    bin_iin=bin_iin,
                    merchant_prefix=merchant_prefix,
                    merchant_id=row.merchantId,
                    instance_name=instance_name,
                    updated_by_user_id=actor_user_id,
                )
                audit_service.create_revision(
                    db,
                    actor_user_id=actor_user_id,
                    action_type="upload",
                    entity_type="bin_record",
                    entity_id=record.id,
                    target_label=_target_label(record),
                    change_description="BIN record created via bulk upload",
                    metadata={
                        "binIin": bin_iin,
                        "merchantPrefix": merchant_prefix,
                        "instanceName": instance_name,
                        # Version History task: same before=null/after=
                        # snapshot convention as the single-row create
                        # path — the smallest additive change to this
                        # already-existing revision call; nothing about
                        # bulk-upload's own request/response contract or
                        # matching logic is touched.
                        "before": None,
                        "after": _snapshot(record),
                    },
                )
        except IntegrityError:
            failed.append(_note(
                index, bin_iin, merchant_prefix,
                "A BIN record already exists for this binIin and merchantPrefix.",
            ))
            continue

        seen_in_batch.add(key)
        created += 1

    return created, failed


def _resolve_update_target(
    row: BulkUploadRowRequest,
    *,
    by_bin_prefix: Dict[Tuple[str, str], BinRecord],
    by_issuer: Dict[str, List[BinRecord]],
) -> Tuple[Optional[BinRecord], Optional[BulkUploadRowNote], bool]:
    """Implements the hybrid matching key (see this module's docstring).
    Returns (record_or_None, note_or_None, is_failure). `is_failure`
    distinguishes an ambiguous/unidentifiable row (a FAILURE — something
    is wrong with the row) from a clean "nothing matches" (a SKIP —nothing
    wrong with the row, there's just no existing record to update)."""
    if row.binIin and row.merchantPrefix:
        record = by_bin_prefix.get((row.binIin, row.merchantPrefix))
        if record is None:
            return None, _note(
                -1, row.binIin, row.merchantPrefix,
                "No existing BIN record matches this binIin/merchantPrefix — not created (use mode=addNew for that).",
            ), False
        return record, None, False

    if row.issuer:
        candidates = by_issuer.get(row.issuer.strip().lower(), [])
        if not candidates:
            return None, _note(
                -1, row.binIin, row.merchantPrefix,
                f"No existing BIN record matches issuer {row.issuer!r}.",
            ), False
        if len(candidates) > 1:
            return None, _note(
                -1, row.binIin, row.merchantPrefix,
                f"Issuer {row.issuer!r} matches {len(candidates)} existing records — ambiguous without "
                "binIin/merchantPrefix to disambiguate.",
            ), True
        return candidates[0], None, False

    return None, _note(
        -1, row.binIin, row.merchantPrefix,
        "Row has no binIin/merchantPrefix and no issuer — cannot identify a record to update.",
    ), True


def _bulk_update_existing(
    db: Session, rows: List[BulkUploadRowRequest], *, instance_name: str, actor_user_id: int
) -> Tuple[int, List[BulkUploadRowNote], List[BulkUploadRowNote]]:
    updated = 0
    skipped_rows: List[BulkUploadRowNote] = []
    failed: List[BulkUploadRowNote] = []

    bin_prefix_pairs = [(r.binIin, r.merchantPrefix) for r in rows if r.binIin and r.merchantPrefix]
    by_bin_prefix = bin_repository.get_by_bin_and_prefix_batch(db, bin_prefix_pairs)

    fallback_issuers = [r.issuer for r in rows if not (r.binIin and r.merchantPrefix) and r.issuer]
    by_issuer = bin_repository.get_by_issuer_batch(db, fallback_issuers)

    for index, row in enumerate(rows):
        record, note, is_failure = _resolve_update_target(row, by_bin_prefix=by_bin_prefix, by_issuer=by_issuer)
        if record is None:
            note.rowIndex = index
            (failed if is_failure else skipped_rows).append(note)
            continue

        field_changes: List[Tuple[str, object, object]] = []
        fields_to_set: Dict[str, object] = {}

        candidate_values = {
            "issuer": (row.issuer or "").strip(),
            "card_program_group_name": (row.cardProgramGroupName or "").strip(),
        }
        for model_attr, value in candidate_values.items():
            if value and value != getattr(record, model_attr):
                field_changes.append((_FIELD_LABELS_BY_ATTR[model_attr], getattr(record, model_attr), value))
                fields_to_set[model_attr] = value

        if row.merchantId is not None and row.merchantId != record.merchant_id:
            if merchant_repository.get_by_id(db, row.merchantId) is None:
                failed.append(_note(index, row.binIin, row.merchantPrefix, f"No merchant found for merchantId={row.merchantId}."))
                continue
            field_changes.append((_FIELD_LABELS_BY_ATTR["merchant_id"], record.merchant_id, row.merchantId))
            fields_to_set["merchant_id"] = row.merchantId

        resulting_instance_name = record.instance_name or instance_name
        if not resulting_instance_name:
            failed.append(_note(
                index, row.binIin, row.merchantPrefix,
                "instanceName is required to update a BIN record that does not already have one.",
            ))
            continue
        if not record.instance_name and instance_name:
            field_changes.append((_FIELD_LABELS_BY_ATTR["instance_name"], record.instance_name, instance_name))
            fields_to_set["instance_name"] = instance_name

        if not fields_to_set:
            skipped_rows.append(_note(index, row.binIin, row.merchantPrefix, "No changed fields — nothing to update."))
            continue

        change_description = _describe_field_changes(field_changes)
        fields_to_set["updated_by_user_id"] = actor_user_id
        # Version History task: same before-then-mutate-then-after
        # convention as the single-row update path — captured here,
        # before update_fields() mutates `record` below.
        before_snapshot = _snapshot(record)

        try:
            with db.begin_nested():
                bin_repository.update_fields(db, record, **fields_to_set)
                audit_service.create_revision(
                    db,
                    actor_user_id=actor_user_id,
                    action_type="upload",
                    entity_type="bin_record",
                    entity_id=record.id,
                    target_label=_target_label(record),
                    change_description=f"{change_description} (via bulk upload)",
                    metadata={
                        "fieldsChanged": {label: {"from": old, "to": new} for label, old, new in field_changes},
                        "before": before_snapshot,
                        "after": _snapshot(record),
                    },
                )
        except IntegrityError:
            failed.append(_note(index, row.binIin, row.merchantPrefix, "Update failed due to a data conflict."))
            continue

        updated += 1

    return updated, skipped_rows, failed


def bulk_upload_bin_records(db: Session, payload: BulkUploadRequest, *, actor_user_id: int) -> BulkUploadResponse:
    """Backs the frontend's "Import Data" upload flow (both tabs). See
    this module's docstring for the matching-key and instanceName design
    decisions."""
    created = updated = 0
    skipped_rows: List[BulkUploadRowNote] = []
    failed_rows: List[BulkUploadRowNote] = []

    if payload.mode == "addNew":
        created, failed_rows = _bulk_add_new(
            db, payload.rows, instance_name=payload.instanceName, actor_user_id=actor_user_id
        )
    else:
        updated, skipped_rows, failed_rows = _bulk_update_existing(
            db, payload.rows, instance_name=payload.instanceName, actor_user_id=actor_user_id
        )

    return BulkUploadResponse(
        mode=payload.mode,
        totalRows=len(payload.rows),
        createdCount=created,
        updatedCount=updated,
        skippedCount=len(skipped_rows),
        failedCount=len(failed_rows),
        failedRows=failed_rows,
        skippedRows=skipped_rows,
    )


def get_bin_series_stats(db: Session) -> BinSeriesStatsResponse:
    total_records, total_issuers, total_card_programs = bin_repository.stats(db)
    return BinSeriesStatsResponse(
        totalRecords=total_records,
        totalIssuers=total_issuers,
        totalCardPrograms=total_card_programs,
    )


def _extract_bin_and_prefix(raw: str) -> Tuple[str, str, str]:
    """Returns (trimmed_input, bin_iin, prefix_digits). bin_iin is '' if
    fewer than 6 digits were present; prefix_digits is '' to 3 chars
    (NOT padded) — the caller decides what "usable" means for each."""
    trimmed = (raw or "").strip()
    digits = re.sub(r"\D", "", raw or "")
    return trimmed, digits[:6], digits[6:9]


def resolve_bin_batch(db: Session, cards: List[str]) -> ResolveBatchResponse:
    """Backs POST /api/v1/bin-series/bulk-lookup. See this module's
    docstring for the matching-rule reasoning."""
    parsed = [_extract_bin_and_prefix(card) for card in cards]

    usable_bins = [bin_iin for _, bin_iin, _ in parsed if len(bin_iin) == 6]
    by_bin = bin_repository.get_by_bin_iin_batch(db, usable_bins)

    results: List[ResolveBatchResultItem] = []
    for trimmed_input, bin_iin, prefix_digits in parsed:
        if len(bin_iin) != 6:
            # Too few digits to even have a BIN — explicit unmatched
            # result, never dropped and never an error.
            results.append(ResolveBatchResultItem(input=trimmed_input, matched=False))
            continue

        candidates = by_bin.get(bin_iin, [])
        hit: Optional[BinRecord] = None
        if len(prefix_digits) == 3:
            hit = next((r for r in candidates if r.merchant_prefix == prefix_digits), None)
        elif len(candidates) == 1:
            # The BIN-uniquely-identifies-one-record shortcut — only
            # when the prefix is partial/absent, and only when there is
            # exactly one candidate. Never guesses among several.
            hit = candidates[0]

        results.append(ResolveBatchResultItem(
            input=trimmed_input,
            binIin=bin_iin,
            merchantPrefix=prefix_digits if len(prefix_digits) == 3 else None,
            issuer=hit.issuer if hit else None,
            cardProgramGroupName=hit.card_program_group_name if hit else None,
            matched=hit is not None,
        ))

    return ResolveBatchResponse(results=results)
