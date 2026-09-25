"""
API tests for the BIN Series Search & Filter capability (Bin Series
priority task): GET /api/v1/bin-series/list (renamed from bare
/api/v1/bin-series by the API Naming task).

FRONTEND INSPECTION FINDING (BinTable.jsx, fully re-read for this task):
the approved UI has exactly ONE global search box (matches every field
present on a row, case-insensitive substring — `Object.values(r).some(v
=> String(v).toLowerCase().includes(q))`), NO filter dropdowns, and NO
column-sort UI at all. The existing GET /api/v1/bin-series/list endpoint
(Part 7) already exceeds this — it already has search, exact
issuer/cardProgramGroupName filters, sortBy/sortOrder, and real
server-side pagination, none of which the frontend currently drives via
dedicated controls beyond the search box.

THE ONE GENUINE GAP found and fixed here: `search` did not previously
match `instance_name`, a real persisted field the frontend's blanket
per-row search would include once a row has one. This file's primary
tests cover that extension; the rest is REGRESSION coverage for the
pre-existing (Part 7) filter/sort/pagination capability, using new,
independent fixtures rather than touching test_bin_series_api.py.

Uses `api_client`/`db_session` (tests/integration/conftest.py) for the
authenticated path, and a local `raw_client` (same pattern as
tests/integration/test_auth.py) for the unauthenticated path.

Fixture rows use bin_iin values in the 999700-999799 range — disjoint
from every other BIN test file's own reserved ranges.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import BinRecord


def _make_bin(
    db_session, *, issuer, card_program_group_name, bin_iin, merchant_prefix, instance_name=None,
):
    record = BinRecord(
        issuer=issuer,
        card_program_group_name=card_program_group_name,
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        instance_name=instance_name,
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


SEARCH_SCOPE = "Test Bin SF"


# =====================================================================
# NEW CAPABILITY: search now matches instance_name
# =====================================================================


def test_search_matches_instance_name(api_client, db_session):
    _make_bin(
        db_session, issuer="Test Bin SF InstanceMatch", card_program_group_name="Program",
        bin_iin="999700", merchant_prefix="001", instance_name="Test Bin SF Priority Tier",
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Priority Tier"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["issuer"] == "Test Bin SF InstanceMatch"
    assert body["items"][0]["instanceName"] == "Test Bin SF Priority Tier"


def test_search_by_instance_name_is_case_insensitive(api_client, db_session):
    _make_bin(
        db_session, issuer="Test Bin SF CaseCheck", card_program_group_name="Program",
        bin_iin="999701", merchant_prefix="001", instance_name="Test Bin SF North Zone",
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "test bin sf north zone"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_search_by_instance_name_is_partial_match(api_client, db_session):
    _make_bin(
        db_session, issuer="Test Bin SF PartialCheck", card_program_group_name="Program",
        bin_iin="999702", merchant_prefix="001", instance_name="Test Bin SF Strategic Accounts",
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Strategic"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_search_still_matches_original_four_fields(api_client, db_session):
    """Regression: extending the OR clause must not disturb the original
    issuer/cardProgramGroupName/binIin/merchantPrefix matches."""
    _make_bin(
        db_session, issuer="Test Bin SF OriginalFields", card_program_group_name="Special Program XYZ",
        bin_iin="999703", merchant_prefix="001",
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Special Program XYZ"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_search_does_not_false_positive_on_null_instance_name(api_client, db_session):
    """Rows with no instance_name (NULL) must never match ANY search term
    via the new clause — proves ILIKE-on-NULL correctly excludes them
    rather than accidentally matching everything."""
    _make_bin(
        db_session, issuer="Test Bin SF NullInstance", card_program_group_name="Program",
        bin_iin="999704", merchant_prefix="001", instance_name=None,
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Bin SF NullInstance Zone Nowhere"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_search_across_multiple_rows_matches_either_field(api_client, db_session):
    """OR semantics: one search term can independently match different
    fields on different rows within the same query."""
    _make_bin(
        db_session, issuer="Test Bin SF OrIssuer", card_program_group_name="Program",
        bin_iin="999705", merchant_prefix="001",
    )
    _make_bin(
        db_session, issuer="Test Bin SF Other", card_program_group_name="Program",
        bin_iin="999706", merchant_prefix="002", instance_name="Test Bin SF OrIssuer Zone",
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Bin SF OrIssuer"})
    body = resp.json()
    assert body["total"] == 2


# =====================================================================
# REGRESSION: pre-existing (Part 7) capability, unaffected
# =====================================================================


def test_no_search_or_filter_returns_full_unfiltered_list(api_client, db_session):
    total_before = db_session.scalar(select(func.count()).select_from(BinRecord))
    resp = api_client.get("/api/v1/bin-series/list", params={"page": 1, "pageSize": 1})
    assert resp.status_code == 200
    assert resp.json()["total"] == total_before


def test_no_match_result(api_client, db_session):
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Bin SF Definitely Does Not Exist Anywhere"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_exact_issuer_filter_still_works(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin SF ExactFilter", card_program_group_name="Program", bin_iin="999707", merchant_prefix="001")
    resp = api_client.get("/api/v1/bin-series/list", params={"issuer": "test bin sf exactfilter"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_exact_card_program_group_name_filter_still_works(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin SF CPFilter", card_program_group_name="Test Bin SF Unique Program", bin_iin="999708", merchant_prefix="001")
    resp = api_client.get("/api/v1/bin-series/list", params={"cardProgramGroupName": "Test Bin SF Unique Program"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_search_combined_with_exact_filter_is_and_semantics(api_client, db_session):
    """search (now including instance_name) AND issuer must combine with
    AND, not OR — same pre-existing semantics, now exercised against the
    extended search clause too."""
    _make_bin(
        db_session, issuer="Test Bin SF AndA", card_program_group_name="Program",
        bin_iin="999709", merchant_prefix="001", instance_name="Test Bin SF Shared Zone",
    )
    _make_bin(
        db_session, issuer="Test Bin SF AndB", card_program_group_name="Program",
        bin_iin="999710", merchant_prefix="002", instance_name="Test Bin SF Shared Zone",
    )
    resp = api_client.get(
        "/api/v1/bin-series/list",
        params={"search": "Test Bin SF Shared Zone", "issuer": "Test Bin SF AndA"},
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["issuer"] == "Test Bin SF AndA"


def test_pagination_still_works(api_client, db_session):
    for i in range(3):
        _make_bin(db_session, issuer=f"Test Bin SF Page{i}", card_program_group_name="Program", bin_iin=f"99971{i}", merchant_prefix="001")
    page1 = api_client.get("/api/v1/bin-series/list", params={"search": "Test Bin SF Page", "page": 1, "pageSize": 2}).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    page2 = api_client.get("/api/v1/bin-series/list", params={"search": "Test Bin SF Page", "page": 2, "pageSize": 2}).json()
    assert len(page2["items"]) == 1


def test_page_size_respected(api_client, db_session):
    for i in range(5):
        _make_bin(db_session, issuer=f"Test Bin SF Size{i}", card_program_group_name="Program", bin_iin=f"99972{i}", merchant_prefix="001")
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Bin SF Size", "pageSize": 3})
    body = resp.json()
    assert body["pageSize"] == 3
    assert len(body["items"]) == 3


def test_sorting_ascending_still_works(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin SF SortB", card_program_group_name="Program", bin_iin="999725", merchant_prefix="001")
    _make_bin(db_session, issuer="Test Bin SF SortA", card_program_group_name="Program", bin_iin="999726", merchant_prefix="001")
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Bin SF Sort", "sortBy": "issuer", "sortOrder": "asc"})
    issuers = [i["issuer"] for i in resp.json()["items"]]
    assert issuers == sorted(issuers)


def test_sorting_descending_still_works(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin SF SortDescB", card_program_group_name="Program", bin_iin="999727", merchant_prefix="001")
    _make_bin(db_session, issuer="Test Bin SF SortDescA", card_program_group_name="Program", bin_iin="999728", merchant_prefix="001")
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Bin SF SortDesc", "sortBy": "issuer", "sortOrder": "desc"})
    issuers = [i["issuer"] for i in resp.json()["items"]]
    assert issuers == sorted(issuers, reverse=True)


def test_invalid_sort_field_rejected(api_client):
    resp = api_client.get("/api/v1/bin-series/list", params={"sortBy": "notARealField"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_sort_order_rejected(api_client):
    resp = api_client.get("/api/v1/bin-series/list", params={"sortOrder": "sideways"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_page_and_page_size_rejected(api_client):
    assert api_client.get("/api/v1/bin-series/list", params={"page": 0}).status_code == 422
    assert api_client.get("/api/v1/bin-series/list", params={"pageSize": 500}).status_code == 422


# =====================================================================
# Authentication
# =====================================================================


def test_authentication_required(raw_client):
    resp = raw_client.get("/api/v1/bin-series/list")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_authentication_bogus_token_rejected(raw_client):
    resp = raw_client.get("/api/v1/bin-series/list", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_PROVIDER_NOT_CONFIGURED"
