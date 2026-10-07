"""
Aggregates all /api/v1 routers into a single APIRouter.

BIN Series (Part 7), Merchants (Part 8), SOP (Part 9), User Management
(Part 10), Revision History-read (Part 11), Dashboard-read (Part 12),
and Instances-read (Bin Series gap-analysis API #1) are implemented.

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

New Excel-format Bin Series task, Stage 6 (routers + registration):
bin_series_gift_card.router (mounted at "/bin-series/gift-card") and
bin_series_wallet.router (mounted at "/bin-series/wallet") add no path
conflicts — every old bin_series.router path lives directly under
"/bin-series" (e.g. "/bin-series/list"), never under a "/gift-card" or
"/wallet" sub-path.

bin_series_shared.router (the NEW combined lookup/bulk-lookup/
version-history endpoints) is mounted at the SAME "/bin-series" prefix
as the OLD bin_series.router, and both define GET /lookup, POST
/bulk-lookup, and GET /version-history — an unavoidable conflict, since
the new architecture's combined (Gift Card + Wallet) lookup is meant to
replace the old single-table one at these exact URLs (see
app/api/v1/bin_series_shared.py's module docstring for the full
reasoning). Starlette matches routes in registration order, so
bin_series_shared.router is included BEFORE bin_series.router below —
its /lookup, /bulk-lookup, /version-history win the match; every OTHER
route on the old router (/list, /create, /{binRecordId}/update,
/{binRecordId}/delete, /export, /statistics, /custom-columns/*) is
completely unaffected and stays fully reachable. Nothing is deleted —
the old router's own /lookup, /bulk-lookup, /version-history handlers
simply become unreachable dead code until Stage 7's approved cleanup.
"""
from fastapi import APIRouter, Depends

from app.api.v1 import (
    bin_series,
    bin_series_gift_card,
    bin_series_shared,
    bin_series_wallet,
    dashboard,
    instances,
    merchants,
    revisions,
    sop,
    users,
)
from app.core.auth import get_current_user

api_router = APIRouter()

_auth_required = [Depends(get_current_user)]

# Registered BEFORE bin_series.router so its /lookup, /bulk-lookup,
# /version-history win the route match — see this module's docstring.
api_router.include_router(bin_series_shared.router, prefix="/bin-series", tags=["bin-series"], dependencies=_auth_required)
api_router.include_router(
    bin_series_gift_card.router, prefix="/bin-series/gift-card", tags=["bin-series"], dependencies=_auth_required
)
api_router.include_router(
    bin_series_wallet.router, prefix="/bin-series/wallet", tags=["bin-series"], dependencies=_auth_required
)
api_router.include_router(bin_series.router, prefix="/bin-series", tags=["bin-series"], dependencies=_auth_required)
api_router.include_router(instances.router, prefix="/instances", tags=["instances"], dependencies=_auth_required)
api_router.include_router(merchants.router, prefix="/merchants", tags=["merchants"], dependencies=_auth_required)
api_router.include_router(sop.merchant_sheets_router, prefix="/merchants", tags=["sop"], dependencies=_auth_required)
api_router.include_router(sop.common_escalation_router, prefix="/sop", tags=["sop"], dependencies=_auth_required)
api_router.include_router(users.router, prefix="/users", tags=["users"], dependencies=_auth_required)
api_router.include_router(revisions.router, prefix="/revisions", tags=["revisions"], dependencies=_auth_required)
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"], dependencies=_auth_required)
api_router.include_router(instances.router, prefix="/instances", tags=["instances"], dependencies=_auth_required)

# Future domain routers will be registered here, e.g.:
#
#   from app.api.v1 import auth
#
#   api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
