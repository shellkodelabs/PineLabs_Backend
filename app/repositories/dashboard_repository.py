"""
Repository for dashboard aggregate queries — SQLAlchemy queries only. No
business interpretation (deciding what "sopSheetCount" should mean, or
that ticket/volume data doesn't exist) lives here — that belongs to
app/services/dashboard_service.py. Every function here is a single SQL
aggregation (COUNT/GROUP BY/ORDER BY/LIMIT) — nothing loads a full table
into Python to count or group it there.

Index usage:
  - count_active_users: ix_users_status
  - count_revisions_last_7_days: ix_revisions_occurred_at
  - merchants_by_classification: ix_merchants_classification
  - latest_revisions: ix_revisions_occurred_at (DESC, matches the query's
    own ORDER BY exactly)
  - bin_records_by_issuer: no dedicated index on `issuer` alone (only
    the trigram expression index covering issuer+program together) — a
    GROUP BY over 611 rows is trivial regardless; not adding a new index
    since schema changes are out of scope for this part.
"""
from typing import List, Sequence, Tuple

from sqlalchemy import func, select, text
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models.bin_record import BinRecord
from app.models.merchant import Merchant
from app.models.revision import Revision
from app.models.sop import SopSheet
from app.models.user import User


def count_bin_records(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(BinRecord)) or 0


def count_merchants(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Merchant)) or 0


def count_active_users(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User).where(User.status == "Active")) or 0


def count_sop_sheets(session: Session) -> int:
    """Total rows in sop_sheets — merchant-owned sheets AND the shared
    default sheet together. See app/schemas/dashboard.py's DashboardKpis
    docstring for the documented interpretation."""
    return session.scalar(select(func.count()).select_from(SopSheet)) or 0


def count_revisions_last_7_days(session: Session) -> int:
    """The 7-day window is evaluated entirely by PostgreSQL's own now()
    at query time — never Python's local clock (per the task's explicit
    instruction)."""
    stmt = (
        select(func.count())
        .select_from(Revision)
        .where(text("occurred_at >= now() - INTERVAL '7 days'"))
    )
    return session.scalar(stmt) or 0


def merchants_by_classification(session: Session) -> List[Tuple[str, int]]:
    stmt = (
        select(Merchant.classification, func.count())
        .group_by(Merchant.classification)
        .order_by(Merchant.classification)
    )
    return list(session.execute(stmt).all())


def latest_revisions(session: Session, *, limit: int = 10) -> Sequence[Row]:
    """The `limit` most recent revisions, newest first (occurred_at DESC,
    id DESC as a deterministic tiebreaker), LEFT OUTER JOINed to users so
    a (currently impossible, per Part 11's finding — revisions.user_id is
    NOT NULL) userless revision would still be returned rather than
    silently dropped by an INNER JOIN. Only the columns actually needed
    for the dashboard's smaller activity-user shape are selected (no
    email — see DashboardActivityUser)."""
    stmt = (
        select(
            Revision.id,
            Revision.occurred_at,
            Revision.action_type,
            Revision.entity_type,
            Revision.target_label,
            User.id.label("user_id"),
            User.name.label("user_name"),
        )
        .select_from(Revision)
        .outerjoin(User, User.id == Revision.user_id)
        .order_by(Revision.occurred_at.desc(), Revision.id.desc())
        .limit(limit)
    )
    return session.execute(stmt).all()


def bin_records_by_issuer(session: Session, *, limit: int = 10) -> List[Tuple[str, int]]:
    """Top `limit` issuers by BIN RECORD COUNT (not transaction volume —
    no such data exists), most records first."""
    stmt = (
        select(BinRecord.issuer, func.count())
        .group_by(BinRecord.issuer)
        .order_by(func.count().desc(), BinRecord.issuer)
        .limit(limit)
    )
    return list(session.execute(stmt).all())
