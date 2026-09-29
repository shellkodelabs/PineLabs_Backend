"""
Shared/combined Bin Series router — HTTP concerns only, wired to
app.services.bin_series_shared (the Stage 5 module that queries BOTH
gift_card_bin_records and wallet_bin_records). No SQLAlchemy usage, no
business logic here — same conventions as
app/api/v1/bin_series_gift_card.py (see that module's docstring).

ROUTE CONFLICT WITH THE OLD ROUTER (flagged, not silently resolved —
see this stage's completion report): the old, untouched
app/api/v1/bin_series.py already defines GET /lookup, POST /bulk-lookup,
and GET /version-history under the SAME "/bin-series" prefix this
router also mounts at (see app/api/v1/router.py). Because these are the
identical (method, path) pairs, both routers cannot serve them
simultaneously — FastAPI/Starlette matches routes in registration
order, so only ONE implementation can ever be reachable at each of
these three URLs. This is a genuine, unavoidable conflict (not a naming
accident): the new two-table combined lookup/bulk-lookup/history is
meant to REPLACE the old single-table bin_records-only behavior at
these exact URLs, per this task's own Section 5 instruction ("These
must use the Stage 5 shared service"). The minimum safe fix (no code
deleted): app/api/v1/router.py registers THIS router's three routes
BEFORE including the old bin_series.router, so they win the match.
Every one of the old router's OTHER routes (/list, /create,
/{binRecordId}/update, /{binRecordId}/delete, /export, /statistics,
/custom-columns/*) is untouched and still fully reachable — only its
own /lookup, /bulk-lookup, /version-history become dead code (present,
importable, but unreachable through HTTP) until Stage 7's cleanup
removes them.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.bin_series_history import BinSeriesHistoryResponse
from app.schemas.bin_series_shared import BinLookupResult, BulkLookupRequest, BulkLookupResponse
from app.services import bin_series_shared

router = APIRouter()


@router.get("/lookup", response_model=BinLookupResult)
def lookup_bin(
    binIin: str = Query(..., pattern=r"^\d{6}$", description="Exactly 6 digits"),
    merchantPrefix: str = Query(..., pattern=r"^\d{3}$", description="Exactly 3 digits"),
    db: Session = Depends(get_db),
) -> BinLookupResult:
    """Resolves the single BIN record matching an exact (binIin,
    merchantPrefix) pair across BOTH Gift Card and Wallet tables — never
    filters by status. Returns the project's standard 404 error envelope
    if neither table has a match. `type` in the response discriminates
    which table matched ("giftCard" | "wallet")."""
    return bin_series_shared.lookup_bin(db, bin_iin=binIin, merchant_prefix=merchantPrefix)


@router.post("/bulk-lookup", response_model=BulkLookupResponse)
def bulk_lookup_bin(payload: BulkLookupRequest, db: Session = Depends(get_db)) -> BulkLookupResponse:
    """Bulk/multi-card resolve across BOTH tables — returns exactly one
    result per input card, in input order, including duplicates. Each
    card is processed independently: a malformed value (not exactly 9
    digits after stripping non-digit characters) never aborts the whole
    request, it only yields `matched: false` for that one card."""
    return bin_series_shared.bulk_lookup_bin(db, payload)


@router.get("/version-history", response_model=BinSeriesHistoryResponse)
def get_bin_series_history(
    limit: int = Query(
        5, ge=1, le=200,
        description="Max entries to return, newest first (default 5). Scoped to ONLY the four new "
        "entity types (gift_card_bin_record, wallet_bin_record, gift_card_bin_custom_column, "
        "wallet_bin_custom_column) — the old bin_record/bin_custom_column history stays on the "
        "legacy endpoint.",
    ),
    db: Session = Depends(get_db),
) -> BinSeriesHistoryResponse:
    """Combined version history across the new Gift Card and Wallet Bin
    Series tables (row and custom-column events alike)."""
    return bin_series_shared.list_bin_series_history(db, limit=limit)
