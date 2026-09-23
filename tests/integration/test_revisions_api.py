"""
API tests for Revision History (Part 11, read-only):
  GET /api/v1/revisions

Same hybrid strategy as Parts 7-10: this database already holds 10 real
seeded revisions, so most tests scope with a synthetic "Test Revision "
target/change prefix (collision-proof against the real seed and every
other test in this file, since each test gets its own isolated,
rolled-back transaction). One test explicitly checks the real Part 6
seed data, per the task's requirement to confirm the importer data is
exposed correctly.

IMPORTANT — schema conflict (documented, not silently worked around):
app.models.revision.Revision.user_id is NOT NULL by design (Part 3 §9 —
"a user with revision history should not be silently deletable out from
under it"). The task requires the API to return "user": null for a
revision with no user, and requires a test for it (#24) — but no such
row can actually be inserted under the current schema (an attempt would
raise a NOT NULL constraint violation). Per the task's explicit
instruction not to change the model, the repository/service ARE built
to handle this correctly (LEFT OUTER JOIN, null-safe mapping — see
app/repositories/revision_repository.py and app/services/revision_service.py),
and test_null_user_is_mapped_to_none_in_response below verifies that
code path directly (calling the service's row-to-response mapping with
a constructed row where the user fields are None) rather than via a real
database insert, which is impossible here.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import Revision, User
from app.services.revision_service import _row_to_response

SEARCH_SCOPE = "Test Revision"


@pytest.fixture()
def seeded_revisions(db_session):
    user_a = User(name="Test Revision UserA", email="test.revision.usera@example.invalid", role="Admin", status="Active")
    user_b = User(name="Test Revision UserB", email="test.revision.userb@example.invalid", role="Auditor", status="Active")
    db_session.add_all([user_a, user_b])
    db_session.flush()

    base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    revisions = {
        "alpha": Revision(
            occurred_at=base_time,
            user_id=user_a.id,
            action_type="update",
            entity_type="user",
            target_label="Test Revision Target Alpha",
            change_description="Test Revision Change Alpha involving activation",
        ),
        "beta": Revision(
            occurred_at=base_time + timedelta(hours=1),
            user_id=user_a.id,
            action_type="create",
            entity_type="bin_record",
            target_label="Test Revision Target Beta",
            change_description="Test Revision Change Beta",
        ),
        "gamma": Revision(
            occurred_at=base_time + timedelta(hours=2),
            user_id=user_b.id,
            action_type="delete",
            entity_type="sop_sheet",
            target_label="Test Revision Target Gamma",
            change_description="Test Revision Change Gamma",
        ),
        "delta": Revision(
            occurred_at=base_time + timedelta(hours=3),
            user_id=user_b.id,
            action_type="upload",
            entity_type="merchant",
            target_label="Test Revision Target Delta",
            change_description="Test Revision Change Delta",
        ),
        "epsilon": Revision(
            occurred_at=base_time + timedelta(hours=3),  # same timestamp as delta, on purpose
            user_id=user_a.id,
            action_type="update",
            entity_type="sop_row",
            target_label="Test Revision Target Epsilon",
            change_description="Test Revision Change Epsilon",
        ),
    }
    db_session.add_all(revisions.values())
    db_session.flush()

    return {"user_a": user_a, "user_b": user_b, "revisions": revisions}


# =====================================================================
# 1-2: list / pagination
# =====================================================================


def test_list_revisions(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"items", "total", "page", "pageSize"}
    assert body["total"] == 5

    item = body["items"][0]
    assert set(item.keys()) == {"id", "timestamp", "user", "action", "entity", "target", "change"}


def test_pagination(api_client, seeded_revisions):
    page1 = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "page": 1, "pageSize": 2}).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2

    page2 = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "page": 2, "pageSize": 2}).json()
    assert len(page2["items"]) == 2

    page3 = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "page": 3, "pageSize": 2}).json()
    assert len(page3["items"]) == 1

    ids = [i["id"] for i in page1["items"] + page2["items"] + page3["items"]]
    assert len(ids) == len(set(ids)) == 5


# =====================================================================
# 3-7: search
# =====================================================================


def test_search_by_target(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": "Target Alpha"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["target"] == "Test Revision Target Alpha"


def test_search_by_change_description(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": "involving activation"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["target"] == "Test Revision Target Alpha"


def test_search_by_user_name(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": "Test Revision UserA"})
    body = resp.json()
    assert body["total"] == 3  # alpha, beta, epsilon


def test_search_by_user_email(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": "test.revision.userb@example.invalid"})
    body = resp.json()
    assert body["total"] == 2  # gamma, delta


def test_search_is_case_insensitive(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": "TEST REVISION TARGET ALPHA"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["target"] == "Test Revision Target Alpha"


# =====================================================================
# 8-11: filters
# =====================================================================


def test_filter_by_action_type(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "actionType": "update"})
    body = resp.json()
    assert body["total"] == 2  # alpha, epsilon
    assert all(i["action"] == "update" for i in body["items"])


def test_filter_by_entity_type(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "entityType": "sop_row"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["target"] == "Test Revision Target Epsilon"


def test_filter_by_user_id(api_client, seeded_revisions):
    user_a_id = seeded_revisions["user_a"].id
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "userId": user_a_id})
    body = resp.json()
    assert body["total"] == 3  # alpha, beta, epsilon
    assert all(i["user"]["id"] == user_a_id for i in body["items"])


def test_combine_filters_with_and_semantics(api_client, seeded_revisions):
    # action_type=update matches alpha+epsilon; entity_type=user matches
    # only alpha among those two (epsilon is sop_row) — AND must narrow
    # to exactly alpha, not the union of the two filters.
    resp = api_client.get(
        "/api/v1/revisions",
        params={"search": SEARCH_SCOPE, "actionType": "update", "entityType": "user"},
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["target"] == "Test Revision Target Alpha"


# =====================================================================
# 12-17: sorting
# =====================================================================


def test_sort_by_occurred_at(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "sortBy": "occurred_at", "sortOrder": "asc"})
    timestamps = [i["timestamp"] for i in resp.json()["items"]]
    assert timestamps == sorted(timestamps)


def test_sort_by_action(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "sortBy": "action", "sortOrder": "asc"})
    actions = [i["action"] for i in resp.json()["items"]]
    assert actions == sorted(actions)


def test_sort_by_entity(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "sortBy": "entity", "sortOrder": "asc"})
    entities = [i["entity"] for i in resp.json()["items"]]
    assert entities == sorted(entities)


def test_sort_by_target(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE, "sortBy": "target", "sortOrder": "desc"})
    targets = [i["target"] for i in resp.json()["items"]]
    assert targets == sorted(targets, reverse=True)


def test_default_sort_is_occurred_at_descending(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE})
    timestamps = [i["timestamp"] for i in resp.json()["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_deterministic_ordering_when_timestamps_match(api_client, seeded_revisions):
    # delta and epsilon share the exact same occurred_at.
    resp = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE})
    items = resp.json()["items"]

    delta = next(i for i in items if "Delta" in i["target"])
    epsilon = next(i for i in items if "Epsilon" in i["target"])
    assert delta["timestamp"] == epsilon["timestamp"]

    # default sortOrder=desc -> tiebreak by id in the same (desc) direction
    delta_index, epsilon_index = items.index(delta), items.index(epsilon)
    if delta["id"] > epsilon["id"]:
        assert delta_index < epsilon_index
    else:
        assert epsilon_index < delta_index

    # and it's repeatable, not incidental.
    resp2 = api_client.get("/api/v1/revisions", params={"search": SEARCH_SCOPE})
    assert [i["id"] for i in resp2.json()["items"]] == [i["id"] for i in items]


# =====================================================================
# 18-22: invalid input
# =====================================================================


def test_invalid_action_type_rejected(api_client):
    resp = api_client.get("/api/v1/revisions", params={"actionType": "explode"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_entity_type_rejected(api_client):
    resp = api_client.get("/api/v1/revisions", params={"entityType": "spaceship"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_sort_by_rejected(api_client):
    resp = api_client.get("/api/v1/revisions", params={"sortBy": "not_a_field"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_sort_order_rejected(api_client):
    resp = api_client.get("/api/v1/revisions", params={"sortOrder": "sideways"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_pagination_rejected(api_client):
    assert api_client.get("/api/v1/revisions", params={"page": 0}).status_code == 422
    assert api_client.get("/api/v1/revisions", params={"pageSize": 0}).status_code == 422
    assert api_client.get("/api/v1/revisions", params={"pageSize": 500}).status_code == 422


# =====================================================================
# 23-26: user association / edge cases
# =====================================================================


def test_revision_with_user_returns_user_details(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": "Target Alpha"})
    item = resp.json()["items"][0]
    assert item["user"] == {
        "id": seeded_revisions["user_a"].id,
        "name": "Test Revision UserA",
        "email": "test.revision.usera@example.invalid",
    }


def test_null_user_is_mapped_to_none_in_response():
    """revisions.user_id is NOT NULL (Part 3/5 design) — a real row with
    no user cannot be inserted, so this exercises the service's
    row-to-response mapping directly instead of via the database. See
    this file's module docstring for the full explanation."""
    fake_row = SimpleNamespace(
        id=999,
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        action_type="update",
        entity_type="user",
        target_label="Some Target",
        change_description="Some change",
        user_id=None,
        user_name=None,
        user_email=None,
    )
    response = _row_to_response(fake_row)
    assert response.user is None


def test_nonexistent_user_id_filter_returns_empty_result(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"userId": 2147483000})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 0
    assert body["items"] == []


def test_search_does_not_match_unrelated_revisions(api_client, seeded_revisions):
    resp = api_client.get("/api/v1/revisions", params={"search": "zzz-substring-that-matches-nothing"})
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


# =====================================================================
# Real seeded data (Part 6 importer) — confirms it's exposed correctly
# =====================================================================


def test_real_seeded_revision_is_exposed_correctly(api_client):
    """Does not assume the seed is present — skips gracefully if not,
    consistent with Parts 8-9's established pattern."""
    resp = api_client.get("/api/v1/revisions", params={"search": "HDFC Regalia"})
    body = resp.json()
    if body["total"] == 0:
        pytest.skip("Part 6 seed data not loaded — no revision mentioning 'HDFC Regalia' found.")

    item = body["items"][0]
    assert item["target"] == "BIN 401288 · HDFC Regalia"
    assert item["action"] == "update"
    assert item["entity"] == "bin_record"
    assert item["user"] is not None
    assert item["user"]["name"] == "Ravi Kumar"
    assert item["user"]["email"] == "ravi.kumar@pinelabs.in"
