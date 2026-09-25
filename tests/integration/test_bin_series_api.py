"""
API tests for BIN Series (Part 7; endpoints renamed by the API Naming
task from bare /api/v1/bin-series and /resolve respectively):
  GET /api/v1/bin-series/list
  GET /api/v1/bin-series/lookup

Uses `api_client` (tests/integration/conftest.py) so requests run against
the exact same transactional session as this test's own fixture inserts.

IMPORTANT — this database is NOT empty: the Part 6 seed script has
already loaded 611 real BIN records into it, and `GET /api/v1/bin-series/list`
has no per-test/tenant scoping (by design — nothing in the schema
supports that). So these tests do NOT assume the table only contains
their own fixture rows. Every fixture row uses values that cannot
possibly collide with the real seed data:
  - issuer names all start with "Test Issuer " (real seed issuers are
    plain bank names like "HDFC Bank" — never that prefix)
  - bin_iin values are all in the 999xxx range (the real seed's
    generator only ever produces 400000-482063 — see binSeries.js)
For the one filter that CAN collide with real data on its own
(merchant_prefix, a bare 3-digit code many real records also use), the
test combines it with an `issuer` filter scoped to a single synthetic
fixture row so the match is unambiguous regardless of what else exists.
"""
import pytest

from app.models import BinRecord

ALPHA = {"issuer": "Test Issuer Alpha Bank", "cardProgramGroupName": "Alpha Regalia", "binIin": "999001", "merchantPrefix": "001"}
ALPHA_2 = {"issuer": "Test Issuer Alpha Bank", "cardProgramGroupName": "Alpha Millennia", "binIin": "999001", "merchantPrefix": "021"}
BETA = {"issuer": "Test Issuer Beta Bank", "cardProgramGroupName": "Beta Coral", "binIin": "999002", "merchantPrefix": "004"}
GAMMA = {"issuer": "Test Issuer Gamma Bank", "cardProgramGroupName": "Gamma Magnus", "binIin": "999003", "merchantPrefix": "002"}
DELTA = {"issuer": "Test Issuer Delta Bank", "cardProgramGroupName": "Delta Rewards", "binIin": "999004", "merchantPrefix": "008"}

FIXTURE_RECORDS = [ALPHA, ALPHA_2, BETA, GAMMA, DELTA]
SEARCH_SCOPE = "Test Issuer"  # substring shared by every fixture row only


@pytest.fixture()
def seeded_bins(db_session):
    for r in FIXTURE_RECORDS:
        db_session.add(
            BinRecord(
                issuer=r["issuer"],
                card_program_group_name=r["cardProgramGroupName"],
                bin_iin=r["binIin"],
                merchant_prefix=r["merchantPrefix"],
            )
        )
    db_session.flush()


# --- GET /api/v1/bin-series/list -----------------------------------------


def test_list_bin_series_returns_paginated_envelope(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/list", params={"search": SEARCH_SCOPE})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"items", "total", "page", "pageSize"}
    assert body["total"] == len(FIXTURE_RECORDS)
    assert body["page"] == 1
    assert body["pageSize"] == 50
    assert len(body["items"]) == len(FIXTURE_RECORDS)

    item = body["items"][0]
    assert set(item.keys()) == {
        "id",
        "issuer",
        "cardProgramGroupName",
        "binIin",
        "merchantPrefix",
        "merchantId",
        "instanceName",
        "updatedBy",
        "updatedAt",
    }
    assert item["merchantId"] is None  # no merchant was created/linked for these fixture rows
    # Part 16/17: additive fields — these fixture rows were inserted
    # directly (not via POST /api/v1/bin-series), so neither an actor nor
    # an instanceName is attached to them.
    assert item["instanceName"] is None
    assert item["updatedBy"] is None


def test_list_bin_series_pagination(api_client, seeded_bins):
    page1 = api_client.get("/api/v1/bin-series/list", params={"search": SEARCH_SCOPE, "page": 1, "pageSize": 2}).json()
    assert page1["total"] == 5
    assert page1["page"] == 1
    assert page1["pageSize"] == 2
    assert len(page1["items"]) == 2

    page2 = api_client.get("/api/v1/bin-series/list", params={"search": SEARCH_SCOPE, "page": 2, "pageSize": 2}).json()
    assert len(page2["items"]) == 2

    page3 = api_client.get("/api/v1/bin-series/list", params={"search": SEARCH_SCOPE, "page": 3, "pageSize": 2}).json()
    assert len(page3["items"]) == 1  # 5 records / pageSize 2 -> last page has 1

    ids_seen = [i["id"] for i in page1["items"] + page2["items"] + page3["items"]]
    assert len(ids_seen) == len(set(ids_seen)) == 5  # no duplicates, no gaps across pages


def test_search_by_issuer(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Issuer Alpha"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 2
    assert all(item["issuer"] == "Test Issuer Alpha Bank" for item in body["items"])


def test_search_by_bin(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "999002"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["binIin"] == "999002"
    assert body["items"][0]["issuer"] == "Test Issuer Beta Bank"


def test_search_by_merchant_prefix(api_client, seeded_bins):
    # merchant_prefix "002" alone isn't guaranteed unique across the whole
    # table (real seed data reuses bare 3-digit codes too), so this is
    # combined with an exact issuer filter scoped to Gamma's one row —
    # see the module docstring.
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "002", "issuer": "Test Issuer Gamma Bank"}
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["merchantPrefix"] == "002"
    assert body["items"][0]["binIin"] == "999003"

    # negative control: the same issuer with a prefix it doesn't have
    # should match nothing, confirming the match above was really driven
    # by merchant_prefix and not just the issuer filter.
    empty = api_client.get("/api/v1/bin-series/list", params={"search": "999999-not-a-real-prefix", "issuer": "Test Issuer Gamma Bank"}
    ).json()
    assert empty["total"] == 0


def test_filter_by_exact_issuer_case_insensitive(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/list", params={"issuer": "test issuer alpha bank"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 2
    assert all(item["issuer"] == "Test Issuer Alpha Bank" for item in body["items"])


def test_sorting_by_issuer_desc(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/list", params={"search": SEARCH_SCOPE, "sortBy": "issuer", "sortOrder": "desc"}
    )
    body = resp.json()
    assert resp.status_code == 200
    issuers = [item["issuer"] for item in body["items"]]
    assert issuers == sorted(issuers, reverse=True)


def test_sorting_by_bin_iin_asc(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/list", params={"search": SEARCH_SCOPE, "sortBy": "binIin", "sortOrder": "asc"})
    body = resp.json()
    bins = [item["binIin"] for item in body["items"]]
    assert bins == sorted(bins)


def test_sorting_invalid_field_rejected(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/list", params={"sortBy": "notAField"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_page_rejected(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/list", params={"page": 0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    resp2 = api_client.get("/api/v1/bin-series/list", params={"page": -1})
    assert resp2.status_code == 422


def test_invalid_page_size_rejected(api_client, seeded_bins):
    too_small = api_client.get("/api/v1/bin-series/list", params={"pageSize": 0})
    assert too_small.status_code == 422

    too_large = api_client.get("/api/v1/bin-series/list", params={"pageSize": 500})
    assert too_large.status_code == 422


# --- GET /api/v1/bin-series/lookup ----------------------------------------


def test_resolve_valid_bin_and_prefix(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999001", "merchantPrefix": "001"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == "Test Issuer Alpha Bank"
    assert body["cardProgramGroupName"] == "Alpha Regalia"
    assert body["binIin"] == "999001"
    assert body["merchantPrefix"] == "001"


def test_resolve_nonexistent_bin_returns_standard_404(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999999", "merchantPrefix": "999"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "BIN_RECORD_NOT_FOUND"
    assert "message" in body["error"]


def test_resolve_does_not_return_partial_matches(api_client, seeded_bins):
    # 999001 has TWO fixture records (prefixes 001 and 021) — a prefix
    # that matches neither must 404, never silently return one of them.
    resp = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999001", "merchantPrefix": "999"})
    assert resp.status_code == 404


def test_resolve_invalid_bin_format_rejected(api_client, seeded_bins):
    non_digits = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "12AB56", "merchantPrefix": "001"})
    assert non_digits.status_code == 422
    assert non_digits.json()["error"]["code"] == "VALIDATION_ERROR"

    wrong_length = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "1234", "merchantPrefix": "001"})
    assert wrong_length.status_code == 422


def test_resolve_invalid_merchant_prefix_format_rejected(api_client, seeded_bins):
    wrong_length = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999001", "merchantPrefix": "1"})
    assert wrong_length.status_code == 422
    assert wrong_length.json()["error"]["code"] == "VALIDATION_ERROR"

    non_digits = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999001", "merchantPrefix": "abc"})
    assert non_digits.status_code == 422


def test_resolve_missing_required_params_rejected(api_client, seeded_bins):
    resp = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999001"})
    assert resp.status_code == 422
