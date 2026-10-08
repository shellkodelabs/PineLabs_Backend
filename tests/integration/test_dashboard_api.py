"""
API tests for the Dashboard (Part 12, read-only):
  GET /api/v1/dashboard/kpis

Unlike prior list endpoints, this one has NO query/filter parameters at
all — it aggregates across the whole table every time. So these tests
don't isolate via a synthetic "Test X" search scope (there's no search
param to scope with). Instead:
  - Count-style assertions compare a BEFORE/AFTER delta around inserting
    one known synthetic row, proving the aggregation genuinely reacts to
    real data rather than merely happening to match a static number.
  - Set-comparison assertions (classification/issuer breakdowns) compare
    the API's response against a value computed via a direct SQL query
    run through the SAME test session at the SAME point in time —
    correct regardless of whether the real Part 6 seed is present.
  - The "empty aggregation" requirement is tested as a direct unit test
    of the service's pure list-building helpers (no endpoint has zero
    merchants/BIN records/revisions in this shared, seeded database to
    exercise a genuinely empty result through a real request).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import BinRecord, Merchant, Revision, SopSheet, User
from app.services.dashboard_service import _build_activity_list, _build_classification_list, _build_issuer_list


# =====================================================================
# 1: basic shape
# =====================================================================


def test_dashboard_returns_200(api_client):
    resp = api_client.get("/api/v1/dashboard/kpis")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"kpis", "sopsByClassification", "recentActivity", "binRecordsByIssuer"}
    assert set(body["kpis"].keys()) == {
        "binSeriesCount",
        "merchantCount",
        "activeUserCount",
        "revisionsLast7Days",
        "sopSheetCount",
    }


# =====================================================================
# 2-6: counts match / react to the database
# =====================================================================


def test_bin_count_matches_database(api_client, db_session):
    before = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["binSeriesCount"]
    db_count = db_session.scalar(select(func.count()).select_from(BinRecord))
    assert before == db_count

    db_session.add(
        BinRecord(issuer="Test Dashboard Bank", card_program_group_name="Test Program", bin_iin="999501", merchant_prefix="501")
    )
    db_session.flush()

    after = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["binSeriesCount"]
    assert after == before + 1


def test_merchant_count_matches_database(api_client, db_session):
    before = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["merchantCount"]
    db_count = db_session.scalar(select(func.count()).select_from(Merchant))
    assert before == db_count

    db_session.add(Merchant(name="Test Dashboard Merchant", classification="Reward Card"))
    db_session.flush()

    after = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["merchantCount"]
    assert after == before + 1


def test_active_user_count_matches_database(api_client, db_session):
    before = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["activeUserCount"]
    db_count = db_session.scalar(select(func.count()).select_from(User).where(User.status == "Active"))
    assert before == db_count

    db_session.add(
        User(name="Test Dashboard ActiveUser", email="test.dashboard.activeuser@example.invalid", role="SME", status="Active")
    )
    db_session.flush()

    after = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["activeUserCount"]
    assert after == before + 1


def test_active_user_count_excludes_inactive(api_client, db_session):
    before = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["activeUserCount"]

    db_session.add(
        User(name="Test Dashboard InactiveUser", email="test.dashboard.inactiveuser@example.invalid", role="SME", status="Inactive")
    )
    db_session.flush()

    after = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["activeUserCount"]
    assert after == before  # unchanged — an Inactive user must not count


def test_sop_sheet_count_matches_database(api_client, db_session):
    before = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["sopSheetCount"]
    db_count = db_session.scalar(select(func.count()).select_from(SopSheet))
    assert before == db_count

    merchant = Merchant(name="Test Dashboard SheetMerchant", classification="Reward Card")
    db_session.add(merchant)
    db_session.flush()
    db_session.add(SopSheet(merchant_id=merchant.id, key="test-dashboard-sheet", name="Test Sheet"))
    db_session.flush()

    after = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["sopSheetCount"]
    assert after == before + 1  # a merchant-owned sheet counts too


def test_sop_sheet_count_includes_shared_sheet(api_client, db_session):
    """Direct confirmation of the documented interpretation: a shared
    (merchant_id IS NULL) sheet increments sopSheetCount too."""
    before = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["sopSheetCount"]

    db_session.add(SopSheet(merchant_id=None, key="test-dashboard-shared-sheet", name="Test Shared Sheet"))
    db_session.flush()

    after = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["sopSheetCount"]
    assert after == before + 1


def test_revisions_last_7_days_reflects_real_window(api_client, db_session):
    """Proves the 7-day filter both INCLUDES a recent row and EXCLUDES
    an old one — not just "counts everything"."""
    user = User(name="Test Dashboard RevUser", email="test.dashboard.revuser@example.invalid", role="SME", status="Active")
    db_session.add(user)
    db_session.flush()

    before = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["revisionsLast7Days"]

    db_session.add(
        Revision(
            occurred_at=datetime.now(timezone.utc) - timedelta(days=1),
            user_id=user.id,
            action_type="update",
            entity_type="user",
            target_label="Test Dashboard Recent Revision",
            change_description="within the 7-day window",
        )
    )
    db_session.add(
        Revision(
            occurred_at=datetime.now(timezone.utc) - timedelta(days=30),
            user_id=user.id,
            action_type="update",
            entity_type="user",
            target_label="Test Dashboard Old Revision",
            change_description="outside the 7-day window",
        )
    )
    db_session.flush()

    after = api_client.get("/api/v1/dashboard/kpis").json()["kpis"]["revisionsLast7Days"]
    assert after == before + 1  # only the recent one


# =====================================================================
# 7-8: classification aggregation
# =====================================================================


def test_classification_aggregation_is_correct(api_client, db_session):
    expected = dict(db_session.execute(select(Merchant.classification, func.count()).group_by(Merchant.classification)).all())
    body = api_client.get("/api/v1/dashboard/kpis").json()
    actual = {item["classification"]: item["merchantCount"] for item in body["sopsByClassification"]}
    assert actual == expected


def test_classification_counts_sum_to_merchant_count(api_client):
    body = api_client.get("/api/v1/dashboard/kpis").json()
    total = sum(item["merchantCount"] for item in body["sopsByClassification"])
    assert total == body["kpis"]["merchantCount"]


# =====================================================================
# 9-11: recent activity
# =====================================================================


def test_recent_activity_returns_latest_revisions(api_client, db_session):
    user = User(name="Test Dashboard NewestUser", email="test.dashboard.newestuser@example.invalid", role="SME", status="Active")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        Revision(
            occurred_at=datetime.now(timezone.utc),
            user_id=user.id,
            action_type="create",
            entity_type="merchant",
            target_label="Test Dashboard Newest Activity",
            change_description="newest",
        )
    )
    db_session.flush()

    body = api_client.get("/api/v1/dashboard/kpis").json()
    targets = [item["target"] for item in body["recentActivity"]]
    assert targets[0] == "Test Dashboard Newest Activity"
    assert len(body["recentActivity"]) <= 10


def test_recent_activity_is_ordered_newest_first(api_client):
    body = api_client.get("/api/v1/dashboard/kpis").json()
    timestamps = [item["timestamp"] for item in body["recentActivity"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_recent_activity_includes_user_information(api_client, db_session):
    user = User(name="Test Dashboard UserInfo", email="test.dashboard.userinfo@example.invalid", role="SME", status="Active")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        Revision(
            occurred_at=datetime.now(timezone.utc),
            user_id=user.id,
            action_type="update",
            entity_type="user",
            target_label="Test Dashboard UserInfo Target",
            change_description="...",
        )
    )
    db_session.flush()

    body = api_client.get("/api/v1/dashboard/kpis").json()
    item = next(i for i in body["recentActivity"] if i["target"] == "Test Dashboard UserInfo Target")
    assert item["user"] == {"id": user.id, "name": "Test Dashboard UserInfo"}


# =====================================================================
# 12-13: issuer aggregation
# =====================================================================


def test_issuer_aggregation_matches_actual_bin_counts(api_client, db_session):
    expected = dict(db_session.execute(select(BinRecord.issuer, func.count()).group_by(BinRecord.issuer)).all())
    body = api_client.get("/api/v1/dashboard/kpis").json()
    for item in body["binRecordsByIssuer"]:
        assert item["binRecordCount"] == expected[item["issuer"]]


def test_issuer_aggregation_does_not_claim_to_be_volume(api_client):
    body = api_client.get("/api/v1/dashboard/kpis").json()
    assert "volume" not in json.dumps(body).lower()
    for item in body["binRecordsByIssuer"]:
        assert set(item.keys()) == {"issuer", "binRecordCount"}


# =====================================================================
# 14: no fabricated ticket data
# =====================================================================


def test_no_tickets_resolved_fake_value(api_client):
    body = api_client.get("/api/v1/dashboard/kpis").json()
    assert "ticketsResolved" not in body
    assert "ticketsResolved" not in body["kpis"]
    assert "tickets" not in json.dumps(body).lower()


# =====================================================================
# 15: works with real seeded data
# =====================================================================


def test_dashboard_works_with_real_seeded_data(api_client):
    body = api_client.get("/api/v1/dashboard/kpis").json()
    if body["kpis"]["merchantCount"] < 510:
        pytest.skip("Part 6 seed data not fully loaded.")

    assert body["kpis"]["binSeriesCount"] >= 611
    assert body["kpis"]["merchantCount"] >= 510
    assert body["kpis"]["sopSheetCount"] >= 1533
    assert len(body["sopsByClassification"]) == 4


# =====================================================================
# 16: empty aggregation behavior (unit-level — see module docstring)
# =====================================================================


def test_empty_aggregation_is_handled_sensibly():
    """No endpoint in this shared, seeded database can produce a
    genuinely empty merchants/BIN-records/revisions result through a
    real request (there is no filter param, and the real seed always has
    rows). So this tests the service's pure list-building helpers
    directly with empty input, proving they return `[]` rather than
    erroring — the actual code path the endpoint would take if the
    underlying tables were ever empty."""
    assert _build_classification_list([]) == []
    assert _build_issuer_list([]) == []
    assert _build_activity_list([]) == []
