"""
Instance router (read-only, API #1 of the Bin Series gap analysis) —
HTTP concerns only: parses/validates query parameters, injects the DB
session, calls the service layer, returns its result. No SQLAlchemy
usage and no business logic here — same pattern as
app/api/v1/merchants.py.

Authentication is enforced at inclusion time for this entire router (see
app/api/v1/router.py's `dependencies=[Depends(get_current_user)]`) — any
authenticated user of any role can read the instance list, same as every
other read endpoint in this project (no role-based filtering exists
anywhere in the read APIs today).

page/pageSize validated directly via FastAPI Query(ge=..., le=...)
(rejects out-of-range values with 422) rather than the shared
app.api.deps.get_pagination_params dependency, which clamps instead of
rejecting — same reasoning as bin_series.py/merchants.py.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.instance import STATUS_VALUES
from app.schemas.common import PaginatedResponse
from app.schemas.instance import InstanceResponse
from app.services import instance_service

router = APIRouter()

SortField = Literal["name", "status", "id"]
SortOrder = Literal["asc", "desc"]
StatusFilter = Literal[STATUS_VALUES]


@router.get("", response_model=PaginatedResponse[InstanceResponse])
def list_instances(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(50, ge=1, le=200, description="Items per page (1-200)"),
    search: Optional[str] = Query(None, min_length=1, description="Substring match on instance name"),
    status_filter: Optional[StatusFilter] = Query(None, alias="status", description="Exact status filter"),
    sortBy: Optional[SortField] = Query(None, description="Field to sort by (default: name)"),
    sortOrder: SortOrder = Query("asc"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[InstanceResponse]:
    """Paginated, searchable, filterable, sortable list of instances —
    backs the BIN Series Add/Clone form's "Select an instance" dropdown."""
    return instance_service.list_instances(
        db,
        page=page,
        page_size=pageSize,
        search=search,
        status=status_filter,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
