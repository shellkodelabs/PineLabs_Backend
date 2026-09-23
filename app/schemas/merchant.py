"""
Pydantic response schemas for the Merchant API.

Field names are already the same in snake_case-free form as the model
(`id`, `name`, `classification`), so no camelCase translation is needed
here (unlike BinRecordResponse) — the service layer still builds these
explicitly from ORM objects rather than relying on from_attributes/alias
machinery, for the same reasons documented in app/schemas/bin_series.py.
"""
from typing import List

from pydantic import BaseModel


class MerchantResponse(BaseModel):
    id: int
    name: str
    classification: str


class MerchantSheetResponse(BaseModel):
    """A merchant-owned SOP sheet, as returned by
    GET /api/v1/merchants/{merchantId}/sheets. Deliberately minimal —
    just enough to populate a tab bar (id/key/name); groups/columns/rows
    belong to the not-yet-implemented sheet-detail endpoint."""

    id: int
    key: str
    name: str


class MerchantSheetsResponse(BaseModel):
    merchant: MerchantResponse
    sheets: List[MerchantSheetResponse]
