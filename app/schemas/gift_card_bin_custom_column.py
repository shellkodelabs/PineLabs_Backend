"""
Pydantic schemas for Gift Card Bin Series dynamic/custom columns —
GET/POST/PATCH /api/v1/bin-series/gift-card/custom-columns/... (routers
built in a later stage). Backs the `gift_card_bin_custom_columns`
table (see app/models/gift_card_bin_custom_column.py) — an INDEPENDENT
registry from `wallet_bin_custom_columns`, never combined.

Structural mirror of the existing (old, untouched)
app/schemas/bin_custom_column.py — same conventions: `name` is the
request/response field for the display label, `key` is exposed
read-only (never accepted from a request, derived server-side from the
initial `name` at creation and immutable thereafter). Deliberately a
fully independent, duplicated definition rather than importing from the
old module or sharing a base class with the Wallet registry's schemas —
per this task's explicit instruction not to combine the two registries
into one schema/model.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.bin_series_shared import reject_blank_name


class GiftCardCustomColumnResponse(BaseModel):
    id: int
    key: str
    name: str
    displayOrder: int
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None


class CreateGiftCardCustomColumnRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Display name. Also becomes the column's immutable key at creation.")
    defaultValue: Optional[str] = Field(
        None,
        description=(
            "Applied to every EXISTING gift_card_bin_records row's custom_fields at creation "
            "time only — matches AddColumnModal.jsx's own 'Default value for existing records' "
            "field. Not persisted as an ongoing column property."
        ),
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return reject_blank_name(value)


class RenameGiftCardCustomColumnRequest(BaseModel):
    name: str = Field(..., min_length=1, description="New display name. Does not affect the column's key or any stored row values.")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return reject_blank_name(value)
