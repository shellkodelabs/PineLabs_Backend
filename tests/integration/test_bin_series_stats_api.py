"""
API tests for BIN Series stats (Bin Series priority task; renamed from
/stats to /statistics by the API Naming task):
  GET /api/v1/bin-series/statistics

Backs the frontend's StatCards on the BIN Series page
(src/components/dashboard/StatCards.jsx — "Total Records", "Issuers",
"Card Programs"), today computed client-side as binSeries.length / a
Set of unique issuers / a Set of unique cardProgramGroupName values
from the full local mock array. This endpoint computes the same three
numbers as real SQL aggregates over the whole table (not paginated,
not filtered — always a whole-table count).

Uses `api_client`/`db_session`/`act_as` (tests/integration/conftest.py)
for the authenticated path, and a local `raw_client` (same pattern as
tests/integration/test_auth.py) for the unauthenticated path.

Fixture rows use bin_iin values in the 999900-999949 range — disjoint
from every other BIN test file's own reserved ranges. Because this
endpoint aggregates the ENTIRE table (no per-test scoping is possible
for a global count), these tests assert against DELTAS (before/after
inserting known fixture rows) rather than exact totals — the same
"don't assume the table is empty" discipline every other BIN Series
test file already follows, just applied to a whole-table aggregate
instead of a filtered query.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import BinRecord, User


def _make_bin(db_session, *, issuer, card_program_group_name, bin_iin, merchant_prefix):
    record = BinRecord(
        issuer=issuer,
        card_program_group_name=card_program_group_name,
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
    )
    db_session.add(record)
    db_session.flush()
    return record


@pytest.fixture()
def raw_client():
    """A TestClient with NO dependency overrides — real authentication
    runs for real on every request made through this. Same pattern as
    tests/integration/test_auth.py's fixture of the same name."""
    return TestClient(app)


def _current_stats(api_client):
    return api_client.get("/api/v1/bin-series/statistics").json()


# =====================================================================
# Auth
# =====================================================================


def test_authenticated_get_succeeds(api_client):
    resp = api_client.get("/api/v1/bin-series/statistics")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"totalRecords", "totalIssuers", "totalCardPrograms"}


def test_unauthenticated_get_rejected(raw_client):
    resp = raw_client.get("/api/v1/bin-series/statistics")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_unauthenticated_get_with_bogus_token_rejected(raw_client):
    resp = raw_client.get("/api/v1/bin-series/statistics", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_PROVIDER_NOT_CONFIGURED"


# =====================================================================
# Whole-table aggregates — asserted as deltas, since this endpoint
# deliberately has no filter/scope to isolate fixture rows.
# =====================================================================


def test_total_records_reflects_at_least_the_real_611_seeded_rows(api_client, db_session):
    real_total = db_session.scalar(select(func.count()).select_from(BinRecord))
    assert real_total >= 611

    body = _current_stats(api_client)
    assert body["totalRecords"] == real_total


def test_adding_a_new_bin_record_increases_total_records_by_one(api_client, db_session):
    before = _current_stats(api_client)
    _make_bin(db_session, issuer="Test Bin Stats NewRecord", card_program_group_name="Program", bin_iin="999900", merchant_prefix="001")
    after = _current_stats(api_client)
    assert after["totalRecords"] == before["totalRecords"] + 1


def test_adding_a_brand_new_issuer_increases_total_issuers_by_one(api_client, db_session):
    before = _current_stats(api_client)
    _make_bin(db_session, issuer="Test Bin Stats Unique Issuer XYZ", card_program_group_name="Program", bin_iin="999901", merchant_prefix="001")
    after = _current_stats(api_client)
    assert after["totalIssuers"] == before["totalIssuers"] + 1


def test_reusing_an_existing_issuer_does_not_increase_total_issuers(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Stats Repeated Issuer", card_program_group_name="Program A", bin_iin="999902", merchant_prefix="001")
    before = _current_stats(api_client)
    # Same issuer, different binIin/merchantPrefix — a real, valid second record.
    _make_bin(db_session, issuer="Test Bin Stats Repeated Issuer", card_program_group_name="Program B", bin_iin="999903", merchant_prefix="002")
    after = _current_stats(api_client)
    assert after["totalRecords"] == before["totalRecords"] + 1
    assert after["totalIssuers"] == before["totalIssuers"]  # unchanged — same issuer, not a new one


def test_adding_a_brand_new_card_program_increases_total_card_programs_by_one(api_client, db_session):
    before = _current_stats(api_client)
    _make_bin(db_session, issuer="Test Bin Stats CP Issuer", card_program_group_name="Test Bin Stats Unique Program XYZ", bin_iin="999904", merchant_prefix="001")
    after = _current_stats(api_client)
    assert after["totalCardPrograms"] == before["totalCardPrograms"] + 1


def test_reusing_an_existing_card_program_does_not_increase_total_card_programs(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Stats CPA", card_program_group_name="Test Bin Stats Shared Program", bin_iin="999905", merchant_prefix="001")
    before = _current_stats(api_client)
    _make_bin(db_session, issuer="Test Bin Stats CPB", card_program_group_name="Test Bin Stats Shared Program", bin_iin="999906", merchant_prefix="001")
    after = _current_stats(api_client)
    assert after["totalRecords"] == before["totalRecords"] + 1
    assert after["totalCardPrograms"] == before["totalCardPrograms"]  # unchanged


def test_stats_are_not_paginated_or_filtered(api_client, db_session):
    """Confirms this is a whole-table aggregate, not something scoped by
    default pagination (pageSize defaults to 50 elsewhere in this API,
    but the real table already has 611+ rows)."""
    body = _current_stats(api_client)
    assert body["totalRecords"] >= 611


def test_created_via_create_api_is_reflected_in_stats(api_client, act_as, db_session):
    actor = User(name="Test Bin Stats Actor", email="test.bin.stats.actor@example.invalid", role="Admin", status="Active")
    db_session.add(actor)
    db_session.flush()
    act_as(actor)

    before = _current_stats(api_client)
    resp = api_client.post(
        "/api/v1/bin-series/create",
        json={
            "issuer": "Test Bin Stats ViaCreateApi",
            "cardProgramGroupName": "Test Bin Stats ViaCreateApi Program",
            "binIin": "999907",
            "merchantPrefix": "001",
            "instanceName": "North Zone",
        },
    )
    assert resp.status_code == 201
    after = _current_stats(api_client)
    assert after["totalRecords"] == before["totalRecords"] + 1
    assert after["totalIssuers"] == before["totalIssuers"] + 1
    assert after["totalCardPrograms"] == before["totalCardPrograms"] + 1


# =====================================================================
# Regression — existing endpoints unaffected
# =====================================================================


def test_regression_existing_list_and_resolve_unaffected(api_client, db_session):
    total = db_session.scalar(select(func.count()).select_from(BinRecord))
    assert total >= 611

    resp = api_client.get("/api/v1/bin-series/list", params={"page": 1, "pageSize": 1})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 611
