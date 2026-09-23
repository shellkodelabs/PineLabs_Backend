"""
Pydantic schemas for the User Management API.

ACCESS MAPPING STRATEGY (see completion report for full reasoning):
the `access` dict's keys are MERCHANT NAMES (e.g. "HDFC Bank"), not the
frontend's internal slug-style merchant ids (e.g. "hdfc" — see
CreateUserModal.jsx: `access: { [merchant.id]: Set(subsheetKeys) }`,
where `merchant.id` comes from sopData.js's client-side data array).
Those slugs were never persisted anywhere in the database — the Part 6
import used them only transiently, in-memory, to build a one-time
frontend-id -> db-id mapping during that single script run. The only
identifier that is BOTH present in the frontend's conceptual model AND
durably queryable in this database is `merchants.name` (unique on both
sides), which is also exactly what GET /api/v1/merchants/resolve (Part
8) already uses. Inventing a way to resolve "hdfc" specifically would
mean guessing a slug-to-name rule that only happens to work for this
particular mock dataset's naming convention — explicitly avoided per
the task's "do NOT guess silently" instruction.

Row/user access values (e.g. "block", "activation") are sop_sheets.key
values directly — these ARE persisted verbatim and require no mapping.
"""
import re
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.user import ROLE_VALUES, STATUS_VALUES
from app.schemas.common import PaginatedResponse

# Built from the model's own constants (single source of truth), same
# pattern as ClassificationFilter in app/api/v1/merchants.py.
Role = Literal[ROLE_VALUES]
Status = Literal[STATUS_VALUES]

_EMAIL_PATTERN_MESSAGE = "must be a valid email address"


def _normalize_email(value: str) -> str:
    value = value.strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        raise ValueError(_EMAIL_PATTERN_MESSAGE)
    return value.lower()


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    mobile: Optional[str] = None
    role: str
    status: str
    lastActiveAt: Optional[datetime] = None


# Named per the task's schema list; implemented as a reuse of the shared
# generic pagination envelope rather than a duplicate hand-written class
# (same pattern as every prior part — Part 3 §13/§14).
UserListResponse = PaginatedResponse[UserResponse]


class UserAccessResponse(UserResponse):
    """UserResponse plus the user's resolved SOP sheet access, keyed by
    merchant name (see module docstring)."""

    access: Dict[str, List[str]] = Field(default_factory=dict)


class CreateUserRequest(BaseModel):
    name: str = Field(..., min_length=1)
    email: str
    mobile: Optional[str] = Field(None, min_length=1)
    role: Role
    access: Dict[str, List[str]] = Field(default_factory=dict)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return _normalize_email(value)


class UpdateUserRequest(BaseModel):
    """All fields optional — a partial update. `access`, specifically,
    uses a three-state convention: omitted (key absent from the JSON
    body) = leave access unchanged; {} = clear all access; a populated
    dict = replace the complete access state with exactly this."""

    name: Optional[str] = Field(None, min_length=1)
    email: Optional[str] = None
    mobile: Optional[str] = Field(None, min_length=1)
    role: Optional[Role] = None
    status: Optional[Status] = None
    access: Optional[Dict[str, List[str]]] = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _normalize_email(value)


class DeleteUserResponse(BaseModel):
    success: bool
    message: str
