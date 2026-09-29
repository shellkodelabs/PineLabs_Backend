"""
Service-layer logic SHARED between the Gift Card and Wallet Bin Series
domains — cross-table uniqueness, the combined lookup/bulk-lookup
endpoints, and the combined version-history endpoint. Orchestrates
app.repositories.gift_card_bin_repository / wallet_bin_repository /
revision_repository — no SQLAlchemy usage, no HTTP-layer concerns.

DEPENDENCY DIRECTION (deliberate, to avoid a circular import): this
module imports ONLY repositories, schemas, and app.services.audit_service
— it NEVER imports app.services.gift_card_bin_service or
app.services.wallet_bin_service. Those two modules import FROM this one
(for `check_bin_prefix_available`), so the reverse direction would
create a cycle. Where this module needs a record's full response/
lookup-result shape (`_to_gift_card_lookup_result`/
`_to_wallet_lookup_result` below), it maps the ORM object directly
rather than calling into the type-specific service's own `_to_response`.

CROSS-TABLE (bin_iin, merchant_prefix) UNIQUENESS — confirmed
architecture decision: `check_bin_prefix_available()` is an
APPLICATION-LEVEL check, not a database constraint (a plain UNIQUE
constraint cannot span two independent tables, and no reservation-table
or other DB mechanism is being introduced — explicitly out of scope).
This is a read-then-write check: a genuine race between two concurrent
requests (one per type, targeting the same pair) can still both pass it
before either commits. That gap is accepted, not closed, in this stage
— each type's own same-table IntegrityError catch (see
gift_card_bin_service.py/wallet_bin_service.py) still closes the
SAME-table half of that race; only the CROSS-table half remains
application-level-only.

LOOKUP (GET /api/v1/bin-series/lookup — a later stage's router):
searches BOTH gift_card_bin_records and wallet_bin_records by the exact
(bin_iin, merchant_prefix) pair, NEVER filtering by status (confirmed
by inspecting the approved frontend's BinResolver.jsx: it searches the
full combined dataset regardless of Active/Inactive). If a genuine
cross-table duplicate is ever found (which `check_bin_prefix_available`
is meant to prevent on every write, but the read-then-write race above
could theoretically still produce), this is surfaced as a loud
ConflictError rather than silently picking one side — see `lookup_bin`'s
own docstring for the full reasoning, including why this differs from
`bulk_lookup_bin`'s per-row degrade-to-unresolved behavior for the same
anomaly.

BULK LOOKUP (POST /api/v1/bin-series/bulk-lookup): confirmed by
inspecting the approved frontend's BulkLookupModal.jsx that the
"BIN uniquely identifies one record" partial-match shortcut the OLD
single-table resolve-batch had is GONE — every value must reduce to
EXACTLY 9 digits (via app.schemas.bin_series_shared.extract_bin_and_prefix)
or it is an unresolved row, never a request-level failure (Stage 3's
explicit design). Never filters by status, same reasoning as the single
lookup.

VERSION HISTORY (GET /api/v1/bin-series/version-history — a later
stage's router): reuses the EXACT same generic
`revision_repository.list_for_entity_types`/`count_for_entity_types`
and `app.schemas.bin_series_history` schemas the OLD (untouched)
`app.services.bin_series_history_service` already uses — scoped to
ONLY the four NEW entity types (gift_card_bin_record, wallet_bin_record,
gift_card_bin_custom_column, wallet_bin_custom_column), never the two
OLD ones (bin_record, bin_custom_column stay exclusively served by the
old, still-registered endpoint) — per explicit instruction not to
include legacy history here. See `_ACTION_LABELS` below for how a
STATUS-CHANGE event is represented: it reuses action_type="update" (see
gift_card_bin_service.py's module docstring for why — there is no
"status_change" value in Revision.ACTION_TYPE_VALUES, and adding one
would require a model/migration change explicitly out of scope this
stage), so a status-change revision is indistinguishable from an
ordinary field-edit by `action` alone; its `changeDescription`
("Status changed from Active to Inactive...") is what actually conveys
it, exactly matching the approved frontend's own local changeLog
(BinTable.jsx's confirmStatusChange() calls log('update', ...) too, not
a distinct type).
"""
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.gift_card_bin_record import GiftCardBinRecord
from app.models.revision import Revision
from app.models.wallet_bin_record import WalletBinRecord
from app.repositories import gift_card_bin_repository, revision_repository, wallet_bin_repository
from app.schemas.bin_series_history import (
    BinSeriesHistoryEntryResponse,
    BinSeriesHistoryResponse,
    HistoryActorResponse,
)
from app.schemas.bin_series_shared import (
    BinLookupResult,
    BulkLookupRequest,
    BulkLookupResponse,
    BulkLookupResultItem,
    GiftCardLookupResult,
    WalletLookupResult,
    extract_bin_and_prefix,
)

# ---------------------------------------------------------------------
# Cross-table uniqueness
# ---------------------------------------------------------------------


def check_bin_prefix_available(
    db: Session,
    *,
    bin_iin: str,
    merchant_prefix: str,
    exclude_gift_card_id: Optional[int] = None,
    exclude_wallet_id: Optional[int] = None,
) -> None:
    """Raises ConflictError if (bin_iin, merchant_prefix) is already in
    use by EITHER table. See this module's docstring for the full
    reasoning (application-level, not a DB constraint; read-then-write,
    a documented and accepted race-condition gap).

    Usage:
      - CREATE (either type): call with neither exclude_* argument — a
        match in EITHER table is a conflict.
      - Gift Card UPDATE (only when bin_iin/merchant_prefix actually
        changes): pass exclude_gift_card_id=record.id so the record
        can keep its own pair; a Wallet match is STILL always a
        conflict (no wallet exclusion is ever appropriate here).
      - Wallet UPDATE: the same, in reverse
        (exclude_wallet_id=record.id).
    """
    gc_match = gift_card_bin_repository.get_by_bin_and_prefix(db, bin_iin=bin_iin, merchant_prefix=merchant_prefix)
    if gc_match is not None and gc_match.id != exclude_gift_card_id:
        raise ConflictError(
            f"A Gift Card BIN record already exists for binIin={bin_iin!r} and merchantPrefix={merchant_prefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        )

    wallet_match = wallet_bin_repository.get_by_bin_and_prefix(db, bin_iin=bin_iin, merchant_prefix=merchant_prefix)
    if wallet_match is not None and wallet_match.id != exclude_wallet_id:
        raise ConflictError(
            f"A Wallet BIN record already exists for binIin={bin_iin!r} and merchantPrefix={merchant_prefix!r}.",
            code="BIN_RECORD_ALREADY_EXISTS",
        )


# ---------------------------------------------------------------------
# Lookup / bulk-lookup result mapping (see module docstring for why
# these are defined HERE rather than reusing gift_card_bin_service.py's/
# wallet_bin_service.py's own _to_response — avoids a circular import).
# ---------------------------------------------------------------------


def _to_gift_card_lookup_result(record: GiftCardBinRecord) -> GiftCardLookupResult:
    return GiftCardLookupResult(
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


def _to_wallet_lookup_result(record: WalletBinRecord) -> WalletLookupResult:
    return WalletLookupResult(
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


def lookup_bin(db: Session, *, bin_iin: str, merchant_prefix: str) -> BinLookupResult:
    """Backs GET /api/v1/bin-series/lookup (a later stage's router).
    Exact (bin_iin, merchant_prefix) match across BOTH tables, never
    filtering by status. Raises NotFoundError if neither table has a
    match — mirrors the old single-table resolve_bin()'s 404 behavior.

    If BOTH tables somehow have a match for the same pair (a data-
    integrity anomaly `check_bin_prefix_available` is meant to prevent
    on every write, but the documented application-level race could
    theoretically still produce), this raises a loud ConflictError
    rather than silently returning one side and hiding the other —
    deliberately NOT changed to a list-shaped response here, since
    Stage 3's already-approved `BinLookupResult` schema is a single
    discriminated object (matching the old single-lookup convention);
    changing that shape is a schema decision, not something to make
    silently in this stage. Flagged in this task's completion report."""
    gc_record = gift_card_bin_repository.get_by_bin_and_prefix(db, bin_iin=bin_iin, merchant_prefix=merchant_prefix)
    wallet_record = wallet_bin_repository.get_by_bin_and_prefix(db, bin_iin=bin_iin, merchant_prefix=merchant_prefix)

    if gc_record is not None and wallet_record is not None:
        raise ConflictError(
            f"Data integrity anomaly: binIin={bin_iin!r} and merchantPrefix={merchant_prefix!r} matches BOTH "
            "a Gift Card and a Wallet BIN record. This should never happen under normal operation.",
            code="BIN_RECORD_CROSS_TABLE_DUPLICATE",
        )
    if gc_record is not None:
        return _to_gift_card_lookup_result(gc_record)
    if wallet_record is not None:
        return _to_wallet_lookup_result(wallet_record)

    raise NotFoundError(
        f"No BIN record found for binIin={bin_iin!r} and merchantPrefix={merchant_prefix!r}.",
        code="BIN_RECORD_NOT_FOUND",
    )


def bulk_lookup_bin(db: Session, payload: BulkLookupRequest) -> BulkLookupResponse:
    """Backs POST /api/v1/bin-series/bulk-lookup (a later stage's
    router). Every input value is processed independently — a malformed
    value (not exactly 9 digits after stripping non-digit characters)
    NEVER rejects the request, it just produces one unresolved result
    row (Stage 3's explicit design; confirmed by inspecting
    BulkLookupModal.jsx, which does the same per-card, never aborting
    the whole upload).

    A cross-table duplicate anomaly for one specific card (see
    lookup_bin()'s docstring) degrades to an unresolved row for THAT
    card only here, rather than raising — bulk-lookup's own established
    philosophy (never abort the batch over one bad/anomalous row) takes
    precedence over lookup_bin()'s louder single-request failure."""
    results: List[BulkLookupResultItem] = []

    # Batch-prefetch: parse every input once, then ONE query per table
    # for all exact 9-digit pairs — never one query per card.
    parsed = []
    valid_pairs = []
    for raw in payload.cards:
        try:
            bin_iin, merchant_prefix = extract_bin_and_prefix(raw)
        except ValueError:
            parsed.append((raw.strip() if raw else raw, None, None))
            continue
        parsed.append((raw.strip(), bin_iin, merchant_prefix))
        valid_pairs.append((bin_iin, merchant_prefix))

    gc_matches = gift_card_bin_repository.get_by_bin_and_prefix_batch(db, valid_pairs)
    wallet_matches = wallet_bin_repository.get_by_bin_and_prefix_batch(db, valid_pairs)

    for trimmed_input, bin_iin, merchant_prefix in parsed:
        if bin_iin is None:
            results.append(BulkLookupResultItem(input=trimmed_input or "", matched=False, result=None))
            continue

        gc_hit = gc_matches.get((bin_iin, merchant_prefix))
        wallet_hit = wallet_matches.get((bin_iin, merchant_prefix))

        if gc_hit is not None and wallet_hit is not None:
            # Anomaly — degrade to unresolved for THIS row only, never
            # abort the batch (see this function's docstring).
            results.append(BulkLookupResultItem(input=trimmed_input, matched=False, result=None))
        elif gc_hit is not None:
            results.append(
                BulkLookupResultItem(input=trimmed_input, matched=True, result=_to_gift_card_lookup_result(gc_hit))
            )
        elif wallet_hit is not None:
            results.append(
                BulkLookupResultItem(input=trimmed_input, matched=True, result=_to_wallet_lookup_result(wallet_hit))
            )
        else:
            results.append(BulkLookupResultItem(input=trimmed_input, matched=False, result=None))

    return BulkLookupResponse(results=results)


# ---------------------------------------------------------------------
# Version history (combined, four new entity types only)
# ---------------------------------------------------------------------

_NEW_BIN_SERIES_ENTITY_TYPES = (
    "gift_card_bin_record",
    "wallet_bin_record",
    "gift_card_bin_custom_column",
    "wallet_bin_custom_column",
)

_ACTION_LABELS = {
    ("gift_card_bin_record", "create"): "create",
    ("gift_card_bin_record", "update"): "update",
    ("gift_card_bin_record", "delete"): "delete",
    ("gift_card_bin_record", "upload"): "upload",
    ("wallet_bin_record", "create"): "create",
    ("wallet_bin_record", "update"): "update",
    ("wallet_bin_record", "delete"): "delete",
    ("wallet_bin_record", "upload"): "upload",
    ("gift_card_bin_custom_column", "create"): "column",
    ("gift_card_bin_custom_column", "update"): "rename",
    ("wallet_bin_custom_column", "create"): "column",
    ("wallet_bin_custom_column", "update"): "rename",
}


def _to_history_entry(revision: Revision) -> BinSeriesHistoryEntryResponse:
    action = _ACTION_LABELS.get((revision.entity_type, revision.action_type), revision.action_type)
    metadata = revision.metadata_ or {}
    actor: Optional[HistoryActorResponse] = None
    if revision.user is not None:
        actor = HistoryActorResponse(id=revision.user.id, name=revision.user.name)

    return BinSeriesHistoryEntryResponse(
        revisionId=revision.id,
        action=action,
        entityType=revision.entity_type,
        entityId=revision.entity_id,
        occurredAt=revision.occurred_at,
        actor=actor,
        targetLabel=revision.target_label,
        changeDescription=revision.change_description,
        fieldsChanged=metadata.get("fieldsChanged"),
        before=metadata.get("before"),
        after=metadata.get("after"),
    )


def list_bin_series_history(db: Session, *, limit: int) -> BinSeriesHistoryResponse:
    """Backs GET /api/v1/bin-series/version-history (a later stage's
    router) — GLOBAL across both new types, scoped to ONLY the four new
    entity types (never the old bin_record/bin_custom_column, which stay
    exclusively served by the old, still-registered endpoint)."""
    revisions = revision_repository.list_for_entity_types(db, entity_types=_NEW_BIN_SERIES_ENTITY_TYPES, limit=limit)
    total = revision_repository.count_for_entity_types(db, entity_types=_NEW_BIN_SERIES_ENTITY_TYPES)
    return BinSeriesHistoryResponse(items=[_to_history_entry(r) for r in revisions], total=total)
