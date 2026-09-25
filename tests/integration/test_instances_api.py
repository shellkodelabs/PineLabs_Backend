"""
API tests for Instances (Bin Series gap-analysis API #1):
  GET /api/v1/instances

Backs the frontend's BIN Series Add/Clone form "Select an instance"
dropdown. Read-only — there is no create/update/delete endpoint in this
part, so all fixture rows are inserted directly via the ORM.

Uses `api_client`/`db_session`/`act_as` (tests/integration/conftest.py)
for the authenticated path, and a local `raw_client` (TestClient(app)
with zero overrides — same pattern as tests/integration/test_auth.py)
for the unauthenticated path.

No seed data exists for `instances` (confirmed by inspection: nothing in
app/seed/ references it) — every fixture here uses synthetic
"Test Instance "-prefixed names, collision-proof regardless of whatever
real data may or may not exist in this table by the time these run.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Instance, User


def _make_user(db_session, *, name, email, role="Admin", status="Active"):
    user = User(name=name, email=email, role=role, status=status)
    db_session.add(user)
    db_session.flush()
    return user


def _make_instance(db_session, *, name, description=None, status="Active", updated_by_user_id=None):
    instance = Instance(name=name, description=description, status=status, updated_by_user_id=updated_by_user_id)
    db_session.add(instance)
    db_session.flush()
    return instance


@pytest.fixture()
def raw_client():
    """A TestClient with NO dependency overrides — real authentication
    runs for real on every request made through this. Same pattern as
    tests/integration/test_auth.py's fixture of the same name."""
    return TestClient(app)


ALPHA = {"name": "Test Instance Alpha Zone", "description": "Alpha region", "status": "Active"}
BETA = {"name": "Test Instance Beta Zone", "description": "Beta region", "status": "Inactive"}
GAMMA = {"name": "Test Instance Gamma Zone", "description": None, "status": "Active"}

FIXTURE_INSTANCES = [ALPHA, BETA, GAMMA]
SEARCH_SCOPE = "Test Instance"


@pytest.fixture()
def seeded_instances(db_session):
    for i in FIXTURE_INSTANCES:
        db_session.add(Instance(name=i["name"], description=i["description"], status=i["status"]))
    db_session.flush()


# =====================================================================
# 1. Authenticated GET succeeds / 6. response shape
# =====================================================================


def test_list_instances_returns_paginated_envelope(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": SEARCH_SCOPE})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"items", "total", "page", "pageSize"}
    assert body["total"] == len(FIXTURE_INSTANCES)
    assert body["page"] == 1
    assert body["pageSize"] == 50
    assert len(body["items"]) == len(FIXTURE_INSTANCES)

    item = body["items"][0]
    assert set(item.keys()) == {"id", "name", "description", "status", "updatedBy", "updatedAt"}


# =====================================================================
# 2. Unauthenticated GET follows existing authentication behavior
# =====================================================================


def test_unauthenticated_get_returns_401(raw_client):
    resp = raw_client.get("/api/v1/instances")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_unauthenticated_get_with_bogus_token_returns_401(raw_client):
    resp = raw_client.get("/api/v1/instances", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_PROVIDER_NOT_CONFIGURED"


# =====================================================================
# 3. Empty result set
# =====================================================================


def test_search_with_no_matches_returns_empty_result(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": "Test Instance Definitely Does Not Exist"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


# =====================================================================
# 4. Multiple instances returned
# =====================================================================


def test_multiple_instances_returned(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": SEARCH_SCOPE})
    body = resp.json()
    assert body["total"] == 3
    names = {item["name"] for item in body["items"]}
    assert names == {ALPHA["name"], BETA["name"], GAMMA["name"]}


# =====================================================================
# 5. Active and Inactive statuses preserved
# =====================================================================


def test_active_and_inactive_statuses_preserved(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": SEARCH_SCOPE})
    body = resp.json()
    by_name = {item["name"]: item for item in body["items"]}
    assert by_name[ALPHA["name"]]["status"] == "Active"
    assert by_name[BETA["name"]]["status"] == "Inactive"


def test_status_filter(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": SEARCH_SCOPE, "status": "Inactive"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == BETA["name"]


def test_invalid_status_filter_rejected(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"status": "NotARealStatus"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# =====================================================================
# 6. Response field / description / updatedBy details
# =====================================================================


def test_description_null_when_not_set(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": GAMMA["name"]})
    body = resp.json()
    assert body["items"][0]["description"] is None


def test_updated_by_resolves_actor_name(api_client, db_session):
    actor = _make_user(db_session, name="Test Instance Actor", email="test.instance.actor@example.invalid")
    _make_instance(db_session, name="Test Instance UpdatedByCheck", updated_by_user_id=actor.id)

    resp = api_client.get("/api/v1/instances", params={"search": "Test Instance UpdatedByCheck"})
    body = resp.json()
    assert body["items"][0]["updatedBy"] == "Test Instance Actor"


def test_updated_by_null_when_never_touched(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": ALPHA["name"]})
    body = resp.json()
    assert body["items"][0]["updatedBy"] is None
    assert body["items"][0]["updatedAt"] is not None


# =====================================================================
# 7. Ordering / pagination
# =====================================================================


def test_default_sort_is_by_name_ascending(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": SEARCH_SCOPE})
    body = resp.json()
    names = [item["name"] for item in body["items"]]
    assert names == sorted(names)


def test_sort_by_name_descending(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"search": SEARCH_SCOPE, "sortBy": "name", "sortOrder": "desc"})
    body = resp.json()
    names = [item["name"] for item in body["items"]]
    assert names == sorted(names, reverse=True)


def test_pagination_no_duplicates_no_gaps(api_client, seeded_instances):
    page1 = api_client.get("/api/v1/instances", params={"search": SEARCH_SCOPE, "page": 1, "pageSize": 2}).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2

    page2 = api_client.get("/api/v1/instances", params={"search": SEARCH_SCOPE, "page": 2, "pageSize": 2}).json()
    assert len(page2["items"]) == 1

    ids_seen = [i["id"] for i in page1["items"] + page2["items"]]
    assert len(ids_seen) == len(set(ids_seen)) == 3


def test_invalid_page_rejected(api_client, seeded_instances):
    resp = api_client.get("/api/v1/instances", params={"page": 0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_page_size_rejected(api_client, seeded_instances):
    too_large = api_client.get("/api/v1/instances", params={"pageSize": 500})
    assert too_large.status_code == 422
