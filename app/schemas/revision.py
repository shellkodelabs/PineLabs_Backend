"""
Pydantic response schemas for the Revision History (read-only) API.

NOTE on `user`: app.models.revision.Revision.user_id is NOT NULL by
design (Part 3 §9 / Part 5 — "a user with revision history should not
be silently deletable out from under it"). The schema/repository/service
below are still built to handle a NULL-user row correctly (Optional
field, LEFT OUTER JOIN, null-safe mapping) per this task's explicit
requirement, but no such row can actually exist in this database today —
see app/repositories/revision_repository.py and the completion report
for the full explanation. This is a documented, deliberate gap, not an
oversight.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, field_serializer

from app.schemas.common import PaginatedResponse


class RevisionUserResponse(BaseModel):
    id: int
    name: str
    email: str


class RevisionResponse(BaseModel):
    id: int
    timestamp: datetime
    user: Optional[RevisionUserResponse] = None
    action: str
    entity: str
    target: str
    change: str

    @field_serializer("timestamp")
    def _serialize_timestamp(self, value: datetime) -> str:
        """Always emits a UTC, 'Z'-suffixed ISO-8601 string, regardless
        of what timezone the underlying datetime carries when it arrives
        from the DB driver (which reflects the active session's
        timezone, not necessarily UTC — see Part 6's completion report).
        The stored instant is unaffected either way; this only
        normalizes the display format."""
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Named per the task's schema list; implemented as a reuse of the shared
# generic pagination envelope (same pattern as every prior part).
RevisionListResponse = PaginatedResponse[RevisionResponse]
