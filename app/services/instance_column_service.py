"""
Service layer for Instance Management CUSTOM COLUMNS.

Two responsibilities:
  1. CRUD + reorder over the column DEFINITIONS (instance_columns), incl.
     backfilling existing instances when a column is added and stripping
     the key from every instance when a column is deleted.
  2. `coerce_and_validate_custom_fields()` — the single shared helper
     that create/update/import all call to turn a raw
     {key-or-label: value} map into a validated {key: value} map ready
     to store in Instance.custom_fields, per each column's type/required
     rules. Skipped OPTIONAL cells become the column default, or 'NA' if
     no default is set (per product decision — a cell is never left
     empty/ambiguous).

No SQLAlchemy or HTTP concerns here beyond calling the repository.
"""
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.instance import BUILTIN_COLUMN_DEFS, InstanceColumn
from app.repositories import instance_column_repository
from app.schemas.instance import (
    CreateInstanceColumnRequest,
    DeleteInstanceColumnRequest,
    InstanceColumnResponse,
    InstanceColumnsResponse,
    LayoutColumnResponse,
    UpdateInstanceColumnRequest,
)

# The fixed built-in columns of the Instance Management table. Their
# left-to-right ORDER is no longer hardcoded here — it is persisted in
# `instance_builtin_columns` (see InstanceBuiltinColumn) so drag-and-drop
# reordering survives a reload. This map is just the label lookup (built-
# ins are not renameable) keyed by the fixed built-in key.
# Custom columns are positioned relative to these (or to each other) via
# `after_key`; a custom column with after_key=None sits BEFORE the first
# built-in. The trailing actions column is a UI concern (always pinned
# last) and is NOT part of this orderable set.
BUILTIN_LABELS = {c["key"]: c["label"] for c in BUILTIN_COLUMN_DEFS}
BUILTIN_KEYS = set(BUILTIN_LABELS)


def _ordered_builtins(db: Session) -> List[dict]:
    """The built-in columns in their persisted left-to-right order, as
    [{'key','label'}, ...]. Order comes from instance_builtin_columns;
    the label comes from BUILTIN_LABELS."""
    return [
        {"key": row.key, "label": BUILTIN_LABELS.get(row.key, row.key)}
        for row in instance_column_repository.list_builtin_order(db)
    ]

# The placeholder stored for an optional cell the user left blank when
# the column has no default (product decision — never store empty/NULL).
_MISSING_PLACEHOLDER = "NA"




# ---------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------
def _to_response(column: InstanceColumn) -> InstanceColumnResponse:
    return InstanceColumnResponse(
        id=column.id,
        key=column.key,
        label=column.label,
        type=column.type,
        required=column.required,
        defaultValue=column.default_value,
        options=column.options,
        afterKey=column.after_key,
        sortOrder=column.sort_order,
        ticketNumber=column.ticket_number,
        revisedBy=column.revised_by,
        reviewer=column.reviewer,
    )


def _slugify_key(label: str) -> str:
    """Derive a stable machine key from a label: lowercase, non-alnum ->
    underscore, collapsed/trimmed. e.g. 'Card Network!' -> 'card_network'."""
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug or "column"


def _unique_key(db: Session, base: str) -> str:
    """Ensure the derived key doesn't collide with an existing column's
    key (labels can be reused after delete; keys must stay unique)."""
    candidate = base
    suffix = 2
    while instance_column_repository.get_by_key(db, candidate) is not None:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


# ---------------------------------------------------------------------
# Definition CRUD
# ---------------------------------------------------------------------
def list_columns(db: Session) -> List[InstanceColumnResponse]:
    return [_to_response(c) for c in instance_column_repository.list_all(db)]


def _resolve_order(builtins: List[dict], custom_columns: List[InstanceColumn]) -> List:
    """Compute the single merged left-to-right column order (built-ins +
    custom) by resolving each custom column's `after_key` anchor.

    `builtins` is the persisted built-in order ([{'key','label'}, ...]);
    see _ordered_builtins.

    Algorithm: start from the built-in order. Then repeatedly place each
    custom column immediately after its anchor:
      - after_key is None  -> insert at the very beginning (before the
        first built-in)
      - after_key == some   -> insert right after that key
    Multiple customs sharing the same anchor keep a stable order by
    (sort_order, id). Anchors that point at another custom column are
    handled by iterating until all are placed (a custom can follow a
    custom). Any custom whose anchor can't be found (e.g. its target was
    deleted) falls back to the end, so nothing is ever lost.

    Returns a list of dicts: built-ins -> {'builtin': True, 'key','label'};
    customs -> {'builtin': False, 'column': InstanceColumn}."""
    order = [{"builtin": True, "key": c["key"], "label": c["label"]} for c in builtins]

    # Stable processing order for deterministic placement.
    pending = sorted(custom_columns, key=lambda c: (c.sort_order, c.id))

    # Iterate until no more can be placed this pass; place leftovers at end.
    progress = True
    while pending and progress:
        progress = False
        still_pending = []
        placed_keys = {e["key"] if e["builtin"] else e["column"].key for e in order}
        for col in pending:
            anchor = col.after_key
            if anchor is None:
                order.insert(0, {"builtin": False, "column": col})
                progress = True
            elif anchor in placed_keys:
                idx = next(
                    i
                    for i, e in enumerate(order)
                    if (e["key"] if e["builtin"] else e["column"].key) == anchor
                )
                order.insert(idx + 1, {"builtin": False, "column": col})
                progress = True
            else:
                still_pending.append(col)
        pending = still_pending

    # Anything unresolved (dangling anchor) goes to the end, stable order.
    for col in pending:
        order.append({"builtin": False, "column": col})

    return order


def get_columns_view(db: Session) -> InstanceColumnsResponse:
    """Both column views in one payload (see InstanceColumnsResponse):
    the custom `definitions` and the full merged `layout`. Backs the
    single GET /instances/columns endpoint."""
    return InstanceColumnsResponse(definitions=list_columns(db), layout=build_layout(db))


def build_layout(db: Session) -> List[LayoutColumnResponse]:
    """The full merged, ordered column layout (built-ins + custom
    interleaved) that the frontend renders the table from."""
    resolved = _resolve_order(_ordered_builtins(db), instance_column_repository.list_all(db))
    layout: List[LayoutColumnResponse] = []
    for entry in resolved:
        if entry["builtin"]:
            layout.append(LayoutColumnResponse(key=entry["key"], label=entry["label"], builtin=True))
        else:
            c = entry["column"]
            layout.append(
                LayoutColumnResponse(
                    key=c.key,
                    label=c.label,
                    builtin=False,
                    id=c.id,
                    type=c.type,
                    required=c.required,
                    options=c.options,
                )
            )
    return layout


def _validate_after_key(db: Session, after_key: Optional[str], *, exclude_id: Optional[int] = None) -> None:
    """after_key must be None (start) or reference an existing column —
    a built-in key or another custom column's key (not the column itself)."""
    if after_key is None:
        return
    if after_key in BUILTIN_KEYS:
        return
    target = instance_column_repository.get_by_key(db, after_key)
    if target is None or (exclude_id is not None and target.id == exclude_id):
        raise ValidationError(
            f"afterKey {after_key!r} does not reference an existing column.",
            code="INSTANCE_COLUMN_INVALID_POSITION",
        )


def _require_column(db: Session, column_id: int) -> InstanceColumn:
    column = instance_column_repository.get_by_id(db, column_id)
    if column is None:
        raise NotFoundError(f"No instance column found for id={column_id}.", code="INSTANCE_COLUMN_NOT_FOUND")
    return column


def create_column(db: Session, payload: CreateInstanceColumnRequest) -> InstanceColumnResponse:
    label = payload.label.strip()
    if instance_column_repository.get_by_label(db, label) is not None:
        raise ConflictError(f"A column already exists with label={label!r}.", code="INSTANCE_COLUMN_LABEL_EXISTS")

    _validate_after_key(db, payload.afterKey)

    # Custom columns are plain free text now — no type/required/dropdown
    # validation. The default (if any) is just trimmed; existing rows are
    # backfilled with it, or with the 'NA' placeholder when no default.
    default_value = payload.defaultValue.strip() if payload.defaultValue else None
    backfill_value = default_value if default_value else _MISSING_PLACEHOLDER

    key = _unique_key(db, _slugify_key(label))

    try:
        with db.begin_nested():
            column = instance_column_repository.create(
                db,
                key=key,
                label=label,
                # Always a plain text, optional column.
                type_="text",
                required=False,
                default_value=default_value,
                options=None,
                sort_order=instance_column_repository.next_sort_order(db),
                after_key=payload.afterKey,
                # Audit trail (mandatory, schema-enforced non-blank).
                ticket_number=payload.ticketNumber,
                revised_by=payload.revisedBy,
                reviewer=payload.reviewer,
            )
            # Backfill every existing instance with the default (or NA) so
            # no row is missing this key.
            instance_column_repository.backfill_key(db, key=key, value=backfill_value)
    except IntegrityError as exc:
        raise ConflictError(
            f"A column already exists with label={label!r}.",
            code="INSTANCE_COLUMN_LABEL_EXISTS",
        ) from exc

    return _to_response(column)


def update_column(db: Session, *, column_id: int, payload: UpdateInstanceColumnRequest) -> InstanceColumnResponse:
    column = _require_column(db, column_id)

    fields: Dict[str, Any] = {}

    if payload.label is not None:
        new_label = payload.label.strip()
        if new_label.lower() != column.label.lower():
            existing = instance_column_repository.get_by_label(db, new_label)
            if existing is not None and existing.id != column.id:
                raise ConflictError(
                    f"A column already exists with label={new_label!r}.",
                    code="INSTANCE_COLUMN_LABEL_EXISTS",
                )
            fields["label"] = new_label

    # Custom columns are plain text now — no type/required/dropdown rules.
    # `options`/`required` in the payload are ignored; only the default
    # value (plain text) can still be changed.
    if payload.defaultValue is not None:
        fields["default_value"] = payload.defaultValue.strip() or None

    # Audit trail — the Rename Column dialog sends all three. Persist
    # whichever are provided (validators already enforced non-blank).
    if payload.ticketNumber is not None:
        fields["ticket_number"] = payload.ticketNumber
    if payload.revisedBy is not None:
        fields["revised_by"] = payload.revisedBy
    if payload.reviewer is not None:
        fields["reviewer"] = payload.reviewer

    # Repositioning: `afterKey` is Optional[str] where None legitimately
    # means "move to the very start", so we distinguish "provided" from
    # "omitted" via model_fields_set rather than a None check.
    if "afterKey" in payload.model_fields_set:
        if payload.afterKey == column.key:
            raise ValidationError(
                "A column cannot be positioned after itself.",
                code="INSTANCE_COLUMN_INVALID_POSITION",
            )
        _validate_after_key(db, payload.afterKey, exclude_id=column.id)
        fields["after_key"] = payload.afterKey

    if not fields:
        return _to_response(column)

    with db.begin_nested():
        instance_column_repository.update_fields(db, column, **fields)

    return _to_response(column)


def delete_column(
    db: Session,
    *,
    column_id: int,
    payload: DeleteInstanceColumnRequest,
    actor_user_id: int,
) -> int:
    column = _require_column(db, column_id)
    deleted_id = column.id
    key = column.key
    label = column.label
    with db.begin_nested():
        # Record the deletion audit FIRST (captures the column's key/label
        # before the row is gone), then delete the column and strip its
        # key from every instance's custom_fields. All in one savepoint,
        # so either the whole deletion (audit + removal) commits or none.
        instance_column_repository.create_deletion(
            db,
            column_key=key,
            column_label=label,
            ticket_number=payload.ticketNumber,
            revised_by=payload.revisedBy,
            reviewer=payload.reviewer,
            deleted_by_user_id=actor_user_id,
        )
        instance_column_repository.delete(db, column)
        instance_column_repository.remove_key(db, key=key)
    return deleted_id


def reorder_columns(db: Session, *, ordered_keys: List[str]) -> List[LayoutColumnResponse]:
    """Persist a brand-new left-to-right order for the WHOLE table from a
    single list of column keys (built-ins + custom interleaved).

    `ordered_keys` must be exactly the current set of column keys (every
    built-in key + every custom column key), no more, no fewer, in the new
    display order. From this we:

      - rewrite each built-in's stored sort_order to its index among the
        built-ins (preserving their new relative order), and
      - set each custom column's after_key to the key of whatever column
        sits immediately to its LEFT in the list (None if it's the very
        first column), so it re-anchors wherever it was dropped — even
        before the first built-in or between two built-ins.

    A custom column's sort_order is also rewritten to its global index, so
    two customs sharing the same anchor keep the dropped order.

    Returns the freshly resolved layout (same shape as build_layout) so
    the caller can render immediately without a second round-trip.
    """
    custom_columns = instance_column_repository.list_all(db)
    custom_by_key = {c.key: c for c in custom_columns}
    builtin_rows = instance_column_repository.list_builtin_order(db)
    builtin_by_key = {row.key: row for row in builtin_rows}

    expected = set(custom_by_key) | set(builtin_by_key)
    received = list(ordered_keys)
    if len(received) != len(set(received)):
        raise ValidationError(
            "orderedKeys must not contain duplicate keys.",
            code="INSTANCE_COLUMN_REORDER_MISMATCH",
        )
    if set(received) != expected:
        raise ValidationError(
            "orderedKeys must contain exactly the current column keys "
            "(every built-in key and every custom column key).",
            code="INSTANCE_COLUMN_REORDER_MISMATCH",
        )

    with db.begin_nested():
        builtin_index = 0
        previous_key: Optional[str] = None
        for position, key in enumerate(ordered_keys):
            if key in builtin_by_key:
                # Built-ins keep their own 0..N ordering among themselves.
                instance_column_repository.set_builtin_sort_order(db, builtin_by_key[key], builtin_index)
                builtin_index += 1
            else:
                column = custom_by_key[key]
                # Re-anchor after whatever sits immediately to the left.
                instance_column_repository.update_fields(
                    db, column, after_key=previous_key, sort_order=position
                )
            previous_key = key

    return build_layout(db)


# ---------------------------------------------------------------------
# Value coercion / validation (shared by create/update/import)
# ---------------------------------------------------------------------
def _is_blank(value: Any) -> bool:
    """Blank ONLY when empty or whitespace-only. An NA-style placeholder
    (NA/na/null/none/-) is NOT blank — it's a real typed value and is
    stored verbatim. The only checkpoint is 'not empty'; a truly empty
    optional cell falls back to the column default / NA placeholder."""
    return value is None or not str(value).strip()


def _coerce_value(
    raw: Any,
    *,
    type_: str = "text",
    required: bool = False,
    default: Optional[str] = None,
    options: Optional[List[str]] = None,
    column_label: str = "",
) -> Any:
    """Resolve one custom-column cell to the value to STORE. There is NO
    data-type validation anymore — every custom column is plain free text,
    so the value is simply trimmed and stored as a string. A blank cell
    falls back to the column's default (if any), otherwise the 'NA'
    placeholder so a cell is never stored empty/ambiguous.

    The `type_`/`required`/`options`/`column_label` parameters are kept
    only so the existing call sites don't have to change; they are
    ignored. Nothing here ever raises."""
    if _is_blank(raw):
        if default is not None and str(default).strip():
            return str(default).strip()
        return _MISSING_PLACEHOLDER
    return str(raw).strip()


def coerce_row_custom_fields(
    db: Session,
    *,
    incoming: Dict[str, Any],
    columns: Optional[List] = None,
) -> "tuple[Dict[str, Any], List[str]]":
    """
    Import-row variant: resolve the custom-column cells present in
    `incoming` to their stored string values. Custom columns are plain
    text with no type/required rules, so this NEVER produces errors — the
    returned error list is always empty. The signature is kept (returns
    (coerced, errors)) so the import caller doesn't have to change.

    `columns` may be passed in to avoid a repeat DB fetch when processing
    many rows; otherwise it's loaded once here.
    """
    if columns is None:
        columns = instance_column_repository.list_all(db)

    # Index incoming by normalized key AND normalized label (imports key
    # by column key; be tolerant of labels too, same as the map helper).
    normalized = {}
    for k, v in (incoming or {}).items():
        normalized[str(k).strip().lower()] = v

    coerced: Dict[str, Any] = {}

    for col in columns:
        key_l = col.key.lower()
        label_l = col.label.strip().lower()
        if key_l in normalized:
            raw = normalized[key_l]
        elif label_l in normalized:
            raw = normalized[label_l]
        else:
            # Column not present in this row's incoming set — skip it;
            # absent columns are backfilled at write time.
            continue
        coerced[col.key] = _coerce_value(raw, default=col.default_value)

    return coerced, []


def coerce_and_validate_custom_fields(
    db: Session,
    *,
    incoming: Dict[str, Any],
    existing: Optional[Dict[str, Any]] = None,
    partial: bool = False,
) -> Dict[str, Any]:
    """
    Build the custom_fields map to store for an instance. Custom columns
    are plain free text — there is NO data-type or required validation;
    each supplied value is simply trimmed and stored (blank -> the
    column default, or 'NA' if none).

    - `incoming` may key values by column KEY or by column LABEL
      (case-insensitive) — imports use labels (the header text), the API
      uses keys; both are accepted.
    - `existing` is the instance's current custom_fields (for updates),
      so untouched columns keep their value.
    - `partial=True` (update): a column not present in `incoming` keeps
      its existing value. `partial=False` (create/import row): every
      column is resolved, filling absent cells with default/NA.
    """
    columns = instance_column_repository.list_all(db)
    existing = existing or {}

    # Index incoming by normalized key AND normalized label for lookup.
    normalized = {}
    for k, v in (incoming or {}).items():
        normalized[str(k).strip().lower()] = v

    result: Dict[str, Any] = dict(existing)

    for col in columns:
        supplied_key = col.key.lower() in normalized
        supplied_label = col.label.strip().lower() in normalized
        supplied = supplied_key or supplied_label

        if partial and not supplied:
            # Update that didn't mention this column: leave as-is.
            continue

        raw = None
        if supplied_key:
            raw = normalized[col.key.lower()]
        elif supplied_label:
            raw = normalized[col.label.strip().lower()]
        elif not partial:
            # create/import row didn't supply it: treat as blank so the
            # default/NA fallback kicks in.
            raw = existing.get(col.key)

        result[col.key] = _coerce_value(raw, default=col.default_value)

    return result
