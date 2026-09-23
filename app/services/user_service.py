"""
Service layer for User Management. Orchestrates
app.repositories.user_repository (plus merchant_repository/sop_repository
for access resolution and app.services.audit_service for revision
creation), maps ORM objects to app.schemas.user response schemas, and
raises the shared domain exceptions for not-found/conflict cases — no
raw SQLAlchemy query building here (only session.begin_nested() for
transaction boundaries, which is a session-lifecycle concern, not a
query).

TRANSACTION STRATEGY (unchanged from Part 10, now also covering the
audit write): create_user()/update_user()/delete_user() each wrap their
DB-mutating work — including the audit_service.create_revision() call —
in ONE `with db.begin_nested():` block. A real SAVEPOINT, independent of
whatever transaction the caller's session is already in (the
request-scoped session from app.core.database.get_db(), or a test's
long-lived session). If anything inside the block raises — a domain
ValidationError from access resolution, an IntegrityError from a DB
constraint, or a failure while creating the revision itself — SQLAlchemy
rolls back to the savepoint automatically, undoing every write made
inside the block (the user/access change AND the revision) before the
exception propagates. This is what makes "the business write and its
audit record must succeed or fail together" true, in both directions,
regardless of how the outer session is managed. The audit call is
placed as the LAST statement in each block specifically so that if it
is what fails, everything written before it in that same request is
still rolled back too (not just "nothing was ever attempted").

ACTOR HANDLING (Part 13 — see app/api/v1/users.py and
app/services/audit_service.py for the full picture): authentication is
not implemented yet, so every write-service function that needs to
record "who did this" now takes an explicit `actor_user_id: int`
parameter. Nothing in this module invents, defaults, or hardcodes an
actor. The actor is validated to be a real, existing user (the same way
the target user_id is) — an unresolvable actor is a 404, not silently
ignored.

ACCESS MAPPING: see app/schemas/user.py's module docstring for why
access dict keys are merchant NAMES, not the frontend's slug-style ids.
"""
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.user import User
from app.repositories import merchant_repository, sop_repository, user_repository
from app.schemas.common import PaginatedResponse
from app.schemas.user import CreateUserRequest, DeleteUserResponse, UpdateUserRequest, UserAccessResponse, UserResponse
from app.services import audit_service

# Human-readable labels for the fields update_user() can change, in the
# fixed order they should appear in a change_description when several
# change at once — matches the task's own example ordering (role, then
# status).
_UPDATABLE_FIELD_LABELS = [
    ("name", "Name"),
    ("email", "Email"),
    ("mobile", "Mobile"),
    ("role", "Role"),
    ("status", "Status"),
]


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        mobile=user.mobile,
        role=user.role,
        status=user.status,
        lastActiveAt=user.last_active_at,
    )


def _build_access_dict(db: Session, user_id: int) -> Dict[str, List[str]]:
    access: Dict[str, List[str]] = {}
    for merchant_name, sheet_key in user_repository.get_access_details(db, user_id):
        access.setdefault(merchant_name, []).append(sheet_key)
    return access


def _user_with_access_response(db: Session, user: User) -> UserAccessResponse:
    base = _user_to_response(user)
    return UserAccessResponse(**base.model_dump(), access=_build_access_dict(db, user.id))


def _require_user(db: Session, user_id: int) -> User:
    user = user_repository.get_by_id(db, user_id)
    if user is None:
        raise NotFoundError(f"No user found for id={user_id}.", code="USER_NOT_FOUND")
    return user


def _require_actor(db: Session, actor_user_id: int) -> User:
    """Same lookup as _require_user, with a distinct error message —
    an unresolvable actor is a real, reportable problem (e.g. a stale
    actorUserId), not something to silently substitute a default for."""
    actor = user_repository.get_by_id(db, actor_user_id)
    if actor is None:
        raise NotFoundError(f"No user found for actorUserId={actor_user_id}.", code="ACTOR_NOT_FOUND")
    return actor


def _resolve_sheet_id(db: Session, *, merchant_name: str, sheet_key: str) -> int:
    """Resolves one (merchant name, sheet key) access-payload entry to a
    concrete sop_sheets.id. Raises ValidationError (422) if either side
    doesn't resolve — this is request-body content validation, not a
    path-resource lookup, hence 422 rather than 404 (consistent with the
    task's own grouping of "merchant must exist"/"sheet key must exist"
    under the "Validation" heading alongside role/email checks)."""
    merchant = merchant_repository.get_by_name(db, name=merchant_name)
    if merchant is None:
        raise ValidationError(
            f"No merchant found for name={merchant_name!r} in access.",
            code="ACCESS_MERCHANT_NOT_FOUND",
        )
    # Scoped to THIS merchant's own sheets only — can never resolve a
    # sheet belonging to a different merchant, and can never resolve the
    # shared/default sheet (merchant_id IS NULL never matches merchant.id).
    sheet = sop_repository.get_merchant_sheet(db, merchant_id=merchant.id, key=sheet_key)
    if sheet is None:
        raise ValidationError(
            f"No SOP sheet with key={sheet_key!r} found for merchant {merchant_name!r} in access.",
            code="ACCESS_SHEET_NOT_FOUND",
        )
    return sheet.id


def _resolve_requested_sheet_ids(db: Session, access: Dict[str, List[str]]) -> Set[int]:
    """Read-only: resolves every (merchant, sheet_key) pair in `access`
    to a sheet id, validating each (raises ValidationError on a bad
    reference). Writes nothing — safe to call before deciding whether
    anything will actually change."""
    requested: Set[int] = set()
    for merchant_name, sheet_keys in access.items():
        for sheet_key in sheet_keys:
            requested.add(_resolve_sheet_id(db, merchant_name=merchant_name, sheet_key=sheet_key))
    return requested


def _describe_field_changes(field_changes: List[Tuple[str, object, object]]) -> str:
    """Builds e.g. "Role changed from Support Agent to Support Lead;
    status changed from Invited to Active" — matches the task's example
    exactly (first clause capitalized, subsequent clauses lowercased,
    joined with "; ")."""
    clauses = []
    for label, old, new in field_changes:
        clause = f"{label} changed from {old} to {new}"
        if clauses:
            clause = clause[0].lower() + clause[1:]
        clauses.append(clause)
    return "; ".join(clauses)


def _describe_access_change(added_count: int, removed_count: int, *, is_first_clause: bool) -> str:
    bits = []
    if added_count:
        bits.append(f"granted {added_count} sheet access(es)")
    if removed_count:
        bits.append(f"revoked {removed_count} sheet access(es)")
    clause = " and ".join(bits)
    if not is_first_clause:
        clause = clause[0].lower() + clause[1:]
    return clause


def list_users(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
) -> PaginatedResponse[UserResponse]:
    users, total = user_repository.search(
        db,
        search=search,
        role=role,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse[UserResponse](
        items=[_user_to_response(u) for u in users], total=total, page=page, pageSize=page_size
    )


def get_user(db: Session, *, user_id: int) -> UserAccessResponse:
    user = _require_user(db, user_id)
    return _user_with_access_response(db, user)


def create_user(db: Session, payload: CreateUserRequest, *, actor_user_id: int) -> UserAccessResponse:
    actor = _require_actor(db, actor_user_id)

    if user_repository.get_by_email(db, payload.email) is not None:
        raise ConflictError(f"A user with email={payload.email!r} already exists.", code="USER_EMAIL_ALREADY_EXISTS")

    try:
        with db.begin_nested():
            user = user_repository.create(db, name=payload.name, email=payload.email, mobile=payload.mobile, role=payload.role)
            # Resolved and granted one at a time (not pre-validated in
            # bulk first) so a bad reference partway through genuinely
            # exercises rollback of everything already written in this
            # request — not just "nothing was ever attempted".
            for merchant_name, sheet_keys in payload.access.items():
                for sheet_key in sheet_keys:
                    sheet_id = _resolve_sheet_id(db, merchant_name=merchant_name, sheet_key=sheet_key)
                    user_repository.grant_sheet_access(db, user_id=user.id, sheet_id=sheet_id)

            audit_service.create_revision(
                db,
                actor_user_id=actor.id,
                action_type="create",
                entity_type="user",
                entity_id=user.id,
                target_label=user.name,
                change_description="User created",
                metadata={"role": user.role, "status": user.status},
            )
    except IntegrityError as exc:
        # Defense in depth beyond the upfront email check above (e.g. a
        # concurrent request inserting the same email in between).
        raise ConflictError(
            f"A user with email={payload.email!r} already exists.", code="USER_EMAIL_ALREADY_EXISTS"
        ) from exc

    return _user_with_access_response(db, user)


def update_user(db: Session, *, user_id: int, payload: UpdateUserRequest, actor_user_id: int) -> UserAccessResponse:
    user = _require_user(db, user_id)
    actor = _require_actor(db, actor_user_id)

    if payload.email is not None and payload.email != user.email.lower():
        conflicting = user_repository.get_by_email(db, payload.email)
        if conflicting is not None and conflicting.id != user.id:
            raise ConflictError(
                f"A user with email={payload.email!r} already exists.", code="USER_EMAIL_ALREADY_EXISTS"
            )

    # --- Compute what would actually change, WITHOUT writing anything
    # yet, so we can (a) skip the whole operation (and any revision) if
    # nothing would really change, and (b) build an accurate
    # change_description from real before/after values.
    field_changes: List[Tuple[str, object, object]] = []
    fields_to_set: Dict[str, object] = {}
    for field_name, label in _UPDATABLE_FIELD_LABELS:
        value = getattr(payload, field_name)
        if value is not None:
            old_value = getattr(user, field_name)
            if value != old_value:
                field_changes.append((label, old_value, value))
                fields_to_set[field_name] = value
            # else: client resent the current value — not a real change.

    access_to_add: Set[int] = set()
    access_to_remove: Set[int] = set()
    if payload.access is not None:
        requested_sheet_ids = _resolve_requested_sheet_ids(db, payload.access)
        existing_sheet_ids = set(user_repository.get_granted_sheet_ids(db, user.id))
        access_to_remove = existing_sheet_ids - requested_sheet_ids
        access_to_add = requested_sheet_ids - existing_sheet_ids

    if not fields_to_set and not access_to_add and not access_to_remove:
        # Nothing would actually change — no write, no revision.
        return _user_with_access_response(db, user)

    change_clauses = []
    if field_changes:
        change_clauses.append(_describe_field_changes(field_changes))
    if access_to_add or access_to_remove:
        change_clauses.append(
            _describe_access_change(len(access_to_add), len(access_to_remove), is_first_clause=not change_clauses)
        )
    change_description = "; ".join(change_clauses)

    metadata = {}
    if field_changes:
        metadata["fieldsChanged"] = {label: {"from": old, "to": new} for label, old, new in field_changes}
    if access_to_add:
        metadata["accessGrantedSheetIds"] = sorted(access_to_add)
    if access_to_remove:
        metadata["accessRevokedSheetIds"] = sorted(access_to_remove)

    try:
        with db.begin_nested():
            if fields_to_set:
                user_repository.update_fields(db, user, **fields_to_set)
            if access_to_remove:
                user_repository.revoke_sheet_access(db, user_id=user.id, sheet_ids=access_to_remove)
            for sheet_id in access_to_add:
                user_repository.grant_sheet_access(db, user_id=user.id, sheet_id=sheet_id)

            audit_service.create_revision(
                db,
                actor_user_id=actor.id,
                action_type="update",
                entity_type="user",
                entity_id=user.id,
                target_label=user.name,
                change_description=change_description,
                metadata=metadata or None,
            )
    except IntegrityError as exc:
        raise ConflictError(
            f"A user with email={payload.email!r} already exists.", code="USER_EMAIL_ALREADY_EXISTS"
        ) from exc

    return _user_with_access_response(db, user)


def delete_user(db: Session, *, user_id: int, actor_user_id: int) -> DeleteUserResponse:
    user = _require_user(db, user_id)
    actor = _require_actor(db, actor_user_id)

    # Captured before deletion — the ORM instance may be expired/unusable
    # for attribute access once its row is gone.
    deleted_user_id = user.id
    deleted_user_name = user.name

    try:
        with db.begin_nested():
            user_repository.delete(db, user)
            # revision.user_id = the ACTOR performing the delete, NEVER
            # the deleted user — that FK could not reference a row this
            # same statement just removed. entity_id (not a foreign key
            # — see app/models/revision.py) safely carries the deleted
            # user's former id as a plain soft reference.
            audit_service.create_revision(
                db,
                actor_user_id=actor.id,
                action_type="delete",
                entity_type="user",
                entity_id=deleted_user_id,
                target_label=deleted_user_name,
                change_description="User deleted",
            )
    except IntegrityError as exc:
        # Two distinct scenarios can raise IntegrityError here, both
        # correctly blocked and rolled back by the savepoint either way:
        #   1. The TARGET user still has EXISTING revision history from
        #      past actions (revisions.user_id has no ON DELETE clause
        #      by design — Part 3/5/10) — the pre-existing, already-
        #      tested Part 10 behavior.
        #   2. actor_user_id == user_id (deleting oneself): the user row
        #      is gone by the time this function's OWN new revision tries
        #      to reference it as user_id. Not explicitly required by
        #      this task; surfaces as the same conflict rather than
        #      silently succeeding or leaving partial state.
        raise ConflictError(
            f"User id={user_id} cannot be deleted: existing revision history references this user.",
            code="USER_HAS_REVISION_HISTORY",
        ) from exc

    return DeleteUserResponse(success=True, message="User deleted successfully.")
