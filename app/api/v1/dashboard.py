"""
Dashboard router (READ-ONLY) — HTTP concerns only: injects the DB
session, calls the service layer, returns its result. No SQLAlchemy
usage and no business logic here — same pattern as every prior domain
router. No query parameters — this endpoint aggregates across the whole
dataset, matching the task's spec exactly.

No authentication dependency is wired up yet (explicitly out of scope).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.dashboard import DashboardResponse
from app.services import dashboard_service

router = APIRouter()


@router.get("/kpis", response_model=DashboardResponse)
def get_dashboard(db: Session = Depends(get_db)) -> DashboardResponse:
    """Aggregated dashboard metrics, every value derived from a real
    SQL aggregation query — no fabricated ticket/transaction-volume
    data (see app/schemas/dashboard.py for what's deliberately omitted
    and why)."""
    return dashboard_service.get_dashboard(db)
