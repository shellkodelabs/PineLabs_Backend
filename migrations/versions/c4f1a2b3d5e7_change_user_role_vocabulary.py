"""change user role vocabulary

Revision ID: c4f1a2b3d5e7
Revises: b0892cfc6659
Create Date: 2026-10-08 13:30:00.000000

Reconciles the backend's user role vocabulary with the frontend's and the
new product spec. The OLD roles were:

    Admin, Support Lead, Support Agent, Auditor

The NEW roles are:

    Super Admin, Admin, SME, Viewer

PostgreSQL has no ALTER CHECK, so the ck_users_role constraint is dropped
and recreated with the new allowed set (same mechanism the Dynamic
Columns / gift-card migrations used for ck_revisions_entity_type).

Existing `users` rows are REMAPPED in place first (before the new
constraint is applied, or the UPDATE/validation would fail):

    Admin         -> Super Admin   (was the all-access role)
    Support Lead  -> Admin         (closest "manage everything but users")
    Support Agent -> SME
    Auditor       -> Viewer

This is additive-safe: every old value maps to exactly one new value, so
no row is left violating the new CHECK. The downgrade reverses both the
data remap and the constraint.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c4f1a2b3d5e7"
down_revision: Union[str, None] = "b0892cfc6659"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_ROLES = "'Admin', 'Support Lead', 'Support Agent', 'Auditor'"
_NEW_ROLES = "'Super Admin', 'Admin', 'SME', 'Viewer'"

# old value -> new value (applied in a CASE so the order is irrelevant and
# a single statement rewrites every row).
_FORWARD_MAP = {
    "Admin": "Super Admin",
    "Support Lead": "Admin",
    "Support Agent": "SME",
    "Auditor": "Viewer",
}
# Reverse for downgrade. 'Super Admin' -> 'Admin' and 'Admin' -> 'Support
# Lead' (the inverse of the forward map; unambiguous because the forward
# map is a bijection on these four values).
_REVERSE_MAP = {
    "Super Admin": "Admin",
    "Admin": "Support Lead",
    "SME": "Support Agent",
    "Viewer": "Auditor",
}


def _remap_sql(mapping: dict) -> str:
    cases = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in mapping.items())
    return f"UPDATE users SET role = CASE role {cases} ELSE role END"


def upgrade() -> None:
    # 1. Drop the OLD constraint first — otherwise the data remap below
    #    (which sets values like 'Super Admin' that the old CHECK forbids)
    #    would violate the still-active constraint mid-UPDATE.
    op.drop_constraint("ck_users_role", "users", type_="check")
    # 2. Remap existing rows to the new vocabulary.
    op.execute(_remap_sql(_FORWARD_MAP))
    # 3. Recreate the CHECK with the new allowed set.
    op.create_check_constraint("ck_users_role", "users", f"role IN ({_NEW_ROLES})")


def downgrade() -> None:
    # Mirror image: drop new CHECK, remap back, recreate old CHECK.
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.execute(_remap_sql(_REVERSE_MAP))
    op.create_check_constraint("ck_users_role", "users", f"role IN ({_OLD_ROLES})")
