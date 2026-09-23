"""
Repository for `users` and `user_sop_sheet_access` — SQLAlchemy queries
only. No business rules (role/email validation, access resolution,
transaction orchestration) live here — that belongs to
app/services/user_service.py.

Index usage:
  - `get_by_email` uses `uq_users_email`.
  - `search`'s exact `role`/`status` filters use `ix_users_role`/`ix_users_status`.
  - `search`'s free-text `search` filter is a substring ILIKE across
    name/email/mobile; the trigram index `ix_users_search` (name/email/role
    concatenation) partially accelerates this — `mobile` isn't covered by
    that index, but at 8 seeded users this is a non-issue.
  - `get_access_details` INNER JOINs through sop_sheets to merchants,
    which naturally excludes any (hypothetical) shared/default sheet
    grant, since those always have merchant_id IS NULL.
"""
from typing import Dict, List, Optional, Sequence, Tuple

# Aliased: this module also defines its own `delete(session, user)`
# function below (matching the task's requested repository API), which
# would otherwise shadow sqlalchemy.delete within this file.
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from app.models.merchant import Merchant
from app.models.sop import SopSheet
from app.models.user import User, UserSopSheetAccess

SORT_FIELD_MAP = {
    "name": User.name,
    "email": User.email,
    "role": User.role,
    "status": User.status,
    "id": User.id,
}


def get_by_id(session: Session, user_id: int) -> Optional[User]:
    return session.get(User, user_id)


def get_by_email(session: Session, email: str) -> Optional[User]:
    """Exact, case-insensitive match. Safe to return a single row because
    users.email is unique (and CreateUserRequest/UpdateUserRequest already
    normalize incoming emails to lowercase, so stored values are
    consistently lowercase going forward)."""
    stmt = select(User).where(func.lower(User.email) == email.strip().lower())
    return session.execute(stmt).scalar_one_or_none()


def search(
    session: Session,
    *,
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[User], int]:
    stmt = select(User)

    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(or_(User.name.ilike(term), User.email.ilike(term), User.mobile.ilike(term)))
    if role:
        stmt = stmt.where(User.role == role)
    if status:
        stmt = stmt.where(User.status == status)

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_column = SORT_FIELD_MAP.get(sort_by, User.id)
    order_fn = desc if sort_order == "desc" else asc
    order_clauses = [order_fn(sort_column)]
    if sort_column is not User.id:
        order_clauses.append(User.id)
    stmt = stmt.order_by(*order_clauses)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = list(session.execute(stmt).scalars().all())

    return items, total or 0


def create(session: Session, *, name: str, email: str, mobile: Optional[str], role: str) -> User:
    """New users always start life as 'Invited' — matches the frontend's
    CreateUserModal, which has no status field at all (Part 1/2 finding);
    status is only ever changed later via an explicit update."""
    user = User(name=name, email=email, mobile=mobile, role=role, status="Invited")
    session.add(user)
    session.flush()
    return user


def update_fields(session: Session, user: User, **fields) -> User:
    for key, value in fields.items():
        setattr(user, key, value)
    session.flush()
    return user


def delete(session: Session, user: User) -> None:
    """May raise sqlalchemy.exc.IntegrityError if the user is still
    referenced by revisions.user_id (no ON DELETE clause there by design
    — see app/models/revision.py). Translating that into a domain error
    is the service layer's job, not this one's."""
    session.delete(user)
    session.flush()


def get_access_details(session: Session, user_id: int) -> List[Tuple[str, str]]:
    """Returns (merchant_name, sheet_key) pairs for every sheet this user
    has access to, ordered for deterministic grouping by the caller."""
    stmt = (
        select(Merchant.name, SopSheet.key)
        .select_from(UserSopSheetAccess)
        .join(SopSheet, SopSheet.id == UserSopSheetAccess.sheet_id)
        .join(Merchant, Merchant.id == SopSheet.merchant_id)
        .where(UserSopSheetAccess.user_id == user_id)
        .order_by(Merchant.name, SopSheet.key)
    )
    return list(session.execute(stmt).all())


def get_granted_sheet_ids(session: Session, user_id: int) -> List[int]:
    stmt = select(UserSopSheetAccess.sheet_id).where(UserSopSheetAccess.user_id == user_id)
    return list(session.execute(stmt).scalars().all())


def grant_sheet_access(session: Session, *, user_id: int, sheet_id: int) -> UserSopSheetAccess:
    access = UserSopSheetAccess(user_id=user_id, sheet_id=sheet_id)
    session.add(access)
    session.flush()
    return access


def revoke_sheet_access(session: Session, *, user_id: int, sheet_ids: Sequence[int]) -> None:
    if not sheet_ids:
        return
    stmt = sa_delete(UserSopSheetAccess).where(
        UserSopSheetAccess.user_id == user_id, UserSopSheetAccess.sheet_id.in_(sheet_ids)
    )
    session.execute(stmt)
    session.flush()
