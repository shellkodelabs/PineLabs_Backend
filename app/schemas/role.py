"""
Schemas for the Roles API (GET /api/v1/roles).

Exposes the backend's role vocabulary + per-role permission map (the
single source of truth in app/core/permissions.py) so the frontend can
drive its role dropdowns and permission gating from the server instead
of a hardcoded copy.
"""
from typing import Dict, List

from pydantic import BaseModel, Field


class RoleResponse(BaseModel):
    """One role and the complete set of permission flags it grants."""

    name: str
    permissions: Dict[str, bool] = Field(default_factory=dict)


class RolesResponse(BaseModel):
    """All roles in their canonical order, each with its permission map.

    `roles` is ordered exactly as app.models.user.ROLE_VALUES, so the
    frontend can render the dropdown in a stable, intentional order
    (Super Admin, Admin, SME, Viewer)."""

    roles: List[RoleResponse]
