"""
Repository for `wallet_bin_records` — SQLAlchemy queries only. No
business rules, no response-schema mapping, no HTTP concerns live here.
Mirrors app/repositories/gift_card_bin_repository.py's structure and
reasoning exactly (see that module's docstring for the full
explanation of every design decision below) — independent of it, no
shared base, no polymorphic dispatch, per the confirmed architecture.

The only structural difference: this table has no
`card_program_group_name`/`card_program_group_type`/`card_type` —
`wallet_program_name`/`wallet_program_group_type` take their place, per
the client Excel format's Wallet column set. `stats()` reports
`total_wallet_programs` (distinct `wallet_program_name`) in place of
Gift Card's `total_card_programs`, matching
app/schemas/wallet_bin_series.py's WalletBinSeriesStatsResponse.
"""
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import asc, desc, func, or_, select, text, tuple_, update
from sqlalchemy.orm import Session, joinedload

from app.models.wallet_bin_record import WalletBinRecord

SORT_FIELD_MAP = {
    "instance": WalletBinRecord.instance,
    "issuer": WalletBinRecord.issuer,
    "merchant": WalletBinRecord.merchant,
    "walletProgramName": WalletBinRecord.wallet_program_name,
    "binIin": WalletBinRecord.bin_iin,
    "merchantPrefix": WalletBinRecord.merchant_prefix,
    "walletProgramGroupType": WalletBinRecord.wallet_program_group_type,
    "ticketNumber": WalletBinRecord.ticket_number,
    "status": WalletBinRecord.status,
    "id": WalletBinRecord.id,
}


def _apply_search_filters(stmt, *, search: Optional[str], status: Optional[str]):
    if search:
        term = f"%{search.strip()}%"
        custom_field_value_match = text(
            "EXISTS (SELECT 1 FROM jsonb_each_text(wallet_bin_records.custom_fields) AS kv(key, value) "
            "WHERE kv.value ILIKE :custom_search_term)"
        ).bindparams(custom_search_term=term)
        stmt = stmt.where(
            or_(
                WalletBinRecord.instance.ilike(term),
                WalletBinRecord.issuer.ilike(term),
                WalletBinRecord.merchant.ilike(term),
                WalletBinRecord.wallet_program_name.ilike(term),
                WalletBinRecord.bin_iin.ilike(term),
                WalletBinRecord.merchant_prefix.ilike(term),
                WalletBinRecord.wallet_program_group_type.ilike(term),
                WalletBinRecord.ticket_number.ilike(term),
                WalletBinRecord.status.ilike(term),
                custom_field_value_match,
            )
        )
    if status:
        stmt = stmt.where(WalletBinRecord.status == status)
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
) -> Tuple[List[WalletBinRecord], int]:
    """Filters, sorts, and paginates wallet_bin_records. Returns
    (items, total)."""
    stmt = select(WalletBinRecord).options(joinedload(WalletBinRecord.updated_by_user))
    stmt = _apply_search_filters(stmt, search=search, status=status)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, WalletBinRecord.id)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not WalletBinRecord.id:
        order_clauses.append(WalletBinRecord.id)
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0


def get_by_id(session: Session, record_id: int) -> Optional[WalletBinRecord]:
    stmt = (
        select(WalletBinRecord)
        .options(joinedload(WalletBinRecord.updated_by_user))
        .where(WalletBinRecord.id == record_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_bin_and_prefix(session: Session, *, bin_iin: str, merchant_prefix: str) -> Optional[WalletBinRecord]:
    """Exact match only — never filters by status (see
    gift_card_bin_repository.py's module docstring for the full
    reasoning, identical here)."""
    stmt = (
        select(WalletBinRecord)
        .options(joinedload(WalletBinRecord.updated_by_user))
        .where(
            WalletBinRecord.bin_iin == bin_iin,
            WalletBinRecord.merchant_prefix == merchant_prefix,
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_bin_and_prefix_batch(
    session: Session, pairs: Sequence[Tuple[str, str]]
) -> Dict[Tuple[str, str], WalletBinRecord]:
    if not pairs:
        return {}
    stmt = (
        select(WalletBinRecord)
        .options(joinedload(WalletBinRecord.updated_by_user))
        .where(tuple_(WalletBinRecord.bin_iin, WalletBinRecord.merchant_prefix).in_(list(pairs)))
    )
    records = session.execute(stmt).scalars().all()
    return {(r.bin_iin, r.merchant_prefix): r for r in records}


def get_by_issuer_batch(session: Session, issuers: Sequence[str]) -> Dict[str, List[WalletBinRecord]]:
    normalized = {i.strip().lower() for i in issuers if i and i.strip()}
    if not normalized:
        return {}
    stmt = (
        select(WalletBinRecord)
        .options(joinedload(WalletBinRecord.updated_by_user))
        .where(func.lower(WalletBinRecord.issuer).in_(normalized))
    )
    records = session.execute(stmt).scalars().all()
    result: Dict[str, List[WalletBinRecord]] = {}
    for record in records:
        result.setdefault(record.issuer.strip().lower(), []).append(record)
    return result


def create(
    session: Session,
    *,
    instance: str,
    issuer: str,
    merchant: str,
    wallet_program_name: str,
    bin_iin: str,
    merchant_prefix: str,
    wallet_program_group_type: str,
    ticket_number: str,
    updated_by_user_id: Optional[int],
    custom_fields: Optional[Dict[str, str]] = None,
) -> WalletBinRecord:
    record = WalletBinRecord(
        instance=instance,
        issuer=issuer,
        merchant=merchant,
        wallet_program_name=wallet_program_name,
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        wallet_program_group_type=wallet_program_group_type,
        ticket_number=ticket_number,
        updated_by_user_id=updated_by_user_id,
        custom_fields=custom_fields or {},
    )
    session.add(record)
    session.flush()
    return record


def update_fields(session: Session, record: WalletBinRecord, **fields) -> WalletBinRecord:
    for key, value in fields.items():
        setattr(record, key, value)
    session.flush()
    return record


def delete(session: Session, record: WalletBinRecord) -> None:
    session.delete(record)
    session.flush()


def stats(session: Session) -> Tuple[int, int, int]:
    """Returns (total_records, total_issuers, total_wallet_programs) —
    backs WalletBinSeriesStatsResponse (Stage 3)."""
    stmt = select(
        func.count(),
        func.count(func.distinct(WalletBinRecord.issuer)),
        func.count(func.distinct(WalletBinRecord.wallet_program_name)),
    ).select_from(WalletBinRecord)
    total_records, total_issuers, total_wallet_programs = session.execute(stmt).one()
    return total_records or 0, total_issuers or 0, total_wallet_programs or 0


def count_by_status(session: Session) -> Tuple[int, int]:
    """Returns (active_count, inactive_count) — see
    gift_card_bin_repository.py's identically-named function for the
    full reasoning."""
    stmt = select(
        func.count().filter(WalletBinRecord.status == "Active"),
        func.count().filter(WalletBinRecord.status == "Inactive"),
    ).select_from(WalletBinRecord)
    active_count, inactive_count = session.execute(stmt).one()
    return active_count or 0, inactive_count or 0


def backfill_custom_field(session: Session, *, key: str, default_value: str) -> None:
    """Sets custom_fields[key] = default_value on EVERY
    wallet_bin_records row, in ONE UPDATE statement — see
    gift_card_bin_repository.py's identically-named function for the
    full reasoning. Added in Stage 5 (a Stage 4 gap closed here)."""
    stmt = update(WalletBinRecord).values(
        custom_fields=WalletBinRecord.custom_fields.op("||")(func.jsonb_build_object(key, default_value))
    )
    session.execute(stmt)
