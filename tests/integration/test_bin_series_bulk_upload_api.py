"""
API tests for BIN Series bulk upload (Bin Series priority task):
  POST /api/v1/bin-series/bulk-upload

Backs the frontend's BinTable "Import Data" button
(UploadSheetModal.jsx) — mode="addNew" (BinTable's addSheet) and
mode="updateExisting" (BinTable's updateExisting).

MATCHING KEY: hybrid — (binIin, merchantPrefix) when a row supplies
both (the real DB-unique key); otherwise an issuer-name match, but ONLY
when it resolves to exactly one existing record (ambiguous issuer
matches fail explicitly rather than guessing). See
app/schemas/bin_series.py's module docstring for the full reasoning.
Tests below cover both the primary key and the issuer fallback,
including the ambiguous-issuer case.

Uses `api_client`/`db_session`/`act_as` (tests/integration/conftest.py)
for the authenticated path, and a local `raw_client` (matching
tests/integration/test_auth.py's pattern) for the unauthenticated path.

Fixture rows use bin_iin values in the 999800-999899 range — disjoint
from every other BIN test file's own reserved ranges (see those files'
module docstrings for the same non-collision convention).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import BinRecord, Merchant, Revision, User


def _make_user(db_session, *, name, email, role="Admin", status="Active"):
    user = User(name=name, email=email, role=role, status=status)
    db_session.add(user)
    db_session.flush()
    return user


def _make_bin(
    db_session, *, issuer, bin_iin, merchant_prefix, instance_name=None, card_program_group_name="Program",
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


def _revisions_for(db_session, entity_id):
    stmt = select(Revision).where(Revision.entity_type == "bin_record", Revision.entity_id == entity_id)
    return db_session.execute(stmt).scalars().all()


def _count_revisions(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Revision))


@pytest.fixture()
def raw_client():
    """A TestClient with NO dependency overrides — real authentication
    runs for real on every request made through this. Same pattern as
    tests/integration/test_auth.py's fixture of the same name."""
    return TestClient(app)


@pytest.fixture()
def actor(db_session):
    return _make_user(db_session, name="Test Bin Bulk Actor", email="test.bin.bulk.actor@example.invalid")


@pytest.fixture()
def merchant(db_session):
    m = Merchant(name="Test Bin Bulk Merchant", classification="Reward Card")
    db_session.add(m)
    db_session.flush()
    return m


# =====================================================================
# 1-2. Authenticated / unauthenticated
# =====================================================================


def test_authenticated_upload_succeeds(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "Test Bin Bulk Auth", "cardProgramGroupName": "Y", "binIin": "999800", "merchantPrefix": "001"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["createdCount"] == 1


def test_unauthenticated_upload_rejected(raw_client):
    resp = raw_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={"mode": "addNew", "instanceName": "North Zone", "rows": [{"issuer": "X"}]},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


def test_unauthenticated_upload_with_bogus_token_rejected(raw_client):
    resp = raw_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={"mode": "addNew", "instanceName": "North Zone", "rows": [{"issuer": "X"}]},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_PROVIDER_NOT_CONFIGURED"


# =====================================================================
# 3-4. Valid CSV / XLSX
#
# The endpoint's contract is JSON rows (see the module-level design note
# in app/schemas/bin_series.py: the frontend already parses CSV/XLSX to
# row objects client-side via utils/csv.js before any network call would
# happen). These tests confirm the row shape that readSheetFile() /
# parseCsv() actually produce (plain trimmed strings) round-trips
# correctly — a literal CSV/XLSX file is not sent over the wire.
# =====================================================================


def test_valid_row_shape_matching_csv_parser_output(api_client, act_as, actor):
    """parseCsv()/readSheetFile() always produce plain trimmed strings
    per cell — confirms that shape works end-to-end."""
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "Test Bin Bulk CSV", "cardProgramGroupName": "Program", "binIin": "999801", "merchantPrefix": "001"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["createdCount"] == 1


def test_valid_row_shape_matching_xlsx_parser_output(api_client, act_as, actor):
    """XLSX.utils.sheet_to_json({header:1, raw:false, defval:''}) also
    always yields strings (raw:false) — same shape as CSV, confirmed
    separately since the frontend treats them as two distinct input
    paths even though they converge on the same row shape."""
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "Test Bin Bulk XLSX", "cardProgramGroupName": "Program", "binIin": "999802", "merchantPrefix": "001"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["createdCount"] == 1


# =====================================================================
# 5-6. Add New / Update Existing
# =====================================================================


def test_add_new_creates_records(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [
                {"issuer": "Test Bin Bulk AddA", "cardProgramGroupName": "Y", "binIin": "999803", "merchantPrefix": "001"},
                {"issuer": "Test Bin Bulk AddB", "cardProgramGroupName": "Y", "binIin": "999804", "merchantPrefix": "002"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "addNew"
    assert body["createdCount"] == 2
    row = db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999803")).scalar_one()
    assert row.instance_name == "North Zone"
    assert row.updated_by_user_id == actor.id


def test_update_existing_matches_by_bin_and_prefix(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Bulk UpdA", bin_iin="999805", merchant_prefix="001", instance_name="West")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "West",
            "rows": [{"binIin": "999805", "merchantPrefix": "001", "cardProgramGroupName": "Updated Program"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["updatedCount"] == 1
    db_session.refresh(record)
    assert record.card_program_group_name == "Updated Program"


def test_update_existing_falls_back_to_unambiguous_issuer_match(api_client, db_session, act_as, actor):
    """The frontend's own updateExisting() matches by issuer alone; this
    confirms the backend supports that when binIin/merchantPrefix are
    omitted from the row AND the issuer is unambiguous."""
    record = _make_bin(db_session, issuer="Test Bin Bulk IssuerOnly", bin_iin="999806", merchant_prefix="001", instance_name="West")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "West",
            "rows": [{"issuer": "Test Bin Bulk IssuerOnly", "cardProgramGroupName": "Updated Via Issuer"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["updatedCount"] == 1
    db_session.refresh(record)
    assert record.card_program_group_name == "Updated Via Issuer"


def test_update_existing_ambiguous_issuer_fails_the_row(api_client, db_session, act_as, actor):
    """Same issuer, two different binIin/merchantPrefix pairs (mirrors
    the real seed data's own "Aurora Retail" duplicate) — an issuer-only
    row must never guess which one was meant."""
    _make_bin(db_session, issuer="Test Bin Bulk Ambiguous", bin_iin="999807", merchant_prefix="001", instance_name="West")
    _make_bin(db_session, issuer="Test Bin Bulk Ambiguous", bin_iin="999808", merchant_prefix="002", instance_name="West")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "West",
            "rows": [{"issuer": "Test Bin Bulk Ambiguous", "cardProgramGroupName": "Should Not Apply"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updatedCount"] == 0
    assert body["failedCount"] == 1
    assert "ambiguous" in body["failedRows"][0]["reason"].lower()


def test_update_existing_only_overwrites_non_blank_fields(api_client, db_session, act_as, actor):
    record = _make_bin(
        db_session, issuer="Test Bin Bulk Partial", bin_iin="999809", merchant_prefix="001",
        instance_name="West", card_program_group_name="Original Program",
    )
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "West",
            "rows": [{"binIin": "999809", "merchantPrefix": "001", "issuer": "Test Bin Bulk Partial Renamed", "cardProgramGroupName": ""}],
        },
    )
    assert resp.status_code == 200
    db_session.refresh(record)
    assert record.issuer == "Test Bin Bulk Partial Renamed"
    assert record.card_program_group_name == "Original Program"  # blank -> untouched


def test_update_existing_no_match_is_skipped_not_created(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "West",
            "rows": [{"binIin": "999810", "merchantPrefix": "001", "issuer": "Ghost"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updatedCount"] == 0
    assert body["skippedCount"] == 1
    assert db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999810")).scalar_one_or_none() is None


# =====================================================================
# 7. Duplicate rows
# =====================================================================


def test_duplicate_rows_within_add_new_batch_second_fails(api_client, db_session, act_as, actor):
    row = {"issuer": "Test Bin Bulk Dup", "cardProgramGroupName": "Y", "binIin": "999811", "merchantPrefix": "001"}
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={"mode": "addNew", "instanceName": "North Zone", "rows": [row, row]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["createdCount"] == 1
    assert body["failedCount"] == 1
    assert body["failedRows"][0]["rowIndex"] == 1


def test_duplicate_against_existing_db_row_fails(api_client, db_session, act_as, actor):
    _make_bin(db_session, issuer="Test Bin Bulk Existing", bin_iin="999812", merchant_prefix="001")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "New Issuer", "cardProgramGroupName": "Y", "binIin": "999812", "merchantPrefix": "001"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["createdCount"] == 0
    assert body["failedCount"] == 1
    assert "already exists" in body["failedRows"][0]["reason"]


def test_update_existing_duplicate_rows_targeting_same_record_apply_in_order(api_client, db_session, act_as, actor):
    """Two rows resolving to the same existing record: deterministically
    applied in order, later row's value wins."""
    record = _make_bin(db_session, issuer="Test Bin Bulk SameTarget", bin_iin="999813", merchant_prefix="001", instance_name="West")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "West",
            "rows": [
                {"binIin": "999813", "merchantPrefix": "001", "cardProgramGroupName": "First Value"},
                {"binIin": "999813", "merchantPrefix": "001", "cardProgramGroupName": "Second Value"},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["updatedCount"] == 2
    db_session.refresh(record)
    assert record.card_program_group_name == "Second Value"


# =====================================================================
# 8-11. Row validation
# =====================================================================


def test_invalid_bin_format_fails_that_row(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "X", "cardProgramGroupName": "Y", "binIin": "12AB", "merchantPrefix": "001"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["failedCount"] == 1
    assert "6 digits" in body["failedRows"][0]["reason"]


def test_invalid_merchant_prefix_format_fails_that_row(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "X", "cardProgramGroupName": "Y", "binIin": "999814", "merchantPrefix": "1"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["failedCount"] == 1
    assert "3 digits" in body["failedRows"][0]["reason"]


def test_missing_issuer_fails_add_new_row(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "", "cardProgramGroupName": "Y", "binIin": "999815", "merchantPrefix": "001"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["createdCount"] == 0
    assert body["failedCount"] == 1


def test_missing_instance_name_rejects_whole_request(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={"mode": "addNew", "rows": [{"issuer": "X", "cardProgramGroupName": "Y", "binIin": "999816", "merchantPrefix": "001"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_whitespace_instance_name_rejects_whole_request(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "   ",
            "rows": [{"issuer": "X", "cardProgramGroupName": "Y", "binIin": "999817", "merchantPrefix": "001"}],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_update_existing_legacy_record_without_instance_name_requires_backfill(api_client, db_session, act_as, actor):
    _make_bin(db_session, issuer="Test Bin Bulk Legacy", bin_iin="999818", merchant_prefix="001", instance_name=None)
    act_as(actor)
    # Batch instanceName backfills the legacy record automatically.
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "Priority Tier",
            "rows": [{"binIin": "999818", "merchantPrefix": "001", "cardProgramGroupName": "New Program"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["updatedCount"] == 1
    row = db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999818")).scalar_one()
    assert row.instance_name == "Priority Tier"


# =====================================================================
# 12. Existing record update / 13. new record creation (shape checks)
# =====================================================================


def test_existing_record_update_preserves_immutable_fields_when_not_supplied(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Bulk Immutable", bin_iin="999819", merchant_prefix="001", instance_name="West")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "West",
            "rows": [{"binIin": "999819", "merchantPrefix": "001", "cardProgramGroupName": "Changed"}],
        },
    )
    assert resp.status_code == 200
    db_session.refresh(record)
    assert record.bin_iin == "999819"  # match key itself never mutated by the match
    assert record.merchant_prefix == "001"


def test_new_record_creation_full_shape(api_client, db_session, act_as, actor, merchant):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{
                "issuer": "Test Bin Bulk FullShape", "cardProgramGroupName": "Program",
                "binIin": "999820", "merchantPrefix": "001", "merchantId": merchant.id,
            }],
        },
    )
    assert resp.status_code == 200
    row = db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999820")).scalar_one()
    assert row.issuer == "Test Bin Bulk FullShape"
    assert row.merchant_id == merchant.id
    assert row.instance_name == "North Zone"
    assert row.updated_by_user_id == actor.id


# =====================================================================
# 14. Mixed success/failure (partial success)
# =====================================================================


def test_mixed_success_and_failure_partial_success(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [
                {"issuer": "Test Bin Bulk Good1", "cardProgramGroupName": "Y", "binIin": "999821", "merchantPrefix": "001"},
                {"issuer": "", "cardProgramGroupName": "Y", "binIin": "999822", "merchantPrefix": "001"},  # bad
                {"issuer": "Test Bin Bulk Good2", "cardProgramGroupName": "Y", "binIin": "999823", "merchantPrefix": "001"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalRows"] == 3
    assert body["createdCount"] == 2
    assert body["failedCount"] == 1
    assert db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999821")).scalar_one_or_none() is not None
    assert db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999823")).scalar_one_or_none() is not None
    assert db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999822")).scalar_one_or_none() is None


# =====================================================================
# 15-16. Audit records / actor tracking
# =====================================================================


def test_add_new_creates_upload_audit_revision(api_client, db_session, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "Test Bin Bulk Audit", "cardProgramGroupName": "Y", "binIin": "999824", "merchantPrefix": "001"}],
        },
    )
    assert resp.status_code == 200
    new_id = db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999824")).scalar_one().id
    [revision] = _revisions_for(db_session, new_id)
    assert revision.action_type == "upload"
    assert revision.entity_type == "bin_record"
    assert revision.user_id == actor.id


def test_update_existing_creates_upload_audit_revision(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Bulk UpdAudit", bin_iin="999825", merchant_prefix="001", instance_name="West")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "West",
            "rows": [{"binIin": "999825", "merchantPrefix": "001", "cardProgramGroupName": "Updated"}],
        },
    )
    assert resp.status_code == 200
    [revision] = _revisions_for(db_session, record.id)
    assert revision.action_type == "upload"
    assert revision.user_id == actor.id


def test_actor_never_accepted_from_client(api_client, db_session, act_as, actor):
    """No client-suppliable actor field exists anywhere in
    BulkUploadRequest — the authenticated caller is always the actor."""
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "addNew",
            "instanceName": "North Zone",
            "rows": [{"issuer": "Test Bin Bulk ActorCheck", "cardProgramGroupName": "Y", "binIin": "999826", "merchantPrefix": "001", "actorUserId": 999999999}],
        },
    )
    assert resp.status_code == 200
    new_id = db_session.execute(select(BinRecord).where(BinRecord.bin_iin == "999826")).scalar_one().id
    [revision] = _revisions_for(db_session, new_id)
    assert revision.user_id == actor.id  # the stray actorUserId field is simply ignored (unknown field)


# =====================================================================
# 17. Database uniqueness protection
# =====================================================================


def test_database_uniqueness_protected_even_under_concurrent_style_batch(api_client, db_session, act_as, actor):
    """Three rows all targeting the same (binIin, merchantPrefix) in one
    addNew batch — only the first may succeed; uq_bin_records_bin_prefix
    is never violated regardless of batch contents."""
    act_as(actor)
    row = {"issuer": "Test Bin Bulk UniqueGuard", "cardProgramGroupName": "Y", "binIin": "999827", "merchantPrefix": "001"}
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={"mode": "addNew", "instanceName": "North Zone", "rows": [row, row, row]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["createdCount"] == 1
    assert body["failedCount"] == 2
    total = db_session.scalar(
        select(func.count()).select_from(BinRecord).where(BinRecord.bin_iin == "999827", BinRecord.merchant_prefix == "001")
    )
    assert total == 1


# =====================================================================
# 18-19. Invalid file type / malformed file
#
# File parsing (CSV/XLSX) happens entirely client-side in the approved
# frontend (utils/csv.js) before this endpoint is ever called — this
# API's input is already-parsed JSON rows, so "file type"/"malformed
# file" translate to malformed REQUEST BODIES at this layer.
# =====================================================================


def test_malformed_request_body_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={"mode": "addNew", "instanceName": "North Zone", "rows": "not-a-list"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_mode_value_rejected(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={"mode": "notARealMode", "instanceName": "North Zone", "rows": [{"issuer": "X"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_too_many_rows_rejected(api_client, act_as, actor):
    act_as(actor)
    rows = [{"issuer": "X", "cardProgramGroupName": "Y", "binIin": "999000", "merchantPrefix": "001"}] * 1001
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={"mode": "addNew", "instanceName": "North Zone", "rows": rows},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# =====================================================================
# 20 (bonus, matches Step 11 intent). instanceName never overwrites an
# existing value, and regression: existing read APIs unaffected.
# =====================================================================


def test_batch_instance_name_never_overwrites_existing_value(api_client, db_session, act_as, actor):
    record = _make_bin(db_session, issuer="Test Bin Bulk KeepInstance", bin_iin="999828", merchant_prefix="001", instance_name="Original Zone")
    act_as(actor)
    resp = api_client.post(
        "/api/v1/bin-series/bulk-upload",
        json={
            "mode": "updateExisting",
            "instanceName": "Different Zone",
            "rows": [{"binIin": "999828", "merchantPrefix": "001", "cardProgramGroupName": "New Program"}],
        },
    )
    assert resp.status_code == 200
    db_session.refresh(record)
    assert record.instance_name == "Original Zone"


def test_regression_existing_read_apis_unaffected(api_client, db_session):
    total = db_session.scalar(select(func.count()).select_from(BinRecord))
    assert total >= 611

    resp = api_client.get("/api/v1/bin-series/list", params={"page": 1, "pageSize": 1})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 611
