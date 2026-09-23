"""
API tests for Merchants (Part 8):
  GET /api/v1/merchants
  GET /api/v1/merchants/resolve
  GET /api/v1/merchants/{merchantId}/sheets

Uses `api_client` (tests/integration/conftest.py), same pattern as
Part 7's BIN Series tests: this database already holds real seed data
(510 real merchants, 1532 merchant-owned sheets), and the list endpoint
has no per-test scoping, so most tests use synthetic, collision-proof
fixture values rather than assuming an empty table:
  - merchant names all start with "Test Merchant " (real seed names are
    plain bank names like "HDFC Bank" or "Issuer 042 Bank" — never that
    prefix), which also avoids the merchants.name UNIQUE constraint.
  - the one synthetic "shared sheet" fixture uses a key
    ("test-shared-sheet") distinct from the real seeded "escalation"
    key, to avoid the partial unique index on shared sheets.

Two tests (HDFC/ICICI sheet keys) are a deliberate exception: they
verify against the REAL, already-seeded curated data instead of a
synthetic substitute. The Part 8 task explicitly names these two
merchants and their real, distinctively-different sheet structures
(confirmed in Parts 2/5/6) are exactly the regression this project cares
about — a synthetic fixture couldn't meaningfully stand in for "did the
real import preserve HDFC's actual 4 sheets vs ICICI's different 4
sheets". This relies on the Part 6 seed being loaded, which the task's
own "Current status" section states is the case.
"""
import pytest

from app.models import Merchant, SopSheet

ALPHA = {"name": "Test Merchant Alpha", "classification": "Digital Gift Card"}
BETA = {"name": "Test Merchant Beta", "classification": "Physical Gift Card"}
GAMMA = {"name": "Test Merchant Gamma", "classification": "Corporate Gifting"}
DELTA = {"name": "Test Merchant Delta", "classification": "Reward Card"}
EPSILON = {"name": "Test Merchant Epsilon", "classification": "Digital Gift Card"}

FIXTURE_MERCHANTS = [ALPHA, BETA, GAMMA, DELTA, EPSILON]
SEARCH_SCOPE = "Test Merchant"


@pytest.fixture()
def seeded_merchants(db_session):
    for m in FIXTURE_MERCHANTS:
        db_session.add(Merchant(name=m["name"], classification=m["classification"]))
    db_session.flush()


@pytest.fixture()
def merchant_with_sheets(db_session):
    """One synthetic merchant with 2 owned sheets, plus one synthetic
    SHARED sheet (merchant_id=None) that must never show up for it."""
    merchant = Merchant(name="Test Merchant ForSheets", classification="Corporate Gifting")
    db_session.add(merchant)
    db_session.flush()

    db_session.add(SopSheet(merchant_id=merchant.id, key="block", name="Block"))
    db_session.add(SopSheet(merchant_id=merchant.id, key="poc", name="POC"))
    db_session.add(SopSheet(merchant_id=None, key="test-shared-sheet", name="Test Shared Sheet"))
    db_session.flush()

    return merchant


# --- GET /api/v1/merchants ------------------------------------------------


def test_list_merchants_returns_paginated_envelope(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants", params={"search": SEARCH_SCOPE})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"items", "total", "page", "pageSize"}
    assert body["total"] == len(FIXTURE_MERCHANTS)
    assert body["page"] == 1
    assert body["pageSize"] == 50
    assert len(body["items"]) == len(FIXTURE_MERCHANTS)

    item = body["items"][0]
    assert set(item.keys()) == {"id", "name", "classification"}


def test_list_merchants_pagination_no_duplicates(api_client, seeded_merchants):
    page1 = api_client.get("/api/v1/merchants", params={"search": SEARCH_SCOPE, "page": 1, "pageSize": 2}).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2

    page2 = api_client.get("/api/v1/merchants", params={"search": SEARCH_SCOPE, "page": 2, "pageSize": 2}).json()
    assert len(page2["items"]) == 2

    page3 = api_client.get("/api/v1/merchants", params={"search": SEARCH_SCOPE, "page": 3, "pageSize": 2}).json()
    assert len(page3["items"]) == 1  # 5 records / pageSize 2 -> last page has 1

    ids_seen = [i["id"] for i in page1["items"] + page2["items"] + page3["items"]]
    assert len(ids_seen) == len(set(ids_seen)) == 5  # no duplicates, no gaps across pages


def test_search_by_merchant_name(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants", params={"search": "Test Merchant Alpha"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Test Merchant Alpha"


def test_search_is_case_insensitive(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants", params={"search": "test merchant alpha"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Test Merchant Alpha"


def test_classification_filter(api_client, seeded_merchants):
    # "Digital Gift Card" alone isn't unique to our fixtures (real seed
    # data has ~128 merchants with this classification too), so this is
    # combined with the search scope — see module docstring.
    resp = api_client.get(
        "/api/v1/merchants", params={"search": SEARCH_SCOPE, "classification": "Digital Gift Card"}
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 2  # Alpha and Epsilon
    names = {item["name"] for item in body["items"]}
    assert names == {"Test Merchant Alpha", "Test Merchant Epsilon"}
    assert all(item["classification"] == "Digital Gift Card" for item in body["items"])


def test_invalid_classification_rejected(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants", params={"classification": "Not A Real Classification"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_sorting_ascending(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants", params={"search": SEARCH_SCOPE, "sortBy": "name", "sortOrder": "asc"})
    body = resp.json()
    assert resp.status_code == 200
    names = [item["name"] for item in body["items"]]
    assert names == sorted(names)


def test_sorting_descending(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants", params={"search": SEARCH_SCOPE, "sortBy": "name", "sortOrder": "desc"})
    body = resp.json()
    assert resp.status_code == 200
    names = [item["name"] for item in body["items"]]
    assert names == sorted(names, reverse=True)


# --- GET /api/v1/merchants/resolve -----------------------------------------


def test_resolve_existing_merchant(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants/resolve", params={"name": "Test Merchant Gamma"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Test Merchant Gamma"
    assert body["classification"] == "Corporate Gifting"


def test_resolve_existing_merchant_case_insensitive(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants/resolve", params={"name": "test merchant gamma"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Merchant Gamma"


def test_resolve_nonexistent_merchant_returns_standard_404(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants/resolve", params={"name": "Test Merchant Does Not Exist"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "MERCHANT_NOT_FOUND"
    assert "message" in body["error"]


def test_resolve_does_not_perform_partial_matching(api_client, seeded_merchants):
    # "Test Merchant Gamma" exists; a substring of it must NOT resolve.
    resp = api_client.get("/api/v1/merchants/resolve", params={"name": "Test Merchant Gam"})
    assert resp.status_code == 404


# --- GET /api/v1/merchants/{merchantId}/sheets -----------------------------


def test_get_sheets_for_existing_merchant(api_client, merchant_with_sheets):
    resp = api_client.get(f"/api/v1/merchants/{merchant_with_sheets.id}/sheets")
    assert resp.status_code == 200
    body = resp.json()

    assert body["merchant"] == {
        "id": merchant_with_sheets.id,
        "name": "Test Merchant ForSheets",
        "classification": "Corporate Gifting",
    }
    keys = {s["key"] for s in body["sheets"]}
    assert keys == {"block", "poc"}


def test_shared_sheet_is_not_returned(api_client, merchant_with_sheets):
    resp = api_client.get(f"/api/v1/merchants/{merchant_with_sheets.id}/sheets")
    body = resp.json()
    keys = {s["key"] for s in body["sheets"]}
    assert "test-shared-sheet" not in keys
    assert len(body["sheets"]) == 2  # exactly the 2 merchant-owned sheets, not 3


def test_hdfc_returns_its_real_seeded_sheet_keys(api_client, db_session):
    """Relies on the Part 6 seed being loaded (per the task's stated
    current status) — see module docstring for why this one test
    intentionally checks real data instead of a synthetic fixture."""
    hdfc = api_client.get("/api/v1/merchants/resolve", params={"name": "HDFC Bank"})
    if hdfc.status_code != 200:
        pytest.skip("Part 6 seed data not loaded in this database — HDFC Bank not found.")

    resp = api_client.get(f"/api/v1/merchants/{hdfc.json()['id']}/sheets")
    assert resp.status_code == 200
    keys = {s["key"] for s in resp.json()["sheets"]}
    assert keys == {"block", "activation", "cancel-activate", "poc"}


def test_icici_returns_its_real_seeded_sheet_keys(api_client, db_session):
    """See test_hdfc_returns_its_real_seeded_sheet_keys docstring."""
    icici = api_client.get("/api/v1/merchants/resolve", params={"name": "ICICI Bank"})
    if icici.status_code != 200:
        pytest.skip("Part 6 seed data not loaded in this database — ICICI Bank not found.")

    resp = api_client.get(f"/api/v1/merchants/{icici.json()['id']}/sheets")
    assert resp.status_code == 200
    keys = {s["key"] for s in resp.json()["sheets"]}
    assert keys == {"block", "activation", "cancel-redemptions", "escalation"}


def test_nonexistent_merchant_id_returns_404(api_client, seeded_merchants):
    resp = api_client.get("/api/v1/merchants/2147483000/sheets")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "MERCHANT_NOT_FOUND"


def test_invalid_merchant_id_returns_validation_error(api_client, seeded_merchants):
    non_numeric = api_client.get("/api/v1/merchants/not-a-number/sheets")
    assert non_numeric.status_code == 422
    assert non_numeric.json()["error"]["code"] == "VALIDATION_ERROR"

    zero = api_client.get("/api/v1/merchants/0/sheets")
    assert zero.status_code == 422

    negative = api_client.get("/api/v1/merchants/-1/sheets")
    assert negative.status_code == 422
