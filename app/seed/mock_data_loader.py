"""
Loads the frontend's mock data by actually running its JS source (via
Node — see export_frontend_data.mjs) and validates it against the
backend schema's constraints BEFORE any database write is attempted.

No data is invented, renamed, or normalized here. This module only:
  1. runs the real frontend modules and parses their JSON dump,
  2. checks the assumptions the backend schema requires
     (§17 of the Part 6 task), collecting every violation found, and
  3. computes the two cross-dataset matches the task requires — BIN
     issuer -> merchant name, and revision user display-name -> user —
     using a case-insensitive EXACT match only. No fuzzy matching.

If validation finds any issue, the caller (seed_from_mock_data.py) is
expected to abort before touching the database at all.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.models.merchant import CLASSIFICATION_VALUES
from app.models.revision import ACTION_TYPE_VALUES
from app.models.user import ROLE_VALUES, STATUS_VALUES

BIN_IIN_RE = re.compile(r"^\d{6}$")
MERCHANT_PREFIX_RE = re.compile(r"^\d{3}$")

# The frontend's revision `entity` field holds a display label; this is
# the only mapping observed in the mock data (Part 2: only these 3
# values ever appear) and it is unambiguous. No entry is guessed for
# labels that don't appear here — an unmapped label is a validation
# failure, not a silent guess.
REVISION_ENTITY_LABEL_MAP = {
    "BIN Series": "bin_record",
    "SOP Sheet": "sop_sheet",
    "User": "user",
}

# Role reconciliation between the frontend's mock users.js vocabulary
# (Admin/SME/Automation/Viewer) and the backend's role vocabulary. Per
# the product spec, the four frontend roles map onto the backend roles
# (Super Admin/Admin/SME/Viewer — see app/models/user.ROLE_VALUES and
# app/core/permissions.py) as:
#   Admin      -> Super Admin  (all access, incl. User Management)
#   Automation -> Admin        (all access EXCEPT User Management)
#   SME        -> SME          (BIN view; SOP CRUD; dashboard + history view)
#   Viewer     -> Viewer       (BIN / SOP / dashboard view-only)
# Unmapped names pass through so validate()/the DB CHECK still catches
# anything unexpected rather than this map hiding it.
_FRONTEND_TO_BACKEND_ROLE = {
    "Admin": "Super Admin",
    "Automation": "Admin",
    "SME": "SME",
    "Viewer": "Viewer",
}


def map_frontend_role(frontend_role: str) -> str:
    """Translate a frontend role name to a backend-allowed one. Unknown
    roles pass through unchanged so validate()/the DB CHECK constraint
    still catches anything genuinely unexpected rather than this map
    silently hiding it."""
    return _FRONTEND_TO_BACKEND_ROLE.get(frontend_role, frontend_role)


THIS_DIR = Path(__file__).resolve().parent
EXPORTER_SCRIPT = THIS_DIR / "export_frontend_data.mjs"

# Default: frontend repo is a sibling of this backend repo, per the
# actual on-disk layout (PineLabs_Backend/ and PineLabs_Frontend/ under
# the same parent). Overridable via FRONTEND_DATA_DIR for other layouts.
DEFAULT_FRONTEND_DATA_DIR = THIS_DIR.parents[2] / "PineLabs_Frontend" / "src" / "data"


@dataclass
class LoadedMockData:
    bin_series: list
    merchants: list
    common_escalation: dict
    users: list
    revisions: list


@dataclass
class ValidationResult:
    # Fatal: the seed script must abort before touching the database.
    issues: list = field(default_factory=list)
    # Non-fatal: reported in the final report, does not block the import.
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, message: str) -> None:
        self.issues.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def run_frontend_exporter(frontend_data_dir: Path) -> LoadedMockData:
    """Runs export_frontend_data.mjs against the real frontend source and
    parses its JSON output. Raises if Node fails or the directory/files
    don't exist — this is a hard prerequisite, not something to fall
    back from."""
    if not frontend_data_dir.is_dir():
        raise FileNotFoundError(
            f"Frontend data directory not found: {frontend_data_dir}\n"
            "Set FRONTEND_DATA_DIR to the PineLabs_Frontend/src/data path if your layout differs."
        )

    # The frontend modules use Vite-style extensionless relative imports
    # (e.g. `import { instances } from './sopData'`) that Node's native
    # ESM loader rejects. REGISTER_HOOK teaches this one-off Node run to
    # resolve them the way Vite does, without modifying frontend source.
    register_hook = THIS_DIR / "register_resolve_hook.mjs"
    result = subprocess.run(
        ["node", "--import", str(register_hook), str(EXPORTER_SCRIPT), str(frontend_data_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"Node exporter failed (exit {result.returncode}):\n{result.stderr}")

    payload = json.loads(result.stdout)
    return LoadedMockData(
        bin_series=payload["binSeries"],
        merchants=payload["merchants"],
        common_escalation=payload["commonEscalation"],
        users=payload["users"],
        revisions=payload["revisions"],
    )


def _sheet_flat_columns(sheet: dict) -> list:
    return [col for group in sheet["groups"] for col in group["columns"]]


def validate(data: LoadedMockData) -> ValidationResult:
    result = ValidationResult()

    # --- BIN records -----------------------------------------------------
    seen_bin_prefix = {}
    for i, rec in enumerate(data.bin_series):
        if not BIN_IIN_RE.match(rec["binIin"]):
            result.add(f"bin_series[{i}]: binIin {rec['binIin']!r} is not exactly 6 digits")
        if not MERCHANT_PREFIX_RE.match(rec["merchantPrefix"]):
            result.add(f"bin_series[{i}]: merchantPrefix {rec['merchantPrefix']!r} is not exactly 3 digits")
        key = (rec["binIin"], rec["merchantPrefix"])
        if key in seen_bin_prefix:
            result.add(
                f"bin_series[{i}]: duplicate (bin_iin, merchant_prefix) {key} "
                f"also seen at index {seen_bin_prefix[key]}"
            )
        else:
            seen_bin_prefix[key] = i

    # --- Merchants ---------------------------------------------------------
    seen_names = {}
    for i, m in enumerate(data.merchants):
        name_key = m["name"].strip().lower()
        if name_key in seen_names:
            result.add(f"merchants[{i}]: duplicate name {m['name']!r} also seen at index {seen_names[name_key]}")
        else:
            seen_names[name_key] = i
        if m["classification"] not in CLASSIFICATION_VALUES:
            result.add(f"merchants[{i}] ({m['name']!r}): classification {m['classification']!r} is not an allowed value")

    # --- SOP structure (sheets/groups/columns/rows all nested under a
    # merchant already validated above, or under the shared default) -------
    def _validate_sop_tree(owner_label: str, subsheets: list, row_issue_limit: int = 3):
        for sheet in subsheets:
            if not sheet.get("key"):
                result.add(f"{owner_label}: a subsheet has an empty key")
            if not sheet.get("name"):
                result.add(f"{owner_label}: subsheet {sheet.get('key')!r} has an empty name")
            flat_columns = set(_sheet_flat_columns(sheet))
            if not flat_columns:
                result.add(f"{owner_label}/{sheet.get('key')}: has no columns")
            mismatched = 0
            for row in sheet.get("rows", []):
                if set(row.keys()) != flat_columns:
                    mismatched += 1
            if mismatched:
                # Informational, not fatal: the task's required SOP checks
                # (§17) are structural (sheet->merchant, group->sheet,
                # column->group, row->sheet), not "row keys == declared
                # columns". Reported as a warning, not a blocking issue.
                result.warn(
                    f"{owner_label}/{sheet.get('key')}: {mismatched} row(s) have keys that don't "
                    f"exactly match the sheet's declared columns"
                )

    for m in data.merchants:
        _validate_sop_tree(f"merchant {m['name']!r}", m["subsheets"])
    _validate_sop_tree("shared default (commonEscalation)", [data.common_escalation])

    # --- Users ---------------------------------------------------------------
    seen_emails = {}
    for i, u in enumerate(data.users):
        email_key = u["email"].strip().lower()
        if email_key in seen_emails:
            result.add(f"users[{i}]: duplicate email {u['email']!r} also seen at index {seen_emails[email_key]}")
        else:
            seen_emails[email_key] = i
        # Validate the MAPPED role (see map_frontend_role / the Option A
        # note). The frontend's new role vocabulary is translated to the
        # backend's allowed set before insert, so validate against the
        # translated value — an unmapped role still fails here.
        mapped_role = map_frontend_role(u["role"])
        if mapped_role not in ROLE_VALUES:
            result.add(
                f"users[{i}] ({u['name']!r}): role {u['role']!r} "
                f"(mapped to {mapped_role!r}) is not an allowed value"
            )
        if u["status"] not in STATUS_VALUES:
            result.add(f"users[{i}] ({u['name']!r}): status {u['status']!r} is not an allowed value")

    # --- Revisions -------------------------------------------------------------
    # The frontend's revisions.js has since been redesigned into a
    # SOP-revision-workbook shape (instance/issuer/version/date/revisedBy/
    # description/reviewer/ticket) and no longer carries the
    # type/entity/user/timestamp/target/change fields the backend
    # `revisions` table maps from. The frontend's Revision History screen
    # also still reads that mock data directly and is NOT wired to the
    # backend /api/v1/revisions endpoint. So when the source lacks the
    # backend-contract fields, revision import is skipped (see
    # revisions_importable / main()) rather than failing the whole seed —
    # the backend revision rows aren't consumed by anything today, and
    # fabricating the missing fields would be inventing data. Only
    # validate revisions when they actually match the old importable shape.
    if revisions_importable(data):
        user_names_lower = {u["name"].strip().lower() for u in data.users}
        for i, r in enumerate(data.revisions):
            if r["type"] not in ACTION_TYPE_VALUES:
                result.add(f"revisions[{i}]: type {r['type']!r} is not an allowed action_type")
            if r["entity"] not in REVISION_ENTITY_LABEL_MAP:
                result.add(
                    f"revisions[{i}]: entity {r['entity']!r} has no unambiguous mapping to an allowed entity_type "
                    f"(known mappings: {list(REVISION_ENTITY_LABEL_MAP)})"
                )
            if r["user"].strip().lower() not in user_names_lower:
                result.add(
                    f"revisions[{i}]: user {r['user']!r} does not match any imported user by "
                    f"case-insensitive exact name — cannot resolve the required revisions.user_id FK"
                )

    return result


# The field set the backend `revisions` import maps from. If the frontend
# mock data no longer provides these (it was redesigned — see validate()),
# revision import is skipped rather than treated as a fatal error.
_REVISION_REQUIRED_KEYS = frozenset({"type", "entity", "user", "timestamp", "target", "change"})


def revisions_importable(data: LoadedMockData) -> bool:
    """True only if every revision record carries the backend-contract
    fields. An empty revision list is considered importable (nothing to
    do). A single record missing any required key makes the whole set
    non-importable, so the seed skips revisions cleanly instead of
    partially importing a shape it doesn't understand."""
    return all(_REVISION_REQUIRED_KEYS <= set(r.keys()) for r in data.revisions)


def match_bin_issuers_to_merchants(data: LoadedMockData) -> dict:
    """Returns {bin_series_index: merchant_name_lower} for every BIN record
    whose issuer exactly (case-insensitively) matches a merchant name.
    Records with no match are simply absent from the returned dict — no
    merchant_id is invented for them."""
    merchant_names_lower = {m["name"].strip().lower() for m in data.merchants}
    matches = {}
    for i, rec in enumerate(data.bin_series):
        issuer_key = rec["issuer"].strip().lower()
        if issuer_key in merchant_names_lower:
            matches[i] = issuer_key
    return matches


def resolve_revision_user_names(data: LoadedMockData) -> dict:
    """Returns {revision_index: user_name_lower} for every revision whose
    `user` display name matches an imported user by case-insensitive exact
    name. Validated to be total (every revision resolves) by validate()
    before this is trusted — see the NOT NULL FK requirement."""
    user_names_lower = {u["name"].strip().lower() for u in data.users}
    resolved = {}
    for i, r in enumerate(data.revisions):
        name_key = r["user"].strip().lower()
        if name_key in user_names_lower:
            resolved[i] = name_key
    return resolved
