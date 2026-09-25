"""
API tests for Bin Series dynamic/custom columns (Dynamic/Custom Columns
task; endpoints renamed by the API Naming task, from /columns,
/columns, and /columns/{columnId} respectively):
  GET   /api/v1/bin-series/custom-columns
  POST  /api/v1/bin-series/custom-columns/create
  PATCH /api/v1/bin-series/custom-columns/{columnId}/rename

Backs BinTable.jsx's "Add Column" button (AddColumnModal.jsx) and
EditableHeader.jsx's rename. Confirmed by frontend inspection: no delete
endpoint (frontend has no delete-column capability at all), no reorder
endpoint (no ordering UI exists — order is pure creation sequence).

Uses `api_client`/`db_session`/`act_as` (tests/integration/conftest.py)
for the authenticated path, and a local `raw_client` (same pattern as
tests/integration/test_auth.py) for the unauthenticated path.

Fixture column names are all prefixed "Test Custom Col " to stay
collision-proof against whatever real custom columns may already exist.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import BinCustomColumn, Revision, User


def _make_user(db_session, *, name, email, role="Admin", status="Active"):
    user = User(name=name, email=email, role=role, status=status)
    db_session.add(user)
    db_session.flush()
    return user


def _make_column(db_session, *, key, name, display_order=1):
    column = BinCustomColumn(key=key, name=name, display_order=display_order)
    db_session.add(column)
    db_session.flush()
    return column


def _revisions_for(db_session, entity_id):
    stmt = select(Revision).where(Revision.entity_type == "bin_custom_column", Revision.entity_id == entity_id)
    return db_session.execute(stmt).scalars().all()


@pytest.fixture()
def raw_client():
    """A TestClient with NO dependency overrides — real authentication
    runs for real on every request made through this. Same pattern as
    tests/integration/test_auth.py's fixture of the same name."""
    return TestClient(app)


@pytest.fixture()
def actor(db_session):
    return _make_user(db_session, name="Test Custom Col Actor", email="test.custom.col.actor@example.invalid")


# =====================================================================
# 1. List
# =====================================================================


def test_list_custom_columns_empty_or_existing(api_client):
    resp = api_client.get("/api/v1/bin-series/custom-columns")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_returns_created_column(api_client, act_as, actor):
    act_as(actor)
    api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col ListCheck"})
    resp = api_client.get("/api/v1/bin-series/custom-columns")
    names = [c["name"] for c in resp.json()]
    assert "Test Custom Col ListCheck" in names


# =====================================================================
# 16. Column ordering (creation sequence)
# =====================================================================


def test_columns_returned_in_creation_order(api_client, act_as, actor):
    act_as(actor)
    api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col OrderA"})
    api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col OrderB"})
    api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col OrderC"})

    resp = api_client.get("/api/v1/bin-series/custom-columns")
    names = [c["name"] for c in resp.json()]
    idx_a = names.index("Test Custom Col OrderA")
    idx_b = names.index("Test Custom Col OrderB")
    idx_c = names.index("Test Custom Col OrderC")
    assert idx_a < idx_b < idx_c


# =====================================================================
# 2. Create
# =====================================================================


def test_create_custom_column(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col Region"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Custom Col Region"
    assert body["key"] == "Test Custom Col Region"  # key == initial name
    assert body["updatedBy"] == actor.name
    assert body["updatedAt"] is not None
    assert isinstance(body["displayOrder"], int)


def test_create_with_default_value_backfills_existing_rows(api_client, db_session, act_as, actor):
    from app.models import BinRecord
    existing = BinRecord(issuer="Test Custom Col Backfill Issuer", card_program_group_name="P", bin_iin="999950", merchant_prefix="001")
    db_session.add(existing)
    db_session.flush()

    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create",
        json={"name": "Test Custom Col Priority", "defaultValue": "Medium"},
    )
    assert resp.status_code == 201

    db_session.refresh(existing)
    assert existing.custom_fields.get("Test Custom Col Priority") == "Medium"


def test_create_without_default_value_backfills_empty_string(api_client, db_session, act_as, actor):
    from app.models import BinRecord
    existing = BinRecord(issuer="Test Custom Col NoDefaultIssuer", card_program_group_name="P", bin_iin="999951", merchant_prefix="001")
    db_session.add(existing)
    db_session.flush()

    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col NoDefault"})
    assert resp.status_code == 201

    db_session.refresh(existing)
    assert existing.custom_fields.get("Test Custom Col NoDefault") == ""


def test_create_whitespace_name_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "   "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_missing_name_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create", json={})
    assert resp.status_code == 422


# =====================================================================
# 3. Duplicate name rejection
# =====================================================================


def test_create_duplicate_name_rejected(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test Custom Col Dup", name="Test Custom Col Dup")
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col Dup"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BIN_CUSTOM_COLUMN_ALREADY_EXISTS"


def test_create_duplicate_name_case_insensitive_rejected(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test Custom Col CaseDup", name="Test Custom Col CaseDup")
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "test custom col casedup"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BIN_CUSTOM_COLUMN_ALREADY_EXISTS"


# =====================================================================
# 4-5. Rename / rename preserves row values
# =====================================================================


def test_rename_column(api_client, db_session, act_as, actor):
    column = _make_column(db_session, key="Test Custom Col RenameMe", name="Test Custom Col RenameMe")
    act_as(actor)
    resp = api_client.patch(f"/api/v1/bin-series/custom-columns/{column.id}/rename", json={"name": "Test Custom Col Renamed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Test Custom Col Renamed"
    assert body["key"] == "Test Custom Col RenameMe"  # key never changes


def test_rename_preserves_existing_row_values(api_client, db_session, act_as, actor):
    from app.models import BinRecord
    column = _make_column(db_session, key="Test Custom Col StableKey", name="Test Custom Col StableKey")
    record = BinRecord(
        issuer="Test Custom Col RenamePreserve", card_program_group_name="P", bin_iin="999952", merchant_prefix="001",
        custom_fields={"Test Custom Col StableKey": "SomeValue"},
    )
    db_session.add(record)
    db_session.flush()

    act_as(actor)
    resp = api_client.patch(f"/api/v1/bin-series/custom-columns/{column.id}/rename", json={"name": "Test Custom Col NewLabel"})
    assert resp.status_code == 200

    db_session.refresh(record)
    assert record.custom_fields.get("Test Custom Col StableKey") == "SomeValue"  # untouched, still under the OLD key


def test_rename_to_duplicate_name_rejected(api_client, db_session, act_as, actor):
    _make_column(db_session, key="Test Custom Col Taken", name="Test Custom Col Taken", display_order=1)
    column_b = _make_column(db_session, key="Test Custom Col ToRename", name="Test Custom Col ToRename", display_order=2)
    act_as(actor)
    resp = api_client.patch(f"/api/v1/bin-series/custom-columns/{column_b.id}/rename", json={"name": "Test Custom Col Taken"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BIN_CUSTOM_COLUMN_ALREADY_EXISTS"


def test_rename_to_same_name_is_noop(api_client, db_session, act_as, actor):
    column = _make_column(db_session, key="Test Custom Col SameName", name="Test Custom Col SameName")
    act_as(actor)
    before = _revisions_for(db_session, column.id)
    resp = api_client.patch(f"/api/v1/bin-series/custom-columns/{column.id}/rename", json={"name": "Test Custom Col SameName"})
    assert resp.status_code == 200
    after = _revisions_for(db_session, column.id)
    assert len(after) == len(before)  # no new revision for a no-op rename


def test_rename_nonexistent_column_returns_404(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.patch("/api/v1/bin-series/custom-columns/999999999/rename", json={"name": "Test Custom Col Ghost"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "BIN_CUSTOM_COLUMN_NOT_FOUND"


def test_rename_whitespace_name_rejected(api_client, db_session, act_as, actor):
    column = _make_column(db_session, key="Test Custom Col RenameBlank", name="Test Custom Col RenameBlank")
    act_as(actor)
    resp = api_client.patch(f"/api/v1/bin-series/custom-columns/{column.id}/rename", json={"name": "   "})
    assert resp.status_code == 422


# =====================================================================
# Audit
# =====================================================================


def test_create_column_creates_audit_revision(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col AuditCreate"})
    column_id = resp.json()["id"]

    [revision] = _revisions_for(db_session, column_id)
    assert revision.action_type == "create"
    assert revision.entity_type == "bin_custom_column"
    assert revision.user_id == actor.id


def test_rename_column_creates_audit_revision(api_client, db_session, act_as, actor):
    column = _make_column(db_session, key="Test Custom Col AuditRename", name="Test Custom Col AuditRename")
    act_as(actor)
    resp = api_client.patch(f"/api/v1/bin-series/custom-columns/{column.id}/rename", json={"name": "Test Custom Col AuditRenamed"})
    assert resp.status_code == 200

    [revision] = _revisions_for(db_session, column.id)
    assert revision.action_type == "update"
    assert revision.entity_type == "bin_custom_column"
    assert revision.user_id == actor.id
    assert "Test Custom Col AuditRename" in revision.change_description
    assert "Test Custom Col AuditRenamed" in revision.change_description


# =====================================================================
# 17. Authentication
# =====================================================================


def test_list_columns_requires_authentication(raw_client):
    resp = raw_client.get("/api/v1/bin-series/custom-columns")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_create_column_requires_authentication(raw_client):
    resp = raw_client.post("/api/v1/bin-series/custom-columns/create", json={"name": "Test Custom Col NoAuth"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_rename_column_requires_authentication(raw_client):
    resp = raw_client.patch("/api/v1/bin-series/custom-columns/1/rename", json={"name": "Test Custom Col NoAuth"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_bogus_token_rejected(raw_client):
    resp = raw_client.get("/api/v1/bin-series/custom-columns", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_PROVIDER_NOT_CONFIGURED"
