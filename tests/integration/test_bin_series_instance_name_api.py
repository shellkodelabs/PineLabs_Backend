"""
API tests for `instanceName` becoming mandatory on BIN Series write
operations (Part 17):
  POST /api/v1/bin-series
  PUT  /api/v1/bin-series/{binRecordId}

Background: no bulk BIN import/upsert API exists in this backend (Part
16 explicitly excluded it, and it still does not exist as of this part)
and no SOP write APIs exist at all (app/api/v1/sop.py has only GET
endpoints) — both were confirmed by inspection before implementation, so
neither has anything to test here; see the Part 17 completion report.

Also verifies the additive `instanceName` field on BinRecordResponse and
that the real 611 seeded records (which have no instance information)
are unaffected — they simply return `instanceName: null`.

Uses `api_client`/`db_session`/`act_as` (tests/integration/conftest.py).
Fixture rows use bin_iin values in the 999600-999699 range — disjoint
from every other BIN test file's own reserved ranges (see those files'
module docstrings for the same non-collision convention).
"""
import pytest
from sqlalchemy import func, select

from app.models import BinRecord, Revision, User


def _make_user(db_session, *, name, email, role="Admin", status="Active"):
    user = User(name=name, email=email, role=role, status=status)
    db_session.add(user)
    db_session.flush()
    return user


def _make_bin(db_session, *, issuer, bin_iin, merchant_prefix, instance_name=None):
    record = BinRecord(
        issuer=issuer,
        card_program_group_name="Program",
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        instance_name=instance_name,
    )
    db_session.add(record)
    db_session.flush()
    return record


def _revisions_for(db_session, entity_id):
    stmt = select(Revision).where(Revision.entity_type == "bin_record", Revision.entity_id == entity_id)
    return db_session.execute(stmt).scalars().all()


@pytest.fixture()
def actor(db_session):
    return _make_user(db_session, name="Test Bin Instance Actor", email="test.bin.instance.actor@example.invalid")


BASE_PAYLOAD = {
    "issuer": "Test Bin Instance Issuer",
    "cardProgramGroupName": "Instance Program",
    "binIin": "999600",
    "merchantPrefix": "001",
}


# =====================================================================
# CREATE
# =====================================================================


def test_create_without_instance_name_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series", json=BASE_PAYLOAD)  # instanceName key entirely absent
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_with_null_instance_name_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series", json={**BASE_PAYLOAD, "instanceName": None})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_with_empty_instance_name_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series", json={**BASE_PAYLOAD, "instanceName": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_with_whitespace_instance_name_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series", json={**BASE_PAYLOAD, "instanceName": "   "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_with_valid_instance_name_succeeds(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series", json={**BASE_PAYLOAD, "instanceName": "North Zone"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["instanceName"] == "North Zone"

    db_row = db_session.get(BinRecord, body["id"])
    assert db_row.instance_name == "North Zone"


def test_create_instance_name_is_trimmed(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series",
        json={**BASE_PAYLOAD, "binIin": "999601", "instanceName": "  South Zone  "},
    )
    assert resp.status_code == 201
    assert resp.json()["instanceName"] == "South Zone"


def test_create_instance_name_included_in_audit_metadata(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series",
        json={**BASE_PAYLOAD, "binIin": "999602", "instanceName": "East Zone"},
    )
    new_id = resp.json()["id"]
    [revision] = _revisions_for(db_session, new_id)
    assert revision.metadata_["instanceName"] == "East Zone"


# =====================================================================
# UPDATE
# =====================================================================


def test_update_explicit_null_instance_name_rejected(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Instance U1", bin_iin="999610", merchant_prefix="001", instance_name="West Zone")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}", json={"instanceName": None})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BIN_INSTANCE_NAME_REQUIRED"


def test_update_empty_instance_name_rejected(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Instance U2", bin_iin="999611", merchant_prefix="001", instance_name="West Zone")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}", json={"instanceName": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_update_whitespace_instance_name_rejected(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Instance U3", bin_iin="999612", merchant_prefix="001", instance_name="West Zone")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}", json={"instanceName": "   "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_update_with_valid_instance_name_succeeds(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Instance U4", bin_iin="999613", merchant_prefix="001", instance_name="West Zone")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}", json={"instanceName": "Central Zone"})
    assert resp.status_code == 200
    assert resp.json()["instanceName"] == "Central Zone"


def test_update_omitting_instance_name_on_record_that_already_has_one_succeeds(api_client, db_session, act_as, actor):
    """Ordinary partial-update ergonomics: a record that already complies
    (non-blank instance_name) does not need to resend it just to change
    an unrelated field."""
    record = _make_bin(db_session, issuer="Test Bin Instance U5", bin_iin="999614", merchant_prefix="001", instance_name="West Zone")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}", json={"issuer": "Test Bin Instance U5 Renamed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == "Test Bin Instance U5 Renamed"
    assert body["instanceName"] == "West Zone"  # left untouched


def test_update_legacy_record_without_instance_name_requires_it_to_change_other_fields(api_client, db_session, act_as, actor):
    """A legacy row (instance_name=None, same shape as all 611 real
    seeded records) cannot be updated further without ALSO supplying a
    valid instanceName — per the task's "mandatory ... for NEW/UPDATED
    records" requirement."""
    record = _make_bin(db_session, issuer="Test Bin Instance Legacy", bin_iin="999615", merchant_prefix="001", instance_name=None)
    act_as(actor)

    missing = api_client.put(f"/api/v1/bin-series/{record.id}", json={"issuer": "Test Bin Instance Legacy Renamed"})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "BIN_INSTANCE_NAME_REQUIRED"

    # Supplying instanceName alongside the other change fixes it.
    ok = api_client.put(
        f"/api/v1/bin-series/{record.id}",
        json={"issuer": "Test Bin Instance Legacy Renamed", "instanceName": "Pilot Program"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["issuer"] == "Test Bin Instance Legacy Renamed"
    assert body["instanceName"] == "Pilot Program"


def test_update_legacy_record_pure_noop_is_exempt(api_client, db_session, act_as, actor):
    """A legacy row with no instanceName, updated with a payload that
    changes NOTHING at all, is still a true no-op — it must not be forced
    to backfill instanceName just because the endpoint was called."""
    record = _make_bin(db_session, issuer="Test Bin Instance NoopLegacy", bin_iin="999616", merchant_prefix="001", instance_name=None)
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}", json={"issuer": "Test Bin Instance NoopLegacy"})
    assert resp.status_code == 200
    assert resp.json()["instanceName"] is None


def test_update_instance_name_change_recorded_in_audit_description(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Instance U6", bin_iin="999617", merchant_prefix="001", instance_name="West Zone")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}", json={"instanceName": "Priority Tier"})
    assert resp.status_code == 200

    [revision] = _revisions_for(db_session, record.id)
    assert revision.change_description == "Instance Name changed from West Zone to Priority Tier"


# =====================================================================
# REGRESSION
# =====================================================================


def test_regression_existing_seeded_records_have_null_instance_name(api_client, db_session):
    """The real 611 seeded records have no instance information — GET
    /api/v1/bin-series must keep working for them, returning
    instanceName: null rather than erroring."""
    total = db_session.scalar(select(func.count()).select_from(BinRecord))
    assert total >= 611

    resp = api_client.get("/api/v1/bin-series", params={"page": 1, "pageSize": 1})
    assert resp.status_code == 200
    assert "instanceName" in resp.json()["items"][0]


def test_regression_resolve_still_works_without_instance_name(api_client, db_session):
    _make_bin(db_session, issuer="Test Bin Instance Resolve", bin_iin="999618", merchant_prefix="001", instance_name=None)
    resp = api_client.get("/api/v1/bin-series/resolve", params={"binIin": "999618", "merchantPrefix": "001"})
    assert resp.status_code == 200
    assert resp.json()["instanceName"] is None
