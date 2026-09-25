"""
API tests for the Bin Series Version History (read-only) endpoint
(Version History + Before/After task; renamed from /history to
/version-history by the API Naming task):
  GET /api/v1/bin-series/version-history

Backs BinTable.jsx's "Version History" button (VersionHistoryModal.jsx /
ChangePreviewModal.jsx). Confirmed by frontend inspection: GLOBAL to the
whole table (no per-row trigger), newest-first, the frontend's own local
changeLog uses a 6-way action vocabulary (create/update/delete/upload/
column/rename), and clicking an entry re-uses the SAME entry object
(no second request) — so every entry here must already carry its full
before/after snapshot.

These tests exercise the REAL mutation endpoints (POST /bin-series/create,
PUT .../{id}/update, DELETE .../{id}/delete, POST/PATCH
/bin-series/custom-columns, POST /bulk-upload) and then
read back GET /bin-series/version-history to verify the resulting
revision has the right action/before/after/actor — since history is
GLOBAL, tests
locate "their" entry among possibly-other entries by matching
`entityId` (the created/updated/deleted record or column's own id,
which is unique) rather than assuming position, except where explicitly
testing ordering/limit.

Fixture bin_iin range reserved for this file: 999730-999759 (see other
test_bin_series_*.py files for their own reserved ranges — no overlap).
"""
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


def _make_bin(db_session, *, bin_iin, merchant_prefix, issuer="Test Hist Issuer", custom_fields=None, **kwargs):
    record = BinRecord(
        issuer=issuer,
        card_program_group_name=kwargs.pop("card_program_group_name", "Test Hist Program"),
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        custom_fields=custom_fields or {},
        **kwargs,
    )
    db_session.add(record)
    db_session.flush()
    return record


def _find_entry(items, *, entity_id, action):
    for item in items:
        if item["entityId"] == entity_id and item["action"] == action:
            return item
    return None


@pytest.fixture()
def raw_client():
    """A TestClient with NO dependency overrides — real authentication
    runs for real on every request made through this. Same pattern as
    tests/integration/test_auth.py's fixture of the same name."""
    return TestClient(app)


@pytest.fixture()
def actor(db_session):
    return _make_user(db_session, name="Test Hist Actor", email="test.hist.actor@example.invalid")


def _history(api_client, limit=50):
    resp = api_client.get("/api/v1/bin-series/version-history", params={"limit": limit})
    assert resp.status_code == 200
    return resp.json()


# =====================================================================
# Authentication
# =====================================================================


def test_history_requires_authentication(raw_client):
    resp = raw_client.get("/api/v1/bin-series/version-history")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


# =====================================================================
# Create
# =====================================================================


def test_create_generates_history_entry(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/create",
        json={
            "issuer": "Test Hist Create Issuer",
            "cardProgramGroupName": "Program",
            "binIin": "999730",
            "merchantPrefix": "001",
            "instanceName": "Instance A",
        },
    )
    assert resp.status_code == 201
    record_id = resp.json()["id"]

    entry = _find_entry(_history(api_client)["items"], entity_id=record_id, action="create")
    assert entry is not None
    assert entry["before"] is None
    assert entry["after"] == {
        "id": record_id,
        "issuer": "Test Hist Create Issuer",
        "cardProgramGroupName": "Program",
        "binIin": "999730",
        "merchantPrefix": "001",
        "merchantId": None,
        "instanceName": "Instance A",
        "customFields": {},
    }
    assert entry["actor"]["id"] == actor.id
    assert entry["actor"]["name"] == actor.name
    assert entry["entityType"] == "bin_record"
    assert entry["changeDescription"]


def test_create_with_custom_fields_captured_in_after(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test Hist Region")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/create",
        json={
            "issuer": "Test Hist Create Custom",
            "cardProgramGroupName": "Program",
            "binIin": "999731",
            "merchantPrefix": "001",
            "instanceName": "Instance A",
            "customFields": {"Test Hist Region": "North"},
        },
    )
    assert resp.status_code == 201
    record_id = resp.json()["id"]

    entry = _find_entry(_history(api_client)["items"], entity_id=record_id, action="create")
    assert entry["after"]["customFields"] == {"Test Hist Region": "North"}


# =====================================================================
# Update
# =====================================================================


def test_update_generates_history_with_before_and_after(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, bin_iin="999732", merchant_prefix="001", issuer="Test Hist Update Before",
        instance_name="Instance A",
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Hist Update After"})
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="update")
    assert entry is not None
    assert entry["before"]["issuer"] == "Test Hist Update Before"
    assert entry["after"]["issuer"] == "Test Hist Update After"


def test_update_unchanged_fields_preserved_in_both_snapshots(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, bin_iin="999733", merchant_prefix="001", issuer="Test Hist Preserve",
        card_program_group_name="Original Program", instance_name="Instance A",
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Hist Preserve Changed"})
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="update")
    assert entry["before"]["cardProgramGroupName"] == "Original Program"
    assert entry["after"]["cardProgramGroupName"] == "Original Program"
    assert entry["before"]["binIin"] == "999733"
    assert entry["after"]["binIin"] == "999733"


def test_update_fields_changed_present(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, bin_iin="999734", merchant_prefix="001", issuer="Test Hist FieldsChanged",
        instance_name="Instance A",
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Hist FieldsChanged V2"})
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="update")
    assert entry["fieldsChanged"] is not None
    assert "Issuer" in entry["fieldsChanged"]
    assert entry["fieldsChanged"]["Issuer"] == {"from": "Test Hist FieldsChanged", "to": "Test Hist FieldsChanged V2"}


def test_no_op_update_does_not_generate_history_entry(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, bin_iin="999735", merchant_prefix="001", issuer="Test Hist NoOp", instance_name="Instance A",
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Hist NoOp"})
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="update")
    assert entry is None


# =====================================================================
# Custom field value changes (via the same update path)
# =====================================================================


def test_custom_field_update_captures_before_and_after(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test Hist Priority")
    record = _make_bin(
        db_session, bin_iin="999736", merchant_prefix="001", issuer="Test Hist CF Update",
        instance_name="Instance A", custom_fields={"Test Hist Priority": "Low"},
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"customFields": {"Test Hist Priority": "High"}})
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="update")
    assert entry["before"]["customFields"] == {"Test Hist Priority": "Low"}
    assert entry["after"]["customFields"] == {"Test Hist Priority": "High"}


def test_custom_field_merge_preserves_unrelated_keys_in_snapshots(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test Hist A", display_order=1)
    _make_column(db_session, key="Test Hist B", display_order=2)
    record = _make_bin(
        db_session, bin_iin="999737", merchant_prefix="001", issuer="Test Hist CF Merge",
        instance_name="Instance A", custom_fields={"Test Hist A": "Alpha", "Test Hist B": "Beta"},
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"customFields": {"Test Hist A": "Alpha2"}})
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="update")
    assert entry["before"]["customFields"] == {"Test Hist A": "Alpha", "Test Hist B": "Beta"}
    assert entry["after"]["customFields"] == {"Test Hist A": "Alpha2", "Test Hist B": "Beta"}


def test_clearing_custom_field_reflected_as_empty_string(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test Hist Clearable")
    record = _make_bin(
        db_session, bin_iin="999738", merchant_prefix="001", issuer="Test Hist CF Clear",
        instance_name="Instance A", custom_fields={"Test Hist Clearable": "SomeValue"},
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"customFields": {"Test Hist Clearable": ""}})
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="update")
    assert entry["before"]["customFields"] == {"Test Hist Clearable": "SomeValue"}
    assert entry["after"]["customFields"] == {"Test Hist Clearable": ""}


# =====================================================================
# Delete
# =====================================================================


def test_delete_generates_history_with_full_before_and_null_after(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, bin_iin="999739", merchant_prefix="001", issuer="Test Hist Delete",
        card_program_group_name="Doomed Program", instance_name="Instance A",
        custom_fields={},
    )
    record_id = record.id
    act_as(actor)
    resp = api_client.delete(f"/api/v1/bin-series/{record_id}/delete")
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=record_id, action="delete")
    assert entry is not None
    assert entry["before"] == {
        "id": record_id,
        "issuer": "Test Hist Delete",
        "cardProgramGroupName": "Doomed Program",
        "binIin": "999739",
        "merchantPrefix": "001",
        "merchantId": None,
        "instanceName": "Instance A",
        "customFields": {},
    }
    assert entry["after"] is None


def test_deleted_record_history_still_available_after_deletion(api_client, db_session, act_as, actor):
    """Step 8: the row no longer exists in bin_records, but its history
    must still be retrievable — the endpoint never queries bin_records
    to reconstruct this, only the persisted revision snapshot."""
    record = _make_bin(
        db_session, bin_iin="999740", merchant_prefix="001", issuer="Test Hist DeletedStillVisible",
        instance_name="Instance A",
    )
    record_id = record.id
    act_as(actor)
    api_client.delete(f"/api/v1/bin-series/{record_id}/delete")

    # The record is truly gone now.
    get_resp = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999740", "merchantPrefix": "001"})
    assert get_resp.status_code == 404

    # But its history (both the create fixture wrote directly, so only
    # delete exists via the API here) is still there.
    entry = _find_entry(_history(api_client)["items"], entity_id=record_id, action="delete")
    assert entry is not None
    assert entry["before"]["issuer"] == "Test Hist DeletedStillVisible"


# =====================================================================
# Custom columns: creation, rename
# =====================================================================


def test_custom_column_creation_history(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Hist NewCol"})
    assert resp.status_code == 201
    column_id = resp.json()["id"]

    entry = _find_entry(_history(api_client)["items"], entity_id=column_id, action="column")
    assert entry is not None
    assert entry["before"] is None
    assert entry["after"] == {"id": column_id, "key": "Test Hist NewCol", "name": "Test Hist NewCol", "displayOrder": resp.json()["displayOrder"]}
    assert entry["entityType"] == "bin_custom_column"


def test_custom_column_rename_history_preserves_immutable_key(api_client, db_session, act_as, actor):
    column = _make_column(db_session, key="Test Hist StableKey", name="Test Hist StableKey")
    act_as(actor)
    resp = api_client.patch(f"/api/v1/bin-series/custom-columns/{column.id}/rename", json={"name": "Test Hist Renamed"})
    assert resp.status_code == 200

    entry = _find_entry(_history(api_client)["items"], entity_id=column.id, action="rename")
    assert entry is not None
    assert entry["before"]["key"] == "Test Hist StableKey"
    assert entry["after"]["key"] == "Test Hist StableKey"
    assert entry["before"]["name"] == "Test Hist StableKey"
    assert entry["after"]["name"] == "Test Hist Renamed"


# =====================================================================
# Bulk upload history
# =====================================================================


def test_bulk_upload_add_new_generates_upload_history_with_after_snapshot(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "Instance A",
            "rows": [
                {
                    "issuer": "Test Hist Bulk New",
                    "cardProgramGroupName": "Program",
                    "binIin": "999741",
                    "merchantPrefix": "001",
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["createdCount"] == 1

    items = _history(api_client)["items"]
    entry = next((e for e in items if e["action"] == "upload" and (e.get("after") or {}).get("binIin") == "999741"), None)
    assert entry is not None
    assert entry["before"] is None
    assert entry["after"]["issuer"] == "Test Hist Bulk New"


def test_bulk_upload_update_existing_generates_upload_history_with_before_after(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, bin_iin="999742", merchant_prefix="001", issuer="Test Hist Bulk Old",
        instance_name="Instance A",
    )
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "Instance A",
            "rows": [{"binIin": "999742", "merchantPrefix": "001", "issuer": "Test Hist Bulk New Name"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["updatedCount"] == 1

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="upload")
    assert entry is not None
    assert entry["before"]["issuer"] == "Test Hist Bulk Old"
    assert entry["after"]["issuer"] == "Test Hist Bulk New Name"


# =====================================================================
# History API: ordering, limit, actor
# =====================================================================


def test_history_returns_newest_first(api_client, db_session, act_as, actor):
    record_a = _make_bin(db_session, bin_iin="999743", merchant_prefix="001", issuer="Test Hist OrderA", instance_name="Instance A")
    record_b = _make_bin(db_session, bin_iin="999744", merchant_prefix="001", issuer="Test Hist OrderB", instance_name="Instance A")
    act_as(actor)
    api_client.delete(f"/api/v1/bin-series/{record_a.id}/delete")
    api_client.delete(f"/api/v1/bin-series/{record_b.id}/delete")

    items = _history(api_client, limit=2)["items"]
    assert len(items) == 2
    # record_b was deleted LAST, so it must appear FIRST (newest-first).
    assert items[0]["entityId"] == record_b.id
    assert items[1]["entityId"] == record_a.id


def test_history_limit_is_respected(api_client, db_session, act_as, actor):
    for i in range(3):
        record = _make_bin(
            db_session, bin_iin=f"99974{5 + i}", merchant_prefix="001", issuer=f"Test Hist Limit {i}",
            instance_name="Instance A",
        )
        act_as(actor)
        api_client.delete(f"/api/v1/bin-series/{record.id}/delete")

    resp = api_client.get("/api/v1/bin-series/version-history", params={"limit": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["total"] >= 3


def test_history_default_limit_is_five(api_client, db_session, act_as, actor):
    for i in range(6):
        record = _make_bin(
            db_session, bin_iin=f"99975{i}", merchant_prefix="002", issuer=f"Test Hist Default Limit {i}",
            instance_name="Instance A",
        )
        act_as(actor)
        api_client.delete(f"/api/v1/bin-series/{record.id}/delete")

    resp = api_client.get("/api/v1/bin-series/version-history")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 5


def test_history_actor_reflects_authenticated_caller(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, bin_iin="999756", merchant_prefix="001", issuer="Test Hist ActorCheck", instance_name="Instance A")
    act_as(actor)
    api_client.delete(f"/api/v1/bin-series/{record.id}/delete")

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="delete")
    assert entry["actor"] == {"id": actor.id, "name": actor.name}


def test_history_target_label_and_change_description_present(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, bin_iin="999757", merchant_prefix="001", issuer="Test Hist LabelCheck", instance_name="Instance A")
    act_as(actor)
    api_client.delete(f"/api/v1/bin-series/{record.id}/delete")

    entry = _find_entry(_history(api_client)["items"], entity_id=record.id, action="delete")
    assert "Test Hist LabelCheck" in entry["targetLabel"]
    assert entry["changeDescription"] == "BIN record deleted"


# =====================================================================
# Regression
# =====================================================================


def test_generic_revisions_endpoint_still_works_regression(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, bin_iin="999758", merchant_prefix="001", issuer="Test Hist RevisionsRegression", instance_name="Instance A")
    act_as(actor)
    api_client.delete(f"/api/v1/bin-series/{record.id}/delete")

    resp = api_client.get("/api/v1/revisions", params={"search": "Test Hist RevisionsRegression"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_bulk_upload_response_contract_unchanged_regression(api_client, act_as, actor):
    """Bulk Upload is frozen — this task only added metadata keys to its
    existing internal audit calls, never touched its request/response
    contract. Confirms the response still has exactly the established
    shape."""
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "Instance A",
            "rows": [
                {
                    "issuer": "Test Hist BulkContract",
                    "cardProgramGroupName": "Program",
                    "binIin": "999759",
                    "merchantPrefix": "001",
                }
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "mode", "totalRows", "createdCount", "updatedCount", "skippedCount",
        "failedCount", "failedRows", "skippedRows",
    }
