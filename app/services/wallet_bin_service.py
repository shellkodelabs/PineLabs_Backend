"""
Service layer for Wallet Bin Series records. Structural mirror of
app/services/gift_card_bin_service.py (see that module's docstring for
the full reasoning behind every pattern below — transaction strategy,
status/ticket handling, action_type="update" resolution for status
changes, cross-table uniqueness, bulk-upload issuer-only matching,
partial-batch-failure semantics) — independent of it, no shared base,
no import from it, per the confirmed architecture. The only structural
difference: this table has no cardProgramGroupName/cardProgramGroupType/
cardType — walletProgramName/walletProgramGroupType take their place,
and GiftCardBinSeriesStatsResponse's totalCardPrograms becomes
WalletBinSeriesStatsResponse's totalWalletPrograms.

Export (GET .../wallet/export): ADDED in Stage 6, mirroring
gift_card_bin_service.py's export_gift_card_bin_records_csv() exactly
(see that module's docstring for the full reasoning) — reuses the
existing, unmodified wallet_bin_repository.search() with a large
page_size rather than a dedicated no-pagination repository method.
"""
import csv
import io
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.wallet_bin_record import WalletBinRecord
from app.repositories import gift_card_bin_repository, wallet_bin_custom_column_repository, wallet_bin_repository
from app.schemas.bin_series_shared import (
    BinBulkUploadResponse,
    BinBulkUploadRowNote,
    BinRecordDeleteResponse,
    BinStatusUpdateRequest,
)
from app.schemas.common import PaginatedResponse
from app.schemas.wallet_bin_series import (
    CreateWalletBinRecordRequest,
    UpdateWalletBinRecordRequest,
    WalletBinRecordResponse,
    WalletBinSeriesStatsResponse,
    WalletBulkUploadRequest,
    WalletBulkUploadRowRequest,
)
from app.services import audit_service, bin_series_shared

_BIN_IIN_PATTERN = re.compile(r"^\d{6}$")
_MERCHANT_PREFIX_PATTERN = re.compile(r"^\d{3}$")

_UPDATABLE_FIELDS = [
    ("instance", "instance", "Instance"),
    ("issuer", "issuer", "Issuer"),
    ("merchant", "merchant", "Merchant"),
    ("walletProgramName", "wallet_program_name", "Wallet Program Name"),
    ("binIin", "bin_iin", "BIN / IIN"),
    ("merchantPrefix", "merchant_prefix", "Merchant Prefix"),
    ("walletProgramGroupType", "wallet_program_group_type", "Wallet Program Group Type"),
    ("ticketNumber", "ticket_number", "Ticket Number"),
]
_FIELD_LABELS_BY_ATTR = {model_attr: label for _, model_attr, label in _UPDATABLE_FIELDS}


def _snapshot(record: WalletBinRecord) -> dict:
    return {
        "id": record.id,
        "instance": record.instance,
        "issuer": record.issuer,
        "merchant": record.merchant,
        "walletProgramName": record.wallet_program_name,
        "binIin": record.bin_iin,
        "merchantPrefix": record.merchant_prefix,
        "walletProgramGroupType": record.wallet_program_group_type,
        "ticketNumber": record.ticket_number,
        "status": record.status,
        "customFields": dict(record.custom_fields or {}),
    }


def _to_response(record: WalletBinRecord) -> WalletBinRecordResponse:
    return WalletBinRecordResponse(
        id=record.id,
        instance=record.instance,
        issuer=record.issuer,
        merchant=record.merchant,
        walletProgramName=record.wallet_program_name,
        binIin=record.bin_iin,
        merchantPrefix=record.merchant_prefix,
        walletProgramGroupType=record.wallet_program_group_type,
        ticketNumber=record.ticket_number,
        status=record.status,
        customFields=record.custom_fields or {},
        updatedBy=record.updated_by_user.name if record.updated_by_user else None,
        updatedAt=record.updated_at,
    )


def _check_custom_fields_exist(db: Session, custom_fields: Optional[Dict[str, str]]) -> None:
    if not custom_fields:
        return
    known_keys = wallet_bin_custom_column_repository.existing_keys(db, set(custom_fields.keys()))
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


def _require_record(db: Session, record_id: int) -> WalletBinRecord:
    record = wallet_bin_repository.get_by_id(db, record_id)
    if record is None:
        raise NotFoundError(f"No Wallet BIN record found for id={record_id}.", code="BIN_RECORD_NOT_FOUND")
    return record


def _target_label(record: WalletBinRecord) -> str:
    return f"{record.issuer} — {record.bin_iin}{record.merchant_prefix}"


def list_wallet_bin_records(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
) -> PaginatedResponse[WalletBinRecordResponse]:
    records, total = wallet_bin_repository.search(
        db, search=search, status=status, sort_by=sort_by, sort_order=sort_order, page=page, page_size=page_size
    )
    return PaginatedResponse[WalletBinRecordResponse](
        items=[_to_response(r) for r in records], total=total, page=page, pageSize=page_size
    )


def get_wallet_bin_record(db: Session, record_id: int) -> WalletBinRecordResponse:
    return _to_response(_require_record(db, record_id))


def create_wallet_bin_record(
    db: Session, payload: CreateWalletBinRecordRequest, *, actor_user_id: int
) -> WalletBinRecordResponse:
    bin_series_shared.check_bin_prefix_available(db, bin_iin=payload.binIin, merchant_prefix=payload.merchantPrefix)
    _check_custom_fields_exist(db, payload.customFields)

    try:
        with db.begin_nested():
            record = wallet_bin_repository.create(
                db,
                instance=payload.instance.strip(),
                issuer=payload.issuer.strip(),
                merchant=payload.merchant.strip(),
                wallet_program_name=payload.walletProgramName.strip(),
                bin_iin=payload.binIin,
                merchant_prefix=payload.merchantPrefix,
                wallet_program_group_type=payload.walletProgramGroupType.strip(),
                ticket_number=payload.ticketNumber.strip(),
                updated_by_user_id=actor_user_id,
                custom_fields=payload.customFields,
            )
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="create",
                entity_type="wallet_bin_record",
                entity_id=record.id,
                target_label=_target_label(record),
                change_description="Wallet BIN record created",
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
            f"A Wallet BIN record already exists for binIin={payload.binIin!r} "
            f"and merchantPrefix={payload.merchantPrefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        ) from exc

    return _to_response(record)


def update_wallet_bin_record(
    db: Session, *, record_id: int, payload: UpdateWalletBinRecordRequest, actor_user_id: int
) -> WalletBinRecordResponse:
    record = _require_record(db, record_id)

    new_bin_iin = payload.binIin if payload.binIin is not None else record.bin_iin
    new_merchant_prefix = payload.merchantPrefix if payload.merchantPrefix is not None else record.merchant_prefix
    if (new_bin_iin, new_merchant_prefix) != (record.bin_iin, record.merchant_prefix):
        bin_series_shared.check_bin_prefix_available(
            db, bin_iin=new_bin_iin, merchant_prefix=new_merchant_prefix, exclude_wallet_id=record.id
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
            wallet_bin_repository.update_fields(db, record, **fields_to_set)
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="update",
                entity_type="wallet_bin_record",
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
            f"A Wallet BIN record already exists for binIin={new_bin_iin!r} "
            f"and merchantPrefix={new_merchant_prefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        ) from exc

    return _to_response(record)


def update_wallet_bin_status(
    db: Session, *, record_id: int, payload: BinStatusUpdateRequest, actor_user_id: int
) -> WalletBinRecordResponse:
    """See gift_card_bin_service.update_gift_card_bin_status()'s
    docstring for the full mandatory-ticket / action_type="update"
    reasoning — identical here."""
    record = _require_record(db, record_id)

    if payload.status == record.status:
        return _to_response(record)

    old_status = record.status
    old_ticket = record.ticket_number
    before_snapshot = _snapshot(record)

    with db.begin_nested():
        wallet_bin_repository.update_fields(
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
            entity_type="wallet_bin_record",
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


def delete_wallet_bin_record(db: Session, *, record_id: int, actor_user_id: int) -> BinRecordDeleteResponse:
    record = _require_record(db, record_id)

    deleted_id = record.id
    deleted_target_label = _target_label(record)
    before_snapshot = _snapshot(record)

    with db.begin_nested():
        wallet_bin_repository.delete(db, record)
        audit_service.create_revision(
            db,
            actor_user_id=actor_user_id,
            action_type="delete",
            entity_type="wallet_bin_record",
            entity_id=deleted_id,
            target_label=deleted_target_label,
            change_description="Wallet BIN record deleted",
            metadata={"before": before_snapshot, "after": None},
        )

    return BinRecordDeleteResponse(id=deleted_id, deleted=True)


def get_wallet_bin_series_stats(db: Session) -> WalletBinSeriesStatsResponse:
    total_records, total_issuers, total_wallet_programs = wallet_bin_repository.stats(db)
    return WalletBinSeriesStatsResponse(
        totalRecords=total_records, totalIssuers=total_issuers, totalWalletPrograms=total_wallet_programs
    )


_EXPORT_BASE_COLUMNS: List[Tuple[str, str]] = [
    ("instance", "Instance"),
    ("issuer", "Issuer"),
    ("merchant", "Merchant"),
    ("wallet_program_name", "Wallet Program Name"),
    ("bin_iin", "BIN"),
    ("merchant_prefix", "Merchant Prefix"),
    ("wallet_program_group_type", "Wallet Program Group Type"),
    ("ticket_number", "Ticket Number"),
]


def export_wallet_bin_records_csv(
    db: Session, *, search: Optional[str] = None, status: Optional[str] = None
) -> str:
    """See gift_card_bin_service.export_gift_card_bin_records_csv()'s
    docstring for the full reasoning — identical here."""
    records, _ = wallet_bin_repository.search(
        db, search=search, status=status, sort_by=None, sort_order="asc", page=1, page_size=1_000_000
    )
    custom_columns = wallet_bin_custom_column_repository.list_all(db)

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
    db: Session, rows: List[WalletBulkUploadRowRequest], *, actor_user_id: int
) -> Tuple[int, List[BinBulkUploadRowNote]]:
    created = 0
    failed: List[BinBulkUploadRowNote] = []
    seen_in_batch = set()

    candidate_pairs = [(r.binIin, r.merchantPrefix) for r in rows if r.binIin and r.merchantPrefix]
    existing_same_type = wallet_bin_repository.get_by_bin_and_prefix_batch(db, candidate_pairs)
    # Cross-table check (see gift_card_bin_service.py's docstring) —
    # batch-prefetched once for the whole "Add New" batch.
    existing_other_type = gift_card_bin_repository.get_by_bin_and_prefix_batch(db, candidate_pairs)

    for index, row in enumerate(rows):
        instance = (row.instance or "").strip()
        issuer = (row.issuer or "").strip()
        merchant = (row.merchant or "").strip()
        wallet_program_name = (row.walletProgramName or "").strip()
        bin_iin = row.binIin
        merchant_prefix = row.merchantPrefix
        wallet_program_group_type = (row.walletProgramGroupType or "").strip()
        ticket_number = (row.ticketNumber or "").strip()

        if not all(
            [instance, issuer, merchant, wallet_program_name, bin_iin, merchant_prefix,
             wallet_program_group_type, ticket_number]
        ):
            failed.append(_note(
                index, bin_iin, merchant_prefix,
                "instance, issuer, merchant, walletProgramName, binIin, merchantPrefix, "
                "walletProgramGroupType, and ticketNumber are all required to add a new row.",
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
                record = wallet_bin_repository.create(
                    db,
                    instance=instance,
                    issuer=issuer,
                    merchant=merchant,
                    wallet_program_name=wallet_program_name,
                    bin_iin=bin_iin,
                    merchant_prefix=merchant_prefix,
                    wallet_program_group_type=wallet_program_group_type,
                    ticket_number=ticket_number,
                    updated_by_user_id=actor_user_id,
                )
                audit_service.create_revision(
                    db,
                    actor_user_id=actor_user_id,
                    action_type="upload",
                    entity_type="wallet_bin_record",
                    entity_id=record.id,
                    target_label=_target_label(record),
                    change_description="Wallet BIN record created via bulk upload",
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
    db: Session, rows: List[WalletBulkUploadRowRequest], *, actor_user_id: int
) -> Tuple[int, List[BinBulkUploadRowNote], List[BinBulkUploadRowNote]]:
    """ISSUER-ONLY matching, scoped to Wallet records — see
    gift_card_bin_service.py's docstring for the full reasoning."""
    updated = 0
    skipped_rows: List[BinBulkUploadRowNote] = []
    failed: List[BinBulkUploadRowNote] = []

    issuers = [r.issuer for r in rows if r.issuer]
    by_issuer = wallet_bin_repository.get_by_issuer_batch(db, issuers)

    for index, row in enumerate(rows):
        issuer = (row.issuer or "").strip()
        if not issuer:
            failed.append(_note(index, row.binIin, row.merchantPrefix, "issuer is required to identify a row to update."))
            continue

        candidates = by_issuer.get(issuer.lower(), [])
        if not candidates:
            skipped_rows.append(_note(index, row.binIin, row.merchantPrefix, f"No existing Wallet BIN record matches issuer {issuer!r}."))
            continue
        if len(candidates) > 1:
            failed.append(_note(
                index, row.binIin, row.merchantPrefix,
                f"Issuer {issuer!r} matches {len(candidates)} existing Wallet records — ambiguous.",
            ))
            continue
        record = candidates[0]

        field_changes: List[Tuple[str, object, object]] = []
        fields_to_set: Dict[str, object] = {}

        candidate_values = {
            "instance": (row.instance or "").strip(),
            "merchant": (row.merchant or "").strip(),
            "walletProgramName": (row.walletProgramName or "").strip(),
            "walletProgramGroupType": (row.walletProgramGroupType or "").strip(),
            "ticketNumber": (row.ticketNumber or "").strip(),
        }
        payload_to_attr = {
            "instance": "instance",
            "merchant": "merchant",
            "walletProgramName": "wallet_program_name",
            "walletProgramGroupType": "wallet_program_group_type",
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
                    db, bin_iin=new_bin_iin, merchant_prefix=new_merchant_prefix, exclude_wallet_id=record.id
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
                wallet_bin_repository.update_fields(db, record, **fields_to_set)
                audit_service.create_revision(
                    db,
                    actor_user_id=actor_user_id,
                    action_type="upload",
                    entity_type="wallet_bin_record",
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


def bulk_upload_wallet_bin_records(
    db: Session, payload: WalletBulkUploadRequest, *, actor_user_id: int
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
