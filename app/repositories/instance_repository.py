"""
Repository for `instances` — SQLAlchemy queries only. No business rules,
no response-schema mapping, no HTTP concerns live here. Same shape as
merchant_repository / bin_repository.

Index usage:
  - `search`'s free-text `search` filter is a substring ILIKE across
    `name` and `ticket_number` (the UI searches both); the trigram index
    `ix_instances_name_trgm` accelerates the name half. ticket_number is
    not trigram-indexed — at expected volumes this is negligible.
  - `search`'s exact `status` filter uses `ix_instances_status`.
  - `get_by_name` is an exact, case-insensitive match, backed by the
    `uq_instances_name` unique constraint (at most one row can match).
  - `updated_by_user` is eager-loaded via joinedload on every read that
    feeds a response, so rendering a page of instances doesn't issue an
    extra per-row query to resolve each one's `updatedBy` name (N+1),
    same reasoning as bin_repository's joinedload of updated_by_user.
"""
from typing import List, Optional, Tuple

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.instance import Instance, InstanceDeletion, InstanceEdit

# Maps the API's camelCase sortBy values to actual ORM columns.
SORT_FIELD_MAP = {
    "name": Instance.name,
    "status": Instance.status,
    "id": Instance.id,
}


def get_by_id(session: Session, instance_id: int) -> Optional[Instance]:
    stmt = (
        select(Instance)
        .options(joinedload(Instance.updated_by_user))
        .where(Instance.id == instance_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_by_name(session: Session, name: str) -> Optional[Instance]:
    """Exact, case-insensitive match only — never a partial match. Safe
    to return a single row because instances.name is unique."""
    stmt = (
        select(Instance)
        .options(joinedload(Instance.updated_by_user))
        .where(func.lower(Instance.name) == name.strip().lower())
    )
    return session.execute(stmt).scalar_one_or_none()


def list_by_names(session: Session, names: List[str]) -> List[Instance]:
    """Fetch every instance whose (case-insensitive) name is in `names`,
    in ONE query. Used by the bulk import to resolve which rows are
    updates vs. creates without a per-row round-trip (avoids thousands of
    get_by_name calls on a large upload). Matching mirrors get_by_name:
    exact, case-insensitive."""
    if not names:
        return []
    lowered = list({n.strip().lower() for n in names})
    stmt = select(Instance).where(func.lower(Instance.name).in_(lowered))
    return list(session.execute(stmt).scalars().all())


def count_all(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Instance)) or 0


def count_by_status(session: Session, status: str) -> int:
    return session.scalar(
        select(func.count()).select_from(Instance).where(Instance.status == status)
    ) or 0


def list_for_export(
    session: Session,
    *,
    search: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Instance]:
    """All matching instances (no pagination), ordered by name — used to
    build an export file. Applies the SAME search/status filters as
    `search()` so an export reflects what the user is currently viewing."""
    stmt = select(Instance)
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(or_(Instance.name.ilike(term), Instance.ticket_number.ilike(term)))
    if status:
        stmt = stmt.where(Instance.status == status)
    stmt = stmt.order_by(func.lower(Instance.name), Instance.id)
    return list(session.execute(stmt).scalars().all())


def create(
    session: Session,
    *,
    name: str,
    status: str,
    ticket_number: Optional[str],
    updated_by_user_id: Optional[int],
    custom_fields: Optional[dict] = None,
) -> Instance:
    instance = Instance(
        name=name,
        status=status,
        ticket_number=ticket_number,
        updated_by_user_id=updated_by_user_id,
        custom_fields=custom_fields or {},
    )
    session.add(instance)
    session.flush()
    return instance


def update_fields(session: Session, instance: Instance, **fields) -> Instance:
    for key, value in fields.items():
        setattr(instance, key, value)
    session.flush()
    return instance


def delete(session: Session, instance: Instance) -> None:
    session.delete(instance)
    session.flush()


def create_deletion(
    session: Session,
    *,
    instance_id: int,
    instance_name: str,
    ticket_number: str,
    revised_by: str,
    reviewer: str,
    deleted_by_user_id: Optional[int],
) -> InstanceDeletion:
    """Record an instance-deletion audit row (write-only trail)."""
    record = InstanceDeletion(
        instance_id=instance_id,
        instance_name=instance_name,
        ticket_number=ticket_number,
        revised_by=revised_by,
        reviewer=reviewer,
        deleted_by_user_id=deleted_by_user_id,
    )
    session.add(record)
    session.flush()
    return record


def create_edit(
    session: Session,
    *,
    instance_id: int,
    instance_name: str,
    ticket_number: str,
    revised_by: str,
    reviewer: str,
    edited_by_user_id: Optional[int],
) -> InstanceEdit:
    """Record an instance-edit audit row (write-only trail). One row per
    edit, so the full edit history is preserved."""
    record = InstanceEdit(
        instance_id=instance_id,
        instance_name=instance_name,
        ticket_number=ticket_number,
        revised_by=revised_by,
        reviewer=reviewer,
        edited_by_user_id=edited_by_user_id,
    )
    session.add(record)
    session.flush()
    return record


def search(
    session: Session,
    *,
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Instance], int]:
    """Filters, sorts, and paginates instances. Returns (items, total)."""
    stmt = select(Instance).options(joinedload(Instance.updated_by_user))

    if search:
        term = f"%{search.strip()}%"
        # Match the instance name OR the ticket number — the UI's search
        # box is labelled "Search instance or ticket number". ticket_number
        # is nullable; ILIKE against NULL is simply NULL (never matches),
        # so no explicit NULL guard is needed.
        stmt = stmt.where(or_(Instance.name.ilike(term), Instance.ticket_number.ilike(term)))
    if status:
        stmt = stmt.where(Instance.status == status)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, Instance.id)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not Instance.id:
        # Deterministic tiebreaker for stable pagination across pages.
        order_clauses.append(Instance.id)
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0
