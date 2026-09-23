"""
Service layer for the Dashboard (read-only). Orchestrates
app.repositories.dashboard_repository, maps aggregate results to
app.schemas.dashboard response schemas — no SQLAlchemy usage here.

TICKETS RESOLVED / TRANSACTION VOLUME: deliberately not fabricated. See
app/schemas/dashboard.py's module docstring for the full reasoning —
this database has no ticket/case table and no transaction-volume data,
confirmed back in Parts 1-2's frontend analysis (the frontend's own
"Tickets Resolved" chart and "Top Issuers by Volume" percentages were
pure hardcoded demo data with no real backing source even there).
"""
from typing import List, Sequence, Tuple

from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.repositories import dashboard_repository
from app.schemas.dashboard import (
    ClassificationSummary,
    DashboardActivityUser,
    DashboardIssuerSummary,
    DashboardKpis,
    DashboardRecentActivity,
    DashboardResponse,
)

RECENT_ACTIVITY_LIMIT = 10
TOP_ISSUERS_LIMIT = 10


def _build_classification_list(rows: Sequence[Tuple[str, int]]) -> List[ClassificationSummary]:
    """Pure mapping, no I/O — safe (and tested directly, see
    tests/integration/test_dashboard_api.py) for an empty `rows`, which
    simply yields an empty list rather than erroring."""
    return [ClassificationSummary(classification=c, merchantCount=n) for c, n in rows]


def _build_issuer_list(rows: Sequence[Tuple[str, int]]) -> List[DashboardIssuerSummary]:
    return [DashboardIssuerSummary(issuer=issuer, binRecordCount=count) for issuer, count in rows]


def _build_activity_list(rows: Sequence[Row]) -> List[DashboardRecentActivity]:
    items = []
    for row in rows:
        user = None
        if row.user_id is not None:
            user = DashboardActivityUser(id=row.user_id, name=row.user_name)
        items.append(
            DashboardRecentActivity(
                id=row.id,
                timestamp=row.occurred_at,
                user=user,
                action=row.action_type,
                entity=row.entity_type,
                target=row.target_label,
            )
        )
    return items


def get_dashboard(db: Session) -> DashboardResponse:
    kpis = DashboardKpis(
        binSeriesCount=dashboard_repository.count_bin_records(db),
        merchantCount=dashboard_repository.count_merchants(db),
        activeUserCount=dashboard_repository.count_active_users(db),
        revisionsLast7Days=dashboard_repository.count_revisions_last_7_days(db),
        sopSheetCount=dashboard_repository.count_sop_sheets(db),
    )

    sops_by_classification = _build_classification_list(dashboard_repository.merchants_by_classification(db))
    recent_activity = _build_activity_list(dashboard_repository.latest_revisions(db, limit=RECENT_ACTIVITY_LIMIT))
    bin_records_by_issuer = _build_issuer_list(dashboard_repository.bin_records_by_issuer(db, limit=TOP_ISSUERS_LIMIT))

    return DashboardResponse(
        kpis=kpis,
        sopsByClassification=sops_by_classification,
        recentActivity=recent_activity,
        binRecordsByIssuer=bin_records_by_issuer,
    )
