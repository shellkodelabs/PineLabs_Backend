"""
Database/model tests for the Part 5 schema.

Covers: table existence, basic inserts, the FK link from BinRecord to
Merchant, the (bin_iin, merchant_prefix) uniqueness constraint, the full
SOP nesting (sheet -> column group -> column, plus JSONB rows), the
shared/default SOP sheet (merchant_id IS NULL) and its one-per-key
partial unique index, user sheet-access grants and their uniqueness,
a revision referencing a user, and the role/status/classification CHECK
constraints.

Repository- and service-layer tests are explicitly out of scope here —
those layers don't exist yet (Part 5 is models + migration only).
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import (
    BinRecord,
    Merchant,
    Revision,
    SopColumn,
    SopColumnGroup,
    SopRow,
    SopSheet,
    User,
    UserSopSheetAccess,
)

EXPECTED_TABLES = {
    "merchants",
    "bin_records",
    "sop_sheets",
    "sop_column_groups",
    "sop_columns",
    "sop_rows",
    "users",
    "user_sop_sheet_access",
    "revisions",
    # Instance Management (added after Part 5): the instances entity, its
    # user-defined custom column definitions, and the persisted built-in
    # column display order (drag-and-drop reordering).
    "instances",
    "instance_columns",
    "instance_builtin_columns",
}


def test_all_expected_tables_exist(db_session):
    """1. Tables can be created through Alembic."""
    table_names = (
        db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name != 'alembic_version'"
            )
        )
        .scalars()
        .all()
    )
    assert set(table_names) == EXPECTED_TABLES


def test_merchant_can_be_inserted(db_session):
    """2. Merchant can be inserted."""
    merchant = Merchant(name="Test Bank", classification="Digital Gift Card")
    db_session.add(merchant)
    db_session.flush()

    assert merchant.id is not None
    assert merchant.created_at is not None
    assert merchant.updated_at is not None


def test_bin_record_can_reference_a_merchant(db_session):
    """3. BIN record can reference a merchant."""
    merchant = Merchant(name="Reference Bank", classification="Reward Card")
    db_session.add(merchant)
    db_session.flush()

    bin_record = BinRecord(
        issuer="Reference Bank",
        card_program_group_name="Reference Rewards",
        bin_iin="123456",
        merchant_prefix="001",
        merchant_id=merchant.id,
    )
    db_session.add(bin_record)
    db_session.flush()

    assert bin_record.id is not None
    assert bin_record.merchant_id == merchant.id
    assert bin_record.merchant.name == "Reference Bank"


def test_duplicate_bin_and_prefix_is_rejected(db_session):
    """4. Duplicate (bin_iin, merchant_prefix) is rejected."""
    db_session.add(
        BinRecord(issuer="A Bank", card_program_group_name="A Program", bin_iin="654321", merchant_prefix="099")
    )
    db_session.flush()

    db_session.add(
        BinRecord(issuer="B Bank", card_program_group_name="B Program", bin_iin="654321", merchant_prefix="099")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_bin_iin_and_merchant_prefix_format_checks(db_session):
    """Bonus: the digit-format CHECK constraints reject malformed values."""
    db_session.add(
        BinRecord(issuer="Bad Bank", card_program_group_name="Bad Program", bin_iin="12AB56", merchant_prefix="001")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(
        BinRecord(issuer="Bad Bank 2", card_program_group_name="Bad Program 2", bin_iin="123456", merchant_prefix="1")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_sop_sheet_can_contain_groups_columns_and_jsonb_rows(db_session):
    """5. SOP sheet can contain groups, columns and JSONB rows."""
    merchant = Merchant(name="SOP Bank", classification="Corporate Gifting")
    db_session.add(merchant)
    db_session.flush()

    sheet = SopSheet(merchant_id=merchant.id, key="block", name="Block")
    db_session.add(sheet)
    db_session.flush()

    group = SopColumnGroup(sheet_id=sheet.id, label="Prerequisites", sort_order=0)
    db_session.add(group)
    db_session.flush()

    db_session.add_all(
        [
            SopColumn(group_id=group.id, name="Card Status", sort_order=0),
            SopColumn(group_id=group.id, name="Action", sort_order=1),
        ]
    )
    db_session.add(SopRow(sheet_id=sheet.id, data={"Card Status": "Active", "Action": "Approve"}))
    db_session.flush()

    db_session.expire_all()
    reloaded = db_session.get(SopSheet, sheet.id)
    assert len(reloaded.column_groups) == 1
    assert len(reloaded.column_groups[0].columns) == 2
    assert reloaded.rows[0].data == {"Card Status": "Active", "Action": "Approve"}


def test_shared_sop_sheet_with_null_merchant_id_works(db_session):
    """6. Shared SOP sheet with merchant_id=NULL works.

    Uses a synthetic key rather than the real "escalation" key: since
    Part 6, the database may already contain a real, committed shared
    escalation sheet (seeded from the frontend's commonEscalation) that
    lives outside this test's rollback scope — colliding with it would
    test the partial unique index by accident, not the behavior this
    test is actually about. Same reasoning as the "poc-access-test" /
    "poc-dup-test" synthetic keys used elsewhere in this file.
    """
    shared = SopSheet(merchant_id=None, key="shared-sheet-test", name="Test Shared Sheet")
    db_session.add(shared)
    db_session.flush()

    assert shared.id is not None
    assert shared.merchant_id is None


def test_two_shared_sheets_with_same_key_are_rejected(db_session):
    """7. Two shared sheets with the same key are rejected (partial unique index)."""
    db_session.add(SopSheet(merchant_id=None, key="shared-sheet-dup-test", name="Test Shared Sheet"))
    db_session.flush()

    db_session.add(SopSheet(merchant_id=None, key="shared-sheet-dup-test", name="Test Shared Sheet (duplicate)"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_user_can_receive_sheet_access(db_session):
    """8. User can receive sheet access."""
    user = User(name="Test Agent", email="test.agent@example.invalid", role="Support Agent", status="Active")
    sheet = SopSheet(merchant_id=None, key="poc-access-test", name="POC")
    db_session.add_all([user, sheet])
    db_session.flush()

    db_session.add(UserSopSheetAccess(user_id=user.id, sheet_id=sheet.id))
    db_session.flush()

    db_session.expire_all()
    reloaded = db_session.get(User, user.id)
    assert len(reloaded.sheet_access) == 1
    assert reloaded.sheet_access[0].sheet_id == sheet.id


def test_duplicate_user_sheet_access_is_rejected(db_session):
    """9. Duplicate user-sheet access is rejected."""
    user = User(name="Dup Agent", email="dup.agent@example.invalid", role="Support Agent", status="Active")
    sheet = SopSheet(merchant_id=None, key="poc-dup-test", name="POC")
    db_session.add_all([user, sheet])
    db_session.flush()

    db_session.add(UserSopSheetAccess(user_id=user.id, sheet_id=sheet.id))
    db_session.flush()

    db_session.add(UserSopSheetAccess(user_id=user.id, sheet_id=sheet.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_revision_can_reference_a_user(db_session):
    """10. Revision can reference a user."""
    user = User(name="Auditor One", email="auditor.one@example.invalid", role="Auditor", status="Active")
    db_session.add(user)
    db_session.flush()

    revision = Revision(
        user_id=user.id,
        action_type="update",
        entity_type="merchant",
        entity_id=None,
        target_label="Test Bank",
        change_description="Changed classification",
    )
    db_session.add(revision)
    db_session.flush()

    assert revision.id is not None
    assert revision.user.email == "auditor.one@example.invalid"


def test_check_constraints_reject_invalid_role_status_classification(db_session):
    """11. CHECK constraints reject invalid role/status/classification."""
    db_session.add(User(name="Bad Role", email="bad.role@example.invalid", role="Superuser", status="Active"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(User(name="Bad Status", email="bad.status@example.invalid", role="Admin", status="Deleted"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(Merchant(name="Bad Classification Bank", classification="Not A Real Classification"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
