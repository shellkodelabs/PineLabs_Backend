"""
Pydantic schemas for Wallet Bin Series records —
GET/POST/PUT/DELETE /api/v1/bin-series/wallet/... (routers built in a
later stage). Backs the `wallet_bin_records` table (see
app/models/wallet_bin_record.py).

Mirrors app/schemas/gift_card_bin_series.py's field/requiredness
reasoning exactly (see that module's docstring for the full
explanation — status excluded from Create/Update, every other field
required on create, no merchantId/bin_type, customFields
optional-and-merging) with one structural difference: no
`cardProgramGroupName`/`cardProgramGroupType`/`cardType` — Wallet
records use `walletProgramName`/`walletProgramGroupType` instead, per
the client Excel format's Wallet column set (Instance, Issuer,
Merchant, Wallet Program Name, BIN, Merchant Prefix, Wallet Program
Group Type, Ticket Number).

`customFields` here is backed by an INDEPENDENT registry
(`wallet_bin_custom_columns` — see
app/schemas/wallet_bin_custom_column.py), never the Gift Card one.
"""
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class WalletBinRecordResponse(BaseModel):
    id: int
    instance: str
    issuer: str
    merchant: str
    walletProgramName: str
    binIin: str
    merchantPrefix: str
    walletProgramGroupType: str
    ticketNumber: str
    status: str
    customFields: Dict[str, str] = Field(default_factory=dict)
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None


class CreateWalletBinRecordRequest(BaseModel):
    instance: str = Field(..., min_length=1)
    issuer: str = Field(..., min_length=1)
    merchant: str = Field(..., min_length=1)
    walletProgramName: str = Field(..., min_length=1)
    binIin: str = Field(..., pattern=r"^\d{6}$", description="Exactly 6 digits")
    merchantPrefix: str = Field(..., pattern=r"^\d{3}$", description="Exactly 3 digits")
    walletProgramGroupType: str = Field(..., min_length=1)
    ticketNumber: str = Field(..., min_length=1)
    customFields: Optional[Dict[str, str]] = Field(
        None, description="Optional. Every key must reference an existing Wallet custom column."
    )


class UpdateWalletBinRecordRequest(BaseModel):
    """All fields optional — a partial update. A field omitted from the
    JSON body is left unchanged (same convention as every other Update
    schema in this project)."""

    instance: Optional[str] = Field(None, min_length=1)
    issuer: Optional[str] = Field(None, min_length=1)
    merchant: Optional[str] = Field(None, min_length=1)
    walletProgramName: Optional[str] = Field(None, min_length=1)
    binIin: Optional[str] = Field(None, pattern=r"^\d{6}$", description="Exactly 6 digits")
    merchantPrefix: Optional[str] = Field(None, pattern=r"^\d{3}$", description="Exactly 3 digits")
    walletProgramGroupType: Optional[str] = Field(None, min_length=1)
    ticketNumber: Optional[str] = Field(None, min_length=1)
    customFields: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "Optional. MERGES into existing customFields — only the keys you include change, "
            "every other existing custom key is left untouched."
        ),
    )


# ---------------------------------------------------------------------
# Bulk upload (POST /api/v1/bin-series/wallet/bulk-upload, a later
# stage). See app/schemas/gift_card_bin_series.py's equivalent section
# for the full reasoning (per-row `instance`, all-optional row schema).
# ---------------------------------------------------------------------


class WalletBulkUploadRowRequest(BaseModel):
    instance: Optional[str] = None
    issuer: Optional[str] = None
    merchant: Optional[str] = None
    walletProgramName: Optional[str] = None
    binIin: Optional[str] = None
    merchantPrefix: Optional[str] = None
    walletProgramGroupType: Optional[str] = None
    ticketNumber: Optional[str] = None


class WalletBulkUploadRequest(BaseModel):
    mode: Literal["updateExisting", "addNew"]
    rows: List[WalletBulkUploadRowRequest] = Field(..., min_length=1, max_length=1000)


class WalletBinSeriesStatsResponse(BaseModel):
    """Backs GET /api/v1/bin-series/wallet/statistics (a later stage) —
    always a whole-table aggregate, no pagination/filters. `totalWalletPrograms`
    is the Wallet-domain equivalent of the Gift Card stats'
    `totalCardPrograms` (distinct `walletProgramName` count)."""

    totalRecords: int
    totalIssuers: int
    totalWalletPrograms: int
