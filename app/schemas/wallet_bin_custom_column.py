"""
Pydantic schemas for Wallet Bin Series dynamic/custom columns —
GET/POST/PATCH /api/v1/bin-series/wallet/custom-columns/... (routers
built in a later stage). Backs the `wallet_bin_custom_columns` table
(see app/models/wallet_bin_custom_column.py) — an INDEPENDENT registry
from `gift_card_bin_custom_columns`, never combined.

See app/schemas/gift_card_bin_custom_column.py's module docstring for
the full reasoning (identical structure/conventions, deliberately
independent, not shared).
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.bin_series_shared import reject_blank_name


class WalletCustomColumnResponse(BaseModel):
    id: int
    key: str
    name: str
    displayOrder: int
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None


class CreateWalletCustomColumnRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Display name. Also becomes the column's immutable key at creation.")
    defaultValue: Optional[str] = Field(
        None,
        description=(
            "Applied to every EXISTING wallet_bin_records row's custom_fields at creation "
            "time only — matches AddColumnModal.jsx's own 'Default value for existing records' "
            "field. Not persisted as an ongoing column property."
        ),
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return reject_blank_name(value)


class RenameWalletCustomColumnRequest(BaseModel):
    name: str = Field(..., min_length=1, description="New display name. Does not affect the column's key or any stored row values.")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return reject_blank_name(value)
