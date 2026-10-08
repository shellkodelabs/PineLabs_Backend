"""
Pydantic schemas for the Instance Management API.

Field names are camelCase (matching the frontend convention used across
this project's other domains, e.g. bin_series.py); the underlying
SQLAlchemy model (app/models/instance.py) uses snake_case columns. The
translation is done explicitly in the service layer
(app/services/instance_service.py), field by field — same division of
responsibility as bin_service (service maps ORM objects to response
schemas), not via Pydantic alias machinery.

Fields mirror the Instance Management UI columns: name (INSTANCE),
issuerCount (ISSUERS), status (STATUS), and updatedBy/updatedAt (UPDATED
BY). No `description` field — the frontend has no description column on
this screen. `status` is server-managed: defaults to "Active" on create
and can be changed via the normal partial update (activate/deactivate).
`issuerCount`/`updatedBy`/`updatedAt` are read-only response fields
resolved server-side — never accepted from a request body. `issuerCount`
is a DERIVED count (issuers grouped under the instance); until issuers
are linked to instances it is 0.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.instance import COLUMN_TYPE_VALUES, STATUS_VALUES

# Spelled-out Literal (not Literal[SOME_TUPLE], whose handling is
# fragile/version-dependent across typing + Pydantic). The runtime
# assertion keeps it in lockstep with the model's single source of truth
# (and the DB CHECK constraint).
Status = Literal["Active", "Inactive"]
ColumnType = Literal["text", "number", "date", "dropdown"]

assert set(Status.__args__) == set(STATUS_VALUES), (
    "instance schema Status literal is out of sync with model STATUS_VALUES"
)
assert set(ColumnType.__args__) == set(COLUMN_TYPE_VALUES), (
    "instance schema ColumnType literal is out of sync with model COLUMN_TYPE_VALUES"
)


def _reject_blank(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank.")
    return stripped


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    """Blank/whitespace -> None, otherwise trimmed. Used for optional free
    text (ticketNumber) so an empty string is never stored."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class InstanceResponse(BaseModel):
    id: int
    name: str
    issuerCount: int = 0
    status: str
    ticketNumber: Optional[str] = None
    # Values for the user-defined custom columns, keyed by column key
    # (see InstanceColumn). Always present (possibly empty).
    customFields: Dict[str, Any] = Field(default_factory=dict)
    updatedBy: Optional[str] = None
    updatedAt: Optional[datetime] = None


class CreateInstanceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Instance name, e.g. 'North Zone'. Unique.")
    # Optional on create; defaults to "Active" in the service when omitted.
    status: Optional[Status] = Field(None, description="Defaults to 'Active' if omitted.")
    ticketNumber: Optional[str] = Field(
        None, max_length=100, description="Optional reference ticket, e.g. 'INC1234'."
    )
    # NOTE: create does NOT capture Revised By / Reviewer — those are an
    # EDIT/DELETE audit concern only (see UpdateInstanceRequest and the
    # instance_edits / instance_deletions audit tables).
    # Values for custom columns, keyed by column key. Validated/coerced
    # against the column definitions by the service layer (required cols
    # enforced; skipped optional cols default to their default/NA).
    customFields: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _reject_blank(value, "name")

    @field_validator("ticketNumber")
    @classmethod
    def _normalize_optional(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)


class UpdateInstanceRequest(BaseModel):
    """All fields optional — a partial update. A field omitted from the
    JSON body is left unchanged. `status` doubles as the
    activate/deactivate control (set to 'Active' or 'Inactive')."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[Status] = None
    ticketNumber: Optional[str] = Field(None, max_length=100)
    # Audit trail. On an interactive edit the UI sends both (mandatory in
    # the form); omitted = unchanged. When provided they must be non-blank.
    revisedBy: Optional[str] = Field(None, min_length=1, max_length=255)
    reviewer: Optional[str] = Field(None, min_length=1, max_length=255)
    # If provided, custom column values to merge in (validated/coerced by
    # the service). Omitted = leave all custom fields unchanged.
    customFields: Optional[Dict[str, Any]] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank(value, "name")

    @field_validator("ticketNumber")
    @classmethod
    def _normalize_optional(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator("revisedBy")
    @classmethod
    def _validate_revised_by(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank(value, "revisedBy")

    @field_validator("reviewer")
    @classmethod
    def _validate_reviewer(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank(value, "reviewer")


class DeleteInstanceRequest(BaseModel):
    """Audit trail captured in the Delete Instance dialog — all mandatory.
    Recorded in instance_deletions before the instance row is removed."""

    ticketNumber: str = Field(..., min_length=1, max_length=100, description="Ticket this deletion relates to.")
    revisedBy: str = Field(..., min_length=1, max_length=255, description="Who revised (performed) the deletion.")
    reviewer: str = Field(..., min_length=1, max_length=255, description="Who reviewed the deletion.")

    @field_validator("ticketNumber")
    @classmethod
    def _validate_ticket(cls, value: str) -> str:
        return _reject_blank(value, "ticketNumber")

    @field_validator("revisedBy")
    @classmethod
    def _validate_revised_by(cls, value: str) -> str:
        return _reject_blank(value, "revisedBy")

    @field_validator("reviewer")
    @classmethod
    def _validate_reviewer(cls, value: str) -> str:
        return _reject_blank(value, "reviewer")


class DeleteInstanceResponse(BaseModel):
    id: int
    deleted: bool


class InstanceStatsResponse(BaseModel):
    """Header cards on the Instance Management screen."""

    totalInstances: int
    activeInstances: int
    # Derived count of issuers grouped across all instances. Issuers are
    # not yet linked to instances, so this is 0 for now (see
    # instance_service). Kept in the contract so the UI card can bind to
    # it immediately.
    issuersGrouped: int


class ImportInstanceResponse(BaseModel):
    """Result of a bulk import (single upsert-by-name flow — see
    instance_service.import_instances). `created` counts rows whose
    instance name didn't exist yet; `updated` counts rows that matched
    an existing instance and had its ticket number changed in place."""

    created: int
    updated: int
    total: int


# =====================================================================
# Custom column definition schemas
# =====================================================================
class InstanceColumnResponse(BaseModel):
    id: int
    key: str
    label: str
    type: str
    required: bool
    defaultValue: Optional[str] = None
    options: Optional[List[str]] = None
    afterKey: Optional[str] = None
    sortOrder: int
    # Audit trail for the last add/rename of this column.
    ticketNumber: Optional[str] = None
    revisedBy: Optional[str] = None
    reviewer: Optional[str] = None


class CreateInstanceColumnRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=100, description="Column header text. Must be unique.")
    type: ColumnType = Field("text", description="text | number | date | dropdown")
    required: bool = Field(False, description="If true, the UI shows a * and the value is mandatory.")
    defaultValue: Optional[str] = Field(
        None, description="Applied to existing instances when the column is added; also the optional-cell fallback."
    )
    options: Optional[List[str]] = Field(
        None, description="Allowed choices when type == 'dropdown' (ignored otherwise)."
    )
    afterKey: Optional[str] = Field(
        None,
        description=(
            "Position: this column is placed immediately AFTER the column with this key "
            "(a built-in key like 'name'/'ticketNumber'/'issuerCount'/'status'/'updatedBy', "
            "or another custom column's key). Omit / null = at the very beginning."
        ),
    )
    # Audit trail — mandatory when adding a column (the Add Column dialog
    # requires all three). Persisted on the column row.
    ticketNumber: str = Field(..., min_length=1, max_length=100, description="Ticket this column change relates to.")
    revisedBy: str = Field(..., min_length=1, max_length=255, description="Who revised the column.")
    reviewer: str = Field(..., min_length=1, max_length=255, description="Who reviewed the change.")

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        return _reject_blank(value, "label")

    @field_validator("defaultValue")
    @classmethod
    def _normalize_default(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_optional_text(value)

    @field_validator("ticketNumber")
    @classmethod
    def _validate_ticket(cls, value: str) -> str:
        return _reject_blank(value, "ticketNumber")

    @field_validator("revisedBy")
    @classmethod
    def _validate_revised_by(cls, value: str) -> str:
        return _reject_blank(value, "revisedBy")

    @field_validator("reviewer")
    @classmethod
    def _validate_reviewer(cls, value: str) -> str:
        return _reject_blank(value, "reviewer")


class UpdateInstanceColumnRequest(BaseModel):
    """Partial update of a column definition. `label` renames the header
    (the underlying key never changes). `afterKey` repositions it.
    Omitted fields are unchanged."""

    label: Optional[str] = Field(None, min_length=1, max_length=100)
    required: Optional[bool] = None
    defaultValue: Optional[str] = None
    options: Optional[List[str]] = None
    afterKey: Optional[str] = Field(
        None, description="Reposition after this column key (see CreateInstanceColumnRequest.afterKey)."
    )
    # Audit trail — the Rename Column dialog sends all three; omitted =
    # unchanged. When provided they must be non-blank.
    ticketNumber: Optional[str] = Field(None, min_length=1, max_length=100)
    revisedBy: Optional[str] = Field(None, min_length=1, max_length=255)
    reviewer: Optional[str] = Field(None, min_length=1, max_length=255)

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank(value, "label")

    @field_validator("ticketNumber")
    @classmethod
    def _validate_ticket(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank(value, "ticketNumber")

    @field_validator("revisedBy")
    @classmethod
    def _validate_revised_by(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank(value, "revisedBy")

    @field_validator("reviewer")
    @classmethod
    def _validate_reviewer(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _reject_blank(value, "reviewer")


class ReorderInstanceColumnsRequest(BaseModel):
    """New display order for the WHOLE Instance Management table, as the
    full ordered list of column KEYS (built-ins and custom together),
    exactly as they should appear left-to-right — i.e. the `key` of every
    entry in the `layout` of GET /columns, in the new order.

    The service rewrites each built-in's stored sort_order and each custom
    column's after_key from this single list, so any arrangement (a custom
    column before the Instance column, built-ins reordered, etc.) is
    persisted in one call. Must contain exactly the current set of column
    keys — no more, no fewer."""

    orderedKeys: List[str] = Field(..., min_length=1)


class DeleteInstanceColumnRequest(BaseModel):
    """Audit trail captured in the Delete Column dialog — all mandatory.
    Recorded in instance_column_deletions before the column row is
    removed."""

    ticketNumber: str = Field(..., min_length=1, max_length=100, description="Ticket this deletion relates to.")
    revisedBy: str = Field(..., min_length=1, max_length=255, description="Who revised (performed) the deletion.")
    reviewer: str = Field(..., min_length=1, max_length=255, description="Who reviewed the deletion.")

    @field_validator("ticketNumber")
    @classmethod
    def _validate_ticket(cls, value: str) -> str:
        return _reject_blank(value, "ticketNumber")

    @field_validator("revisedBy")
    @classmethod
    def _validate_revised_by(cls, value: str) -> str:
        return _reject_blank(value, "revisedBy")

    @field_validator("reviewer")
    @classmethod
    def _validate_reviewer(cls, value: str) -> str:
        return _reject_blank(value, "reviewer")


class DeleteInstanceColumnResponse(BaseModel):
    id: int
    deleted: bool


class LayoutColumnResponse(BaseModel):
    """One entry in the full, merged, ordered column layout of the
    Instance Management table (built-ins + custom, interleaved)."""

    key: str
    label: str
    builtin: bool
    # For custom columns only (null on built-ins):
    id: Optional[int] = None
    type: Optional[str] = None
    required: Optional[bool] = None
    options: Optional[List[str]] = None


class InstanceColumnsResponse(BaseModel):
    """The single payload for GET /instances/columns — both views of the
    Instance Management columns in one round-trip:

    - `definitions`: the user-defined CUSTOM column definitions only (no
      built-ins). Drives the Add Column duplicate-label check, the
      required-* markers, the edit forms, and the import/export header
      mapping.
    - `layout`: the full merged, ordered layout (built-ins + custom
      interleaved by position) the table renders left-to-right from.

    Previously these were two endpoints (/columns and /columns/layout);
    merged into one so the frontend fetches the columns once."""

    definitions: List[InstanceColumnResponse]
    layout: List[LayoutColumnResponse]



# =====================================================================
# Instance -> issuers drill-down (GET /instances/{id}/issuers)
# =====================================================================
class InstanceIssuersResponse(BaseModel):
    """The issuers grouped under one instance, for the Create User
    screen's instance->issuer selection.

    `issuers` is the DISTINCT set of issuer names found on the BIN
    records whose `instance` equals this instance's name (there is no
    stored instance->issuer link in the schema — see
    instance_repository.list_issuers_for_instance_name). Sorted,
    case-insensitive. May be empty if no BIN records reference this
    instance yet."""

    instanceId: int
    instanceName: str
    issuers: List[str] = Field(default_factory=list)
    total: int