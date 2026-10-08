"""
API tests for User Management (Part 10; actor/audit wiring Part 13;
authentication Part 14):
  GET    /api/v1/users
  GET    /api/v1/users/{userId}
  POST   /api/v1/users
  PUT    /api/v1/users/{userId}
  DELETE /api/v1/users/{userId}

Same hybrid strategy as Parts 7-9: this database already holds 8 real
seeded users, so LIST/search/filter tests scope with a synthetic
"Test User " name prefix (collision-proof against the real seed and
against every other test in this file, since each test gets its own
isolated, rolled-back transaction). CREATE/UPDATE/DELETE tests use their
own synthetic users and merchants exclusively — none of them touch or
depend on the real seeded users at all, since these are destructive/
mutating operations and the task explicitly asks for deterministic,
non-seed-dependent test data here.

`api_client` (tests/integration/conftest.py) routes every HTTP request
through the SAME transactional session as this test's own fixture
inserts, so writes made via POST/PUT/DELETE are visible to subsequent
calls within the same test and fully rolled back at teardown. It also
authenticates every request by default as a synthetic "Test Session
User" (Part 14).

Part 14: the `actorUserId` query parameter has been REMOVED — the actor
for a write is now whoever is authenticated (see app/api/v1/users.py).
Tests that need a SPECIFIC actor (distinct from api_client's generic
default user) use the `act_as` fixture to authenticate as that user
before making the request — see tests/integration/conftest.py.
Dedicated audit-revision-content tests (what gets written to
`revisions`, atomicity, actor-vs-target semantics) live in
tests/integration/test_audit_logging.py, not here — this file continues
to focus on the CRUD behavior itself. actorUserId-specific removal
tests live in tests/integration/test_auth.py.
"""
import pytest
from sqlalchemy import select

from app.models import Merchant, Revision, SopSheet, User, UserSopSheetAccess

SEARCH_SCOPE = "Test User"


def _make_user(db_session, *, name, email, mobile=None, role="SME", status="Active"):
    user = User(name=name, email=email, mobile=mobile, role=role, status=status)
    db_session.add(user)
    db_session.flush()
    return user


def _make_merchant_with_sheets(db_session, *, name, sheet_specs):
    """sheet_specs: list of (key, display_name) tuples."""
    merchant = Merchant(name=name, classification="Reward Card")
    db_session.add(merchant)
    db_session.flush()
    for key, sheet_name in sheet_specs:
        db_session.add(SopSheet(merchant_id=merchant.id, key=key, name=sheet_name))
    db_session.flush()
    return merchant


@pytest.fixture()
def actor(db_session):
    """The user authenticated (via `act_as`) as having performed each
    write in this file's tests."""
    return _make_user(db_session, name="Test User Actor", email="test.user.actor@example.invalid", role="Admin", status="Active")


@pytest.fixture()
def seeded_users(db_session):
    return [
        _make_user(db_session, name="Test User Alpha", email="test.user.alpha@example.invalid", mobile="9111111111", role="Admin", status="Active"),
        _make_user(db_session, name="Test User Beta", email="test.user.beta@example.invalid", mobile="9222222222", role="Admin", status="Active"),
        _make_user(db_session, name="Test User Gamma", email="test.user.gamma@example.invalid", mobile="9333333333", role="SME", status="Inactive"),
        _make_user(db_session, name="Test User Delta", email="test.user.delta@example.invalid", mobile="9444444444", role="Viewer", status="Invited"),
        _make_user(db_session, name="Test User Epsilon", email="test.user.epsilon@example.invalid", mobile="9555555555", role="SME", status="Active"),
    ]


@pytest.fixture()
def merchant_alpha(db_session):
    """Sheets: block, poc."""
    return _make_merchant_with_sheets(
        db_session, name="Test Merchant UserApiAlpha", sheet_specs=[("block", "Block"), ("poc", "POC")]
    )


@pytest.fixture()
def merchant_beta(db_session):
    """Sheets: block, activation (note: "activation" does NOT exist for
    merchant_alpha — used to prove cross-merchant sheet grants are blocked)."""
    return _make_merchant_with_sheets(
        db_session, name="Test Merchant UserApiBeta", sheet_specs=[("block", "Block"), ("activation", "Activation")]
    )


# =====================================================================
# LIST — GET /api/v1/users
# =====================================================================


def test_list_users(api_client, seeded_users):
    resp = api_client.get("/api/v1/users", params={"search": SEARCH_SCOPE})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"items", "total", "page", "pageSize"}
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["pageSize"] == 50

    item = body["items"][0]
    assert set(item.keys()) == {"id", "name", "email", "mobile", "role", "status", "lastActiveAt"}


def test_list_users_pagination(api_client, seeded_users):
    page1 = api_client.get("/api/v1/users", params={"search": SEARCH_SCOPE, "page": 1, "pageSize": 2}).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2

    page2 = api_client.get("/api/v1/users", params={"search": SEARCH_SCOPE, "page": 2, "pageSize": 2}).json()
    assert len(page2["items"]) == 2

    page3 = api_client.get("/api/v1/users", params={"search": SEARCH_SCOPE, "page": 3, "pageSize": 2}).json()
    assert len(page3["items"]) == 1

    ids = [i["id"] for i in page1["items"] + page2["items"] + page3["items"]]
    assert len(ids) == len(set(ids)) == 5


def test_search_by_name(api_client, seeded_users):
    resp = api_client.get("/api/v1/users", params={"search": "Test User Alpha"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Test User Alpha"


def test_search_by_email(api_client, seeded_users):
    resp = api_client.get("/api/v1/users", params={"search": "test.user.beta@example.invalid"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Test User Beta"


def test_search_by_mobile(api_client, seeded_users):
    resp = api_client.get("/api/v1/users", params={"search": "9333333333"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Test User Gamma"


def test_role_filter(api_client, seeded_users):
    resp = api_client.get("/api/v1/users", params={"search": SEARCH_SCOPE, "role": "SME"})
    body = resp.json()
    assert body["total"] == 2
    names = {i["name"] for i in body["items"]}
    assert names == {"Test User Gamma", "Test User Epsilon"}


def test_status_filter(api_client, seeded_users):
    resp = api_client.get("/api/v1/users", params={"search": SEARCH_SCOPE, "status": "Active"})
    body = resp.json()
    assert body["total"] == 3
    names = {i["name"] for i in body["items"]}
    assert names == {"Test User Alpha", "Test User Beta", "Test User Epsilon"}


def test_sorting(api_client, seeded_users):
    asc_resp = api_client.get("/api/v1/users", params={"search": SEARCH_SCOPE, "sortBy": "name", "sortOrder": "asc"})
    names_asc = [i["name"] for i in asc_resp.json()["items"]]
    assert names_asc == sorted(names_asc)

    desc_resp = api_client.get("/api/v1/users", params={"search": SEARCH_SCOPE, "sortBy": "name", "sortOrder": "desc"})
    names_desc = [i["name"] for i in desc_resp.json()["items"]]
    assert names_desc == sorted(names_desc, reverse=True)


def test_invalid_role_rejected(api_client, seeded_users):
    resp = api_client.get("/api/v1/users", params={"role": "Superuser"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_status_rejected(api_client, seeded_users):
    resp = api_client.get("/api/v1/users", params={"status": "Deleted"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_pagination_rejected(api_client, seeded_users):
    assert api_client.get("/api/v1/users", params={"page": 0}).status_code == 422
    assert api_client.get("/api/v1/users", params={"pageSize": 0}).status_code == 422
    assert api_client.get("/api/v1/users", params={"pageSize": 500}).status_code == 422


# =====================================================================
# GET — GET /api/v1/users/{userId}
# =====================================================================


def test_get_existing_user(api_client, seeded_users):
    user = seeded_users[0]
    resp = api_client.get(f"/api/v1/users/{user.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user.id
    assert body["name"] == "Test User Alpha"
    assert body["mobile"] == "9111111111"
    assert body["access"] == {}


def test_get_nonexistent_user(api_client):
    resp = api_client.get("/api/v1/users/2147483000")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "USER_NOT_FOUND"


def test_user_with_no_access_returns_empty_dict(api_client, seeded_users):
    resp = api_client.get(f"/api/v1/users/{seeded_users[1].id}")
    assert resp.json()["access"] == {}


def test_user_access_is_correctly_derived(api_client, db_session, seeded_users, merchant_alpha):
    user = seeded_users[0]
    block_sheet = next(s for s in merchant_alpha.sop_sheets if s.key == "block")
    poc_sheet = next(s for s in merchant_alpha.sop_sheets if s.key == "poc")
    db_session.add(UserSopSheetAccess(user_id=user.id, sheet_id=block_sheet.id))
    db_session.add(UserSopSheetAccess(user_id=user.id, sheet_id=poc_sheet.id))
    db_session.flush()

    resp = api_client.get(f"/api/v1/users/{user.id}")
    assert resp.status_code == 200
    assert resp.json()["access"] == {"Test Merchant UserApiAlpha": ["block", "poc"]}


# =====================================================================
# CREATE — POST /api/v1/users
# =====================================================================


def test_create_user(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={
            "name": "Test User NewOne",
            "email": "test.user.newone@example.invalid",
            "mobile": "9000000001",
            "role": "SME",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test User NewOne"
    assert body["email"] == "test.user.newone@example.invalid"
    assert body["status"] == "Invited"
    assert body["access"] == {}
    assert body["id"] is not None


def test_create_user_with_no_access(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={
            "name": "Test User NewTwo",
            "email": "test.user.newtwo@example.invalid",
            "role": "Viewer",
            "access": {},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["access"] == {}


def test_create_user_with_sheet_access(api_client, act_as, actor, merchant_alpha):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={
            "name": "Test User NewThree",
            "email": "test.user.newthree@example.invalid",
            "role": "SME",
            "access": {"Test Merchant UserApiAlpha": ["block", "poc"]},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["access"] == {"Test Merchant UserApiAlpha": ["block", "poc"]}

    # confirm it round-trips through GET too
    get_resp = api_client.get(f"/api/v1/users/{body['id']}")
    assert get_resp.json()["access"] == {"Test Merchant UserApiAlpha": ["block", "poc"]}


def test_create_user_invalid_role_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={"name": "Test User Bad", "email": "test.user.badrole@example.invalid", "role": "Superuser"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_user_invalid_email_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={"name": "Test User Bad", "email": "not-an-email", "role": "SME"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_user_duplicate_email_rejected(api_client, act_as, actor):
    act_as(actor)
    payload = {"name": "Test User Dup", "email": "test.user.dup@example.invalid", "role": "SME"}
    first = api_client.post("/api/v1/users", json=payload)
    assert first.status_code == 201

    second = api_client.post("/api/v1/users", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "USER_EMAIL_ALREADY_EXISTS"


def test_create_user_nonexistent_merchant_in_access_rejected(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={
            "name": "Test User BadMerchant",
            "email": "test.user.badmerchant@example.invalid",
            "role": "SME",
            "access": {"Totally Fake Merchant XYZ": ["block"]},
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "ACCESS_MERCHANT_NOT_FOUND"

    created = db_session.execute(
        select(User).where(User.email == "test.user.badmerchant@example.invalid")
    ).scalar_one_or_none()
    assert created is None


def test_create_user_nonexistent_sheet_in_access_rejected(api_client, act_as, actor, merchant_alpha):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={
            "name": "Test User BadSheet",
            "email": "test.user.badsheet@example.invalid",
            "role": "SME",
            "access": {"Test Merchant UserApiAlpha": ["not-a-real-key"]},
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "ACCESS_SHEET_NOT_FOUND"


def test_create_user_sheet_from_another_merchant_rejected(api_client, act_as, actor, merchant_alpha, merchant_beta):
    # "activation" exists for merchant_beta but NOT merchant_alpha —
    # granting it under alpha's name must fail, proving grants are
    # scoped per-merchant and can't leak across merchants.
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={
            "name": "Test User CrossMerchant",
            "email": "test.user.crossmerchant@example.invalid",
            "role": "SME",
            "access": {"Test Merchant UserApiAlpha": ["activation"]},
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "ACCESS_SHEET_NOT_FOUND"


def test_create_user_transaction_rollback_on_partial_access_failure(api_client, db_session, act_as, actor, merchant_alpha):
    act_as(actor)
    email = "test.user.rollback@example.invalid"
    resp = api_client.post(
        "/api/v1/users",
        json={
            "name": "Test User Rollback",
            "email": email,
            "role": "SME",
            # first entry resolves and would succeed; second does not —
            # dict iteration order is insertion order in Python, so the
            # valid grant is attempted BEFORE the failure.
            "access": {
                "Test Merchant UserApiAlpha": ["block"],
                "Totally Fake Merchant XYZ": ["poc"],
            },
        },
    )
    assert resp.status_code == 422

    # the user must not exist at all...
    created_user = db_session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    assert created_user is None

    # ...and the "block" grant that would have succeeded first must also
    # have been rolled back — no orphaned access rows for that sheet.
    block_sheet = next(s for s in merchant_alpha.sop_sheets if s.key == "block")
    leftover = db_session.execute(
        select(UserSopSheetAccess).where(UserSopSheetAccess.sheet_id == block_sheet.id)
    ).scalars().all()
    assert leftover == []


# =====================================================================
# UPDATE — PUT /api/v1/users/{userId}
# =====================================================================


def test_update_basic_fields(api_client, act_as, actor, seeded_users):
    act_as(actor)
    user = seeded_users[0]
    resp = api_client.put(
        f"/api/v1/users/{user.id}",
        json={"name": "Test User Alpha Renamed", "mobile": "9999999999"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Test User Alpha Renamed"
    assert body["mobile"] == "9999999999"
    assert body["role"] == "Admin"  # unchanged
    assert body["status"] == "Active"  # unchanged


def test_update_role(api_client, act_as, actor, seeded_users):
    act_as(actor)
    user = seeded_users[2]  # SME
    resp = api_client.put(f"/api/v1/users/{user.id}", json={"role": "Admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "Admin"


def test_update_status(api_client, act_as, actor, seeded_users):
    act_as(actor)
    user = seeded_users[0]  # Active
    resp = api_client.put(f"/api/v1/users/{user.id}", json={"status": "Inactive"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "Inactive"


def test_update_replaces_complete_access_state(api_client, act_as, actor, seeded_users, merchant_alpha):
    act_as(actor)
    user = seeded_users[0]
    create_access = api_client.put(
        f"/api/v1/users/{user.id}",
        json={"access": {"Test Merchant UserApiAlpha": ["block", "poc"]}},
    )
    assert create_access.json()["access"] == {"Test Merchant UserApiAlpha": ["block", "poc"]}

    replace = api_client.put(
        f"/api/v1/users/{user.id}",
        json={"access": {"Test Merchant UserApiAlpha": ["block"]}},
    )
    assert replace.status_code == 200
    assert replace.json()["access"] == {"Test Merchant UserApiAlpha": ["block"]}


def test_update_removes_all_access(api_client, db_session, act_as, actor, seeded_users, merchant_alpha):
    act_as(actor)
    user = seeded_users[0]
    api_client.put(
        f"/api/v1/users/{user.id}",
        json={"access": {"Test Merchant UserApiAlpha": ["block", "poc"]}},
    )

    resp = api_client.put(f"/api/v1/users/{user.id}", json={"access": {}})
    assert resp.status_code == 200
    assert resp.json()["access"] == {}

    remaining = db_session.execute(
        select(UserSopSheetAccess).where(UserSopSheetAccess.user_id == user.id)
    ).scalars().all()
    assert remaining == []


def test_update_omitted_access_leaves_access_unchanged(api_client, act_as, actor, seeded_users, merchant_alpha):
    act_as(actor)
    user = seeded_users[0]
    api_client.put(
        f"/api/v1/users/{user.id}",
        json={"access": {"Test Merchant UserApiAlpha": ["block"]}},
    )

    # "access" key is entirely absent from this request body.
    resp = api_client.put(f"/api/v1/users/{user.id}", json={"name": "Test User Alpha Still Has Access"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Test User Alpha Still Has Access"
    assert body["access"] == {"Test Merchant UserApiAlpha": ["block"]}


def test_update_duplicate_email_rejected(api_client, act_as, actor, seeded_users):
    act_as(actor)
    user_a, user_b = seeded_users[0], seeded_users[1]
    resp = api_client.put(f"/api/v1/users/{user_b.id}", json={"email": user_a.email})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "USER_EMAIL_ALREADY_EXISTS"


def test_update_nonexistent_user_returns_404(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.put("/api/v1/users/2147483000", json={"name": "Nobody"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "USER_NOT_FOUND"


# =====================================================================
# DELETE — DELETE /api/v1/users/{userId}
# =====================================================================


def test_delete_existing_user(api_client, act_as, actor, seeded_users):
    act_as(actor)
    user = seeded_users[4]  # no access, no revisions
    resp = api_client.delete(f"/api/v1/users/{user.id}")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "message": "User deleted successfully."}

    follow_up = api_client.get(f"/api/v1/users/{user.id}")
    assert follow_up.status_code == 404


def test_delete_nonexistent_user(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.delete("/api/v1/users/2147483000")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "USER_NOT_FOUND"


def test_delete_user_cascades_access_records(api_client, db_session, act_as, actor, seeded_users, merchant_alpha):
    act_as(actor)
    user = seeded_users[0]
    block_sheet = next(s for s in merchant_alpha.sop_sheets if s.key == "block")
    db_session.add(UserSopSheetAccess(user_id=user.id, sheet_id=block_sheet.id))
    db_session.flush()

    resp = api_client.delete(f"/api/v1/users/{user.id}")
    assert resp.status_code == 200

    remaining = db_session.execute(
        select(UserSopSheetAccess).where(UserSopSheetAccess.user_id == user.id)
    ).scalars().all()
    assert remaining == []


def test_delete_user_blocked_by_revision_history(api_client, db_session, act_as, actor, seeded_users):
    act_as(actor)
    user = seeded_users[0]
    revision = Revision(
        user_id=user.id,
        action_type="update",
        entity_type="user",
        target_label="Test User Alpha",
        change_description="Synthetic revision for FK test",
    )
    db_session.add(revision)
    db_session.flush()
    revision_id = revision.id

    resp = api_client.delete(f"/api/v1/users/{user.id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "USER_HAS_REVISION_HISTORY"

    # neither the revision nor the user was actually removed.
    assert db_session.get(Revision, revision_id) is not None
    assert db_session.get(User, user.id) is not None
