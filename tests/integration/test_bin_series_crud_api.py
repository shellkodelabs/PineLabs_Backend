"""
API tests for BIN Series Create/Update/Delete (Part 16):
  POST   /api/v1/bin-series
  PUT    /api/v1/bin-series/{binRecordId}
  DELETE /api/v1/bin-series/{binRecordId}

Plus the additive `updatedBy`/`updatedAt` fields on BinRecordResponse and
automatic audit/revision logging for all three operations — same
conventions as tests/integration/test_audit_logging.py (User write APIs).

Uses `api_client`/`db_session`/`act_as` (tests/integration/conftest.py).
Fixture rows use bin_iin values in the 999100-999599 range and
issuer names prefixed "Test Bin Crud "/"Test Bin Regression " — outside
the real Part 6 seed's 400000-482063 bin_iin range and plain-bank-name
issuer convention (see test_bin_series_api.py's module docstring for the
same non-collision reasoning), and disjoint from that file's own
999001-999004 range.
"""
import pytest
from sqlalchemy import func, select

from app.models import BinRecord, Merchant, Revision, User


def _make_user(db_session, *, name, email, role="Admin", status="Active"):
    user = User(name=name, email=email, role=role, status=status)
    db_session.add(user)
    db_session.flush()
    return user


def _make_bin(
    db_session, *, issuer, card_program_group_name, bin_iin, merchant_prefix, merchant_id=None,
    instance_name="Test Bin Crud Instance",
):
    """Defaults to a valid instance_name so every PRE-EXISTING (Part 16)
    test in this file — which predates instanceName and doesn't care
    about it — keeps behaving exactly as before under Part 17's new
    "record must end up with a non-blank instanceName" update rule. Pass
    `instance_name=None` explicitly to simulate a genuinely legacy row
    (e.g. one of the real 611 seeded records, which have no instance
    information at all) for tests that specifically exercise that case."""
    record = BinRecord(
        issuer=issuer,
        card_program_group_name=card_program_group_name,
        bin_iin=bin_iin,
        merchant_prefix=merchant_prefix,
        merchant_id=merchant_id,
        instance_name=instance_name,
    )
    db_session.add(record)
    db_session.flush()
    return record


def _revisions_for(db_session, entity_id):
    stmt = select(Revision).where(Revision.entity_type == "bin_record", Revision.entity_id == entity_id)
    return db_session.execute(stmt).scalars().all()


def _count_revisions(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Revision))


@pytest.fixture()
def actor(db_session):
    return _make_user(db_session, name="Test Bin Crud Actor", email="test.bin.crud.actor@example.invalid")


@pytest.fixture()
def merchant(db_session):
    m = Merchant(name="Test Bin Crud Merchant", classification="Reward Card")
    db_session.add(m)
    db_session.flush()
    return m


VALID_PAYLOAD = {
    "issuer": "Test Bin Crud Issuer",
    "cardProgramGroupName": "Crud Program",
    "binIin": "999101",
    "merchantPrefix": "001",
    "instanceName": "Test Bin Crud Instance",
}


# =====================================================================
# CREATE
# =====================================================================


def test_create_valid_bin_record(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/create", json=VALID_PAYLOAD)
    assert resp.status_code == 201
    body = resp.json()
    assert body["issuer"] == VALID_PAYLOAD["issuer"]
    assert body["cardProgramGroupName"] == VALID_PAYLOAD["cardProgramGroupName"]
    assert body["binIin"] == VALID_PAYLOAD["binIin"]
    assert body["merchantPrefix"] == VALID_PAYLOAD["merchantPrefix"]
    assert body["merchantId"] is None
    assert body["instanceName"] == VALID_PAYLOAD["instanceName"]
    assert body["updatedBy"] == actor.name
    assert body["updatedAt"] is not None


def test_create_with_merchant_id(api_client, act_as, actor, merchant):
    act_as(actor)
    payload = {**VALID_PAYLOAD, "binIin": "999102", "merchantId": merchant.id}
    resp = api_client.post("/api/v1/bin-series/create", json=payload)
    assert resp.status_code == 201
    assert resp.json()["merchantId"] == merchant.id


def test_create_invalid_bin_iin_rejected(api_client, act_as, actor):
    act_as(actor)
    too_short = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "binIin": "1234"})
    assert too_short.status_code == 422
    assert too_short.json()["error"]["code"] == "VALIDATION_ERROR"

    non_digits = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "binIin": "12AB56"})
    assert non_digits.status_code == 422


def test_create_invalid_merchant_prefix_rejected(api_client, act_as, actor):
    act_as(actor)
    too_short = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "merchantPrefix": "1"})
    assert too_short.status_code == 422
    assert too_short.json()["error"]["code"] == "VALIDATION_ERROR"

    non_digits = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "merchantPrefix": "abc"})
    assert non_digits.status_code == 422


def test_create_blank_issuer_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "issuer": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_blank_card_program_group_name_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "cardProgramGroupName": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_duplicate_bin_iin_and_merchant_prefix_returns_409(api_client, db_session, act_as, actor):
    _make_bin(
        db_session, issuer="Test Bin Crud Existing", card_program_group_name="Existing Program",
        bin_iin="999103", merchant_prefix="001",
    )
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/create",
        json={**VALID_PAYLOAD, "binIin": "999103", "merchantPrefix": "001"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BIN_RECORD_ALREADY_EXISTS"


def test_create_invalid_merchant_id_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "binIin": "999104", "merchantId": 999999999})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BIN_MERCHANT_NOT_FOUND"


def test_create_audit_revision_created(api_client, db_session, act_as, actor):
    act_as(actor)
    before = _count_revisions(db_session)
    resp = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "binIin": "999105"})
    assert resp.status_code == 201
    new_id = resp.json()["id"]

    assert _count_revisions(db_session) == before + 1
    [revision] = _revisions_for(db_session, new_id)
    assert revision.action_type == "create"
    assert revision.entity_type == "bin_record"
    assert revision.entity_id == new_id
    assert VALID_PAYLOAD["issuer"] in revision.target_label


def test_create_actor_comes_from_current_user(api_client, db_session, act_as, actor):
    """The revision's actor AND the row's updated_by_user_id must both be
    whoever is authenticated — there is no client-suppliable field for
    either (CreateBinRecordRequest has no actor/updatedBy field at all)."""
    act_as(actor)
    resp = api_client.post("/api/v1/bin-series/create", json={**VALID_PAYLOAD, "binIin": "999106"})
    new_id = resp.json()["id"]

    [revision] = _revisions_for(db_session, new_id)
    assert revision.user_id == actor.id

    db_row = db_session.get(BinRecord, new_id)
    assert db_row.updated_by_user_id == actor.id


# =====================================================================
# UPDATE
# =====================================================================


def test_update_issuer(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud Before", card_program_group_name="P", bin_iin="999200", merchant_prefix="001")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Bin Crud After"})
    assert resp.status_code == 200
    assert resp.json()["issuer"] == "Test Bin Crud After"


def test_update_card_program_group_name(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud X", card_program_group_name="Before Program", bin_iin="999201", merchant_prefix="001")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"cardProgramGroupName": "After Program"})
    assert resp.status_code == 200
    assert resp.json()["cardProgramGroupName"] == "After Program"


def test_update_bin_iin(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud X", card_program_group_name="P", bin_iin="999202", merchant_prefix="001")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"binIin": "999203"})
    assert resp.status_code == 200
    assert resp.json()["binIin"] == "999203"


def test_update_merchant_prefix(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud X", card_program_group_name="P", bin_iin="999204", merchant_prefix="001")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"merchantPrefix": "099"})
    assert resp.status_code == 200
    assert resp.json()["merchantPrefix"] == "099"


def test_update_merchant_id(api_client, db_session, act_as, actor, merchant):
    record = _make_bin(db_session, issuer="Test Bin Crud X", card_program_group_name="P", bin_iin="999205", merchant_prefix="001")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"merchantId": merchant.id})
    assert resp.status_code == 200
    assert resp.json()["merchantId"] == merchant.id


def test_update_invalid_merchant_id_rejected(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud X", card_program_group_name="P", bin_iin="999206", merchant_prefix="001")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"merchantId": 999999999})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "BIN_MERCHANT_NOT_FOUND"


def test_update_partial_only_changes_supplied_field(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, issuer="Test Bin Crud Partial", card_program_group_name="Original Program",
        bin_iin="999207", merchant_prefix="001",
    )
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Bin Crud Partial Updated"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == "Test Bin Crud Partial Updated"
    assert body["cardProgramGroupName"] == "Original Program"  # untouched
    assert body["binIin"] == "999207"  # untouched
    assert body["merchantPrefix"] == "001"  # untouched


def test_update_nonexistent_id_returns_404(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.put("/api/v1/bin-series/999999999/update", json={"issuer": "Nope"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "BIN_RECORD_NOT_FOUND"


def test_update_duplicate_natural_key_returns_409(api_client, db_session, act_as, actor):
    a = _make_bin(db_session, issuer="Test Bin Crud A", card_program_group_name="P", bin_iin="999208", merchant_prefix="001")
    b = _make_bin(db_session, issuer="Test Bin Crud B", card_program_group_name="P", bin_iin="999209", merchant_prefix="002")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{b.id}/update", json={"binIin": a.bin_iin, "merchantPrefix": a.merchant_prefix})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BIN_RECORD_ALREADY_EXISTS"


def test_update_unchanged_creates_no_revision(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, issuer="Test Bin Crud NoChange", card_program_group_name="Same Program",
        bin_iin="999210", merchant_prefix="001",
    )
    act_as(actor)
    before = _count_revisions(db_session)

    # Resending the SAME current values — not a real change.
    resp = api_client.put(
        f"/api/v1/bin-series/{record.id}/update",
        json={"issuer": "Test Bin Crud NoChange", "cardProgramGroupName": "Same Program"},
    )
    assert resp.status_code == 200
    assert _count_revisions(db_session) == before
    assert _revisions_for(db_session, record.id) == []


def test_update_unchanged_does_not_touch_updated_by_or_updated_at(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, issuer="Test Bin Crud Untouched", card_program_group_name="P",
        bin_iin="999211", merchant_prefix="001",
    )
    assert record.updated_by_user_id is None
    original_updated_at = record.updated_at

    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Bin Crud Untouched"})
    assert resp.status_code == 200

    db_session.refresh(record)
    assert record.updated_by_user_id is None
    assert record.updated_at == original_updated_at


def test_update_sets_updated_by_user_id_to_current_authenticated_user(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud UpdatedBy", card_program_group_name="P", bin_iin="999212", merchant_prefix="001")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Bin Crud UpdatedBy2"})
    assert resp.status_code == 200
    assert resp.json()["updatedBy"] == actor.name

    db_session.refresh(record)
    assert record.updated_by_user_id == actor.id


def test_update_revision_contains_correct_actor(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud RevActor", card_program_group_name="P", bin_iin="999213", merchant_prefix="001")
    act_as(actor)
    resp = api_client.put(f"/api/v1/bin-series/{record.id}/update", json={"issuer": "Test Bin Crud RevActor2"})
    assert resp.status_code == 200

    [revision] = _revisions_for(db_session, record.id)
    assert revision.action_type == "update"
    assert revision.entity_type == "bin_record"
    assert revision.entity_id == record.id
    assert revision.user_id == actor.id
    assert "Issuer changed from Test Bin Crud RevActor to Test Bin Crud RevActor2" in revision.change_description


# =====================================================================
# DELETE
# =====================================================================


def test_delete_successful(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud Delete", card_program_group_name="P", bin_iin="999300", merchant_prefix="001")
    record_id = record.id
    act_as(actor)
    resp = api_client.delete(f"/api/v1/bin-series/{record_id}/delete")
    assert resp.status_code == 200
    assert resp.json() == {"id": record_id, "deleted": True}
    assert db_session.get(BinRecord, record_id) is None


def test_delete_nonexistent_id_returns_404(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.delete("/api/v1/bin-series/999999999/delete")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "BIN_RECORD_NOT_FOUND"


def test_delete_creates_revision(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud DeleteRev", card_program_group_name="P", bin_iin="999301", merchant_prefix="001")
    record_id = record.id
    act_as(actor)
    before = _count_revisions(db_session)
    resp = api_client.delete(f"/api/v1/bin-series/{record_id}/delete")
    assert resp.status_code == 200
    assert _count_revisions(db_session) == before + 1


def test_delete_revision_entity_id_equals_deleted_row_id(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud DeleteId", card_program_group_name="P", bin_iin="999302", merchant_prefix="001")
    record_id = record.id
    act_as(actor)
    resp = api_client.delete(f"/api/v1/bin-series/{record_id}/delete")
    assert resp.status_code == 200

    [revision] = _revisions_for(db_session, record_id)
    assert revision.entity_id == record_id
    assert revision.action_type == "delete"
    assert revision.entity_type == "bin_record"


def test_delete_actor_is_current_authenticated_user(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Crud DeleteActor", card_program_group_name="P", bin_iin="999303", merchant_prefix="001")
    record_id = record.id
    act_as(actor)
    resp = api_client.delete(f"/api/v1/bin-series/{record_id}/delete")
    assert resp.status_code == 200

    [revision] = _revisions_for(db_session, record_id)
    assert revision.user_id == actor.id
    # The actor (still a real, existing row) resolves fine — the deleted
    # BIN record does not, which is exactly why entity_id is a soft
    # reference and revision.user_id must be the actor, never the target.
    assert db_session.get(User, revision.user_id) is not None


# =====================================================================
# REGRESSION — existing read APIs / existing seeded data
# =====================================================================


def test_regression_list_api_still_works(api_client):
    resp = api_client.get("/api/v1/bin-series/list", params={"page": 1, "pageSize": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "page", "pageSize"}
    assert body["total"] >= 611
    assert len(body["items"]) <= 5


def test_regression_resolve_api_still_works_for_pre_existing_style_row(api_client, db_session):
    """Simulates a row that predates Part 16/17 (no updated_by_user_id,
    no instance_name — same as every one of the real 611 seeded records)
    — GET /resolve must keep returning it correctly, with the new fields
    present but null."""
    record = _make_bin(
        db_session, issuer="Test Bin Regression Resolve", card_program_group_name="Regression Program",
        bin_iin="999501", merchant_prefix="001", instance_name=None,
    )
    resp = api_client.get("/api/v1/bin-series/lookup", params={"binIin": "999501", "merchantPrefix": "001"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == "Test Bin Regression Resolve"
    assert body["id"] == record.id
    assert body["instanceName"] is None
    assert body["updatedBy"] is None
    assert body["updatedAt"] is not None


def test_regression_seeded_bin_records_remain_intact(db_session):
    total = db_session.scalar(select(func.count()).select_from(BinRecord))
    assert total >= 611
