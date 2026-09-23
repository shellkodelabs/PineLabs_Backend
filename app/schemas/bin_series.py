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
Optional so existing consumers of GET /api/v1/bin-series and
GET /api/v1/bin-series/resolve keep working unchanged — updatedBy is
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
"""
from datetime import datetime
from typing import Optional

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
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None


class CreateBinRecordRequest(BaseModel):
    issuer: str = Field(..., min_length=1)
    cardProgramGroupName: str = Field(..., min_length=1)
    binIin: str = Field(..., pattern=r"^\d{6}$", description="Exactly 6 digits")
    merchantPrefix: str = Field(..., pattern=r"^\d{3}$", description="Exactly 3 digits")
    merchantId: Optional[int] = None
    instanceName: str = Field(..., description="Mandatory. Must not be blank/whitespace-only.")

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

    @field_validator("instanceName")
    @classmethod
    def _validate_instance_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank_instance_name(value)


class DeleteBinRecordResponse(BaseModel):
    id: int
    deleted: bool
