"""
Pydantic response schemas for the Dashboard (read-only) API.

DELIBERATELY ABSENT: `ticketsResolved` and any "volume" field. Per
Parts 1-2's frontend analysis, this database has no ticket/case concept
and no transaction-volume data anywhere — the frontend's own "Tickets
Resolved" chart and "Top Issuers by Volume" percentages were confirmed
to be pure hardcoded demo data with no real backing source. Adding
either here (even as `null`) would still require inventing a field name
and shape for a concept this backend has no data for; the chosen
approach (the task's own "Preferred" option) is to omit it entirely
rather than publish a field that always returns a fabricated/null value.
`binRecordsByIssuer` is offered instead, explicitly named and shaped as
a count of BIN records — never called "volume".
"""
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, field_serializer


class DashboardKpis(BaseModel):
    """
    sopSheetCount = COUNT(*) FROM sop_sheets — includes BOTH
    merchant-owned sheets AND the shared/common escalation sheet
    (merchant_id IS NULL). Per the task's explicit instruction ("use the
    total number of rows in sop_sheets") and its own example value
    (1533 = 1532 merchant-owned + 1 shared).
    """

    binSeriesCount: int
    merchantCount: int
    activeUserCount: int
    revisionsLast7Days: int
    sopSheetCount: int


class ClassificationSummary(BaseModel):
    classification: str
    merchantCount: int


class DashboardActivityUser(BaseModel):
    """Deliberately smaller than RevisionUserResponse (Part 11) — the
    task's recentActivity example shows only {id, name}, no email."""

    id: int
    name: str


class DashboardRecentActivity(BaseModel):
    id: int
    timestamp: datetime
    user: Optional[DashboardActivityUser] = None
    action: str
    entity: str
    target: str

    @field_serializer("timestamp")
    def _serialize_timestamp(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DashboardIssuerSummary(BaseModel):
    """A factual count of BIN records per issuer — NOT transaction
    volume. No such data exists in this database."""

    issuer: str
    binRecordCount: int


class DashboardResponse(BaseModel):
    kpis: DashboardKpis
    sopsByClassification: List[ClassificationSummary]
    recentActivity: List[DashboardRecentActivity]
    binRecordsByIssuer: List[DashboardIssuerSummary]
