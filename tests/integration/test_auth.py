"""
Tests for the authentication boundary (Part 14):
  app/core/auth.py — CurrentUser, AuthenticationProvider,
  UnconfiguredAuthenticationProvider, get_current_user, get_auth_provider,
  require_roles
  app/api/v1/router.py — per-router `dependencies=[Depends(get_current_user)]`
  app/api/v1/users.py — actorUserId removed, actor now from authentication

These deliberately use a RAW `TestClient(app)` (`raw_client` fixture
below), NOT the `api_client` fixture, for every test that needs to
observe REAL unauthenticated/misauthenticated behavior — `api_client`
always overrides get_current_user with a valid identity by design (see
tests/integration/conftest.py), which would defeat the purpose of these
specific tests. `raw_client` sets no overrides at all: real
authentication runs for real, against the real (unconfigured) provider,
on every request made through it.
"""
import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import auth as auth_module
from app.core.auth import (
    AuthenticationProvider,
    CurrentUser,
    get_auth_provider,
    require_roles,
)
from app.core.exceptions import ForbiddenError
from app.main import app
from app.models import Revision, User

# One representative endpoint per protected group named in the task
# ("at minimum protect: /api/v1/users/*, /api/v1/revisions,
# /api/v1/dashboard/*, /api/v1/bin-series/*, /api/v1/merchants/*,
# /api/v1/sop/*").
PROTECTED_ENDPOINTS = [
    "/api/v1/bin-series",
    "/api/v1/merchants",
    "/api/v1/sop/escalation/common",
    "/api/v1/users",
    "/api/v1/revisions",
    "/api/v1/dashboard/kpis",
]


@pytest.fixture()
def raw_client():
    """A TestClient with NO dependency overrides — real authentication
    runs for real on every request made through this."""
    return TestClient(app)


# =====================================================================
# 1-4: baseline auth behavior
# =====================================================================


def test_protected_endpoints_without_credentials_return_401(raw_client):
    for path in PROTECTED_ENDPOINTS:
        resp = raw_client.get(path)
        assert resp.status_code == 401, f"{path} did not return 401 without credentials"
        assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_protected_endpoint_with_invalid_credentials_returns_401(raw_client):
    # Well-formed "Bearer <token>" header, but no real provider exists to
    # validate ANY token against yet — always rejected.
    resp = raw_client.get("/api/v1/bin-series", headers={"Authorization": "Bearer this-is-not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_PROVIDER_NOT_CONFIGURED"


def test_protected_endpoint_with_malformed_bearer_token_returns_401(raw_client):
    # Wrong auth scheme entirely (not "Bearer") — FastAPI's HTTPBearer
    # (auto_error=False) can't parse this as bearer credentials at all,
    # so it's treated as missing, distinctly from a well-formed-but-
    # rejected token above.
    resp = raw_client.get("/api/v1/bin-series", headers={"Authorization": "TotallyNotBearer abc123"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_health_endpoint_remains_public(raw_client):
    resp = raw_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# =====================================================================
# 5-6: the provider boundary and the test override mechanism
# =====================================================================


def test_get_current_user_resolves_via_provider_boundary(raw_client):
    """Overrides ONLY get_auth_provider (not get_current_user itself) to
    prove get_current_user's own plumbing — extract the bearer token,
    call provider.authenticate(token, db), return its result — works
    end-to-end, without needing a real identity provider. The request
    reaches the real route/service/repository, proving the resolved
    CurrentUser was genuinely accepted by the boundary, not just
    constructed in isolation."""

    class _FakeSucceedingProvider(AuthenticationProvider):
        def authenticate(self, token: str, db: Session) -> CurrentUser:
            assert token == "a-token-value"
            return CurrentUser(id=1, name="Fake Resolved User", email="fake@example.invalid", role="Admin", status="Active")

    app.dependency_overrides[get_auth_provider] = lambda: _FakeSucceedingProvider()
    try:
        resp = raw_client.get("/api/v1/bin-series", headers={"Authorization": "Bearer a-token-value"})
    finally:
        app.dependency_overrides.pop(get_auth_provider, None)

    assert resp.status_code == 200


def test_dependency_override_grants_access_to_protected_endpoint(api_client):
    """api_client (tests/integration/conftest.py) overrides
    get_current_user — confirms that override actually takes effect: a
    protected endpoint that 401s for an unauthenticated raw client (see
    test_protected_endpoints_without_credentials_return_401) succeeds
    normally here, via the SAME override mechanism every other test file
    in this suite relies on."""
    resp = api_client.get("/api/v1/bin-series")
    assert resp.status_code == 200


# =====================================================================
# 7: no hardcoded identity in production code
# =====================================================================


def test_production_auth_never_returns_a_hardcoded_identity(raw_client):
    """Behavioral proof, not just a source-code claim: hitting the REAL
    (unconfigured) provider boundary with a syntactically valid bearer
    token never succeeds and never returns any CurrentUser — there is no
    fallback/default identity anywhere in the real (non-overridden) code
    path, for any token value."""
    resp = raw_client.get("/api/v1/bin-series", headers={"Authorization": "Bearer any-token-whatsoever"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_PROVIDER_NOT_CONFIGURED"


def test_no_hardcoded_user_id_literal_in_auth_source():
    """Belt-and-suspenders source check alongside the behavioral test
    above: the Part 4 stub this module superseded literally constructed
    `CurrentUser(id=0, ...)` as a hardcoded fallback identity; confirm
    that CODE pattern is gone. (The module's docstring legitimately
    MENTIONS "id=0" as historical documentation of the old stub's
    behavior — checking for the specific construction call, not the bare
    substring, avoids that false positive.)"""
    source = inspect.getsource(auth_module)
    assert "CurrentUser(id=0" not in source
    assert "CurrentUser(id=0," not in source.replace(" ", "")


# =====================================================================
# 8-13: actorUserId removed; audit uses the authenticated actor
# =====================================================================


def test_user_post_ignores_actor_user_id_and_uses_authenticated_actor(api_client, db_session, act_as):
    """Covers items 8 and 11: passing ?actorUserId=<someone else> to
    POST /users has zero effect — the resulting revision is attributed
    to whoever is AUTHENTICATED, never the query param."""
    real_actor = User(name="Test Auth RealActorCreate", email="test.auth.realactorcreate@example.invalid", role="Admin", status="Active")
    decoy = User(name="Test Auth DecoyCreate", email="test.auth.decoycreate@example.invalid", role="Admin", status="Active")
    db_session.add_all([real_actor, decoy])
    db_session.flush()

    act_as(real_actor)
    resp = api_client.post(
        "/api/v1/users",
        params={"actorUserId": decoy.id},  # must be completely ignored
        json={
            "name": "Test Auth PostIgnoresActorParam",
            "email": "test.auth.postignoresactorparam@example.invalid",
            "role": "Support Agent",
        },
    )
    assert resp.status_code == 201
    new_id = resp.json()["id"]

    revision = db_session.execute(
        select(Revision).where(Revision.entity_type == "user", Revision.entity_id == new_id)
    ).scalar_one()
    assert revision.user_id == real_actor.id
    assert revision.user_id != decoy.id


def test_user_put_ignores_actor_user_id_and_uses_authenticated_actor(api_client, db_session, act_as):
    """Covers items 9 and 12."""
    target = User(name="Test Auth PutTarget", email="test.auth.puttarget@example.invalid", role="Support Agent", status="Invited")
    real_actor = User(name="Test Auth RealActorUpdate", email="test.auth.realactorupdate@example.invalid", role="Admin", status="Active")
    decoy = User(name="Test Auth DecoyUpdate", email="test.auth.decoyupdate@example.invalid", role="Admin", status="Active")
    db_session.add_all([target, real_actor, decoy])
    db_session.flush()

    act_as(real_actor)
    resp = api_client.put(
        f"/api/v1/users/{target.id}",
        params={"actorUserId": decoy.id},  # must be completely ignored
        json={"status": "Active"},
    )
    assert resp.status_code == 200

    revision = db_session.execute(
        select(Revision).where(Revision.entity_type == "user", Revision.entity_id == target.id)
    ).scalar_one()
    assert revision.user_id == real_actor.id
    assert revision.user_id != decoy.id


def test_user_delete_ignores_actor_user_id_and_uses_authenticated_actor(api_client, db_session, act_as):
    """Covers items 10 and 13."""
    target = User(name="Test Auth DeleteTarget", email="test.auth.deletetarget@example.invalid", role="Support Agent", status="Active")
    real_actor = User(name="Test Auth RealActorDelete", email="test.auth.realactordelete@example.invalid", role="Admin", status="Active")
    decoy = User(name="Test Auth DecoyDelete", email="test.auth.decoydelete@example.invalid", role="Admin", status="Active")
    db_session.add_all([target, real_actor, decoy])
    db_session.flush()
    target_id = target.id

    act_as(real_actor)
    resp = api_client.delete(f"/api/v1/users/{target_id}", params={"actorUserId": decoy.id})  # must be ignored
    assert resp.status_code == 200

    revision = db_session.execute(
        select(Revision).where(Revision.entity_type == "user", Revision.entity_id == target_id)
    ).scalar_one()
    assert revision.user_id == real_actor.id
    assert revision.user_id != decoy.id


# =====================================================================
# 14-18: unauthenticated access is denied for every protected group
# =====================================================================


def test_unauthenticated_cannot_access_revisions(raw_client):
    assert raw_client.get("/api/v1/revisions").status_code == 401


def test_unauthenticated_cannot_access_dashboard(raw_client):
    assert raw_client.get("/api/v1/dashboard/kpis").status_code == 401


def test_unauthenticated_cannot_access_bin_apis(raw_client):
    assert raw_client.get("/api/v1/bin-series").status_code == 401
    assert raw_client.get("/api/v1/bin-series/resolve", params={"binIin": "401288", "merchantPrefix": "001"}).status_code == 401


def test_unauthenticated_cannot_access_merchant_apis(raw_client):
    assert raw_client.get("/api/v1/merchants").status_code == 401
    assert raw_client.get("/api/v1/merchants/1/sheets").status_code == 401


def test_unauthenticated_cannot_access_sop_apis(raw_client):
    assert raw_client.get("/api/v1/merchants/1/sheets/block").status_code == 401
    assert raw_client.get("/api/v1/sop/escalation/common").status_code == 401


# =====================================================================
# 19: token contents never leak into an error response
# =====================================================================


def test_auth_failure_does_not_expose_token_contents(raw_client):
    secret_looking_token = "super-secret-token-value-should-never-appear-in-response-xyz123"
    resp = raw_client.get("/api/v1/bin-series", headers={"Authorization": f"Bearer {secret_looking_token}"})
    assert resp.status_code == 401
    assert secret_looking_token not in resp.text


# =====================================================================
# 20: require_roles authorization helper
# =====================================================================


def test_require_roles_rejects_user_without_required_role():
    dependency = require_roles("Admin", "Support Lead")
    support_agent = CurrentUser(id=1, name="Test Auth SupportAgent", email="x@example.invalid", role="Support Agent", status="Active")

    with pytest.raises(ForbiddenError) as exc_info:
        dependency(current_user=support_agent)

    assert exc_info.value.code == "INSUFFICIENT_ROLE"
    assert exc_info.value.status_code == 403


def test_require_roles_allows_user_with_required_role():
    dependency = require_roles("Admin", "Support Lead")
    admin = CurrentUser(id=1, name="Test Auth Admin", email="x@example.invalid", role="Admin", status="Active")

    result = dependency(current_user=admin)

    assert result is admin
