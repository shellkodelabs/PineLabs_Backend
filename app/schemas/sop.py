"""
Pydantic response schemas for the SOP API.

Row `data` is typed as Dict[str, Any] deliberately — SOP columns are
heterogeneous per merchant/sheet (confirmed in Parts 2/5/6: 5 distinct
"Block" column schemas alone across 510 merchants), so there is no fixed
row shape to model. This is a pass-through of the JSONB column, not a
fixed Pydantic model.
"""
from typing import Any, Dict, List

from pydantic import BaseModel

from app.schemas.common import PaginatedResponse
from app.schemas.merchant import MerchantResponse


class SopColumnResponse(BaseModel):
    id: int
    name: str
    sortOrder: int


class SopColumnGroupResponse(BaseModel):
    id: int
    label: str
    sortOrder: int
    columns: List[SopColumnResponse]


class SopSheetResponse(BaseModel):
    id: int
    key: str
    name: str
    groups: List[SopColumnGroupResponse]


class MerchantSheetDetailResponse(BaseModel):
    """GET /api/v1/merchants/{merchantId}/sheets/{sheetKey}"""

    merchant: MerchantResponse
    sheet: SopSheetResponse


class SopRowResponse(BaseModel):
    """One SOP row. `data` is returned exactly as stored — no fixed
    columns, no renaming, no type coercion of values."""

    id: int
    data: Dict[str, Any]


class CommonEscalationResponse(BaseModel):
    """GET /api/v1/sop/escalation/common"""

    sheet: SopSheetResponse
    rows: PaginatedResponse[SopRowResponse]
