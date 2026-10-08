"""
Roles router (READ-ONLY) — exposes the backend's role vocabulary and the
per-role permission map so the frontend can source its role dropdowns and
permission gating from the server instead of hardcoding them (previously
PineLabs_Frontend/src/theme/RoleContext.jsx held its own copy).

HTTP concerns only — no business logic. The data comes straight from the
single source of truth: app.models.user.ROLE_VALUES (order/vocabulary) +
app.core.permissions.ROLE_PERMISSIONS (what each role can do).

Authentication is enforced at inclusion time (see app/api/v1/router.py),
same as every other /api/v1 router.
"""
from fastapi import APIRouter

from app.core.permissions import ROLE_PERMISSIONS
from app.models.user import ROLE_VALUES
from app.schemas.role import RoleResponse, RolesResponse

router = APIRouter()


@router.get("", response_model=RolesResponse)
def list_roles() -> RolesResponse:
    """All assignable roles, in canonical order, each with its full
    permission flag map. The frontend renders its role dropdown from
    `roles[].name` and derives UI gating from `roles[].permissions`."""
    return RolesResponse(
        roles=[
            RoleResponse(name=name, permissions=ROLE_PERMISSIONS[name])
            for name in ROLE_VALUES
        ]
    )
