"""
Pydantic schemas for the BIN Series API.

Field names are camelCase (matching the frontend's existing binSeries.js
row shape: issuer/cardProgramGroupName/binIin/merchantPrefix) while the
underlying SQLAlchemy model uses snake_case columns
(card_program_group_name/bin_iin/merchant_prefix). The translation is
done explicitly in the service layer (app/services/bin_service.py),
field by field, rather than via Pydantic alias machinery — simpler to
read and to test than juggling separate validation/serialization
aliases for a one-directional (ORM object -> API response) mapping, and
matches the task's own division of responsibility ("service: mapping
database objects to response schemas").

Part 16 (Create/Update/Delete): `updatedBy`/`updatedAt` are additive,
read-only fields on BinRecordResponse — resolved server-side from the
authenticated actor and the row's own updated_at column, never accepted
from a request body (see CreateBinRecordRequest/UpdateBinRecordRequest
below, neither of which has an updatedBy field at all). Both are
Optional so existing consumers of GET /api/v1/bin-series/list and
GET /api/v1/bin-series/lookup keep working unchanged — updatedBy is
genuinely null for the 611 pre-existing records and any record the new
write APIs have never touched.

Part 17 (instanceName mandatory): NOTE ON NAMING — the task that
introduced this field also asked to rename any "merchantName" used as
the BIN issuer/business name to "issuerName". Inspection found no field
called merchantName anywhere in this module or the BIN domain — the
issuer/business-name field has always been called `issuer` (matching
the frontend's own binSeries.js field name), and the only
merchant-related field here (`merchantId`) is a distinct, legitimate
soft link to the real `merchants` table (SOP/classification domain), not
a business-name field. No rename was made here; see the Part 17
completion report for the full reasoning — flagged for product
confirmation rather than performed blindly.

`instanceName` validation: CreateBinRecordRequest.instanceName is a
required `str` — Pydantic itself already rejects a missing key or an
explicit JSON `null` (a `str`-typed required field cannot bind to None),
so `_validate_instance_name` only needs to additionally catch the two
cases Pydantic's own type system can't: an empty string and a
whitespace-only string. UpdateBinRecordRequest.instanceName stays
`Optional[str] = None` (the same "omitted = unchanged" partial-update
convention as every other field here) — its validator only rejects an
EXPLICIT blank/whitespace value, never None, because None is also what
a legitimately omitted field looks like after parsing; the service layer
(app/services/bin_service.py) uses `model_fields_set` to tell an
omitted field apart from an explicit `null`, since only the latter
should be rejected on update.

Dynamic/Custom Columns task — `customFields`:
  - BinRecordResponse.customFields is ALWAYS a dict (never null,
    default {}) — matches bin_records.custom_fields' NOT NULL DEFAULT
    '{}' at the DB level exactly, so there's nothing to special-case on
    read.
  - CreateBinRecordRequest.customFields is optional (omit = no custom
    values on the new row, matching AddRowModal.jsx: a row can be
    created with zero, some, or all currently-known custom columns
    filled in). Every key supplied MUST reference an existing
    bin_custom_columns.key — validated in the service layer (not here,
    since Pydantic has no DB access) — an unknown key is a 422, never
    silently accepted (that would let the row-write endpoint create
    ad-hoc, unregistered "custom columns" bypassing the metadata
    registry entirely).
  - UpdateBinRecordRequest.customFields is optional and MERGES into the
    record's existing custom_fields — omit the whole field to leave all
    custom values untouched (ordinary partial-update convention); when
    provided, only the KEYS you include change, every other existing
    custom key is preserved untouched. This matches BinTable.jsx's own
    saveCell() (always touches exactly one key, everything else
    unaffected). A value of "" is a legitimate cleared-but-present
    value (matches EditableCell's own commit behavior, which never
    sends anything but a trimmed string) — there is no "remove this key
    entirely" semantics, since the frontend never does that either.
"""
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _reject_blank_instance_name(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("instanceName must not be blank.")
    return stripped


class BinRecordResponse(BaseModel):
    id: int
    issuer: str
    cardProgramGroupName: str
    binIin: str
    merchantPrefix: str
    merchantId: Optional[int] = None
    instanceName: Optional[str] = None
    customFields: Dict[str, str] = Field(default_factory=dict)
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None


class CreateBinRecordRequest(BaseModel):
    issuer: str = Field(..., min_length=1)
    cardProgramGroupName: str = Field(..., min_length=1)
    binIin: str = Field(..., pattern=r"^\d{6}$", description="Exactly 6 digits")
    merchantPrefix: str = Field(..., pattern=r"^\d{3}$", description="Exactly 3 digits")
    merchantId: Optional[int] = None
    instanceName: str = Field(..., description="Mandatory. Must not be blank/whitespace-only.")
    customFields: Optional[Dict[str, str]] = Field(
        None, description="Optional. Every key must reference an existing custom column."
    )

    @field_validator("instanceName")
    @classmethod
    def _validate_instance_name(cls, value: str) -> str:
        return _reject_blank_instance_name(value)


class UpdateBinRecordRequest(BaseModel):
    """All fields optional — a partial update. A field omitted from the
    JSON body is left unchanged (there is no "clear to null" convention
    here, matching UpdateUserRequest's simple fields like `mobile`).

    `instanceName` is the one exception to "None is harmless": since it
    is mandatory, an EXPLICIT `null` is rejected by the service layer
    (not here — see module docstring) even though the field type allows
    None, because None is indistinguishable from omission at this layer.
    """

    issuer: Optional[str] = Field(None, min_length=1)
    cardProgramGroupName: Optional[str] = Field(None, min_length=1)
    binIin: Optional[str] = Field(None, pattern=r"^\d{6}$", description="Exactly 6 digits")
    merchantPrefix: Optional[str] = Field(None, pattern=r"^\d{3}$", description="Exactly 3 digits")
    merchantId: Optional[int] = None
    instanceName: Optional[str] = Field(None, description="If provided, must not be blank/whitespace-only.")
    customFields: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "Optional. MERGES into existing custom_fields — only the keys you include change, "
            "every other existing custom key is left untouched. Every key must reference an "
            "existing custom column."
        ),
    )

    @field_validator("instanceName")
    @classmethod
    def _validate_instance_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank_instance_name(value)


class DeleteBinRecordResponse(BaseModel):
    id: int
    deleted: bool


# ---------------------------------------------------------------------
# Bulk upload (POST /api/v1/bin-series/bulk-upload) — backs the
# frontend's BinTable "Import Data" button (UploadSheetModal.jsx),
# wired to BinTable's updateExisting()/addSheet(). JSON body, not
# multipart: the frontend already parses CSV/XLSX into row objects
# client-side (utils/csv.js's readSheetFile) before any of this would
# be sent — re-uploading a raw file for the backend to re-parse would
# duplicate logic the frontend already owns.
#
# MATCHING KEY — hybrid, not a single-key decision:
#   1. (binIin, merchantPrefix), when a row supplies both — the real,
#      DB-enforced unique key (uq_bin_records_bin_prefix). Primary.
#   2. Issuer name, ONLY when a row has no usable (binIin, merchantPrefix)
#      pair, and ONLY when the issuer matches EXACTLY ONE existing
#      record. Two or more matches -> the row fails as "ambiguous"
#      rather than guessing.
# This exists because the frontend's own updateExisting() (BinTable.jsx)
# currently matches by issuer alone, and the uploaded row genuinely may
# not carry binIin/merchantPrefix for a "just update this one field"
# sheet — but issuer is provably non-unique (the frontend's own seed
# data has "Aurora Retail" twice, at two different binIin/merchantPrefix
# pairs), so it can never be trusted as a SOLE key. See bin_service.py's
# module docstring for the full reasoning.
#
# instanceName: neither tab of UploadSheetModal.jsx collects an
# instanceName anywhere today. This endpoint requires ONE batch-level
# `instanceName` as the smallest possible bridge to the existing
# mandatory-instance rule (Part 17) — applied to every row created in
# "addNew" mode, and used only to backfill a matched row in
# "updateExisting" mode that doesn't already have one (never overwrites
# an existing value). Wiring the frontend to actually collect and send
# this is separate, not-yet-done frontend work — flagged, not assumed.
# ---------------------------------------------------------------------


class BulkUploadRowRequest(BaseModel):
    """One row of an uploaded sheet. Deliberately all-optional here —
    which fields are actually REQUIRED depends on `mode` (addNew needs
    issuer/cardProgramGroupName/binIin/merchantPrefix; updateExisting
    needs enough to identify a row — see the matching-key note above) —
    so per-row requiredness is validated in the service layer, where a
    bad row is reported as one entry in the response's `failedRows`
    instead of rejecting the entire batch."""

    issuer: Optional[str] = None
    cardProgramGroupName: Optional[str] = None
    binIin: Optional[str] = None
    merchantPrefix: Optional[str] = None
    merchantId: Optional[int] = None


class BulkUploadRequest(BaseModel):
    mode: Literal["updateExisting", "addNew"]
    instanceName: str = Field(
        ...,
        description=(
            "Mandatory. Applied to every row created in addNew mode; in "
            "updateExisting mode, backfilled only onto matched rows that "
            "don't already have one."
        ),
    )
    rows: List[BulkUploadRowRequest] = Field(..., min_length=1, max_length=1000)

    @field_validator("instanceName")
    @classmethod
    def _validate_instance_name(cls, value: str) -> str:
        return _reject_blank_instance_name(value)


class BulkUploadRowNote(BaseModel):
    rowIndex: int = Field(..., description="0-indexed position of this row in the submitted `rows` array.")
    binIin: Optional[str] = None
    merchantPrefix: Optional[str] = None
    reason: str


class BulkUploadResponse(BaseModel):
    mode: str
    totalRows: int
    createdCount: int
    updatedCount: int
    skippedCount: int
    failedCount: int
    failedRows: List[BulkUploadRowNote] = Field(default_factory=list)
    skippedRows: List[BulkUploadRowNote] = Field(default_factory=list)


class BinSeriesStatsResponse(BaseModel):
    """Backs GET /api/v1/bin-series/statistics (renamed from /stats by
    the API Naming task) — the frontend's StatCards on
    the BIN Series page (src/components/dashboard/StatCards.jsx), today
    computed client-side as binSeries.length / unique issuer count /
    unique cardProgramGroupName count from the full local mock array."""

    totalRecords: int
    totalIssuers: int
    totalCardPrograms: int


# ---------------------------------------------------------------------
# Bulk/multi-card resolve (POST /api/v1/bin-series/bulk-lookup, renamed
# from /resolve-batch by the API Naming task) —
# backs BulkLookupModal.jsx (opened from both BinResolver.jsx and
# BinTable.jsx). See app/services/bin_service.py's module docstring for
# the full matching-rule reasoning (a documented SUPERSET of GET
# /lookup's rule, not a second inconsistent algorithm): a card resolves
# either via an exact (binIin, merchantPrefix) match, OR — when fewer
# than 3 prefix digits were supplied — via a "BIN uniquely identifies
# exactly one record" shortcut, exactly matching BulkLookupModal.jsx's
# own resolve() function.
#
# FIELD NAMES: the frontend's own local resolve() returns
# {input, bin, prefix, issuer, program, binSeriesNo} — internal JS
# variable names that were never part of any network contract (this
# endpoint doesn't exist in the frontend yet). Deliberately NOT mirrored
# here; this schema instead reuses BinRecordResponse's own established
# field names (binIin, merchantPrefix, cardProgramGroupName) for
# consistency with every other endpoint in this API. No `binSeriesNo`
# concatenation either — the frontend can trivially compute
# `binIin + merchantPrefix` itself, same as it already does today.
#
# INPUT PRESERVATION: `input` is ALWAYS the raw, trimmed original string
# exactly as submitted — never reformatted. The frontend's own resolve()
# reformats it as "{bin} {prefix}" for successfully-length-parsed cards
# but keeps the literal raw string for too-short ones; that's a
# display-layer inconsistency this API deliberately does not replicate,
# since a stable, unambiguous "which of my inputs was this" value is
# more broadly useful for an API contract than a partially-reformatted
# one. The frontend can still reformat `input` for display however it
# likes.
# ---------------------------------------------------------------------


class ResolveBatchRequest(BaseModel):
    cards: List[str] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Raw card number strings, in any format — non-digit characters are stripped before matching.",
    )


class ResolveBatchResultItem(BaseModel):
    input: str
    binIin: Optional[str] = Field(None, description="The 6-digit BIN extracted from `input`, if at least 6 digits were present — regardless of match outcome.")
    merchantPrefix: Optional[str] = Field(None, description="The 3-digit merchant prefix extracted from `input`, if a full 3 digits were present — regardless of match outcome.")
    issuer: Optional[str] = None
    cardProgramGroupName: Optional[str] = None
    matched: bool


class ResolveBatchResponse(BaseModel):
    results: List[ResolveBatchResultItem]
