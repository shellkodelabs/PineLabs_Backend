"""
API tests for `customFields` on the existing Bin Series row endpoints
(Dynamic/Custom Columns task, Steps 4-6): how a custom column's VALUES
attach to an individual bin_records row via the already-existing
create/update/list/resolve/search endpoints — as opposed to
test_bin_custom_columns_api.py, which covers the column REGISTRY itself
(GET /bin-series/custom-columns, POST .../custom-columns/create,
PATCH .../custom-columns/{id}/rename — renamed from /columns by the
API Naming task).

Fixture bin_iin range reserved for this file: 999960-999979 (see other
test_bin_series_*.py files for other files' reserved ranges — no overlap).
"""
import pytest

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


def _make_bin(db_session, *, bin_iin, merchant_prefix, issuer="Test CF Issuer", custom_fields=None, **kwargs):
    record = BinRecord(
        issuer=issuer,
        card_program_group_name=kwargs.pop("card_program_group_name", "Test CF Program"),
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        custom_fields=custom_fields or {},
        **kwargs,
    )
    db_session.add(record)
    db_session.flush()
    return record


@pytest.fixture()
def actor(db_session):
    return _make_user(db_session, name="Test CF Actor", email="test.cf.actor@example.invalid")


# =====================================================================
# 7. Custom value creation (via POST /bin-series)
# =====================================================================


def test_create_bin_record_with_custom_fields(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test CF Region")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/create",
        json={
            "issuer": "Test CF Create Issuer",
            "cardProgramGroupName": "Program",
            "binIin": "999960",
            "merchantPrefix": "001",
            "instanceName": "Instance A",
            "customFields": {"Test CF Region": "North"},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["customFields"] == {"Test CF Region": "North"}


def test_create_bin_record_without_custom_fields_defaults_to_empty_dict(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/create",
        json={
            "issuer": "Test CF NoCustom Issuer",
            "cardProgramGroupName": "Program",
            "binIin": "999961",
            "merchantPrefix": "001",
            "instanceName": "Instance A",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["customFields"] == {}


def test_create_bin_record_unknown_custom_field_key_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/create",
        json={
            "issuer": "Test CF Unknown Issuer",
            "cardProgramGroupName": "Program",
            "binIin": "999962",
            "merchantPrefix": "001",
            "instanceName": "Instance A",
            "customFields": {"Test CF Nonexistent Column": "value"},
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BIN_CUSTOM_COLUMN_NOT_FOUND"


# =====================================================================
# 8-10. Custom value update / clearing / unrelated values preserved
# =====================================================================


def test_update_bin_record_sets_custom_field(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test CF Priority")
    record = _make_bin(db_session, bin_iin="999963", merchant_prefix="001", instance_name="Instance A")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"customFields": {"Test CF Priority": "High"}})
    assert resp.status_code == 200
    assert resp.json()["customFields"] == {"Test CF Priority": "High"}


def test_update_bin_record_custom_field_merges_preserving_unrelated_keys(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test CF A", display_order=1)
    _make_column(db_session, key="Test CF B", display_order=2)
    record = _make_bin(
        db_session, bin_iin="999964", merchant_prefix="001", instance_name="Instance A",
        custom_fields={"Test CF A": "Alpha", "Test CF B": "Beta"},
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"customFields": {"Test CF A": "Alpha2"}})
    assert resp.status_code == 200
    assert resp.json()["customFields"] == {"Test CF A": "Alpha2", "Test CF B": "Beta"}  # B untouched


def test_update_bin_record_custom_field_to_empty_string_is_a_valid_clear(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test CF Clearable")
    record = _make_bin(
        db_session, bin_iin="999965", merchant_prefix="001", instance_name="Instance A",
        custom_fields={"Test CF Clearable": "SomeValue"},
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"customFields": {"Test CF Clearable": ""}})
    assert resp.status_code == 200
    assert resp.json()["customFields"] == {"Test CF Clearable": ""}


def test_update_bin_record_unknown_custom_field_key_rejected(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, bin_iin="999966", merchant_prefix="001", instance_name="Instance A")
    act_as(actor)
    resp = api_client.put(
        f"/api/v1/bin-series/{record.id}/update", json={"customFields": {"Test CF Nonexistent": "x"}}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BIN_CUSTOM_COLUMN_NOT_FOUND"


def test_update_bin_record_other_fields_preserves_custom_fields(api_client, db_session, act_as, actor):
    """14. Custom values survive a normal (non-customFields) row update."""
    _make_column(db_session, key="Test CF Survivor")
    record = _make_bin(
        db_session, bin_iin="999967", merchant_prefix="001", instance_name="Instance A",
        custom_fields={"Test CF Survivor": "StillHere"},
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test CF Renamed Issuer"})
    assert resp.status_code == 200
    assert resp.json()["customFields"] == {"Test CF Survivor": "StillHere"}


def test_update_bin_record_custom_field_creates_audit_revision(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test CF Audited")
    record = _make_bin(db_session, bin_iin="999968", merchant_prefix="001", instance_name="Instance A")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"customFields": {"Test CF Audited": "V1"}})
    assert resp.status_code == 200

    from sqlalchemy import select
    from app.models import Revision
    stmt = select(Revision).where(Revision.entity_type == "bin_record", Revision.entity_id == record.id)
    revisions = db_session.execute(stmt).scalars().all()
    assert any("Test CF Audited" in r.change_description for r in revisions)


# =====================================================================
# 12. Custom fields returned by the Bin Series list
# =====================================================================


def test_list_bin_series_includes_custom_fields(api_client, db_session, act_as, actor):
    _make_bin(
        db_session, bin_iin="999969", merchant_prefix="001", issuer="Test CF List Issuer",
        custom_fields={"Test CF ListKey": "ListValue"},
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test CF List Issuer"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["customFields"] == {"Test CF ListKey": "ListValue"}


# =====================================================================
# 13. Custom fields returned by GET /lookup (single); NOT part of
# bulk-lookup's response shape (ResolveBatchResultItem has no
# customFields field at all — confirmed in app/schemas/bin_series.py).
# =====================================================================


def test_resolve_single_includes_custom_fields(api_client, db_session):
    _make_bin(
        db_session, bin_iin="999970", merchant_prefix="001",
        custom_fields={"Test CF ResolveKey": "ResolveValue"},
    )
    resp = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999970", "merchantPrefix": "001"})
    assert resp.status_code == 200
    assert resp.json()["customFields"] == {"Test CF ResolveKey": "ResolveValue"}


def test_resolve_batch_response_has_no_custom_fields_key(api_client, db_session):
    """bulk-lookup's schema (ResolveBatchResultItem) was deliberately
    NOT extended with customFields — BulkLookupModal.jsx only ever
    displays issuer/program/bin/prefix, never custom columns."""
    _make_bin(
        db_session, bin_iin="999971", merchant_prefix="001",
        custom_fields={"Test CF Unseen": "Value"},
    )
    resp = api_client.post("/api/v1/bin-series/bulk-lookup", json={"cards": ["999971001"]})
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert "customFields" not in result


# =====================================================================
# 15. Search across custom values
# =====================================================================


def test_search_matches_custom_field_value(api_client, db_session):
    _make_bin(
        db_session, bin_iin="999972", merchant_prefix="001", issuer="Test CF Search Target",
        custom_fields={"Test CF SearchKey": "UniqueSearchableValue12345"},
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "UniqueSearchableValue12345"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["binIin"] == "999972"


def test_search_does_not_match_custom_field_key_name_only_values(api_client, db_session):
    _make_bin(
        db_session, bin_iin="999973", merchant_prefix="001", issuer="Test CF Search KeyOnly",
        custom_fields={"Test CF UnsearchableKeyLabel98765": "irrelevant"},
    )
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "UnsearchableKeyLabel98765"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["binIin"] != "999973" for i in items)


def test_search_by_existing_fields_still_works_regression(api_client, db_session):
    _make_bin(db_session, bin_iin="999974", merchant_prefix="001", issuer="Test CF Regression Issuer")
    resp = api_client.get("/api/v1/bin-series/list", params={"search": "Test CF Regression Issuer"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["binIin"] == "999974" for i in items)


# =====================================================================
# 19. Migration/model behavior — direct model-level sanity
# =====================================================================


def test_bin_record_custom_fields_defaults_to_empty_dict_at_model_level(db_session):
    record = _make_bin(db_session, bin_iin="999975", merchant_prefix="001")
    db_session.refresh(record)
    assert record.custom_fields == {}


# =====================================================================
# 20. Regression — existing CRUD unaffected by customFields addition
# =====================================================================


def test_create_and_delete_bin_record_without_custom_fields_regression(api_client, db_session, act_as, actor):
    act_as(actor)
    create_resp = api_client.post(
        "/api/v1/bin-series/create",
        json={
            "issuer": "Test CF Regression CRUD",
            "cardProgramGroupName": "Program",
            "binIin": "999976",
            "merchantPrefix": "001",
            "instanceName": "Instance A",
        },
    )
    assert create_resp.status_code == 201
    record_id = create_resp.json()["id"]

    delete_resp = api_client.delete(f"/api/v1/bin-series/{record_id}/delete")
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"id": record_id, "deleted": True}
