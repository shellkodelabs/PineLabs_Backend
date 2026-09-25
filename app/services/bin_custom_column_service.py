"""
Service layer for Bin Series dynamic/custom columns. Orchestrates
app.repositories.bin_custom_column_repository (plus bin_repository for
the create-time backfill), maps ORM objects to
app.schemas.bin_custom_column.CustomColumnResponse, and raises the
shared domain exceptions for not-found/conflict cases.

TRANSACTION STRATEGY: same pattern as every other write path in this
project (app/services/bin_service.py, user_service.py) —
create_column()/rename_column() wrap their DB-mutating work, including
the audit_service.create_revision() call, in ONE `with db.begin_nested():`
block (a real SAVEPOINT). create_column()'s backfill write
(bin_repository.backfill_custom_field — a single bulk UPDATE across
bin_records) is INSIDE that same block: the new column's metadata row
and every existing bin_record's backfilled value succeed or fail
together, exactly matching BinTable.jsx's own addColumn(), which updates
its column registry and every row's data in one synchronous state
update — never a partially-applied column.

ACTOR HANDLING: `actor.id` from the authenticated CurrentUser (see
app/core/auth.py) is used directly as both the revision's actor and
`updated_by_user_id` — never accepted from a request payload, same
convention as every other write in this project.

AUDIT: uses the existing audit_service.create_revision() — no second
audit system. entity_type="bin_custom_column" (added to
ENTITY_TYPE_VALUES by this task's migration) for column-level events;
action_type="create"/"update" (both already existed — no new value
needed). Row-level custom-FIELD-VALUE changes are NOT handled here —
those remain ordinary "bin_record" update revisions, see
app/services/bin_service.py.

Version History task: `_snapshot()` below captures a column's full,
JSON-serializable metadata (id/key/name/displayOrder) for a revision's
before/after — added to the SAME create_revision() calls above (no new
call sites). create_column(): before=None, after=the created column.
rename_column(): before=the column as it was immediately before
renaming, after=the same column immediately after — `key`/`id`/
`displayOrder` are identical in both (only `name` differs), which is
the correct, literal way to show "the key never changes" in a real
snapshot rather than a special-cased partial diff. See
app/services/bin_series_history_service.py for how GET
/api/v1/bin-series/version-history surfaces these.
"""
from typing import List

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.bin_custom_column import BinCustomColumn
from app.repositories import bin_custom_column_repository, bin_repository
from app.schemas.bin_custom_column import CreateCustomColumnRequest, CustomColumnResponse, RenameCustomColumnRequest
from app.services import audit_service


def _snapshot(column: BinCustomColumn) -> dict:
    """A complete, JSON-serializable point-in-time copy of a custom
    column's metadata — used ONLY as a revision's before/after history
    snapshot, never returned directly as an API response. Same
    id/key/name/displayOrder shape as CustomColumnResponse minus
    updatedBy/updatedAt (which describe the CURRENT row, not this
    specific historical moment — same reasoning as
    app/services/bin_service.py's own _snapshot())."""
    return {
        "id": column.id,
        "key": column.key,
        "name": column.name,
        "displayOrder": column.display_order,
    }


def _to_response(column: BinCustomColumn) -> CustomColumnResponse:
    return CustomColumnResponse(
        id=column.id,
        key=column.key,
        name=column.name,
        displayOrder=column.display_order,
        updatedBy=column.updated_by_user.name if column.updated_by_user else None,
        updatedAt=column.updated_at,
    )


def _require_column(db: Session, column_id: int) -> BinCustomColumn:
    column = bin_custom_column_repository.get_by_id(db, column_id)
    if column is None:
        raise NotFoundError(f"No custom column found for id={column_id}.", code="BIN_CUSTOM_COLUMN_NOT_FOUND")
    return column


def list_columns(db: Session) -> List[CustomColumnResponse]:
    columns = bin_custom_column_repository.list_all(db)
    return [_to_response(c) for c in columns]


def create_column(db: Session, payload: CreateCustomColumnRequest, *, actor_user_id: int) -> CustomColumnResponse:
    name = payload.name  # already stripped/whitespace-collapsed/validated non-blank by the schema
    # `key` is derived from the initial name at creation, using the exact
    # same normalization BinTable.jsx's addColumn() applies to its colKey
    # (trim + collapse internal whitespace) — the schema validator
    # already does this, so key and name start identical, per the
    # frontend's own "key = initial name" behavior.
    key = name

    if bin_custom_column_repository.get_by_name(db, name) is not None:
        raise ConflictError(f"A custom column named {name!r} already exists.", code="BIN_CUSTOM_COLUMN_ALREADY_EXISTS")

    display_order = bin_custom_column_repository.next_display_order(db)
    default_value = (payload.defaultValue or "").strip()

    try:
        with db.begin_nested():
            column = bin_custom_column_repository.create(
                db, key=key, name=name, display_order=display_order, updated_by_user_id=actor_user_id
            )
            # One bulk UPDATE across every existing bin_records row —
            # matches AddColumnModal.jsx's "applied to all N existing
            # records at once" behavior, never a per-row loop.
            bin_repository.backfill_custom_field(db, key=key, default_value=default_value)
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="create",
                entity_type="bin_custom_column",
                entity_id=column.id,
                target_label=column.name,
                change_description="Custom column created",
                metadata={
                    "key": key,
                    "defaultValue": default_value or None,
                    "before": None,
                    "after": _snapshot(column),
                },
            )
    except IntegrityError as exc:
        # Defense in depth beyond the upfront name check above (e.g. a
        # concurrent request creating the same key/name in between).
        raise ConflictError(
            f"A custom column named {name!r} already exists.", code="BIN_CUSTOM_COLUMN_ALREADY_EXISTS"
        ) from exc

    return _to_response(column)


def rename_column(
    db: Session, *, column_id: int, payload: RenameCustomColumnRequest, actor_user_id: int
) -> CustomColumnResponse:
    column = _require_column(db, column_id)
    new_name = payload.name

    if new_name == column.name:
        # Resending the current name — not a real change, matches
        # EditableHeader.jsx's own commit(): "next === current value ->
        # just close, no save call". No write, no revision.
        return _to_response(column)

    if bin_custom_column_repository.get_by_name(db, new_name, exclude_id=column.id) is not None:
        raise ConflictError(
            f"A custom column named {new_name!r} already exists.", code="BIN_CUSTOM_COLUMN_ALREADY_EXISTS"
        )

    old_name = column.name
    # Version History task: captured BEFORE the rename mutates `column`
    # below — same before-then-mutate-then-after convention as
    # app/services/bin_service.py's update path.
    before_snapshot = _snapshot(column)

    try:
        with db.begin_nested():
            bin_custom_column_repository.rename(db, column, name=new_name, updated_by_user_id=actor_user_id)
            # Renaming never touches row values — see
            # app/models/bin_custom_column.py's module docstring; `key`
            # (and therefore every bin_records.custom_fields entry keyed
            # by it) is completely untouched by this call — reflected
            # below by `before`/`after` sharing the same key/id/
            # displayOrder and differing only in `name`.
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="update",
                entity_type="bin_custom_column",
                entity_id=column.id,
                target_label=new_name,
                change_description=f"Name changed from {old_name} to {new_name}",
                metadata={
                    "key": column.key,
                    "fieldsChanged": {"Name": {"from": old_name, "to": new_name}},
                    "before": before_snapshot,
                    "after": _snapshot(column),
                },
            )
    except IntegrityError as exc:
        raise ConflictError(
            f"A custom column named {new_name!r} already exists.", code="BIN_CUSTOM_COLUMN_ALREADY_EXISTS"
        ) from exc

    return _to_response(column)
