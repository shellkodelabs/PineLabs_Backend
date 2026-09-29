"""
Service layer for Gift Card Bin Series dynamic/custom columns.
Structural mirror of the old, untouched
app/services/bin_custom_column_service.py (see that module's docstring
for the full reasoning behind every pattern below — transaction
strategy, actor handling, create-time backfill-inside-the-same-savepoint,
before/after snapshot convention) using
app.repositories.gift_card_bin_custom_column_repository (plus
app.repositories.gift_card_bin_repository for the backfill) instead —
independent of the old module, no shared base, no import from it, and
NOT shared with wallet_bin_custom_column_service.py, per the confirmed
architecture (two fully independent registries).

No delete_column(): confirmed by frontend inspection (no delete-column
UI anywhere — same as old bin_custom_column_service.py) that Gift Card
custom columns have no delete capability.
"""
from typing import List

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.gift_card_bin_custom_column import GiftCardBinCustomColumn
from app.repositories import gift_card_bin_custom_column_repository, gift_card_bin_repository
from app.schemas.gift_card_bin_custom_column import (
    CreateGiftCardCustomColumnRequest,
    GiftCardCustomColumnResponse,
    RenameGiftCardCustomColumnRequest,
)
from app.services import audit_service


def _snapshot(column: GiftCardBinCustomColumn) -> dict:
    return {
        "id": column.id,
        "key": column.key,
        "name": column.name,
        "displayOrder": column.display_order,
    }


def _to_response(column: GiftCardBinCustomColumn) -> GiftCardCustomColumnResponse:
    return GiftCardCustomColumnResponse(
        id=column.id,
        key=column.key,
        name=column.name,
        displayOrder=column.display_order,
        updatedBy=column.updated_by_user.name if column.updated_by_user else None,
        updatedAt=column.updated_at,
    )


def _require_column(db: Session, column_id: int) -> GiftCardBinCustomColumn:
    column = gift_card_bin_custom_column_repository.get_by_id(db, column_id)
    if column is None:
        raise NotFoundError(f"No Gift Card custom column found for id={column_id}.", code="BIN_CUSTOM_COLUMN_NOT_FOUND")
    return column


def list_columns(db: Session) -> List[GiftCardCustomColumnResponse]:
    columns = gift_card_bin_custom_column_repository.list_all(db)
    return [_to_response(c) for c in columns]


def create_column(
    db: Session, payload: CreateGiftCardCustomColumnRequest, *, actor_user_id: int
) -> GiftCardCustomColumnResponse:
    name = payload.name
    key = name

    if gift_card_bin_custom_column_repository.get_by_name(db, name) is not None:
        raise ConflictError(f"A Gift Card custom column named {name!r} already exists.", code="BIN_CUSTOM_COLUMN_ALREADY_EXISTS")

    display_order = gift_card_bin_custom_column_repository.next_display_order(db)
    default_value = (payload.defaultValue or "").strip()

    try:
        with db.begin_nested():
            column = gift_card_bin_custom_column_repository.create(
                db, key=key, name=name, display_order=display_order, updated_by_user_id=actor_user_id
            )
            # One bulk UPDATE across every existing gift_card_bin_records
            # row — matches AddColumnModal.jsx's "applied to all N
            # existing records at once" behavior, never a per-row loop.
            gift_card_bin_repository.backfill_custom_field(db, key=key, default_value=default_value)
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="create",
                entity_type="gift_card_bin_custom_column",
                entity_id=column.id,
                target_label=column.name,
                change_description="Gift Card custom column created",
                metadata={
                    "key": key,
                    "defaultValue": default_value or None,
                    "before": None,
                    "after": _snapshot(column),
                },
            )
    except IntegrityError as exc:
        raise ConflictError(
            f"A Gift Card custom column named {name!r} already exists.", code="BIN_CUSTOM_COLUMN_ALREADY_EXISTS"
        ) from exc

    return _to_response(column)


def rename_column(
    db: Session, *, column_id: int, payload: RenameGiftCardCustomColumnRequest, actor_user_id: int
) -> GiftCardCustomColumnResponse:
    column = _require_column(db, column_id)
    new_name = payload.name

    if new_name == column.name:
        return _to_response(column)

    if gift_card_bin_custom_column_repository.get_by_name(db, new_name, exclude_id=column.id) is not None:
        raise ConflictError(
            f"A Gift Card custom column named {new_name!r} already exists.", code="BIN_CUSTOM_COLUMN_ALREADY_EXISTS"
        )

    old_name = column.name
    before_snapshot = _snapshot(column)

    try:
        with db.begin_nested():
            gift_card_bin_custom_column_repository.rename(db, column, name=new_name, updated_by_user_id=actor_user_id)
            audit_service.create_revision(
                db,
                actor_user_id=actor_user_id,
                action_type="update",
                entity_type="gift_card_bin_custom_column",
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
            f"A Gift Card custom column named {new_name!r} already exists.", code="BIN_CUSTOM_COLUMN_ALREADY_EXISTS"
        ) from exc

    return _to_response(column)
