"""
Pydantic schemas for Gift Card Bin Series records —
GET/POST/PUT/DELETE /api/v1/bin-series/gift-card/... (routers built in a
later stage). Backs the `gift_card_bin_records` table (see
app/models/gift_card_bin_record.py).

New Excel-format Bin Series task, Stage 3 (schemas only). Field/
requiredness decisions here were cross-checked directly against the
approved frontend (PineLabs_Frontend `src/data/binSeries.js`,
`BinTable.jsx`, `AddRowModal.jsx`, `UploadSheetModal.jsx`), not
invented:

  - `status` is NOT part of Create/Update — confirmed by inspection
    that AddRowModal.jsx's Add Row form (and bulk-upload's "Add New"
    tab) never collects status; every new record is always hardcoded
    to 'Active' client-side (`status: 'Active'` in BinTable.jsx's
    addRow()). Status only ever changes through the dedicated
    Active/Inactive toggle (PATCH .../status, a later stage) — see
    app/schemas/bin_series_shared.py's `BinStatusUpdateRequest`.
  - Every OTHER field (instance, issuer, merchant,
    cardProgramGroupName, binIin, merchantPrefix, cardProgramGroupType,
    cardType, ticketNumber) IS required on create — confirmed by
    AddRowModal.jsx's `filled` check, which requires every non-select
    column to be non-blank with no exceptions (unlike the old
    single-table CreateBinRecordRequest, where only issuer/
    cardProgramGroupName/binIin/merchantPrefix were required and
    merchantId was optional).
  - No `merchantId`/`bin_type` fields — explicitly excluded per this
    task's instructions; `merchant` is a plain, unrelated string (see
    app/models/gift_card_bin_record.py's module docstring), not a
    foreign key.
  - `instance`, unlike the old `bin_records.instance_name`, gets no
    special "explicit null vs omitted" validator here — that split
    existed only because the OLD column had to stay nullable-in-DB for
    611 legacy rows with no value to backfill (Part 17). This is a
    brand-new table with zero legacy rows, so `instance` is treated
    exactly like every other required string field (plain
    `min_length=1`, consistent across Create/Update). A later stage's
    service layer may still need `model_fields_set`-style handling to
    distinguish an explicit `null` from omission on UpdateBinRecordRequest,
    the same way the old instanceName update path did — noted here,
    not implemented in this schema-only stage.
  - `customFields` follows the exact same convention as the old
    CreateBinRecordRequest/UpdateBinRecordRequest: optional on create
    (omit = no custom values), optional-and-MERGING on update. Backed
    by an INDEPENDENT registry (`gift_card_bin_custom_columns` — see
    app/schemas/gift_card_bin_custom_column.py), never the Wallet one.

Field names are camelCase, matching every other response schema in
this project.
"""
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class GiftCardBinRecordResponse(BaseModel):
    id: int
    instance: str
    issuer: str
    merchant: str
    cardProgramGroupName: str
    binIin: str
    merchantPrefix: str
    cardProgramGroupType: str
    cardType: str
    ticketNumber: str
    status: str
    customFields: Dict[str, str] = Field(default_factory=dict)
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None


class CreateGiftCardBinRecordRequest(BaseModel):
    instance: str = Field(..., min_length=1)
    issuer: str = Field(..., min_length=1)
    merchant: str = Field(..., min_length=1)
    cardProgramGroupName: str = Field(..., min_length=1)
    binIin: str = Field(..., pattern=r"^\d{6}$", description="Exactly 6 digits")
    merchantPrefix: str = Field(..., pattern=r"^\d{3}$", description="Exactly 3 digits")
    cardProgramGroupType: str = Field(..., min_length=1)
    cardType: str = Field(..., min_length=1)
    ticketNumber: str = Field(..., min_length=1)
    customFields: Optional[Dict[str, str]] = Field(
        None, description="Optional. Every key must reference an existing Gift Card custom column."
    )


class UpdateGiftCardBinRecordRequest(BaseModel):
    """All fields optional — a partial update. A field omitted from the
    JSON body is left unchanged (same convention as every other Update
    schema in this project)."""

    instance: Optional[str] = Field(None, min_length=1)
    issuer: Optional[str] = Field(None, min_length=1)
    merchant: Optional[str] = Field(None, min_length=1)
    cardProgramGroupName: Optional[str] = Field(None, min_length=1)
    binIin: Optional[str] = Field(None, pattern=r"^\d{6}$", description="Exactly 6 digits")
    merchantPrefix: Optional[str] = Field(None, pattern=r"^\d{3}$", description="Exactly 3 digits")
    cardProgramGroupType: Optional[str] = Field(None, min_length=1)
    cardType: Optional[str] = Field(None, min_length=1)
    ticketNumber: Optional[str] = Field(None, min_length=1)
    customFields: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "Optional. MERGES into existing customFields — only the keys you include change, "
            "every other existing custom key is left untouched."
        ),
    )


# ---------------------------------------------------------------------
# Bulk upload (POST /api/v1/bin-series/gift-card/bulk-upload, a later
# stage). Matching-key/mode semantics are NOT decided here — schema
# layer only, per this stage's scope. `instance` is now a per-ROW field
# (unlike the old single-table bulk-upload's batch-level `instanceName`
# bridge) since the client Excel format carries Instance as one of the
# Gift Card sheet's own columns.
# ---------------------------------------------------------------------


class GiftCardBulkUploadRowRequest(BaseModel):
    """One row of an uploaded Gift Card sheet. Deliberately all-optional
    here — same convention as the old BulkUploadRowRequest — per-row
    requiredness (which depends on `mode`) is a service-layer concern
    for a later stage, so a bad row can be reported individually rather
    than rejecting the entire batch."""

    instance: Optional[str] = None
    issuer: Optional[str] = None
    merchant: Optional[str] = None
    cardProgramGroupName: Optional[str] = None
    binIin: Optional[str] = None
    merchantPrefix: Optional[str] = None
    cardProgramGroupType: Optional[str] = None
    cardType: Optional[str] = None
    ticketNumber: Optional[str] = None


class GiftCardBulkUploadRequest(BaseModel):
    mode: Literal["updateExisting", "addNew"]
    rows: List[GiftCardBulkUploadRowRequest] = Field(..., min_length=1, max_length=1000)


class GiftCardBinSeriesStatsResponse(BaseModel):
    """Backs GET /api/v1/bin-series/gift-card/statistics (a later
    stage) — always a whole-table aggregate, no pagination/filters,
    same convention as the old BinSeriesStatsResponse."""

    totalRecords: int
    totalIssuers: int
    totalCardPrograms: int
