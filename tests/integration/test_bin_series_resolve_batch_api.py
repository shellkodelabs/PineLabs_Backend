"""
API tests for BIN Series bulk/multi-card resolve (Bin Series priority
task):
  POST /api/v1/bin-series/bulk-lookup

Backs BulkLookupModal.jsx (opened from both BinResolver.jsx and
BinTable.jsx). Read-only — no actor, no audit trail.

MATCHING RULE — a documented superset of GET /lookup's rule (see
app/services/bin_service.py's module docstring): exact (binIin,
merchantPrefix) when 3 prefix digits are present; otherwise a "BIN
uniquely identifies exactly one record" shortcut when the prefix is
partial/absent. Tests below cover both paths, including the ambiguous
(2+ candidates, no shortcut) case.

Uses `api_client`/`db_session` (tests/integration/conftest.py) for the
authenticated path, and a local `raw_client` (same pattern as
tests/integration/test_auth.py) for the unauthenticated path.

Fixture rows use bin_iin values in the 999600-999699 range — disjoint
from every other BIN test file's own reserved ranges.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import BinRecord


def _make_bin(db_session, *, issuer, bin_iin, merchant_prefix, card_program_group_name="Program"):
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


# =====================================================================
# 1-2. Successful single / multiple-card batch
# =====================================================================


def test_successful_single_item_batch(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Resolve Single", bin_iin="999600", merchant_prefix="001")
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["999600001"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    item = body["results"][0]
    assert item["matched"] is True
    assert item["issuer"] == "Test Bin Resolve Single"
    assert item["binIin"] == "999600"
    assert item["merchantPrefix"] == "001"


def test_successful_multiple_card_batch(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Resolve MultiA", bin_iin="999601", merchant_prefix="001")
    _make_bin(db_session, issuer="Test Bin Resolve MultiB", bin_iin="999602", merchant_prefix="002")
    resp = api_client.post(
        "/api/v1/bin-series/bulk-lookup",
        json={"cards": ["999601001", "999602002"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 2
    assert all(r["matched"] for r in body["results"])


# =====================================================================
# 3. Multiple matched cards / 4. unmatched / 5. mixed
# =====================================================================


def test_multiple_matched_cards(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Resolve MM1", bin_iin="999603", merchant_prefix="001")
    _make_bin(db_session, issuer="Test Bin Resolve MM2", bin_iin="999604", merchant_prefix="001")
    resp = api_client.post(
        "/api/v1/bin-series/bulk-lookup",
        json={"cards": ["999603001", "999604001"]},
    )
    body = resp.json()
    assert body["results"][0]["issuer"] == "Test Bin Resolve MM1"
    assert body["results"][1]["issuer"] == "Test Bin Resolve MM2"


def test_unmatched_card_returns_explicit_result(api_client, db_session):
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["999605999"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    item = body["results"][0]
    assert item["matched"] is False
    assert item["issuer"] is None
    assert item["cardProgramGroupName"] is None
    assert item["binIin"] == "999605"  # BIN itself was extractable, just no record for it


def test_mixed_matched_and_unmatched_cards(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Resolve Mixed", bin_iin="999606", merchant_prefix="001")
    resp = api_client.post(
        "/api/v1/bin-series/bulk-lookup",
        json={"cards": ["999606001", "999606999"]},
    )
    body = resp.json()
    assert body["results"][0]["matched"] is True
    assert body["results"][1]["matched"] is False


# =====================================================================
# 6-7. Duplicates / order preservation
# =====================================================================


def test_duplicate_input_cards_preserved(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Resolve Dup", bin_iin="999607", merchant_prefix="001")
    resp = api_client.post(
        "/api/v1/bin-series/bulk-lookup",
        json={"cards": ["999607001", "999607001", "999607001"]},
    )
    body = resp.json()
    assert len(body["results"]) == 3
    assert all(r["matched"] for r in body["results"])


def test_order_preservation(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Resolve OrderA", bin_iin="999608", merchant_prefix="001")
    _make_bin(db_session, issuer="Test Bin Resolve OrderB", bin_iin="999609", merchant_prefix="001")
    cards = ["999609001", "bogus", "999608001", "999609001"]
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": cards})
    body = resp.json()
    inputs = [r["input"] for r in body["results"]]
    assert inputs == cards
    assert body["results"][0]["issuer"] == "Test Bin Resolve OrderB"
    assert body["results"][1]["matched"] is False
    assert body["results"][2]["issuer"] == "Test Bin Resolve OrderA"
    assert body["results"][3]["issuer"] == "Test Bin Resolve OrderB"


# =====================================================================
# 8-9. Invalid card input / empty request
# =====================================================================


def test_invalid_too_short_card_input(api_client):
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["123"]})
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["matched"] is False
    assert item["binIin"] is None
    assert item["input"] == "123"


def test_non_numeric_card_input(api_client):
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["not-a-card-at-all"]})
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["matched"] is False
    assert item["input"] == "not-a-card-at-all"


def test_empty_string_card_returns_unmatched_not_an_error(api_client):
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": [""]})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["matched"] is False


def test_empty_request_rejected(api_client):
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": []})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_missing_cards_field_rejected(api_client):
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_too_many_cards_rejected(api_client):
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["999999999"] * 1001})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# =====================================================================
# 10. Authentication
# =====================================================================


def test_authentication_failure_missing_credentials(raw_client):
    resp = raw_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["401288001"]})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_authentication_failure_bogus_token(raw_client):
    resp = raw_client.post(
        "/api/v1/bin-series/bulk-lookup",
        json={"cards": ["401288001"]},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_PROVIDER_NOT_CONFIGURED"


# =====================================================================
# 11. Batch lookup behavior (partial-prefix shortcut, ambiguity)
# =====================================================================


def test_partial_prefix_resolves_when_bin_is_unique(api_client, db_session):
    """Only one record for this BIN — resolves even without the full
    3-digit prefix, matching BulkLookupModal.jsx's own resolve() rule."""
    _make_bin(db_session, issuer="Test Bin Resolve UniqueBin", bin_iin="999610", merchant_prefix="001")
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["999610"]})
    body = resp.json()
    item = body["results"][0]
    assert item["matched"] is True
    assert item["issuer"] == "Test Bin Resolve UniqueBin"
    assert item["merchantPrefix"] is None  # partial/absent prefix never echoed back as if it were complete


def test_partial_prefix_does_not_resolve_when_bin_is_ambiguous(api_client, db_session):
    """Two records share this BIN — no full prefix given, so this must
    NOT guess between them."""
    _make_bin(db_session, issuer="Test Bin Resolve AmbigA", bin_iin="999611", merchant_prefix="001")
    _make_bin(db_session, issuer="Test Bin Resolve AmbigB", bin_iin="999611", merchant_prefix="002")
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["999611"]})
    item = resp.json()["results"][0]
    assert item["matched"] is False
    assert item["issuer"] is None


def test_full_prefix_never_uses_the_ambiguous_shortcut(api_client, db_session):
    """With a full 3-digit prefix supplied, an exact match is required —
    the unique-bin shortcut must never override an exact non-match."""
    _make_bin(db_session, issuer="Test Bin Resolve ExactA", bin_iin="999612", merchant_prefix="001")
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["999612999"]})
    item = resp.json()["results"][0]
    assert item["matched"] is False


def test_batch_lookup_uses_single_query_per_unique_bin(api_client, db_session, monkeypatch):
    """Confirms the batch repository function is called once (not once
    per card) — the actual N+1-avoidance mechanism."""
    import app.repositories.bin_repository as bin_repository_module

    _make_bin(db_session, issuer="Test Bin Resolve Efficient", bin_iin="999613", merchant_prefix="001")

    call_count = {"n": 0}
    original = bin_repository_module.get_by_bin_iin_batch

    def _counting_wrapper(session, bin_iins):
        call_count["n"] += 1
        return original(session, bin_iins)

    monkeypatch.setattr(bin_repository_module, "get_by_bin_iin_batch", _counting_wrapper)

    resp = api_client.post(
        "/api/v1/bin-series/bulk-lookup",
        json={"cards": ["999613001", "999613001", "999613999", "bogus"]},
    )
    assert resp.status_code == 200
    assert call_count["n"] == 1  # exactly one batch call regardless of card count


# =====================================================================
# 12. Response schema
# =====================================================================


def test_response_schema_shape(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Resolve Shape", bin_iin="999614", merchant_prefix="001")
    resp = api_client.post(
        "/api/v1/bin-series/bulk-lookup",
        json={"cards": ["999614001", "bogus"]},
    )
    body = resp.json()
    assert set(body.keys()) == {"results"}
    for item in body["results"]:
        assert set(item.keys()) == {"input", "binIin", "merchantPrefix", "issuer", "cardProgramGroupName", "matched"}


def test_spaces_and_hyphens_in_card_input_are_tolerated(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Resolve Formatted", bin_iin="999615", merchant_prefix="001")
    resp = api_client.post(
        "/api/v1/bin-series/bulk-lookup",
        json={"cards": ["999-615-001", "999615 001"]},
    )
    body = resp.json()
    assert body["results"][0]["matched"] is True
    assert body["results"][0]["input"] == "999-615-001"  # raw input preserved verbatim
    assert body["results"][1]["matched"] is True
    assert body["results"][1]["input"] == "999615 001"


# =====================================================================
# Regression — existing read APIs unaffected
# =====================================================================


def test_regression_existing_resolve_and_list_unaffected(api_client, db_session):
    total = db_session.scalar(select(func.count()).select_from(BinRecord))
    assert total >= 611

    resp = api_client.get("/api/v1/bin-series/list", params={"page": 1, "pageSize": 1})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 611
