"""
Shared FastAPI dependencies used across API routers.

Kept deliberately small: a DB session, the current-user/authorization
dependencies, and a pagination helper — the things every domain router
needs, per the backend design (Part 3 §1, §12).
"""
from pydantic import BaseModel

# Re-exported so routers can import everything they need from one place:
#   from app.api.deps import get_db, get_current_user, require_roles, CurrentUser
from app.core.auth import CurrentUser, get_current_user, require_roles  # noqa: F401
from app.core.database import get_db  # noqa: F401


class PaginationParams(BaseModel):
    """Normalized page/pageSize pair, per the pagination contract in
    Part 3 §13 ({"items": [...], "total", "page", "pageSize"})."""

    page: int = 1
    pageSize: int = 50


def get_pagination_params(page: int = 1, pageSize: int = 50) -> PaginationParams:
    """
    Shared pagination dependency. Clamps values to sane bounds so every
    list endpoint doesn't have to re-implement the same guard:
      - page is at least 1
      - pageSize is between 1 and 200
    """
    return PaginationParams(page=max(page, 1), pageSize=min(max(pageSize, 1), 200))
