"""
API tests for SOP (Part 9):
  GET /api/v1/merchants/{merchantId}/sheets/{sheetKey}
  GET /api/v1/merchants/{merchantId}/sheets/{sheetKey}/rows
  GET /api/v1/sop/escalation/common

Same hybrid strategy as Parts 7/8: tests that need precise control over
row counts, ordering, or search matches use a fully synthetic fixture
(`sop_fixture` below — a merchant named "Test Merchant SopFixture" with
its own sheet/groups/columns/rows, all collision-proof against the real
seed data). Tests that are explicitly about validating the REAL seeded
HDFC/ICICI structure, or the singleton shared escalation sheet (which by
its own partial-unique-index design can only ever have ONE real row for
key="escalation" — a synthetic substitute isn't meaningfully possible
for that endpoint, which is hardcoded to that key), depend on the Part 6
seed and `pytest.skip()` gracefully if it isn't present.
"""
import pytest

from app.models import Merchant, SopColumn, SopColumnGroup, SopRow, SopSheet


@pytest.fixture()
def sop_fixture(db_session):
    """One synthetic merchant, one sheet, 2 groups (created out of
    sort_order sequence to prove ordering isn't accidental insertion
    order), 3 columns, 5 rows (one containing a distinctive, mixed-case
    searchable substring)."""
    merchant = Merchant(name="Test Merchant SopFixture", classification="Reward Card")
    db_session.add(merchant)
    db_session.flush()

    sheet = SopSheet(merchant_id=merchant.id, key="test-block", name="Test Block")
    db_session.add(sheet)
    db_session.flush()

    group_b = SopColumnGroup(sheet_id=sheet.id, label="Group B", sort_order=1)
    group_a = SopColumnGroup(sheet_id=sheet.id, label="Group A", sort_order=0)
    db_session.add_all([group_b, group_a])  # inserted B-then-A on purpose
    db_session.flush()

    db_session.add_all(
        [
            SopColumn(group_id=group_b.id, name="B-Second", sort_order=1),
            SopColumn(group_id=group_b.id, name="B-First", sort_order=0),  # inserted 2nd-then-1st
            SopColumn(group_id=group_a.id, name="A-Only", sort_order=0),
        ]
    )

    rows_data = [
        {"A-Only": "Row One", "B-First": "Contains Activation Keyword Here", "B-Second": "x1"},
        {"A-Only": "Row Two", "B-First": "unrelated text", "B-Second": "x2"},
        {"A-Only": "Row Three", "B-First": "another unrelated value", "B-Second": "x3"},
        {"A-Only": "Row Four", "B-First": "nothing special", "B-Second": "x4"},
        {"A-Only": "Row Five", "B-First": "plain text", "B-Second": "x5"},
    ]
    for rd in rows_data:
        db_session.add(SopRow(sheet_id=sheet.id, data=rd))
    db_session.flush()

    return {"merchant": merchant, "sheet": sheet}


def _resolve_merchant_id(api_client, name):
    resp = api_client.get("/api/v1/merchants/resolve", params={"name": name})
    if resp.status_code != 200:
        return None
    return resp.json()["id"]


# --- 1-3: HDFC block sheet structure (real seeded data) --------------------


def test_get_hdfc_block_sheet_structure(api_client):
    merchant_id = _resolve_merchant_id(api_client, "HDFC Bank")
    if merchant_id is None:
        pytest.skip("Part 6 seed data not loaded — HDFC Bank not found.")

    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/block")
    assert resp.status_code == 200
    body = resp.json()

    assert body["merchant"]["name"] == "HDFC Bank"
    assert body["sheet"]["key"] == "block"
    assert body["sheet"]["name"] == "Block"


def test_hdfc_block_groups(api_client):
    merchant_id = _resolve_merchant_id(api_client, "HDFC Bank")
    if merchant_id is None:
        pytest.skip("Part 6 seed data not loaded — HDFC Bank not found.")

    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/block")
    groups = resp.json()["sheet"]["groups"]
    labels = [g["label"] for g in groups]
    assert labels == ["Prerequisites", "Input from the requester", "Validations"]


def test_hdfc_block_columns(api_client):
    merchant_id = _resolve_merchant_id(api_client, "HDFC Bank")
    if merchant_id is None:
        pytest.skip("Part 6 seed data not loaded — HDFC Bank not found.")

    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/block")
    groups = resp.json()["sheet"]["groups"]

    prerequisites = next(g for g in groups if g["label"] == "Prerequisites")
    assert [c["name"] for c in prerequisites["columns"]] == ["Card Status", "Balance", "Requester"]

    validations = next(g for g in groups if g["label"] == "Validations")
    assert [c["name"] for c in validations["columns"]] == [
        "Activating Merchant Group",
        "CPG",
        "Descriptive Outlet",
        "Action",
    ]


def test_heterogeneous_structure_hdfc_vs_icici_block(api_client):
    """At least one heterogeneous-structure check: HDFC's and ICICI's
    Block sheets have genuinely different column sets — the API must
    not normalize/flatten them into a shared shape."""
    hdfc_id = _resolve_merchant_id(api_client, "HDFC Bank")
    icici_id = _resolve_merchant_id(api_client, "ICICI Bank")
    if hdfc_id is None or icici_id is None:
        pytest.skip("Part 6 seed data not loaded — HDFC/ICICI Bank not found.")

    hdfc_block = api_client.get(f"/api/v1/merchants/{hdfc_id}/sheets/block").json()
    icici_block = api_client.get(f"/api/v1/merchants/{icici_id}/sheets/block").json()

    hdfc_columns = {c["name"] for g in hdfc_block["sheet"]["groups"] for c in g["columns"]}
    icici_columns = {c["name"] for g in icici_block["sheet"]["groups"] for c in g["columns"]}

    assert hdfc_columns != icici_columns
    assert "Descriptive Outlet" in hdfc_columns and "Descriptive Outlet" not in icici_columns
    assert "Card Type" in icici_columns and "Card Type" not in hdfc_columns


# --- 4-5: ordering (synthetic, deliberately inserted out of order) --------


def test_ordering_of_groups(api_client, sop_fixture):
    resp = api_client.get(f"/api/v1/merchants/{sop_fixture['merchant'].id}/sheets/test-block")
    assert resp.status_code == 200
    groups = resp.json()["sheet"]["groups"]
    assert [g["label"] for g in groups] == ["Group A", "Group B"]
    assert [g["sortOrder"] for g in groups] == [0, 1]


def test_ordering_of_columns(api_client, sop_fixture):
    resp = api_client.get(f"/api/v1/merchants/{sop_fixture['merchant'].id}/sheets/test-block")
    groups = resp.json()["sheet"]["groups"]

    group_a = next(g for g in groups if g["label"] == "Group A")
    assert [c["name"] for c in group_a["columns"]] == ["A-Only"]

    group_b = next(g for g in groups if g["label"] == "Group B")
    assert [c["name"] for c in group_b["columns"]] == ["B-First", "B-Second"]
    assert [c["sortOrder"] for c in group_b["columns"]] == [0, 1]


# --- 6-9: rows, pagination, dynamic data -----------------------------------


def test_get_hdfc_block_rows(api_client):
    merchant_id = _resolve_merchant_id(api_client, "HDFC Bank")
    if merchant_id is None:
        pytest.skip("Part 6 seed data not loaded — HDFC Bank not found.")

    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/block/rows")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "page", "pageSize"}
    assert body["total"] == 6  # HDFC's curated Block sheet has 6 rows (Part 2 analysis)
    assert "Card Status" in body["items"][0]["data"]


def test_row_pagination(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    page1 = api_client.get(
        f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"page": 1, "pageSize": 2}
    ).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2

    page2 = api_client.get(
        f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"page": 2, "pageSize": 2}
    ).json()
    assert len(page2["items"]) == 2

    page3 = api_client.get(
        f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"page": 3, "pageSize": 2}
    ).json()
    assert len(page3["items"]) == 1


def test_no_duplicate_rows_across_pages(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    pages = [
        api_client.get(
            f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"page": p, "pageSize": 2}
        ).json()
        for p in (1, 2, 3)
    ]
    all_ids = [item["id"] for page in pages for item in page["items"]]
    assert len(all_ids) == len(set(all_ids)) == 5


def test_dynamic_jsonb_row_data(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"pageSize": 10})
    body = resp.json()

    row_one = next(item for item in body["items"] if item["data"]["A-Only"] == "Row One")
    assert row_one["data"] == {
        "A-Only": "Row One",
        "B-First": "Contains Activation Keyword Here",
        "B-Second": "x1",
    }


# --- 10-12: search -----------------------------------------------------------


def test_search_sop_rows(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"search": "Activation"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["data"]["A-Only"] == "Row One"


def test_search_is_case_insensitive(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    # stored value is "Contains Activation Keyword Here" (mixed case);
    # search using a different case entirely.
    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"search": "ACTIVATION"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["data"]["A-Only"] == "Row One"

    resp2 = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"search": "activation"})
    assert resp2.json()["total"] == 1


def test_search_does_not_return_unrelated_rows(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    resp = api_client.get(
        f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows",
        params={"search": "zzz-substring-that-matches-nothing"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 0
    assert body["items"] == []


# --- 13-15: not-found / shared-sheet exclusion ------------------------------


def test_nonexistent_merchant_returns_404(api_client):
    resp = api_client.get("/api/v1/merchants/2147483000/sheets/block")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "MERCHANT_NOT_FOUND"


def test_nonexistent_sheet_returns_404(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/not-a-real-key")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SOP_SHEET_NOT_FOUND"


def test_merchant_specific_endpoint_does_not_return_shared_escalation(api_client, sop_fixture):
    # sop_fixture's merchant has NO "escalation" sheet of its own — this
    # must 404, never silently fall back to the real shared escalation sheet.
    merchant_id = sop_fixture["merchant"].id
    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/escalation")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SOP_SHEET_NOT_FOUND"


# --- 16-20: common escalation (real, singleton seeded data) ----------------


def _get_common_escalation_or_skip(api_client, **params):
    resp = api_client.get("/api/v1/sop/escalation/common", params=params)
    if resp.status_code == 404:
        pytest.skip("Part 6 seed data not loaded — shared escalation sheet not found.")
    return resp


def test_get_common_escalation(api_client):
    resp = _get_common_escalation_or_skip(api_client)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"sheet", "rows"}
    assert body["sheet"]["key"] == "escalation"


def test_common_escalation_has_merchant_id_null_internally(api_client, db_session):
    resp = _get_common_escalation_or_skip(api_client)
    sheet_id = resp.json()["sheet"]["id"]

    row = db_session.get(SopSheet, sheet_id)
    assert row is not None
    assert row.merchant_id is None
    assert row.key == "escalation"


def test_common_escalation_returns_expected_seeded_structure(api_client):
    resp = _get_common_escalation_or_skip(api_client)
    body = resp.json()

    groups = body["sheet"]["groups"]
    assert len(groups) == 1
    assert groups[0]["label"] == "Escalation Matrix"
    assert [c["name"] for c in groups[0]["columns"]] == ["Severity", "Owner", "TAT", "Next Level"]
    assert body["rows"]["total"] == 4


def test_search_common_escalation(api_client):
    resp = _get_common_escalation_or_skip(api_client, search="Support Head")
    body = resp.json()
    assert body["rows"]["total"] == 1
    assert body["rows"]["items"][0]["data"]["Next Level"] == "Support Head"


def test_common_escalation_pagination(api_client):
    resp = _get_common_escalation_or_skip(api_client, page=1, pageSize=2)
    body = resp.json()
    assert body["rows"]["total"] == 4
    assert len(body["rows"]["items"]) == 2

    resp2 = _get_common_escalation_or_skip(api_client, page=2, pageSize=2)
    body2 = resp2.json()
    assert len(body2["rows"]["items"]) == 2

    ids_page1 = {i["id"] for i in body["rows"]["items"]}
    ids_page2 = {i["id"] for i in body2["rows"]["items"]}
    assert ids_page1.isdisjoint(ids_page2)


# --- 21-22: invalid pagination -----------------------------------------------


def test_invalid_page_returns_422(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    resp = api_client.get(f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"page": 0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    resp2 = api_client.get("/api/v1/sop/escalation/common", params={"page": 0})
    assert resp2.status_code == 422


def test_invalid_page_size_returns_422(api_client, sop_fixture):
    merchant_id = sop_fixture["merchant"].id
    too_small = api_client.get(
        f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"pageSize": 0}
    )
    assert too_small.status_code == 422

    too_large = api_client.get(
        f"/api/v1/merchants/{merchant_id}/sheets/test-block/rows", params={"pageSize": 500}
    )
    assert too_large.status_code == 422

    common_too_large = api_client.get("/api/v1/sop/escalation/common", params={"pageSize": 500})
    assert common_too_large.status_code == 422
