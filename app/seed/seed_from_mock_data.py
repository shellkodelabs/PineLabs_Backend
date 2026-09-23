"""
Imports the PineLab frontend's mock data (src/data/*.js) into PostgreSQL.

Run:
    python -m app.seed.seed_from_mock_data

What this does, in order:
  1. Runs the REAL frontend JS modules via Node (mock_data_loader.py /
     export_frontend_data.mjs) and parses their output — no dataset is
     retyped or reconstructed in Python.
  2. Validates every assumption the backend schema requires (§17 of the
     Part 6 task). If anything fails, the script aborts WITHOUT touching
     the database — see `validate()` in mock_data_loader.py.
  3. Wipes existing rows from all 9 tables (dev-only; see "Idempotency"
     in app/seed/README.md) and re-inserts everything inside one
     transaction, so re-running this script is always safe and never
     produces duplicates.
  4. Prints a reconciliation report: source vs. database counts, BIN <->
     Merchant match statistics, per-category breakdowns, and spot checks.

This script does NOT create API endpoints, repositories, or services —
those are later parts. It only populates the database created in Part 5.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers all models
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    BinRecord,
    Merchant,
    Revision,
    SopColumn,
    SopColumnGroup,
    SopRow,
    SopSheet,
    User,
)
from app.seed.mock_data_loader import (
    DEFAULT_FRONTEND_DATA_DIR,
    REVISION_ENTITY_LABEL_MAP,
    LoadedMockData,
    match_bin_issuers_to_merchants,
    resolve_revision_user_names,
    run_frontend_exporter,
    validate,
)

TABLES_IN_DEPENDENCY_ORDER = [
    "merchants",
    "bin_records",
    "sop_sheets",
    "sop_column_groups",
    "sop_columns",
    "sop_rows",
    "users",
    "user_sop_sheet_access",
    "revisions",
]


def resolve_frontend_data_dir() -> Path:
    override = os.environ.get("FRONTEND_DATA_DIR")
    return Path(override).resolve() if override else DEFAULT_FRONTEND_DATA_DIR


def wipe_existing_dev_data(session: Session) -> None:
    """
    Idempotency strategy (see app/seed/README.md for the full reasoning):
    TRUNCATE every table this seed script owns, then re-insert everything
    fresh. Chosen over upsert/deterministic-matching because most of
    these tables (sop_column_groups, sop_columns, sop_rows) have no
    natural key to upsert on at all — their identity is purely
    positional/nested under a sheet — so a clean wipe is the only
    approach that's both simple and correct here. This is a development
    convenience only; no new constraint was added to the schema to
    support it, and it refuses to run outside development.
    """
    settings = get_settings()
    if settings.ENVIRONMENT == "production":
        raise RuntimeError("Refusing to run the mock-data seed script with ENVIRONMENT=production.")

    session.execute(
        text(
            "TRUNCATE TABLE "
            + ", ".join(reversed(TABLES_IN_DEPENDENCY_ORDER))
            + " RESTART IDENTITY CASCADE"
        )
    )


def insert_merchants(session: Session, data: LoadedMockData) -> dict:
    """Returns {frontend_merchant_id: db.Merchant} — the mapping required
    before SOP sheets can be imported (§4 of the task)."""
    by_frontend_id = {}
    for m in data.merchants:
        db_merchant = Merchant(name=m["name"], classification=m["classification"])
        session.add(db_merchant)
        by_frontend_id[m["id"]] = db_merchant
    session.flush()  # assigns .id to every merchant in one round trip
    return by_frontend_id


def insert_bin_records(session: Session, data: LoadedMockData, merchant_by_frontend_id: dict) -> dict:
    """Inserts all BIN records. merchant_id is set ONLY where the issuer
    name exactly (case-insensitively) matches an imported merchant name —
    never forced, never guessed. Returns match/no-match counts."""
    bin_match_by_index = match_bin_issuers_to_merchants(data)
    merchant_id_by_name_lower = {
        m["name"].strip().lower(): merchant_by_frontend_id[m["id"]].id for m in data.merchants
    }

    matched = 0
    unmatched = 0
    for i, rec in enumerate(data.bin_series):
        merchant_id = None
        if i in bin_match_by_index:
            merchant_id = merchant_id_by_name_lower[bin_match_by_index[i]]
            matched += 1
        else:
            unmatched += 1
        session.add(
            BinRecord(
                issuer=rec["issuer"],
                card_program_group_name=rec["cardProgramGroupName"],
                bin_iin=rec["binIin"],
                merchant_prefix=rec["merchantPrefix"],
                merchant_id=merchant_id,
            )
        )
    session.flush()
    return {"matched": matched, "unmatched": unmatched, "total": len(data.bin_series)}


def _build_sheet(sheet_data: dict, merchant_id) -> SopSheet:
    """Builds one SopSheet with its full column-group/column/row tree,
    preserving order and values exactly as given — no renaming, no
    normalization, no invented columns."""
    sheet = SopSheet(merchant_id=merchant_id, key=sheet_data["key"], name=sheet_data["name"])
    for group_order, group in enumerate(sheet_data["groups"]):
        column_group = SopColumnGroup(label=group["group"], sort_order=group_order)
        for column_order, column_name in enumerate(group["columns"]):
            column_group.columns.append(SopColumn(name=column_name, sort_order=column_order))
        sheet.column_groups.append(column_group)
    for row in sheet_data["rows"]:
        sheet.rows.append(SopRow(data=row))
    return sheet


def insert_sop_data(session: Session, data: LoadedMockData, merchant_by_frontend_id: dict) -> dict:
    """Imports every merchant's subsheets plus the one shared/default
    sheet (commonEscalation, merchant_id=NULL). Returns counts for the
    reconciliation report."""
    merchant_sheet_count = 0
    group_count = 0
    column_count = 0
    row_count = 0

    for m in data.merchants:
        db_merchant = merchant_by_frontend_id[m["id"]]
        for subsheet in m["subsheets"]:
            sheet = _build_sheet(subsheet, db_merchant.id)
            session.add(sheet)
            merchant_sheet_count += 1
            group_count += len(subsheet["groups"])
            column_count += sum(len(g["columns"]) for g in subsheet["groups"])
            row_count += len(subsheet["rows"])
        session.flush()  # one round trip per merchant, not per row

    shared_sheet = _build_sheet(data.common_escalation, merchant_id=None)
    session.add(shared_sheet)
    session.flush()

    return {
        "merchant_sheet_count": merchant_sheet_count,
        "shared_sheet_count": 1,
        "group_count": group_count + len(data.common_escalation["groups"]),
        "column_count": column_count + sum(len(g["columns"]) for g in data.common_escalation["groups"]),
        "row_count": row_count + len(data.common_escalation["rows"]),
    }


def insert_users(session: Session, data: LoadedMockData) -> dict:
    """
    Maps name/email/role/status directly. mobile is left NULL — the
    frontend's users.js has no `mobile` field at all (only
    id/name/email/role/status/lastActive). last_active_at is left NULL
    for every user — `lastActive` in the source is a relative display
    string ("2 min ago", "1 hr ago", "—") with no captured reference
    timestamp, so it cannot be safely converted into an absolute
    TIMESTAMPTZ without inventing a value.
    """
    by_frontend_id = {}
    for u in data.users:
        db_user = User(
            name=u["name"],
            email=u["email"],
            mobile=None,
            role=u["role"],
            status=u["status"],
            last_active_at=None,
        )
        session.add(db_user)
        by_frontend_id[u["id"]] = db_user
    session.flush()
    return by_frontend_id


def insert_revisions(session: Session, data: LoadedMockData, user_by_frontend_id: dict) -> int:
    """
    Maps timestamp -> occurred_at, user (display name) -> user_id (via
    resolve_revision_user_names, already validated total by validate()),
    type -> action_type (already validated to be an allowed value),
    entity -> entity_type (via the explicit, unambiguous label map),
    target -> target_label, change -> change_description verbatim.

    entity_id is left NULL for every revision: the mock data's `target`
    is free text (e.g. "BIN 401288 * HDFC Regalia") with no numeric ID
    anywhere in the source that could be attached to a specific row —
    inventing one would be inventing a relationship the source doesn't
    support.

    ASSUMPTION (documented, not silently applied): the source timestamp
    strings ("YYYY-MM-DD HH:mm") carry no timezone. They are interpreted
    as UTC here, since the mock data gives no basis for any other zone.
    """
    frontend_id_by_name_lower = {u["name"].strip().lower(): u["id"] for u in data.users}
    resolved_by_index = resolve_revision_user_names(data)

    count = 0
    for i, r in enumerate(data.revisions):
        name_key = resolved_by_index[i]  # guaranteed present — validate() already checked totality
        frontend_user_id = frontend_id_by_name_lower[name_key]
        db_user = user_by_frontend_id[frontend_user_id]

        occurred_at = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

        session.add(
            Revision(
                occurred_at=occurred_at,
                user_id=db_user.id,
                action_type=r["type"],
                entity_type=REVISION_ENTITY_LABEL_MAP[r["entity"]],
                entity_id=None,
                target_label=r["target"],
                change_description=r["change"],
                metadata_=None,
            )
        )
        count += 1
    session.flush()
    return count


def print_report(data: LoadedMockData, warnings: list, bin_stats: dict, sop_stats: dict, revision_count: int, session: Session) -> None:
    def count(sql: str) -> int:
        return session.execute(text(sql)).scalar_one()

    print("\n" + "=" * 70)
    print("PINELAB MOCK DATA IMPORT — RECONCILIATION REPORT")
    print("=" * 70)

    print("\n--- BIN Series ---")
    print(f"source count:            {len(data.bin_series)}")
    print(f"database count:          {count('SELECT count(*) FROM bin_records')}")
    print(f"matched to a merchant:   {bin_stats['matched']}")
    print(f"unmatched (merchant_id NULL): {bin_stats['unmatched']}")

    print("\n--- Merchants ---")
    print(f"source count:            {len(data.merchants)}")
    print(f"database count:          {count('SELECT count(*) FROM merchants')}")
    print("classification counts (database):")
    for row in session.execute(
        text("SELECT classification, count(*) FROM merchants GROUP BY classification ORDER BY classification")
    ):
        print(f"  {row[0]:<22} {row[1]}")

    print("\n--- SOP ---")
    print(f"source merchant-subsheet count:  {sop_stats['merchant_sheet_count']}")
    print(f"database merchant-owned sheets:  {count('SELECT count(*) FROM sop_sheets WHERE merchant_id IS NOT NULL')}")
    print(f"database shared/default sheets:  {count('SELECT count(*) FROM sop_sheets WHERE merchant_id IS NULL')}")
    print(f"column group count (source/db):  {sop_stats['group_count']} / {count('SELECT count(*) FROM sop_column_groups')}")
    print(f"column count (source/db):        {sop_stats['column_count']} / {count('SELECT count(*) FROM sop_columns')}")
    print(f"row count (source/db):           {sop_stats['row_count']} / {count('SELECT count(*) FROM sop_rows')}")
    print("sheet counts by key (database):")
    for row in session.execute(text("SELECT key, count(*) FROM sop_sheets GROUP BY key ORDER BY key")):
        print(f"  {row[0]:<22} {row[1]}")

    print("\n--- Users ---")
    print(f"source count:             {len(data.users)}")
    print(f"database count:           {count('SELECT count(*) FROM users')}")
    print("role counts (database):")
    for row in session.execute(text("SELECT role, count(*) FROM users GROUP BY role ORDER BY role")):
        print(f"  {row[0]:<18} {row[1]}")
    print("status counts (database):")
    for row in session.execute(text("SELECT status, count(*) FROM users GROUP BY status ORDER BY status")):
        print(f"  {row[0]:<18} {row[1]}")

    print("\n--- User SOP Sheet Access ---")
    print("source count:             0  (users.js has no `access` field — see README)")
    print(f"database count:           {count('SELECT count(*) FROM user_sop_sheet_access')}")

    print("\n--- Revisions ---")
    print(f"source count:             {len(data.revisions)}")
    print(f"database count:           {count('SELECT count(*) FROM revisions')}")
    print("action_type counts (database):")
    for row in session.execute(text("SELECT action_type, count(*) FROM revisions GROUP BY action_type ORDER BY action_type")):
        print(f"  {row[0]:<10} {row[1]}")
    print("entity_type counts (database):")
    for row in session.execute(text("SELECT entity_type, count(*) FROM revisions GROUP BY entity_type ORDER BY entity_type")):
        print(f"  {row[0]:<12} {row[1]}")

    if warnings:
        print(f"\n--- Non-fatal warnings ({len(warnings)}) ---")
        for w in warnings[:20]:
            print(f"  - {w}")
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more")
    else:
        print("\n--- Non-fatal warnings ---\n  none")

    print("\n" + "=" * 70)


def main() -> int:
    frontend_data_dir = resolve_frontend_data_dir()
    print(f"Loading frontend mock data from: {frontend_data_dir}")
    data = run_frontend_exporter(frontend_data_dir)
    print(
        f"Loaded: {len(data.bin_series)} BIN records, {len(data.merchants)} merchants, "
        f"1 shared escalation sheet, {len(data.users)} users, {len(data.revisions)} revisions"
    )

    print("Validating against backend schema constraints...")
    validation = validate(data)
    if not validation.ok:
        print(f"\nABORTED — {len(validation.issues)} validation issue(s) found. No database changes were made.", file=sys.stderr)
        for issue in validation.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    print(f"Validation passed ({len(validation.warnings)} non-fatal warning(s)).")

    with SessionLocal() as session:
        with session.begin():
            print("Wiping existing development data...")
            wipe_existing_dev_data(session)

            print("Importing merchants...")
            merchant_by_frontend_id = insert_merchants(session, data)

            print("Importing BIN records...")
            bin_stats = insert_bin_records(session, data, merchant_by_frontend_id)

            print("Importing SOP sheets/groups/columns/rows (this is the bulk of the data)...")
            sop_stats = insert_sop_data(session, data, merchant_by_frontend_id)

            print("Importing users...")
            user_by_frontend_id = insert_users(session, data)

            print("Skipping user_sop_sheet_access: no access data exists in the frontend source (see README).")

            print("Importing revisions...")
            revision_count = insert_revisions(session, data, user_by_frontend_id)

        # session.begin() context has committed by this point.
        print_report(data, validation.warnings, bin_stats, sop_stats, revision_count, session)

    print("\nImport complete.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
