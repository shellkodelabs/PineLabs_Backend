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
    # The user's selected scope (persisted in users.scope JSONB). The
    # lists let the Edit modal pre-fill, and the counts drive the "N
    # instances / M issuers" badges in the list page.
    instanceIds: List[int] = Field(default_factory=list)
    issuers: List[str] = Field(default_factory=list)
    instanceCount: int = 0
    issuerCount: int = 0


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
    # Selected scope from the Create User screen. Persisted on the user
    # (users.scope) and echoed back in UserResponse for the list badges /
    # Edit pre-fill. Both default to empty.
    instanceIds: List[int] = Field(default_factory=list)
    issuers: List[str] = Field(default_factory=list)

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
    # Scope lists use the same "omitted = unchanged" convention as the
    # other optional fields: absent -> left as-is; provided -> replaces
    # that list wholesale (an empty list clears it).
    instanceIds: Optional[List[int]] = None
    issuers: Optional[List[str]] = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _normalize_email(value)


class DeleteUserResponse(BaseModel):
    success: bool
    message: str


# =====================================================================
# Consolidated single-endpoint CRUD (POST /users/manage)
# =====================================================================
# One endpoint that dispatches create / update / delete by an
# `operation` discriminator, as an alternative to the individual REST
# verbs (which remain available). Mirrors the "one endpoint, multiple
# operations" convention used elsewhere (e.g. Instance Management's
# /columns reorder). All sub-fields reuse the exact same validation as
# the dedicated Create/Update requests.
UserOperation = Literal["create", "update", "delete"]


class ManageUserRequest(BaseModel):
    """Single CRUD envelope for POST /users/manage.

    - operation="create": `data` (CreateUserRequest shape) is required;
      `userId` must be omitted.
    - operation="update": `userId` and `data` (UpdateUserRequest shape,
      all fields optional) are required.
    - operation="delete": `userId` is required; `data` must be omitted.

    The service validates these combinations and raises a 422
    ValidationError on a mismatch, so the contract is enforced in one
    place regardless of which operation the caller picks."""

    operation: UserOperation
    userId: Optional[int] = Field(None, ge=1, description="Target user id (required for update/delete).")
    # Kept as a permissive dict and re-parsed into the specific
    # Create/Update request inside the service, so this one envelope can
    # carry either shape without a discriminated union at the edge.
    data: Optional[Dict[str, object]] = Field(
        None, description="User fields. CreateUserRequest shape for create; UpdateUserRequest shape for update; omit for delete."
    )


class ManageUserResponse(BaseModel):
    """Uniform result for POST /users/manage. For create/update, `user`
    holds the resulting user (with access); for delete, `user` is null
    and `deleted` is true with the removed user's id in `userId`."""

    operation: UserOperation
    userId: Optional[int] = None
    deleted: bool = False
    user: Optional[UserAccessResponse] = None
