"""
Fixtures for database-backed integration tests.

These tests run against the real PostgreSQL database configured via
DATABASE_URL — Postgres-specific behavior (CHECK constraints, the
partial unique index, JSONB) cannot be faithfully exercised against
SQLite or a mock, so a real connection is required here.

Each test runs inside an outer transaction with the ORM Session joined
to it via a SAVEPOINT (`join_transaction_mode="create_savepoint"`), and
the outer transaction is rolled back after every test — including tests
that call session.flush()/commit() internally. This means the suite
creates NO persistent data; per Part 5's instructions, seeding real data
is out of scope (Part 6).

`api_client` (added in Part 7) builds on `db_session` for API-level
tests: it overrides the app's `get_db` dependency so that HTTP requests
made through the TestClient run against the EXACT SAME session/
transaction as the test itself. Without this, a request would open a
brand-new database connection via SessionLocal() and would not see any
uncommitted data the test just inserted (transactions are isolated
across connections) — it would only see whatever happens to already be
committed (e.g. the Part 6 seed data, or nothing on a fresh DB). Per the
Part 7 instructions ("do not depend on production data being present
unless the existing test setup explicitly supports that"), API tests
insert their own fixture rows via `db_session`/`api_client` rather than
relying on the seed data being present.

Part 14 (authentication): every /api/v1/* router now requires a valid
authenticated caller (see app/api/v1/router.py). `api_client` therefore
ALSO overrides `get_current_user` by default, with a real, synthetic
user row inserted into the same `db_session` — this is the FastAPI-
recommended way to test protected endpoints (override the DEPENDENCY in
tests), and is explicitly NOT a production bypass: nothing in
app/core/auth.py itself is weakened, disabled, or made overridable via
any header/parameter a real client could send. Tests that need control
over WHO the authenticated caller specifically is (e.g. audit-logging
actor-vs-target tests) use the `act_as` fixture below to swap the
override to a different real user mid-test. Tests that need to exercise
the UNAUTHENTICATED path itself (tests/integration/test_auth.py) use a
plain `TestClient(app)` instead of `api_client`, deliberately bypassing
none of these overrides.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers all models against Base.metadata
from app.core.auth import CurrentUser, get_current_user
from app.core.database import engine, get_db
from app.main import app
from app.models.user import User


@pytest.fixture()
def db_session():
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


def _current_user_from(user: User) -> CurrentUser:
    return CurrentUser(id=user.id, name=user.name, email=user.email, role=user.role, status=user.status)


@pytest.fixture()
def api_client(db_session):
    """TestClient whose requests are served by `db_session` (see the
    module docstring) and authenticated by default as a real, synthetic
    "Test Session User" row inserted into that same session."""

    default_user = User(
        name="Test Session User", email="test.session.user@example.invalid", role="Admin", status="Active"
    )
    db_session.add(default_user)
    db_session.flush()

    def _override_get_db():
        yield db_session

    def _override_get_current_user():
        return _current_user_from(default_user)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def act_as(api_client):
    """Returns a function to swap WHO `api_client` is authenticated as,
    for the rest of the current test — call `act_as(some_user_row)`
    before making requests that need a SPECIFIC authenticated identity
    (e.g. to control who the audit `actor` is), rather than the generic
    default `api_client` fixture user."""

    def _act_as(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: _current_user_from(user)

    return _act_as
