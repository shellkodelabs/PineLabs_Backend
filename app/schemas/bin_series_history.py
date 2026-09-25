"""
Pydantic response schemas for the Bin Series Version History (read-only)
API — GET /api/v1/bin-series/version-history (renamed from /history by
the API Naming task).

DISTINCT from app/schemas/revision.py's RevisionResponse (backs the
existing generic, admin-facing GET /api/v1/revisions across ALL entity
types): that schema has no before/after snapshot fields at all, and its
`action`/`entity` are the raw 4-value/6-value revisions.action_type/
entity_type vocabulary. This schema is Bin-Series-specific, scoped to
exactly the two entity types the approved frontend's Version History
panel (VersionHistoryModal.jsx) cares about (bin_record,
bin_custom_column), and maps them onto the frontend's own richer 6-way
display vocabulary (create/update/delete/upload/column/rename — see
app/services/bin_series_history_service.py's _ACTION_LABELS). Both
schemas read the SAME `revisions` table; neither is a second audit
system.

Field names follow this task's explicit naming instructions
(revisionId/action/occurredAt/actor/before/after) rather than
RevisionResponse's existing id/user/action/entity/target/change — a
deliberate, task-directed exception to matching the sibling endpoint's
exact field names, since this is a new, separate contract.

`before`/`after` are plain, already-JSON-serializable dicts pulled
straight from revisions.metadata_ (populated by
app/services/bin_service.py's/bin_custom_column_service.py's `_snapshot()`
helpers) — never reconstructed from the CURRENT bin_records/
bin_custom_columns tables. A revision created before this task shipped
has no such keys in its metadata, so `before`/`after` are simply None
for it — never fabricated from current state (see this task's explicit
"do not invent historical data for old revisions" instruction).
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_serializer


class HistoryActorResponse(BaseModel):
    """Deliberately just id + name (no email): BinTable.jsx's
    VersionHistoryModal/ChangePreviewModal only ever render `by {name}` —
    never an email anywhere in the Version History UI, unlike the
    generic RevisionResponse.user (which also carries email for its own,
    different admin-facing use case)."""

    id: int
    name: str


class BinSeriesHistoryEntryResponse(BaseModel):
    revisionId: int
    action: str
    entityType: str
    entityId: Optional[int] = None
    occurredAt: datetime
    actor: Optional[HistoryActorResponse] = None
    targetLabel: str
    changeDescription: str
    fieldsChanged: Optional[Dict[str, Any]] = None
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None

    @field_serializer("occurredAt")
    def _serialize_occurred_at(self, value: datetime) -> str:
        """Same UTC/'Z'-suffixed convention as
        app/schemas/revision.py's RevisionResponse.timestamp."""
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class BinSeriesHistoryResponse(BaseModel):
    items: List[BinSeriesHistoryEntryResponse]
    total: int
