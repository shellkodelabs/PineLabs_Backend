"""
Aggregates all /api/v1 routers into a single APIRouter.

BIN Series (Part 7), Merchants (Part 8), SOP (Part 9), User Management
(Part 10), Revision History-read (Part 11), and Dashboard-read (Part 12)
are implemented.

Part 14: every router registered here now requires authentication
(`dependencies=[Depends(get_current_user)]`), applied per-router at
inclusion time rather than as one blanket top-level dependency — this
keeps it explicit, per domain, which groups are protected, and makes it
trivial to exempt a future public endpoint without restructuring
anything. GET /health (registered directly on the app in app/main.py,
never on this router) remains the one public endpoint, per the task's
explicit instruction.

Note: sop.merchant_sheets_router shares the "/merchants" prefix with
merchants.router — see app/api/v1/sop.py's module docstring for why this
doesn't create a route conflict.
"""
from fastapi import APIRouter, Depends

from app.api.v1 import bin_series, dashboard, merchants, revisions, sop, users
from app.core.auth import get_current_user

api_router = APIRouter()

_auth_required = [Depends(get_current_user)]

api_router.include_router(bin_series.router, prefix="/bin-series", tags=["bin-series"], dependencies=_auth_required)
api_router.include_router(merchants.router, prefix="/merchants", tags=["merchants"], dependencies=_auth_required)
api_router.include_router(sop.merchant_sheets_router, prefix="/merchants", tags=["sop"], dependencies=_auth_required)
api_router.include_router(sop.common_escalation_router, prefix="/sop", tags=["sop"], dependencies=_auth_required)
api_router.include_router(users.router, prefix="/users", tags=["users"], dependencies=_auth_required)
api_router.include_router(revisions.router, prefix="/revisions", tags=["revisions"], dependencies=_auth_required)
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"], dependencies=_auth_required)

# Future domain routers will be registered here, e.g.:
#
#   from app.api.v1 import auth
#
#   api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
