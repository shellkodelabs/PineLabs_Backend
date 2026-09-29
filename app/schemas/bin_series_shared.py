"""
Pydantic schemas (and small pure helpers) SHARED between the Gift Card
and Wallet Bin Series domains — backs the approved combined/shared
router (`GET /api/v1/bin-series/lookup`, `POST /api/v1/bin-series/bulk-lookup`,
and — a later stage — `GET /api/v1/bin-series/version-history`), plus a
handful of genuinely type-agnostic pieces reused by both type-specific
routers (status-change, delete response, bulk-upload response,
custom-column name validation) rather than duplicated verbatim in each.

WHAT IS SHARED, AND WHY (kept intentionally narrow — the confirmed
architecture is "separate tables, separate registries, type-scoped
routers"; sharing here is ONLY for pieces whose shape has zero
type-specific fields, not a reintroduction of a merged model):
  - `BinRecordDeleteResponse` — {id, deleted}, identical to the old
    DeleteBinRecordResponse, no type-specific field could ever exist.
  - `BinStatusUpdateRequest` — {status, ticketNumber}; the validation
    rule (status enum, ticket format) is identical for both types, per
    this task's own instruction (one shared ticket-number pattern).
  - `BinBulkUploadRowNote` / `BinBulkUploadResponse` — the per-row
    failure/skip note and the batch outcome summary carry no
    type-specific fields either (mirrors the old BulkUploadRowNote/
    BulkUploadResponse exactly, just renamed with a `Bin` prefix to
    avoid any future name collision with the old, untouched
    app/schemas/bin_series.py module if both are ever imported
    together).
  - `reject_blank_name` — the tiny name-normalization helper reused
    identically by both custom-column schema modules.
  - The lookup/bulk-lookup discriminated-union schemas — inherently
    cross-type by definition (that IS what "combined lookup" means).

Deliberately does NOT import anything from the old (untouched, kept as
legacy) app/schemas/bin_series.py or app/schemas/bin_custom_column.py —
even though a few shapes are structurally identical — to avoid coupling
new code to files a later stage's approved cleanup plan removes.

LOOKUP DISCRIMINATOR: per this task's explicit instruction, the
combined lookup response identifies which type a result belongs to via
a plain `type: "giftCard" | "wallet"` field — NOT a database bin_type
column (none exists, and none is being added). `GiftCardLookupResult`/
`WalletLookupResult` each extend their own type's full record response
(see app/schemas/gift_card_bin_series.py / wallet_bin_series.py) purely
to avoid re-declaring every field a second time — this is standard
Pydantic inheritance, not a shared/merged table or model.

EXACT-9-DIGIT BULK LOOKUP VALIDATION: `extract_bin_and_prefix()` below
enforces "exactly 9 digits after stripping non-digit characters" and is
the schema-layer piece of that rule (importable and independently
testable — see this stage's validation). It is deliberately a plain
function, NOT wired into `BulkLookupRequest.cards`' own field
validation, because malformed values must NOT reject the whole batch
request — confirmed by inspecting the approved frontend's
BulkLookupModal.jsx: `resolve()` treats a non-9-digit value as ONE
unresolved result row (with a note), never a request-level failure,
and the OLD backend's own resolve-batch convention never rejected a
whole batch over one bad card either. A later stage's service layer
will call this function once per card, catching its ValueError to
produce a per-row `matched: false` entry — never propagating it as an
HTTP 422 for the whole request.
"""
import re
from typing import Annotated, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field

from app.schemas.gift_card_bin_series import GiftCardBinRecordResponse
from app.schemas.wallet_bin_series import WalletBinRecordResponse

# ---------------------------------------------------------------------
# Shared name-normalization helper (custom-column name validation)
# ---------------------------------------------------------------------


def reject_blank_name(value: str) -> str:
    """Trims and collapses internal whitespace; raises if the result is
    empty. Identical logic to the old app/schemas/bin_custom_column.py's
    private `_reject_blank_name` — duplicated here (not imported from
    that module) to avoid coupling to old, soon-to-be-retired code."""
    stripped = re.sub(r"\s+", " ", value.strip())
    if not stripped:
        raise ValueError("name must not be blank.")
    return stripped


# ---------------------------------------------------------------------
# Shared delete response
# ---------------------------------------------------------------------


class BinRecordDeleteResponse(BaseModel):
    id: int
    deleted: bool


# ---------------------------------------------------------------------
# Status change (PATCH .../gift-card/{id}/status,
# PATCH .../wallet/{id}/status — a later stage). Confirmed by frontend
# inspection: StatusConfirmModal.jsx renders an UNCONDITIONAL, required
# TicketField for a Bin Series status change — unlike Create/Edit/Delete,
# which never require a ticket at all (AddRowModal/DeleteRowModal both
# default requireTicket=false and BinTable.jsx never overrides that for
# Bin Series). This schema therefore has no Create/Update counterpart —
# ticket validation is exclusive to this one flow.
# ---------------------------------------------------------------------

TICKET_NUMBER_PATTERN = r"^[A-Za-z]{2,5}[- ]?\d{3,10}$"


class BinStatusUpdateRequest(BaseModel):
    status: Literal["Active", "Inactive"] = Field(..., description="Target status.")
    ticketNumber: str = Field(
        ...,
        pattern=TICKET_NUMBER_PATTERN,
        min_length=1,
        description="Mandatory helpdesk ticket reference, e.g. PL-10234. Required for every status change.",
    )


# ---------------------------------------------------------------------
# Bulk upload response (shared — see module docstring)
# ---------------------------------------------------------------------


class BinBulkUploadRowNote(BaseModel):
    rowIndex: int = Field(..., description="0-indexed position of this row in the submitted `rows` array.")
    binIin: Optional[str] = None
    merchantPrefix: Optional[str] = None
    reason: str


class BinBulkUploadResponse(BaseModel):
    mode: str
    totalRows: int
    createdCount: int
    updatedCount: int
    skippedCount: int
    failedCount: int
    failedRows: List[BinBulkUploadRowNote] = Field(default_factory=list)
    skippedRows: List[BinBulkUploadRowNote] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Combined lookup (GET /api/v1/bin-series/lookup) and bulk-lookup
# (POST /api/v1/bin-series/bulk-lookup) — a later stage implements the
# services/routers; this stage only defines the response shape.
# ---------------------------------------------------------------------


class GiftCardLookupResult(GiftCardBinRecordResponse):
    type: Literal["giftCard"] = "giftCard"


class WalletLookupResult(WalletBinRecordResponse):
    type: Literal["wallet"] = "wallet"


BinLookupResult = Annotated[Union[GiftCardLookupResult, WalletLookupResult], Field(discriminator="type")]


class BulkLookupRequest(BaseModel):
    cards: List[str] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description=(
            "Raw card values, in any format — non-digit characters are stripped before matching. "
            "Each must reduce to EXACTLY 9 digits (6-digit BIN + 3-digit Merchant Prefix) to match; "
            "anything else always resolves matched=false for that row — never rejects the whole "
            "request (no partial-BIN shortcut; mirrors BulkLookupModal.jsx's own per-row handling)."
        ),
    )


class BulkLookupResultItem(BaseModel):
    input: str
    matched: bool
    result: Optional[BinLookupResult] = None


class BulkLookupResponse(BaseModel):
    results: List[BulkLookupResultItem]


_NINE_DIGIT_LENGTH = 9
_BIN_LENGTH = 6
_MERCHANT_PREFIX_LENGTH = 3


def extract_bin_and_prefix(raw: str) -> Tuple[str, str]:
    """Strips non-digit characters from `raw` and returns
    (bin_iin, merchant_prefix) ONLY if the result is exactly 9 digits.
    Raises ValueError otherwise. See this module's docstring for why
    this is a standalone function rather than a `cards` field
    validator: a bad value must produce a per-row `matched: false`,
    never reject the whole bulk-lookup request."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) != _NINE_DIGIT_LENGTH:
        raise ValueError(
            f"Expected exactly {_NINE_DIGIT_LENGTH} digits (6-digit BIN + 3-digit Merchant Prefix), "
            f"got {len(digits)}."
        )
    return digits[:_BIN_LENGTH], digits[_BIN_LENGTH:_NINE_DIGIT_LENGTH]
