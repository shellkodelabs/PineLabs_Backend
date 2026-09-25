"""
Pydantic schemas for Bin Series dynamic/custom columns
(GET /api/v1/bin-series/custom-columns, POST .../custom-columns/create,
PATCH .../custom-columns/{id}/rename — renamed from /columns,
/columns, and /columns/{id} respectively by the API Naming task).

Based on frontend inspection findings (AddColumnModal.jsx, BinTable.jsx —
see app/models/bin_custom_column.py's module docstring for the full
reasoning): no type field (plain strings only), no delete endpoint (the
frontend has no delete-column capability at all), no reorder endpoint
(no explicit ordering UI exists — order is pure creation sequence).

`name` is used as the request/response field for the display label
(matching AddColumnModal.jsx's own internal variable name and the
task's own conceptual examples), even though the underlying model
column is also literally called `name`. `key` is exposed read-only in
the response — never accepted from a request, since it's derived
server-side from the initial `name` at creation and is immutable
thereafter (see CreateCustomColumnRequest below).
"""
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _reject_blank_name(value: str) -> str:
    stripped = re.sub(r"\s+", " ", value.strip())
    if not stripped:
        raise ValueError("name must not be blank.")
    return stripped


class CustomColumnResponse(BaseModel):
    id: int
    key: str
    name: str
    displayOrder: int
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None


class CreateCustomColumnRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Display name. Also becomes the column's immutable key at creation.")
    defaultValue: Optional[str] = Field(
        None,
        description=(
            "Applied to every EXISTING bin_records row's custom_fields at creation time only — "
            "matches AddColumnModal.jsx's own 'Default value for existing records' field. "
            "Not persisted as an ongoing column property (the frontend doesn't retain it either)."
        ),
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _reject_blank_name(value)


class RenameCustomColumnRequest(BaseModel):
    name: str = Field(..., min_length=1, description="New display name. Does not affect the column's key or any stored row values.")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _reject_blank_name(value)
