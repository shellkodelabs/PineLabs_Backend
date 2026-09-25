"""
API tests for the Bin Series CSV export endpoint (Export task):
  GET /api/v1/bin-series/export

Backs BinTable.jsx's Export button (exportSheet() -> utils/csv.js's
serializeCsv/downloadCsv). Confirmed by frontend inspection: Export
downloads the FULL dataset (ignores the search box entirely, no
pagination, no row-selection UI exists), columns are
issuer/cardProgramGroupName/binIin/merchantPrefix + custom columns (by
display name, in display order) + updatedBy/updatedAt last, comma
delimiter, CRLF rows, RFC4180-style quoting.

Every test scopes its assertions to its OWN fixture rows via a unique
`search` substring, rather than asserting on the whole table's row
count — the real DB may carry other tests' committed... (no, rolled
back) or pre-existing seed data, so "export with no filters returns
everything" is verified via a row-count DELTA / a uniquely-searchable
issuer, never an assumed total.

Fixture bin_iin range reserved for this file: 999980-999999 (see other
test_bin_series_*.py files for their own reserved ranges — no overlap).
"""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import BinCustomColumn, BinRecord, User


def _make_user(db_session, *, name, email, role="Admin", status="Active"):
    user = User(name=name, email=email, role=role, status=status)
    db_session.add(user)
    db_session.flush()
    return user


def _make_column(db_session, *, key, name=None, display_order=1):
    column = BinCustomColumn(key=key, name=name or key, display_order=display_order)
    db_session.add(column)
    db_session.flush()
    return column


def _make_bin(db_session, *, bin_iin, merchant_prefix, issuer="Test Export Issuer", **kwargs):
    record = BinRecord(
        issuer=issuer,
        card_program_group_name=kwargs.pop("card_program_group_name", "Test Export Program"),
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        custom_fields=kwargs.pop("custom_fields", {}) or {},
        **kwargs,
    )
    db_session.add(record)
    db_session.flush()
    return record


def _parse_csv(text: str):
    """Returns (header, rows) using Python's own csv.reader — verifying
    against a real CSV parser rather than string-matching is a much
    stronger check for quoting/escaping correctness."""
    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)
    return all_rows[0], all_rows[1:]


@pytest.fixture()
def raw_client():
    """A TestClient with NO dependency overrides — real authentication
    runs for real on every request made through this. Same pattern as
    tests/integration/test_auth.py's fixture of the same name."""
    return TestClient(app)


@pytest.fixture()
def actor(db_session):
    return _make_user(db_session, name="Test Export Actor", email="test.export.actor@example.invalid")


# =====================================================================
# Authentication
# =====================================================================


def test_export_requires_authentication(raw_client):
    resp = raw_client.get("/api/v1/bin-series/export")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


# =====================================================================
# Headers / content-type / filename
# =====================================================================


def test_export_content_type_and_disposition(api_client):
    resp = api_client.get("/api/v1/bin-series/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="bin-series.csv"'


def test_export_header_row_order_with_no_custom_columns(api_client, db_session):
    # Ensure a clean slate for THIS assertion by not depending on other
    # tests' custom columns — read the header directly, which must
    # start with the four fixed base labels regardless of how many
    # custom columns currently exist in the (shared, rolled-back) DB.
    resp = api_client.get("/api/v1/bin-series/export")
    assert resp.status_code == 200
    header, _ = _parse_csv(resp.text)
    assert header[:4] == ["Issuer", "Card Program Group Name", "BIN / IIN Code", "Merchant Prefix"]
    assert header[-2:] == ["Updated By", "Updated At"]


# =====================================================================
# No filters -> all matching rows; search/filter scoping
# =====================================================================


def test_export_with_no_filters_includes_all_fixture_rows(api_client, db_session):
    _make_bin(db_session, bin_iin="999980", merchant_prefix="001", issuer="Test Export AllRows Alpha")
    _make_bin(db_session, bin_iin="999981", merchant_prefix="001", issuer="Test Export AllRows Beta")

    resp = api_client.get("/api/v1/bin-series/export")
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    issuers = {row[0] for row in rows}
    assert "Test Export AllRows Alpha" in issuers
    assert "Test Export AllRows Beta" in issuers


def test_export_with_search_filters_to_matching_rows_only(api_client, db_session):
    _make_bin(db_session, bin_iin="999982", merchant_prefix="001", issuer="Test Export SearchTarget Unique12345")
    _make_bin(db_session, bin_iin="999983", merchant_prefix="001", issuer="Test Export SearchOther")

    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Unique12345"})
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    assert len(rows) == 1
    assert rows[0][0] == "Test Export SearchTarget Unique12345"


def test_export_search_matches_custom_field_values_too(api_client, db_session):
    _make_column(db_session, key="Test Export SearchCol", display_order=1)
    _make_bin(
        db_session, bin_iin="999984", merchant_prefix="001", issuer="Test Export SearchByCustom",
        custom_fields={"Test Export SearchCol": "UniqueCustomSearchValue987"},
    )
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "UniqueCustomSearchValue987"})
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    assert any(row[0] == "Test Export SearchByCustom" for row in rows)


def test_export_with_issuer_exact_filter(api_client, db_session):
    _make_bin(db_session, bin_iin="999985", merchant_prefix="001", issuer="Test Export ExactIssuer")
    _make_bin(db_session, bin_iin="999986", merchant_prefix="001", issuer="Test Export ExactIssuer Extra")

    resp = api_client.get("/api/v1/bin-series/export", params={"issuer": "Test Export ExactIssuer"})
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    issuers = {row[0] for row in rows}
    assert issuers == {"Test Export ExactIssuer"}


def test_export_with_card_program_group_name_exact_filter(api_client, db_session):
    _make_bin(
        db_session, bin_iin="999987", merchant_prefix="001", issuer="Test Export CPGN A",
        card_program_group_name="Test Export ExactProgram",
    )
    _make_bin(
        db_session, bin_iin="999988", merchant_prefix="001", issuer="Test Export CPGN B",
        card_program_group_name="Test Export Other Program",
    )
    resp = api_client.get(
        "/api/v1/bin-series/export", params={"cardProgramGroupName": "Test Export ExactProgram"}
    )
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    assert {row[0] for row in rows} == {"Test Export CPGN A"}


def test_export_no_match_returns_header_only(api_client):
    resp = api_client.get(
        "/api/v1/bin-series/export", params={"search": "NoSuchIssuerNameXYZUnmatchable999"}
    )
    assert resp.status_code == 200
    header, rows = _parse_csv(resp.text)
    assert header[:4] == ["Issuer", "Card Program Group Name", "BIN / IIN Code", "Merchant Prefix"]
    assert rows == []


def test_export_ignores_pagination_returns_more_than_one_page_worth(api_client, db_session):
    """Regression against the export/list dataset-semantics requirement:
    export must return every matching row, not a single "page". Uses
    bin_iin 999920-999924 — a sub-range of the otherwise-free
    999907-999949 block (see other test_bin_series_*.py files for their
    reserved ranges), distinct from every other bin_iin used elsewhere
    in this file."""
    for i in range(5):
        _make_bin(
            db_session, bin_iin=f"99992{i}", merchant_prefix="002",
            issuer=f"Test Export ManyRows {i}",
        )
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export ManyRows"})
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    assert len(rows) == 5


# =====================================================================
# Custom columns: inclusion, ordering, header labels
# =====================================================================


def test_export_includes_custom_column_header_and_value(api_client, db_session):
    _make_column(db_session, key="Test Export Region", name="Test Export Region", display_order=1)
    _make_bin(
        db_session, bin_iin="999990", merchant_prefix="001", issuer="Test Export WithCustom",
        custom_fields={"Test Export Region": "North"},
    )
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export WithCustom"})
    assert resp.status_code == 200
    header, rows = _parse_csv(resp.text)
    assert "Test Export Region" in header
    col_index = header.index("Test Export Region")
    assert rows[0][col_index] == "North"


def test_export_custom_column_ordering_matches_display_order(api_client, db_session):
    _make_column(db_session, key="Test Export OrderA", name="Test Export OrderA", display_order=10)
    _make_column(db_session, key="Test Export OrderB", name="Test Export OrderB", display_order=11)
    resp = api_client.get("/api/v1/bin-series/export")
    assert resp.status_code == 200
    header, _ = _parse_csv(resp.text)
    idx_a = header.index("Test Export OrderA")
    idx_b = header.index("Test Export OrderB")
    assert idx_a < idx_b
    # Both must appear strictly BEFORE the trailing Updated By/Updated At.
    assert idx_a < header.index("Updated By")
    assert idx_b < header.index("Updated By")


def test_export_uses_custom_column_current_name_not_key(api_client, db_session):
    column = _make_column(db_session, key="Test Export StableKey", name="Test Export StableKey", display_order=1)
    column.name = "Test Export Renamed Label"
    db_session.flush()
    resp = api_client.get("/api/v1/bin-series/export")
    assert resp.status_code == 200
    header, _ = _parse_csv(resp.text)
    assert "Test Export Renamed Label" in header
    assert "Test Export StableKey" not in header


def test_export_empty_custom_field_value_is_included_as_empty_string(api_client, db_session):
    _make_column(db_session, key="Test Export EmptyCol", name="Test Export EmptyCol", display_order=1)
    _make_bin(db_session, bin_iin="999991", merchant_prefix="001", issuer="Test Export EmptyCustom")
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export EmptyCustom"})
    assert resp.status_code == 200
    header, rows = _parse_csv(resp.text)
    col_index = header.index("Test Export EmptyCol")
    assert rows[0][col_index] == ""


# =====================================================================
# CSV escaping: commas, quotes, newlines, UTF-8
# =====================================================================


def test_export_escapes_commas_in_values(api_client, db_session):
    _make_bin(db_session, bin_iin="999992", merchant_prefix="001", issuer="Test Export Comma, Inc.")
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export Comma"})
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    assert rows[0][0] == "Test Export Comma, Inc."
    # Raw text must actually contain a quoted field, not a broken column split.
    assert '"Test Export Comma, Inc."' in resp.text


def test_export_escapes_quotes_in_values(api_client, db_session):
    _make_bin(db_session, bin_iin="999993", merchant_prefix="001", issuer='Test Export "Quoted" Issuer')
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Quoted"})
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    assert rows[0][0] == 'Test Export "Quoted" Issuer'
    assert '""Quoted""' in resp.text


def test_export_escapes_newlines_in_custom_field_values(api_client, db_session):
    _make_column(db_session, key="Test Export NewlineCol", name="Test Export NewlineCol", display_order=1)
    _make_bin(
        db_session, bin_iin="999994", merchant_prefix="001", issuer="Test Export NewlineIssuer",
        custom_fields={"Test Export NewlineCol": "Line1\nLine2"},
    )
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export NewlineIssuer"})
    assert resp.status_code == 200
    header, rows = _parse_csv(resp.text)
    col_index = header.index("Test Export NewlineCol")
    assert rows[0][col_index] == "Line1\nLine2"


def test_export_utf8_non_ascii_values(api_client, db_session):
    _make_bin(db_session, bin_iin="999995", merchant_prefix="001", issuer="Test Export Café Müller")
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Caf"})
    assert resp.status_code == 200
    _, rows = _parse_csv(resp.text)
    assert any(row[0] == "Test Export Café Müller" for row in rows)


def test_export_row_separator_is_crlf(api_client, db_session):
    _make_bin(db_session, bin_iin="999996", merchant_prefix="001", issuer="Test Export CRLFCheck")
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export CRLFCheck"})
    assert resp.status_code == 200
    assert "\r\n" in resp.text


# =====================================================================
# updatedBy / updatedAt
# =====================================================================


def test_export_includes_updated_by_and_updated_at(api_client, db_session, act_as, actor):
    act_as(actor)
    create_resp = api_client.post(
        "/api/v1/bin-series/create",
        json={
            "issuer": "Test Export UpdatedByCheck",
            "cardProgramGroupName": "Program",
            "binIin": "999997",
            "merchantPrefix": "001",
            "instanceName": "Instance A",
        },
    )
    assert create_resp.status_code == 201

    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export UpdatedByCheck"})
    assert resp.status_code == 200
    header, rows = _parse_csv(resp.text)
    updated_by_idx = header.index("Updated By")
    updated_at_idx = header.index("Updated At")
    assert rows[0][updated_by_idx] == actor.name
    assert rows[0][updated_at_idx] != ""


def test_export_updated_by_empty_string_when_never_touched(api_client, db_session):
    _make_bin(db_session, bin_iin="999998", merchant_prefix="001", issuer="Test Export NeverTouched")
    resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export NeverTouched"})
    assert resp.status_code == 200
    header, rows = _parse_csv(resp.text)
    updated_by_idx = header.index("Updated By")
    assert rows[0][updated_by_idx] == ""


# =====================================================================
# Regression: list/search API unaffected
# =====================================================================


def test_list_endpoint_still_works_regression(api_client, db_session):
    _make_bin(db_session, bin_iin="999999", merchant_prefix="001", issuer="Test Export ListRegression")
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Export ListRegression"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["issuer"] == "Test Export ListRegression" for i in items)


def test_export_and_list_agree_on_matching_row_count(api_client, db_session):
    _make_bin(db_session, bin_iin="999989", merchant_prefix="003", issuer="Test Export ParityCheck One")
    _make_bin(db_session, bin_iin="999989", merchant_prefix="004", issuer="Test Export ParityCheck Two")

    list_resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test Export ParityCheck", "pageSize": 200}
    )
    export_resp = api_client.get("/api/v1/bin-series/export", params={"search": "Test Export ParityCheck"})

    assert list_resp.status_code == 200
    assert export_resp.status_code == 200
    _, export_rows = _parse_csv(export_resp.text)
    assert list_resp.json()["total"] == len(export_rows)
