"""
Service layer for Instances (read-only, API #1 of the Bin Series gap
analysis). Orchestrates app.repositories.instance_repository, maps ORM
objects to app.schemas.instance.InstanceResponse — no SQLAlchemy usage,
no HTTP-layer concerns. Same shape as app/services/merchant_service.py's
list_merchants().
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.instance import Instance
from app.repositories import instance_repository
from app.schemas.common import PaginatedResponse
from app.schemas.instance import InstanceResponse


def _to_response(instance: Instance) -> InstanceResponse:
    return InstanceResponse(
        id=instance.id,
        name=instance.name,
        description=instance.description,
        status=instance.status,
        updatedBy=instance.updated_by_user.name if instance.updated_by_user else None,
        updatedAt=instance.updated_at,
    )


def list_instances(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
) -> PaginatedResponse[InstanceResponse]:
    instances, total = instance_repository.search(
        db,
        search=search,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse[InstanceResponse](
        items=[_to_response(i) for i in instances],
        total=total,
        page=page,
        pageSize=page_size,
    )
