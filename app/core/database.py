"""
SQLAlchemy foundation: engine, session factory, declarative base, and the
`get_db` FastAPI dependency.

No application models are declared here (see app/models/, populated in
Part 5) and no tables are created by this module — schema changes are
managed exclusively through Alembic migrations (see migrations/).
"""
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# pool_pre_ping avoids handing out dead connections after DB restarts/idle
# timeouts — cheap insurance, not a feature addition.
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


class Base(DeclarativeBase):
    """Declarative base class for all ORM models (SQLAlchemy 2.0 style)."""

    pass


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a request-scoped SQLAlchemy session.

    Commits once, after the request handler returns successfully, and
    rolls back on any exception (including domain exceptions such as
    NotFoundError/ValidationError/ConflictError) so a partially-modified
    session never leaks into the response or a later request. Always
    closes the session afterward.

    Added in Part 10: every endpoint before this part was read-only, so
    the absence of a commit was invisible — SELECTs work fine on an
    uncommitted session. Part 10 is the first to add real writes
    (POST/PUT/DELETE on /users) through this exact dependency in
    production code, and without this fix every write would silently
    roll back the moment the session closed at the end of the request,
    even though the response would look like a success. This is
    session-management infrastructure, not a schema or model change —
    no table, column, or ORM model was touched.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
