"""
Service layer for Gift Card Bin Series records. Orchestrates
app.repositories.gift_card_bin_repository (plus
app.services.bin_series_shared for cross-table uniqueness), maps ORM
objects to app.schemas.gift_card_bin_series.GiftCardBinRecordResponse,
and raises the shared domain exceptions (app.core.exceptions) — no
SQLAlchemy usage, no HTTP-layer concerns. Structural mirror of the old,
untouched app/services/bin_service.py (see that module's docstring for
the base reasoning behind every pattern reused verbatim below:
transaction strategy, actor handling, snapshot-for-history convention,
partial-batch-failure bulk upload) — independent of it, no shared base,
no import from it, per the confirmed architecture.

TRANSACTION STRATEGY: identical to bin_service.py — every write wraps
its business mutation AND its audit_service.create_revision() call in
ONE `with db.begin_nested():` savepoint, audit call last.

STATUS is NEVER touched by create_*/update_*/delete_* below — confirmed
by frontend inspection (AddRowModal.jsx/EditRowModal-equivalent never
collect status; every new record is hardcoded 'Active' — here, via the
model's own server_default, matching bin_repository.create()'s same
convention). The ONLY way status changes is `update_status()`, which
requires a mandatory ticket number (BinStatusUpdateRequest) — confirmed
by StatusConfirmModal.jsx rendering an unconditional, required
TicketField for this flow only. `update_status()` persists the supplied
ticketNumber onto the record's own `ticket_number` column (Stage 5
validation requirement: "status change persists ticket number") — this
is the same field Create's `ticketNumber` populates initially; a status
change simply supplies a fresh one, same as any other field update
would.

ACTION_TYPE FOR STATUS CHANGES: `Revision.ACTION_TYPE_VALUES` (see
app/models/revision.py) is `("create", "update", "delete", "upload")` —
there is no "status_change" value, and this stage may not modify the
Revision model/migration. `update_status()` therefore uses
action_type="update", which is also literally correct (a status change
IS a field update) and matches the approved frontend's own local
change-log call for this same action — BinTable.jsx's
confirmSaveEdit()-adjacent confirmStatusChange() calls `log('update', ...)`,
never a distinct type. The change is still fully distinguishable via
`change_description` ("Status changed from Active to Inactive
(ticket ABC-123)") and via `metadata.fieldsChanged`/ticketNumber —
flagged in this stage's completion report as a deliberate resolution,
not a silent decision.

CROSS-TABLE UNIQUENESS: create_gift_card_bin_record() and
update_gift_card_bin_record() (only when binIin/merchantPrefix actually
changes) call
`bin_series_shared.check_bin_prefix_available(db, bin_iin=..., merchant_prefix=..., exclude_gift_card_id=...)`
BEFORE writing — this checks BOTH gift_card_bin_records and
wallet_bin_records. The existing `IntegrityError` catch below remains as
a defense-in-depth backstop for the SAME-table half of a race (this
table's own UniqueConstraint); the cross-table half is
application-level-only, a documented and accepted gap (see
bin_series_shared.py's module docstring).

BULK UPLOAD — Stage 5's explicit rules (a deliberate DIFFERENCE from the
old bin_service.py's hybrid (bin_iin, merchant_prefix)-primary /
issuer-fallback matching):
  - "Add New": EVERY non-custom field is required (instance, issuer,
    merchant, cardProgramGroupName, binIin, merchantPrefix,
    cardProgramGroupType, cardType, ticketNumber) — confirmed by
    UploadSheetModal.jsx's `filledRows.every(r => columns.every(c => ...))`,
    which now requires ALL columns non-blank (unlike the old table's
    narrower required set). binIin/merchantPrefix format-checked, then
    checked against BOTH tables (cross-table) via a batch prefetch of
    each, then an in-batch duplicate check.
  - "Update Existing": matches by ISSUER NAME ONLY, scoped to this
    (Gift Card) type — confirmed by inspecting utils/csv.js (unchanged:
    `identityValue`/`issuerKey` still default to `'issuer'`) and this
    stage's explicit instruction. NO (bin_iin, merchant_prefix) primary
    matching path exists here (a deliberate departure from the OLD
    single-table bulk-upload's hybrid key) — an issuer match that is
    non-unique (2+ existing Gift Card records share that issuer) fails
    the row as ambiguous, exactly like the old code's own ambiguous-issuer
    case; zero matches is a SKIP (nothing wrong with the row, there's
    just nothing to update), never a failure.
  - Every row is its own `db.begin_nested()` savepoint — one bad row
    never sinks the batch (Stage 5's explicit instruction, matching the
    old bulk-upload's own partial-failure philosophy).

LOOKUP / BULK-LOOKUP / VERSION HISTORY are NOT implemented in this
module — those are combined, cross-type operations and live in
app.services.bin_series_shared (this module is intentionally never
imported by that one, to avoid a circular import — see its docstring).

Export (GET .../gift-card/export): ADDED in Stage 6 (a Stage 5 gap,
flagged and closed there per Stage 6's explicit instruction to implement
the export route layer). `export_gift_card_bin_records_csv()` mirrors
the old bin_service.py's export_bin_records_csv() shape (CSV via the
stdlib `csv` module, same escaping/CRLF convention) but has no
repository-level `export_all()` to call — Stage 6 forbids repository
changes, so it reuses the EXISTING, unmodified
gift_card_bin_repository.search() with a deliberately large `page_size`
instead of paginating, the smallest safe way to fetch "every matching
row" without adding a new repository method. Column set, order, and
header labels follow Stage 6's explicit instruction verbatim (Instance/
Issuer/Merchant/CardProgramGroupName/BIN/Merchant Prefix/
CardProgramGroupType/CardType/Ticket Number, then every custom column by
its current display name in display_order, then Updated By/Updated At
last — the same trailing-two-columns convention as the old export).
"""
import csv
import io
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.gift_card_bin_record import GiftCardBinRecord
from app.repositories import gift_card_bin_custom_column_repository, gift_card_bin_repository, wallet_bin_repository
from app.schemas.bin_series_shared import (
    BinBulkUploadResponse,
    BinBulkUploadRowNote,
    BinRecordDeleteResponse,
    BinStatusUpdateRequest,
)
from app.schemas.common import PaginatedResponse
from app.schemas.gift_card_bin_series import (
    CreateGiftCardBinRecordRequest,
    GiftCardBinRecordResponse,
    GiftCardBinSeriesStatsResponse,
    GiftCardBulkUploadRequest,
    GiftCardBulkUploadRowRequest,
    UpdateGiftCardBinRecordRequest,
)
from app.services import audit_service, bin_series_shared

_BIN_IIN_PATTERN = re.compile(r"^\d{6}$")
_MERCHANT_PREFIX_PATTERN = re.compile(r"^\d{3}$")

# (request field, model attribute, human-readable label) — fixed order
# for change_description, same convention as bin_service.py's
# _UPDATABLE_FIELDS.
_UPDATABLE_FIELDS = [
    ("instance", "instance", "Instance"),
    ("issuer", "issuer", "Issuer"),
    ("merchant", "merchant", "Merchant"),
    ("cardProgramGroupName", "card_program_group_name", "Card Program Group Name"),
    ("binIin", "bin_iin", "BIN / IIN"),
    ("merchantPrefix", "merchant_prefix", "Merchant Prefix"),
    ("cardProgramGroupType", "card_program_group_type", "Card Program Group Type"),
    ("cardType", "card_type", "Card Type"),
    ("ticketNumber", "ticket_number", "Ticket Number"),
]
_FIELD_LABELS_BY_ATTR = {model_attr: label for _, model_attr, label in _UPDATABLE_FIELDS}


def _snapshot(record: GiftCardBinRecord) -> dict:
    """Complete, JSON-serializable point-in-time copy of a Gift Card BIN
    record's business fields — used ONLY as a revision's before/after
    history snapshot, never returned as an API response. Deliberately
    excludes updatedBy/updatedAt, same reasoning as bin_service.py's own
    _snapshot(). Returns a shallow copy of customFields so a later
    reassignment on the live ORM object can't retroactively mutate an
    already-captured snapshot."""
    return {
        "id": record.id,
        "instance": record.instance,
        "issuer": record.issuer,
        "merchant": record.merchant,
        "cardProgramGroupName": record.card_program_group_name,
        "binIin": record.bin_iin,
        "merchantPrefix": record.merchant_prefix,
        "cardProgramGroupType": record.card_program_group_type,
        "cardType": record.card_type,
        "ticketNumber": record.ticket_number,
        "status": record.status,
        "customFields": dict(record.custom_fields or {}),
    }


def _to_response(record: GiftCardBinRecord) -> GiftCardBinRecordResponse:
    return GiftCardBinRecordResponse(
        id=record.id,
        instance=record.instance,
        issuer=record.issuer,
        merchant=record.merchant,
        cardProgramGroupName=record.card_program_group_name,
        binIin=record.bin_iin,
        merchantPrefix=record.merchant_prefix,
        cardProgramGroupType=record.card_program_group_type,
        cardType=record.card_type,
        ticketNumber=record.ticket_number,
        status=record.status,
        customFields=record.custom_fields or {},
        updatedBy=record.updated_by_user.name if record.updated_by_user else None,
        updatedAt=record.updated_at,
    )


def _check_custom_fields_exist(db: Session, custom_fields: Optional[Dict[str, str]]) -> None:
    if not custom_fields:
        return
    known_keys = gift_card_bin_custom_column_repository.existing_keys(db, set(custom_fields.keys()))
    unknown_keys = set(custom_fields.keys()) - known_keys
    if unknown_keys:
        raise ValidationError(
            f"Unknown custom column key(s): {', '.join(sorted(unknown_keys))}.",
            code="BIN_CUSTOM_COLUMN_NOT_FOUND",
        )


def _describe_field_changes(field_changes: List[Tuple[str, object, object]]) -> str:
    clauses = []
    for label, old, new in field_changes:
        clause = f"{label} changed from {old} to {new}"
        if clauses:
            clause = clause[0].lower() + clause[1:]
        clauses.append(clause)
    return "; ".join(clauses)


def _require_record(db: Session, record_id: int) -> GiftCardBinRecord:
    record = gift_card_bin_repository.get_by_id(db, record_id)
    if record is None:
        raise NotFoundError(f"No Gift Card BIN record found for id={record_id}.", code="BIN_RECORD_NOT_FOUND")
    return record


def _target_label(record: GiftCardBinRecord) -> str:
    return f"{record.issuer} — {record.bin_iin}{record.merchant_prefix}"


def list_gift_card_bin_records(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
) -> PaginatedResponse[GiftCardBinRecordResponse]:
    records, total = gift_card_bin_repository.search(
        db, search=search, status=status, sort_by=sort_by, sort_order=sort_order, page=page, page_size=page_size
    )
    return PaginatedResponse[GiftCardBinRecordResponse](
        items=[_to_response(r) for r in records], total=total, page=page, pageSize=page_size
    )


def get_gift_card_bin_record(db: Session, record_id: int) -> GiftCardBinRecordResponse:
    return _to_response(_require_record(db, record_id))


def create_gift_card_bin_record(
    db: Session, payload: CreateGiftCardBinRecordRequest, *, actor_user_id: int
) -> GiftCardBinRecordResponse:
    bin_series_shared.check_bin_prefix_available(db, bin_iin=payload.binIin, merchant_prefix=payload.merchantPrefix)
    _check_custom_fields_exist(db, payload.customFields)

    try:
        with db.begin_nested():
            record = gift_card_bin_repository.create(
                db,
                instance=payload.instance.strip(),
                issuer=payload.issuer.strip(),
                merchant=payload.merchant.strip(),
                card_program_group_name=payload.cardProgramGroupName.strip(),
                bin_iin=payload.binIin,
                merchant_prefix=payload.merchantPrefix,
                card_program_group_type=payload.cardProgramGroupType.strip(),
                card_type=payload.cardType.strip(),
                ticket_number=payload.ticketNumber.strip(),
                updated_by_user_id=actor_user_id,
                custom_fields=payload.customFields,
            )
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="create",
                entity_type="gift_card_bin_record",
                entity_id=record.id,
                target_label=_target_label(record),
                change_description="Gift Card BIN record created",
                metadata={
                    "binIin": record.bin_iin,
                    "merchantPrefix": record.merchant_prefix,
                    "customFields": record.custom_fields or None,
                    "before": None,
                    "after": _snapshot(record),
                },
            )
    except IntegrityError as exc:
        raise ConflictError(
            f"A Gift Card BIN record already exists for binIin={payload.binIin!r} "
            f"and merchantPrefix={payload.merchantPrefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        ) from exc

    return _to_response(record)


def update_gift_card_bin_record(
    db: Session, *, record_id: int, payload: UpdateGiftCardBinRecordRequest, actor_user_id: int
) -> GiftCardBinRecordResponse:
    record = _require_record(db, record_id)

    new_bin_iin = payload.binIin if payload.binIin is not None else record.bin_iin
    new_merchant_prefix = payload.merchantPrefix if payload.merchantPrefix is not None else record.merchant_prefix
    if (new_bin_iin, new_merchant_prefix) != (record.bin_iin, record.merchant_prefix):
        bin_series_shared.check_bin_prefix_available(
            db, bin_iin=new_bin_iin, merchant_prefix=new_merchant_prefix, exclude_gift_card_id=record.id
        )

    _check_custom_fields_exist(db, payload.customFields)

    field_changes: List[Tuple[str, object, object]] = []
    fields_to_set: Dict[str, object] = {}
    for payload_field, model_attr, label in _UPDATABLE_FIELDS:
        value = getattr(payload, payload_field)
        if value is not None:
            old_value = getattr(record, model_attr)
            if value != old_value:
                field_changes.append((label, old_value, value))
                fields_to_set[model_attr] = value

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

    change_description = _describe_field_changes(field_changes)
    fields_to_set["updated_by_user_id"] = actor_user_id
    before_snapshot = _snapshot(record)

    try:
        with db.begin_nested():
            gift_card_bin_repository.update_fields(db, record, **fields_to_set)
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="update",
                entity_type="gift_card_bin_record",
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
            f"A Gift Card BIN record already exists for binIin={new_bin_iin!r} "
            f"and merchantPrefix={new_merchant_prefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        ) from exc

    return _to_response(record)


def update_gift_card_bin_status(
    db: Session, *, record_id: int, payload: BinStatusUpdateRequest, actor_user_id: int
) -> GiftCardBinRecordResponse:
    """The ONLY path that ever changes `status` — see this module's
    docstring for the mandatory-ticket / action_type="update" reasoning.
    A resend of the current status is a no-op (no write, no revision),
    same "nothing actually changed" convention as every other update
    path in this project — but the ticket number is still NOT persisted
    in that case, since nothing about the record actually needs to
    change; StatusConfirmModal.jsx never even reaches its confirm button
    without a currently-different target status selected."""
    record = _require_record(db, record_id)

    if payload.status == record.status:
        return _to_response(record)

    old_status = record.status
    old_ticket = record.ticket_number
    before_snapshot = _snapshot(record)

    with db.begin_nested():
        gift_card_bin_repository.update_fields(
            db,
            record,
            status=payload.status,
            ticket_number=payload.ticketNumber.strip(),
            updated_by_user_id=actor_user_id,
        )
        audit_service.create_revision(
            db,
            actor_user_id=actor_user_id,
            action_type="update",
            entity_type="gift_card_bin_record",
            entity_id=record.id,
            target_label=_target_label(record),
            change_description=(
                f"Status changed from {old_status} to {payload.status} (ticket {payload.ticketNumber.strip()})"
            ),
            metadata={
                "fieldsChanged": {
                    "Status": {"from": old_status, "to": payload.status},
                    "Ticket Number": {"from": old_ticket, "to": record.ticket_number},
                },
                "before": before_snapshot,
                "after": _snapshot(record),
            },
        )

    return _to_response(record)


def delete_gift_card_bin_record(db: Session, *, record_id: int, actor_user_id: int) -> BinRecordDeleteResponse:
    record = _require_record(db, record_id)

    deleted_id = record.id
    deleted_target_label = _target_label(record)
    before_snapshot = _snapshot(record)

    with db.begin_nested():
        gift_card_bin_repository.delete(db, record)
        audit_service.create_revision(
            db,
            actor_user_id=actor_user_id,
            action_type="delete",
            entity_type="gift_card_bin_record",
            entity_id=deleted_id,
            target_label=deleted_target_label,
            change_description="Gift Card BIN record deleted",
            metadata={"before": before_snapshot, "after": None},
        )

    return BinRecordDeleteResponse(id=deleted_id, deleted=True)


def get_gift_card_bin_series_stats(db: Session) -> GiftCardBinSeriesStatsResponse:
    total_records, total_issuers, total_card_programs = gift_card_bin_repository.stats(db)
    return GiftCardBinSeriesStatsResponse(
        totalRecords=total_records, totalIssuers=total_issuers, totalCardPrograms=total_card_programs
    )


# Fixed base-column export order/labels — per Stage 6's explicit
# instruction (verbatim column list), not derived from
# GiftCardBinRecordResponse's own field order.
_EXPORT_BASE_COLUMNS: List[Tuple[str, str]] = [
    ("instance", "Instance"),
    ("issuer", "Issuer"),
    ("merchant", "Merchant"),
    ("card_program_group_name", "CardProgramGroupName"),
    ("bin_iin", "BIN"),
    ("merchant_prefix", "Merchant Prefix"),
    ("card_program_group_type", "CardProgramGroupType"),
    ("card_type", "CardType"),
    ("ticket_number", "Ticket Number"),
]


def export_gift_card_bin_records_csv(
    db: Session, *, search: Optional[str] = None, status: Optional[str] = None
) -> str:
    """Returns the full CSV document as a string — see this module's
    docstring for the column set/order/header-label rules and why
    `search()` (Stage 4, unmodified) is reused with a large `page_size`
    instead of a dedicated no-pagination repository method."""
    records, _ = gift_card_bin_repository.search(
        db, search=search, status=status, sort_by=None, sort_order="asc", page=1, page_size=1_000_000
    )
    custom_columns = gift_card_bin_custom_column_repository.list_all(db)

    header = (
        [label for _, label in _EXPORT_BASE_COLUMNS]
        + [column.name for column in custom_columns]
        + ["Updated By", "Updated At"]
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for record in records:
        row = [getattr(record, attr) for attr, _ in _EXPORT_BASE_COLUMNS]
        row.extend((record.custom_fields or {}).get(column.key, "") for column in custom_columns)
        row.append(record.updated_by_user.name if record.updated_by_user else "")
        row.append(record.updated_at.isoformat() if record.updated_at else "")
        writer.writerow(row)

    return buffer.getvalue()


def _note(row_index: int, bin_iin: Optional[str], merchant_prefix: Optional[str], reason: str) -> BinBulkUploadRowNote:
    return BinBulkUploadRowNote(rowIndex=row_index, binIin=bin_iin, merchantPrefix=merchant_prefix, reason=reason)


def _bulk_add_new(
    db: Session, rows: List[GiftCardBulkUploadRowRequest], *, actor_user_id: int
) -> Tuple[int, List[BinBulkUploadRowNote]]:
    created = 0
    failed: List[BinBulkUploadRowNote] = []
    seen_in_batch = set()

    candidate_pairs = [(r.binIin, r.merchantPrefix) for r in rows if r.binIin and r.merchantPrefix]
    existing_same_type = gift_card_bin_repository.get_by_bin_and_prefix_batch(db, candidate_pairs)
    # Cross-table check (see this module's docstring) — batch-prefetched
    # once for the whole "Add New" batch, never one query per row.
    existing_other_type = wallet_bin_repository.get_by_bin_and_prefix_batch(db, candidate_pairs)

    for index, row in enumerate(rows):
        instance = (row.instance or "").strip()
        issuer = (row.issuer or "").strip()
        merchant = (row.merchant or "").strip()
        card_program_group_name = (row.cardProgramGroupName or "").strip()
        bin_iin = row.binIin
        merchant_prefix = row.merchantPrefix
        card_program_group_type = (row.cardProgramGroupType or "").strip()
        card_type = (row.cardType or "").strip()
        ticket_number = (row.ticketNumber or "").strip()

        if not all(
            [instance, issuer, merchant, card_program_group_name, bin_iin, merchant_prefix,
             card_program_group_type, card_type, ticket_number]
        ):
            failed.append(_note(
                index, bin_iin, merchant_prefix,
                "instance, issuer, merchant, cardProgramGroupName, binIin, merchantPrefix, "
                "cardProgramGroupType, cardType, and ticketNumber are all required to add a new row.",
            ))
            continue
        if not _BIN_IIN_PATTERN.match(bin_iin):
            failed.append(_note(index, bin_iin, merchant_prefix, "binIin must be exactly 6 digits."))
            continue
        if not _MERCHANT_PREFIX_PATTERN.match(merchant_prefix):
            failed.append(_note(index, bin_iin, merchant_prefix, "merchantPrefix must be exactly 3 digits."))
            continue

        key = (bin_iin, merchant_prefix)
        if key in existing_same_type or key in existing_other_type or key in seen_in_batch:
            failed.append(_note(
                index, bin_iin, merchant_prefix,
                "A BIN record already exists for this binIin and merchantPrefix.",
            ))
            continue

        try:
            with db.begin_nested():
                record = gift_card_bin_repository.create(
                    db,
                    instance=instance,
                    issuer=issuer,
                    merchant=merchant,
                    card_program_group_name=card_program_group_name,
                    bin_iin=bin_iin,
                    merchant_prefix=merchant_prefix,
                    card_program_group_type=card_program_group_type,
                    card_type=card_type,
                    ticket_number=ticket_number,
                    updated_by_user_id=actor_user_id,
                )
                audit_service.create_revision(
                    db,
                    actor_user_id=actor_user_id,
                    action_type="upload",
                    entity_type="gift_card_bin_record",
                    entity_id=record.id,
                    target_label=_target_label(record),
                    change_description="Gift Card BIN record created via bulk upload",
                    metadata={
                        "binIin": bin_iin,
                        "merchantPrefix": merchant_prefix,
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


def _bulk_update_existing(
    db: Session, rows: List[GiftCardBulkUploadRowRequest], *, actor_user_id: int
) -> Tuple[int, List[BinBulkUploadRowNote], List[BinBulkUploadRowNote]]:
    """ISSUER-ONLY matching, scoped to Gift Card records — see this
    module's docstring for why there is no (bin_iin, merchant_prefix)
    matching path here, unlike the old single-table bulk-upload."""
    updated = 0
    skipped_rows: List[BinBulkUploadRowNote] = []
    failed: List[BinBulkUploadRowNote] = []

    issuers = [r.issuer for r in rows if r.issuer]
    by_issuer = gift_card_bin_repository.get_by_issuer_batch(db, issuers)

    for index, row in enumerate(rows):
        issuer = (row.issuer or "").strip()
        if not issuer:
            failed.append(_note(index, row.binIin, row.merchantPrefix, "issuer is required to identify a row to update."))
            continue

        candidates = by_issuer.get(issuer.lower(), [])
        if not candidates:
            skipped_rows.append(_note(index, row.binIin, row.merchantPrefix, f"No existing Gift Card BIN record matches issuer {issuer!r}."))
            continue
        if len(candidates) > 1:
            failed.append(_note(
                index, row.binIin, row.merchantPrefix,
                f"Issuer {issuer!r} matches {len(candidates)} existing Gift Card records — ambiguous.",
            ))
            continue
        record = candidates[0]

        field_changes: List[Tuple[str, object, object]] = []
        fields_to_set: Dict[str, object] = {}

        candidate_values = {
            "instance": (row.instance or "").strip(),
            "merchant": (row.merchant or "").strip(),
            "cardProgramGroupName": (row.cardProgramGroupName or "").strip(),
            "cardProgramGroupType": (row.cardProgramGroupType or "").strip(),
            "cardType": (row.cardType or "").strip(),
            "ticketNumber": (row.ticketNumber or "").strip(),
        }
        payload_to_attr = {
            "instance": "instance",
            "merchant": "merchant",
            "cardProgramGroupName": "card_program_group_name",
            "cardProgramGroupType": "card_program_group_type",
            "cardType": "card_type",
            "ticketNumber": "ticket_number",
        }
        for payload_field, value in candidate_values.items():
            model_attr = payload_to_attr[payload_field]
            if value and value != getattr(record, model_attr):
                field_changes.append((_FIELD_LABELS_BY_ATTR[model_attr], getattr(record, model_attr), value))
                fields_to_set[model_attr] = value

        new_bin_iin = row.binIin if row.binIin else record.bin_iin
        new_merchant_prefix = row.merchantPrefix if row.merchantPrefix else record.merchant_prefix
        if (new_bin_iin, new_merchant_prefix) != (record.bin_iin, record.merchant_prefix):
            if not (row.binIin and _BIN_IIN_PATTERN.match(row.binIin) and row.merchantPrefix and _MERCHANT_PREFIX_PATTERN.match(row.merchantPrefix)):
                failed.append(_note(index, row.binIin, row.merchantPrefix, "binIin/merchantPrefix must both be valid 6/3-digit values when changing them."))
                continue
            try:
                bin_series_shared.check_bin_prefix_available(
                    db, bin_iin=new_bin_iin, merchant_prefix=new_merchant_prefix, exclude_gift_card_id=record.id
                )
            except ConflictError:
                failed.append(_note(index, row.binIin, row.merchantPrefix, "A BIN record already exists for this binIin and merchantPrefix."))
                continue
            field_changes.append((_FIELD_LABELS_BY_ATTR["bin_iin"], record.bin_iin, new_bin_iin))
            field_changes.append((_FIELD_LABELS_BY_ATTR["merchant_prefix"], record.merchant_prefix, new_merchant_prefix))
            fields_to_set["bin_iin"] = new_bin_iin
            fields_to_set["merchant_prefix"] = new_merchant_prefix

        if not fields_to_set:
            skipped_rows.append(_note(index, row.binIin, row.merchantPrefix, "No changed fields — nothing to update."))
            continue

        change_description = _describe_field_changes(field_changes)
        fields_to_set["updated_by_user_id"] = actor_user_id
        before_snapshot = _snapshot(record)

        try:
            with db.begin_nested():
                gift_card_bin_repository.update_fields(db, record, **fields_to_set)
                audit_service.create_revision(
                    db,
                    actor_user_id=actor_user_id,
                    action_type="upload",
                    entity_type="gift_card_bin_record",
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


def bulk_upload_gift_card_bin_records(
    db: Session, payload: GiftCardBulkUploadRequest, *, actor_user_id: int
) -> BinBulkUploadResponse:
    created = updated = 0
    skipped_rows: List[BinBulkUploadRowNote] = []
    failed_rows: List[BinBulkUploadRowNote] = []

    if payload.mode == "addNew":
        created, failed_rows = _bulk_add_new(db, payload.rows, actor_user_id=actor_user_id)
    else:
        updated, skipped_rows, failed_rows = _bulk_update_existing(db, payload.rows, actor_user_id=actor_user_id)

    return BinBulkUploadResponse(
        mode=payload.mode,
        totalRows=len(payload.rows),
        createdCount=created,
        updatedCount=updated,
        skippedCount=len(skipped_rows),
        failedCount=len(failed_rows),
        failedRows=failed_rows,
        skippedRows=skipped_rows,
    )
