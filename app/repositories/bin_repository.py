"""
Repository for `bin_records` — SQLAlchemy queries only. No business
rules, no response-schema mapping, no HTTP concerns live here.

Index usage:
  - `get_by_bin_and_prefix` filters on the exact (bin_iin, merchant_prefix)
    pair, which is exactly the column set behind `uq_bin_records_bin_prefix`
    — a fast, unique lookup.
  - `search`'s free-text `search` filter is an OR of ILIKE substring
    matches across five searchable fields, per the task spec. Only
    the issuer/card_program_group_name pair is covered by the existing
    trigram index (`ix_bin_records_search`, an expression index over
    their concatenation) — bin_iin/merchant_prefix/instance_name substring
    search falls back to a sequential scan. At the current data volume
    (~611 rows) this is a non-issue — the same reasoning already accepted
    for bin_iin/merchant_prefix applies identically to instance_name, so
    no new index was added for it either; `ix_bin_records_bin_iin` still
    helps the common case of an exact/prefix bin_iin match. No new index
    added — schema changes are out of scope for this part.
  - `search`'s exact `issuer`/`card_program_group_name` filters use
    case-insensitive equality (not substring), so they don't depend on
    the trigram index either way.

Search & Filter task (Bin Series priority): `search` now ALSO matches
`instance_name` (added to the existing OR-of-ILIKE clause). Frontend
inspection (BinTable.jsx) confirmed the approved UI's single global
search box matches EVERY field present on a row
(`Object.values(r).some(...)`), which — for a row that has one — includes
`instance` (the frontend's local field name for instance_name). Adding
it here is the one genuine gap found; the OTHER row fields the frontend
technically also searches (`id`, `updatedAt`) are deliberately NOT added
— they're internal/audit bookkeeping, not meaningful search targets, and
were never part of the original Part 7 spec either. `merchant_id` was
never a candidate: the frontend's own row shape has no such field to
search in the first place. Custom columns are explicitly out of scope —
see app/services/bin_service.py's module docstring for the full
reasoning (they're a separate, not-yet-built task).

Frontend inspection ALSO confirmed the approved BinTable.jsx has NO
filter dropdowns and NO column-sort UI at all — only the one global
search box plus fixed-pageSize (50) client-side pagination. The
existing `issuer`/`cardProgramGroupName` exact filters and
`sortBy`/`sortOrder` below therefore have no current frontend trigger;
they pre-date this inspection (Part 7) and are kept exactly as they
were — not removed (no compelling reason to break a working, tested
capability) and not extended (no frontend behavior to justify new
filter/sort parameters).

Part 16 (Create/Update/Delete): every read here (`search`,
`get_by_bin_and_prefix`, `get_by_id`) eager-loads `updated_by_user` via
`joinedload` — a single LEFT OUTER JOIN — rather than letting
bin_service touch the relationship lazily. This mirrors why
revision_repository.search() hand-builds its own outer join instead of
relying on `Revision.user`: without it, rendering a page of up to 200
BIN records for GET /api/v1/bin-series/list would issue up to 200 extra
per-row queries to resolve each one's `updatedBy` name (classic N+1).

Bulk upload (Bin Series priority task): `get_by_bin_and_prefix_batch`
and `get_by_issuer_batch` are the bulk equivalents of
`get_by_bin_and_prefix` — ONE query each for up to 1000 rows' worth of
pairs/names, instead of one query per row. `get_by_issuer_batch`
deliberately returns EVERY matching record per issuer (not just one) —
bin_service needs the full candidate list to detect an ambiguous
(non-unique) issuer match and refuse it, rather than guessing.

`stats()` backs GET /api/v1/bin-series/statistics (renamed from /stats
by the API Naming task; the frontend's StatCards on the BIN Series
page — Total Records / Issuers / Card Programs, today computed
client-side from the full mock array). Three real SQL
aggregations (COUNT(*), COUNT(DISTINCT issuer), COUNT(DISTINCT
card_program_group_name)) — same "aggregate in the database, never load
a full table into Python to count it" convention as
app/repositories/dashboard_repository.py.

`get_by_bin_iin_batch` backs POST /api/v1/bin-series/bulk-lookup
(renamed from /resolve-batch by the API Naming task; bulk/multi-card
resolve — BulkLookupModal.jsx). Deliberately keyed by
bin_iin ALONE (not the (bin_iin, merchant_prefix) pair used elsewhere)
and returns EVERY record per bin_iin: the frontend's own bulk resolver
has a matching rule the single-card GET /lookup endpoint doesn't —  when
a card's prefix digits are incomplete/absent, it still resolves if the
BIN uniquely identifies exactly one record. bin_service needs the full
candidate list per BIN to implement that rule; a single-row lookup
couldn't distinguish "no match" from "ambiguous, multiple candidates".

Dynamic/Custom Columns task:
  - `search` now ALSO matches `custom_fields` VALUES (not keys) via a
    correlated `EXISTS (SELECT 1 FROM jsonb_each_text(...) WHERE value
    ILIKE :term)` — the standard PostgreSQL idiom for "does any value in
    this JSONB object match a substring", expressed via `text()` with a
    BOUND parameter (never string-concatenated — the term itself is
    never spliced into the SQL string). A plain `.cast(Text).ilike(term)`
    over the whole JSONB blob was deliberately rejected: it would also
    match custom-column KEY names (e.g. searching "Region" would match a
    row that merely HAS a "Region" column, regardless of its value),
    which is broader than the frontend's own `Object.values(row)`
    semantics (values only, never keys). No new index: a standard GIN
    index on JSONB (jsonb_ops, e.g. sop_rows.data's own index) accelerates
    containment/key-existence queries, not substring text search — it
    would not speed up THIS query pattern at all, so adding one would be
    exactly the "index blindly for every column" the task warns against.
  - `backfill_custom_field` is the one-shot bulk write behind "Add
    Column"'s default-value backfill (AddColumnModal.jsx) — ONE UPDATE
    statement across the whole table (`custom_fields = custom_fields ||
    jsonb_build_object(:key, :value)`), never a per-row loop. Both `key`
    and `value` are passed through `func.jsonb_build_object(...)` as
    bound parameters, not string-concatenated.

Export task: `export_all` backs GET /api/v1/bin-series/export. Frontend
inspection (BinTable.jsx's exportSheet()) confirmed the approved Export
button downloads `data` — the FULL local dataset — NOT the search-box-
filtered `rows`, NOT the current page, and there is no row-selection UI
at all. So `export_all` takes the exact same optional filters as
`search()` (reusing `_apply_search_filters`, never a second filtering
algorithm) but NO page/pageSize — the endpoint always returns every
matching row in one response, exactly like the button's current
behavior when called with no filters. The filter parameters exist for
forward compatibility with a real search box eventually driving export
too, not because today's approved frontend sends any.
"""
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import asc, desc, func, or_, select, text, tuple_, update
from sqlalchemy.orm import Session, joinedload

from app.models.bin_record import BinRecord

# Maps the API's camelCase sortBy values to actual ORM columns. Keeping
# this in the repository (not the service) since it's purely a
# column-mapping concern — no business rule depends on it.
SORT_FIELD_MAP = {
    "issuer": BinRecord.issuer,
    "cardProgramGroupName": BinRecord.card_program_group_name,
    "binIin": BinRecord.bin_iin,
    "merchantPrefix": BinRecord.merchant_prefix,
    "id": BinRecord.id,
}


def get_by_bin_and_prefix(session: Session, *, bin_iin: str, merchant_prefix: str) -> Optional[BinRecord]:
    """Exact match only — (bin_iin, merchant_prefix) is unique, so this
    returns at most one record. Never returns a partial-match list."""
    stmt = (
        select(BinRecord)
        .options(joinedload(BinRecord.updated_by_user))
        .where(
            BinRecord.bin_iin == bin_iin,
            BinRecord.merchant_prefix == merchant_prefix,
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_bin_and_prefix_batch(
    session: Session, pairs: Sequence[Tuple[str, str]]
) -> Dict[Tuple[str, str], BinRecord]:
    """Bulk equivalent of get_by_bin_and_prefix — ONE query for every
    (bin_iin, merchant_prefix) pair in `pairs`, returned as a dict keyed
    by that same pair for O(1) lookup per row in the caller's loop."""
    if not pairs:
        return {}
    stmt = (
        select(BinRecord)
        .options(joinedload(BinRecord.updated_by_user))
        .where(tuple_(BinRecord.bin_iin, BinRecord.merchant_prefix).in_(list(pairs)))
    )
    records = session.execute(stmt).scalars().all()
    return {(r.bin_iin, r.merchant_prefix): r for r in records}


def get_by_issuer_batch(session: Session, issuers: Sequence[str]) -> Dict[str, List[BinRecord]]:
    """Bulk, ISSUER-based lookup — used only as the bulk-upload fallback
    match (see app/schemas/bin_series.py's module docstring) for rows
    that supply no usable (bin_iin, merchant_prefix) pair. Returns EVERY
    matching record per normalized issuer name (never just one) so the
    caller can detect and refuse an ambiguous (non-unique) match instead
    of guessing which record was meant."""
    normalized = {i.strip().lower() for i in issuers if i and i.strip()}
    if not normalized:
        return {}
    stmt = (
        select(BinRecord)
        .options(joinedload(BinRecord.updated_by_user))
        .where(func.lower(BinRecord.issuer).in_(normalized))
    )
    records = session.execute(stmt).scalars().all()
    result: Dict[str, List[BinRecord]] = {}
    for record in records:
        result.setdefault(record.issuer.strip().lower(), []).append(record)
    return result


def get_by_bin_iin_batch(session: Session, bin_iins: Sequence[str]) -> Dict[str, List[BinRecord]]:
    """Bulk lookup by bin_iin ALONE — see this module's docstring for why
    this differs from get_by_bin_and_prefix_batch. No updated_by_user
    eager-load: the bulk resolve response has no updatedBy/updatedAt
    fields at all (see app/schemas/bin_series.py's ResolveBatchResultItem),
    so there's nothing to avoid N+1 on here."""
    unique_bins = {b for b in bin_iins if b}
    if not unique_bins:
        return {}
    stmt = select(BinRecord).where(BinRecord.bin_iin.in_(unique_bins))
    records = session.execute(stmt).scalars().all()
    result: Dict[str, List[BinRecord]] = {}
    for record in records:
        result.setdefault(record.bin_iin, []).append(record)
    return result


def get_by_id(session: Session, bin_record_id: int) -> Optional[BinRecord]:
    stmt = (
        select(BinRecord)
        .options(joinedload(BinRecord.updated_by_user))
        .where(BinRecord.id == bin_record_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def create(
    session: Session,
    *,
    issuer: str,
    card_program_group_name: str,
    bin_iin: str,
    merchant_prefix: str,
    merchant_id: Optional[int],
    instance_name: str,
    updated_by_user_id: Optional[int],
    custom_fields: Optional[Dict[str, str]] = None,
) -> BinRecord:
    record = BinRecord(
        issuer=issuer,
        card_program_group_name=card_program_group_name,
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        merchant_id=merchant_id,
        instance_name=instance_name,
        updated_by_user_id=updated_by_user_id,
        custom_fields=custom_fields or {},
    )
    session.add(record)
    session.flush()
    return record


def update_fields(session: Session, record: BinRecord, **fields) -> BinRecord:
    for key, value in fields.items():
        setattr(record, key, value)
    session.flush()
    return record


def delete(session: Session, record: BinRecord) -> None:
    session.delete(record)
    session.flush()


def _apply_search_filters(
    stmt,
    *,
    search: Optional[str],
    issuer: Optional[str],
    card_program_group_name: Optional[str],
):
    """Shared WHERE-clause builder for `search()` (paginated list) and
    `export_all()` (unpaginated CSV export) — Export must respect the
    exact same dataset semantics as the list/search API, so this is
    factored out once rather than reimplemented a second time. See this
    module's docstring for what each filter matches and why."""
    if search:
        term = f"%{search.strip()}%"
        custom_field_value_match = text(
            "EXISTS (SELECT 1 FROM jsonb_each_text(bin_records.custom_fields) AS kv(key, value) "
            "WHERE kv.value ILIKE :custom_search_term)"
        ).bindparams(custom_search_term=term)
        stmt = stmt.where(
            or_(
                BinRecord.issuer.ilike(term),
                BinRecord.card_program_group_name.ilike(term),
                BinRecord.bin_iin.ilike(term),
                BinRecord.merchant_prefix.ilike(term),
                BinRecord.instance_name.ilike(term),
                custom_field_value_match,
            )
        )
    if issuer:
        stmt = stmt.where(func.lower(BinRecord.issuer) == issuer.strip().lower())
    if card_program_group_name:
        stmt = stmt.where(func.lower(BinRecord.card_program_group_name) == card_program_group_name.strip().lower())
    return stmt


def search(
    session: Session,
    *,
    search: Optional[str] = None,
    issuer: Optional[str] = None,
    card_program_group_name: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[BinRecord], int]:
    """Filters, sorts, and paginates bin_records. Returns (items, total)."""
    stmt = select(BinRecord).options(joinedload(BinRecord.updated_by_user))
    stmt = _apply_search_filters(stmt, search=search, issuer=issuer, card_program_group_name=card_program_group_name)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, BinRecord.id)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not BinRecord.id:
        # Deterministic tiebreaker — without this, pagination across
        # pages is not guaranteed stable when sorting by a non-unique
        # field (e.g. issuer, which repeats across many records).
        order_clauses.append(BinRecord.id)
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0


def stats(session: Session) -> Tuple[int, int, int]:
    """Returns (total_records, total_issuers, total_card_programs) — one
    query, three aggregates, computed entirely in the database."""
    stmt = select(
        func.count(),
        func.count(func.distinct(BinRecord.issuer)),
        func.count(func.distinct(BinRecord.card_program_group_name)),
    ).select_from(BinRecord)
    total_records, total_issuers, total_card_programs = session.execute(stmt).one()
    return total_records or 0, total_issuers or 0, total_card_programs or 0


def export_all(
    session: Session,
    *,
    search: Optional[str] = None,
    issuer: Optional[str] = None,
    card_program_group_name: Optional[str] = None,
) -> List[BinRecord]:
    """Backs GET /api/v1/bin-series/export. Same filter semantics as
    search() (reuses _apply_search_filters — no second filtering
    algorithm), but deliberately NO pagination: the approved frontend's
    own exportSheet() (BinTable.jsx) downloads every record in one shot,
    never just the current page. Ordered by id ascending for a
    deterministic, reproducible export — the frontend's local array has
    no meaningful sort order of its own to replicate (rows shift around
    on every add/edit), so this simply reuses the same default ordering
    GET /bin-series already falls back to when sortBy is omitted.

    One query, fully materialized (not a server-side cursor / streaming
    query): at the current and reasonably foreseeable Bin Series volume
    (low thousands of rows) this is simpler and safer than a true
    streaming query, which would require the DB session to stay open for
    the entire HTTP response body — a session lifetime FastAPI's
    dependency-injection (`get_db` closes the session right after this
    function returns, not after the response finishes sending) does not
    straightforwardly support without extra plumbing this task does not
    need yet.
    """
    stmt = (
        select(BinRecord)
        .options(joinedload(BinRecord.updated_by_user))
        .order_by(BinRecord.id)
    )
    stmt = _apply_search_filters(stmt, search=search, issuer=issuer, card_program_group_name=card_program_group_name)
    return list(session.execute(stmt).scalars().all())


def backfill_custom_field(session: Session, *, key: str, default_value: str) -> None:
    """Sets custom_fields[key] = default_value on EVERY bin_records row,
    in ONE UPDATE statement — the bulk equivalent of BinTable.jsx's
    addColumn() applying a default value to every existing row. `key`
    and `default_value` are passed as bound parameters via
    func.jsonb_build_object, never string-concatenated."""
    stmt = update(BinRecord).values(
        custom_fields=BinRecord.custom_fields.op("||")(func.jsonb_build_object(key, default_value))
    )
    session.execute(stmt)
