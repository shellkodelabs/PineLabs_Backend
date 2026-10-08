"""
Role → permission map: the single backend source of truth for what each
role can do, exposed to the frontend via GET /api/v1/roles so the UI no
longer hardcodes its own copy (previously in
PineLabs_Frontend/src/theme/RoleContext.jsx).

The permission flags mirror the frontend's existing `permsFor` shape so
RoleContext can consume this payload with no structural change, with ONE
addition: `canEditBin` is split out from the generic SOP write flags so a
role can be BIN view-only while still having full SOP CRUD (required by
the SME role). The generic canCreate/canEdit/canDelete/canUpload flags
continue to govern SOP writes.

Flag meanings:
  canManageUsers      — User Management module (create/edit/delete users)
  canManageInstances  — Instance Management module
  canViewBin          — can open the BIN Series module (view)
  canEditBin          — can create/edit/delete/upload within BIN Series
  canViewAutomation   — Automation Dashboard
  canViewHistory      — Revision History module (view)
  canCreate/canEdit/canDelete/canUpload — SOP data writes
  (viewing SOP + the Generic Dashboard is implied for every role)

Keeping this in one dict means the backend enforces (via require_roles /
future per-endpoint checks) and the frontend renders from the exact same
definition.
"""
from typing import Dict

from app.models.user import ROLE_VALUES

# Every permission flag the UI understands, so each role entry is a
# complete, explicit map (no implicit falsy gaps).
_ALL_FLAGS = (
    "canManageUsers",
    "canManageInstances",
    "canViewBin",
    "canEditBin",
    "canViewAutomation",
    "canViewHistory",
    "canCreate",
    "canEdit",
    "canDelete",
    "canUpload",
)


def _perms(**overrides: bool) -> Dict[str, bool]:
    """Build a full permission map defaulting every flag to False, then
    applying the given True overrides — so each role lists only what it
    CAN do and everything else is explicitly denied."""
    base = {flag: False for flag in _ALL_FLAGS}
    base.update(overrides)
    return base


# Role → full permission map. Order follows ROLE_VALUES.
ROLE_PERMISSIONS: Dict[str, Dict[str, bool]] = {
    # Super Admin — everything, including User Management.
    "Super Admin": _perms(
        canManageUsers=True,
        canManageInstances=True,
        canViewBin=True,
        canEditBin=True,
        canViewAutomation=True,
        canViewHistory=True,
        canCreate=True,
        canEdit=True,
        canDelete=True,
        canUpload=True,
    ),
    # Admin — everything EXCEPT User Management.
    "Admin": _perms(
        canManageInstances=True,
        canViewBin=True,
        canEditBin=True,
        canViewAutomation=True,
        canViewHistory=True,
        canCreate=True,
        canEdit=True,
        canDelete=True,
        canUpload=True,
    ),
    # SME — BIN view-only; SOP full CRUD; Generic Dashboard + Revision
    # History view. No User/Instance management, no Automation, no BIN edit.
    "SME": _perms(
        canViewBin=True,
        canViewHistory=True,
        canCreate=True,
        canEdit=True,
        canDelete=True,
        canUpload=True,
    ),
    # Viewer — BIN / SOP / Generic Dashboard view-only. No Revision History.
    "Viewer": _perms(
        canViewBin=True,
    ),
}

# Defensive: keep the map and the vocabulary in lockstep.
assert set(ROLE_PERMISSIONS) == set(ROLE_VALUES), (
    "ROLE_PERMISSIONS must define exactly the roles in ROLE_VALUES"
)
