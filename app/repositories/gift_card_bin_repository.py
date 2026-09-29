"""
Repository for `gift_card_bin_records` — SQLAlchemy queries only. No
business rules, no response-schema mapping, no HTTP concerns live here.
Mirrors the established conventions in app/repositories/bin_repository.py
(the old, untouched single-table repository) and
app/repositories/instance_repository.py (the `status` filter pattern),
adapted for this table's own column set. Independent of
app/repositories/wallet_bin_repository.py — no shared base, no
polymorphic dispatch, per the confirmed architecture.

New Excel-format Bin Series task, Stage 4 (repositories only).

SEARCH: `search()`'s free-text `search` filter is an OR of ILIKE
substring matches across every field the approved frontend's global
search box actually searches (`Object.values(r).some(...)` in
BinTable.jsx — confirmed by inspection) — instance, issuer, merchant,
card_program_group_name, bin_iin, merchant_prefix,
card_program_group_type, card_type, ticket_number, and status (a
literal "activ"/"inactiv" search term matches via this same clause,
exactly like every other field value). `custom_fields` uses the SAME
`EXISTS (SELECT 1 FROM jsonb_each_text(...) WHERE value ILIKE :term)`
idiom as the old bin_repository.py — bound parameter, never
string-concatenated, values only (never custom-column KEY names). No
new index added for any of this, per this stage's explicit scope (no
migrations/indexes) — same "premature at current data volume" reasoning
already accepted for the old table.

STATUS FILTER: `status` is a SEPARATE, exact-match parameter from
`search` — this is the Active/Inactive TAB the approved frontend
applies BEFORE its search box (`statusView` state in BinTable.jsx:
`data.filter(r => statusView === 'active' ? isActive(r) : !isActive(r))`,
then the search-box filter runs on top of that). Optional here (None =
no filter) even though the frontend always sends one, same convention
as instance_repository.search()'s own `status` parameter.

EXACT LOOKUP: `get_by_bin_and_prefix`/`get_by_bin_and_prefix_batch`
mirror the old bin_repository.py's identically-named functions exactly
(exact (bin_iin, merchant_prefix) match, batch variant keyed by that
same pair). Deliberately NEVER filters by status — confirmed by
inspecting the approved frontend's BinResolver.jsx/BulkLookupModal.jsx:
both search the full combined dataset regardless of status. No
`get_by_bin_iin_batch`-style "BIN uniquely identifies one record"
shortcut lookup here — confirmed by inspecting the approved
BulkLookupModal.jsx that this shortcut no longer exists in the new
frontend (bulk lookup now requires an exact 9-digit match, always); a
bin_iin-alone batch lookup would support a matching rule nothing
upstream needs anymore, so it was deliberately not built.

BULK UPLOAD SUPPORT: `get_by_issuer_batch` mirrors the old
bin_repository.py's fallback-matching support for "Update Existing"
mode — confirmed by inspecting utils/csv.js (unchanged: `identityValue`/
`issuerKey` still match by issuer name alone) that the approved
frontend's bulk-upload "Update Existing" tab still identifies rows by
issuer only. No dedicated bulk-INSERT method: the established
convention (see bin_service.py's `_bulk_add_new`/`_bulk_update_existing`)
is a per-row loop calling the single-row `create()`/`update_fields()`
inside its own savepoint, for partial-batch-failure semantics a single
big INSERT statement can't support — the existing single-row methods
already ARE this table's bulk-persistence primitives.

STATISTICS: `stats()` mirrors the old bin_repository.py's `stats()`
shape exactly (total records / distinct issuers / distinct card
program names — matches app/schemas/gift_card_bin_series.py's
GiftCardBinSeriesStatsResponse). `count_by_status()` is an ADDITIONAL
aggregate (active count / inactive count, one query using Postgres's
`FILTER (WHERE ...)` via SQLAlchemy's `func.count().filter(...)`) —
provided because a later stage's service layer needs it (the approved
frontend's BinTable.jsx shows live Active/Inactive tab-badge counts),
even though the current GiftCardBinSeriesStatsResponse schema (Stage 3)
has no field for it yet; flagged in this stage's report rather than
silently changing that schema.

CROSS-TABLE UNIQUENESS: NOT implemented here (confirmed architecture:
belongs in a later stage's shared service-layer `check_bin_prefix_available()`
helper, which will call this module's `get_by_bin_and_prefix` AND
wallet_bin_repository's equivalent). This module has no awareness of
`wallet_bin_records` at all.

AUDIT: no revision/audit calls anywhere in this module — that is
exclusively a service-layer concern.
"""
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import asc, desc, func, or_, select, text, tuple_, update
from sqlalchemy.orm import Session, joinedload

from app.models.gift_card_bin_record import GiftCardBinRecord

# Maps the API's camelCase sortBy values to actual ORM columns — same
# column-mapping-lives-in-the-repository convention as
# app/repositories/bin_repository.py.
SORT_FIELD_MAP = {
    "instance": GiftCardBinRecord.instance,
    "issuer": GiftCardBinRecord.issuer,
    "merchant": GiftCardBinRecord.merchant,
    "cardProgramGroupName": GiftCardBinRecord.card_program_group_name,
    "binIin": GiftCardBinRecord.bin_iin,
    "merchantPrefix": GiftCardBinRecord.merchant_prefix,
    "cardProgramGroupType": GiftCardBinRecord.card_program_group_type,
    "cardType": GiftCardBinRecord.card_type,
    "ticketNumber": GiftCardBinRecord.ticket_number,
    "status": GiftCardBinRecord.status,
    "id": GiftCardBinRecord.id,
}


def _apply_search_filters(stmt, *, search: Optional[str], status: Optional[str]):
    """Shared WHERE-clause builder for `search()` — factored out so a
    future export/list-adjacent method (a later stage) can reuse it
    exactly, same pattern as bin_repository.py's own
    `_apply_search_filters`."""
    if search:
        term = f"%{search.strip()}%"
        custom_field_value_match = text(
            "EXISTS (SELECT 1 FROM jsonb_each_text(gift_card_bin_records.custom_fields) AS kv(key, value) "
            "WHERE kv.value ILIKE :custom_search_term)"
        ).bindparams(custom_search_term=term)
        stmt = stmt.where(
            or_(
                GiftCardBinRecord.instance.ilike(term),
                GiftCardBinRecord.issuer.ilike(term),
                GiftCardBinRecord.merchant.ilike(term),
                GiftCardBinRecord.card_program_group_name.ilike(term),
                GiftCardBinRecord.bin_iin.ilike(term),
                GiftCardBinRecord.merchant_prefix.ilike(term),
                GiftCardBinRecord.card_program_group_type.ilike(term),
                GiftCardBinRecord.card_type.ilike(term),
                GiftCardBinRecord.ticket_number.ilike(term),
                GiftCardBinRecord.status.ilike(term),
                custom_field_value_match,
            )
        )
    if status:
        stmt = stmt.where(GiftCardBinRecord.status == status)
    return stmt


def search(
    session: Session,
    *,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[GiftCardBinRecord], int]:
    """Filters, sorts, and paginates gift_card_bin_records. Returns
    (items, total)."""
    stmt = select(GiftCardBinRecord).options(joinedload(GiftCardBinRecord.updated_by_user))
    stmt = _apply_search_filters(stmt, search=search, status=status)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, GiftCardBinRecord.id)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not GiftCardBinRecord.id:
        # Deterministic tiebreaker — same reasoning as every other
        # paginated list in this project.
        order_clauses.append(GiftCardBinRecord.id)
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0


def get_by_id(session: Session, record_id: int) -> Optional[GiftCardBinRecord]:
    stmt = (
        select(GiftCardBinRecord)
        .options(joinedload(GiftCardBinRecord.updated_by_user))
        .where(GiftCardBinRecord.id == record_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_bin_and_prefix(session: Session, *, bin_iin: str, merchant_prefix: str) -> Optional[GiftCardBinRecord]:
    """Exact match only — (bin_iin, merchant_prefix) is unique within
    this table (uq_gift_card_bin_records_bin_prefix), so this returns at
    most one record. Never filters by status (see module docstring)."""
    stmt = (
        select(GiftCardBinRecord)
        .options(joinedload(GiftCardBinRecord.updated_by_user))
        .where(
            GiftCardBinRecord.bin_iin == bin_iin,
            GiftCardBinRecord.merchant_prefix == merchant_prefix,
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_bin_and_prefix_batch(
    session: Session, pairs: Sequence[Tuple[str, str]]
) -> Dict[Tuple[str, str], GiftCardBinRecord]:
    """Bulk equivalent of get_by_bin_and_prefix — ONE query for every
    (bin_iin, merchant_prefix) pair in `pairs`. Backs both the combined
    bulk-lookup (exact 9-digit matching, a later stage's shared service)
    and bulk-upload's primary matching key."""
    if not pairs:
        return {}
    stmt = (
        select(GiftCardBinRecord)
        .options(joinedload(GiftCardBinRecord.updated_by_user))
        .where(tuple_(GiftCardBinRecord.bin_iin, GiftCardBinRecord.merchant_prefix).in_(list(pairs)))
    )
    records = session.execute(stmt).scalars().all()
    return {(r.bin_iin, r.merchant_prefix): r for r in records}


def get_by_issuer_batch(session: Session, issuers: Sequence[str]) -> Dict[str, List[GiftCardBinRecord]]:
    """Bulk, ISSUER-based lookup — the bulk-upload "Update Existing"
    fallback match (mirrors bin_repository.py's identically-named
    function). Returns EVERY matching record per normalized issuer name
    (never just one) so a later stage's service can detect and refuse an
    ambiguous (non-unique) match instead of guessing."""
    normalized = {i.strip().lower() for i in issuers if i and i.strip()}
    if not normalized:
        return {}
    stmt = (
        select(GiftCardBinRecord)
        .options(joinedload(GiftCardBinRecord.updated_by_user))
        .where(func.lower(GiftCardBinRecord.issuer).in_(normalized))
    )
    records = session.execute(stmt).scalars().all()
    result: Dict[str, List[GiftCardBinRecord]] = {}
    for record in records:
        result.setdefault(record.issuer.strip().lower(), []).append(record)
    return result


def create(
    session: Session,
    *,
    instance: str,
    issuer: str,
    merchant: str,
    card_program_group_name: str,
    bin_iin: str,
    merchant_prefix: str,
    card_program_group_type: str,
    card_type: str,
    ticket_number: str,
    updated_by_user_id: Optional[int],
    custom_fields: Optional[Dict[str, str]] = None,
) -> GiftCardBinRecord:
    """Persistence only — no validation, no status handling (every new
    record starts 'Active' via the model's own server_default, matching
    the approved frontend's addRow(), which never collects status)."""
    record = GiftCardBinRecord(
        instance=instance,
        issuer=issuer,
        merchant=merchant,
        card_program_group_name=card_program_group_name,
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        card_program_group_type=card_program_group_type,
        card_type=card_type,
        ticket_number=ticket_number,
        updated_by_user_id=updated_by_user_id,
        custom_fields=custom_fields or {},
    )
    session.add(record)
    session.flush()
    return record


def update_fields(session: Session, record: GiftCardBinRecord, **fields) -> GiftCardBinRecord:
    for key, value in fields.items():
        setattr(record, key, value)
    session.flush()
    return record


def delete(session: Session, record: GiftCardBinRecord) -> None:
    session.delete(record)
    session.flush()


def stats(session: Session) -> Tuple[int, int, int]:
    """Returns (total_records, total_issuers, total_card_programs) —
    backs GiftCardBinSeriesStatsResponse (Stage 3). One query, three
    aggregates, computed entirely in the database."""
    stmt = select(
        func.count(),
        func.count(func.distinct(GiftCardBinRecord.issuer)),
        func.count(func.distinct(GiftCardBinRecord.card_program_group_name)),
    ).select_from(GiftCardBinRecord)
    total_records, total_issuers, total_card_programs = session.execute(stmt).one()
    return total_records or 0, total_issuers or 0, total_card_programs or 0


def count_by_status(session: Session) -> Tuple[int, int]:
    """Returns (active_count, inactive_count) — one query, using
    Postgres's FILTER (WHERE ...) via SQLAlchemy's
    func.count().filter(...). See this module's docstring: provided for
    a later stage (the approved frontend's Active/Inactive tab-badge
    counts); GiftCardBinSeriesStatsResponse has no field for this yet."""
    stmt = select(
        func.count().filter(GiftCardBinRecord.status == "Active"),
        func.count().filter(GiftCardBinRecord.status == "Inactive"),
    ).select_from(GiftCardBinRecord)
    active_count, inactive_count = session.execute(stmt).one()
    return active_count or 0, inactive_count or 0


def backfill_custom_field(session: Session, *, key: str, default_value: str) -> None:
    """Sets custom_fields[key] = default_value on EVERY
    gift_card_bin_records row, in ONE UPDATE statement — the Gift Card
    equivalent of bin_repository.py's identically-named function (the
    bulk write behind "Add Column"'s default-value backfill). `key` and
    `default_value` are bound parameters via func.jsonb_build_object,
    never string-concatenated. Added in Stage 5 (a gap in the original
    Stage 4 repository — this capability is required by
    gift_card_bin_custom_column_service.py's create_column(), mirroring
    the old bin_custom_column_service.py's use of
    bin_repository.backfill_custom_field exactly)."""
    stmt = update(GiftCardBinRecord).values(
        custom_fields=GiftCardBinRecord.custom_fields.op("||")(func.jsonb_build_object(key, default_value))
    )
    session.execute(stmt)
