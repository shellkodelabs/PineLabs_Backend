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
"""
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.bin_record import BinRecord
from app.repositories import bin_repository, merchant_repository
from app.schemas.bin_series import (
    BinRecordResponse,
    CreateBinRecordRequest,
    DeleteBinRecordResponse,
    UpdateBinRecordRequest,
)
from app.schemas.common import PaginatedResponse
from app.services import audit_service

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


def _to_response(record: BinRecord) -> BinRecordResponse:
    return BinRecordResponse(
        id=record.id,
        issuer=record.issuer,
        cardProgramGroupName=record.card_program_group_name,
        binIin=record.bin_iin,
        merchantPrefix=record.merchant_prefix,
        merchantId=record.merchant_id,
        instanceName=record.instance_name,
        updatedBy=record.updated_by_user.name if record.updated_by_user else None,
        updatedAt=record.updated_at,
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

    if not fields_to_set:
        return _to_response(record)

    # A real write is about to happen — the record must end up with a
    # non-blank instance_name (Part 17), regardless of whether THIS
    # request is what supplies it.
    _check_resulting_instance_name(record, fields_to_set)

    change_description = _describe_field_changes(field_changes)
    fields_to_set["updated_by_user_id"] = actor_user_id

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
                metadata={"fieldsChanged": {label: {"from": old, "to": new} for label, old, new in field_changes}},
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
        )

    return DeleteBinRecordResponse(id=deleted_id, deleted=True)
