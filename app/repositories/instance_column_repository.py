"""
Repository for `instance_columns` (the custom-column DEFINITIONS shown on
the Instance Management table) plus the helpers that read/rewrite the
`instances.custom_fields` JSONB when a column is added/removed. SQLAlchemy
queries only — no business rules, no HTTP concerns.
"""
from typing import List, Optional

from sqlalchemy import String, cast, func, literal, select, update

from sqlalchemy.orm import Session

from app.models.instance import (
    BUILTIN_COLUMN_DEFS,
    Instance,
    InstanceBuiltinColumn,
    InstanceColumn,
    InstanceColumnDeletion,
)


def list_all(session: Session) -> List[InstanceColumn]:
    """All column definitions, in display order then id (stable)."""
    stmt = select(InstanceColumn).order_by(InstanceColumn.sort_order, InstanceColumn.id)
    return list(session.execute(stmt).scalars().all())


# --- Built-in column ORDER (instance_builtin_columns) ----------------
def list_builtin_order(session: Session) -> List[InstanceBuiltinColumn]:
    """The built-in columns' persisted order rows, ordered by sort_order.

    Self-heals a partially-seeded table: any built-in key from
    BUILTIN_COLUMN_DEFS that has no row yet (e.g. a DB that predates the
    seed, or a newly-added built-in) is inserted at the end in its
    default order, so the resolver always sees every built-in exactly
    once.
    """
    rows = list(
        session.execute(
            select(InstanceBuiltinColumn).order_by(InstanceBuiltinColumn.sort_order, InstanceBuiltinColumn.key)
        )
        .scalars()
        .all()
    )
    present = {row.key for row in rows}
    missing = [d["key"] for d in BUILTIN_COLUMN_DEFS if d["key"] not in present]
    if missing:
        start = (max((row.sort_order for row in rows), default=-1)) + 1
        for offset, key in enumerate(missing):
            session.add(InstanceBuiltinColumn(key=key, sort_order=start + offset))
        session.flush()
        rows = list(
            session.execute(
                select(InstanceBuiltinColumn).order_by(
                    InstanceBuiltinColumn.sort_order, InstanceBuiltinColumn.key
                )
            )
            .scalars()
            .all()
        )
    return rows


def get_builtin(session: Session, key: str) -> Optional[InstanceBuiltinColumn]:
    return session.get(InstanceBuiltinColumn, key)


def set_builtin_sort_order(session: Session, builtin: InstanceBuiltinColumn, sort_order: int) -> None:
    builtin.sort_order = sort_order
    session.flush()


def get_by_id(session: Session, column_id: int) -> Optional[InstanceColumn]:
    return session.get(InstanceColumn, column_id)


def get_by_key(session: Session, key: str) -> Optional[InstanceColumn]:
    stmt = select(InstanceColumn).where(InstanceColumn.key == key)
    return session.execute(stmt).scalar_one_or_none()


def get_by_label(session: Session, label: str) -> Optional[InstanceColumn]:
    """Exact, case-insensitive label match — used to reject a duplicate
    header on create/rename."""
    stmt = select(InstanceColumn).where(func.lower(InstanceColumn.label) == label.strip().lower())
    return session.execute(stmt).scalar_one_or_none()


def next_sort_order(session: Session) -> int:
    current_max = session.scalar(select(func.max(InstanceColumn.sort_order)))
    return 0 if current_max is None else current_max + 1


def create(
    session: Session,
    *,
    key: str,
    label: str,
    type_: str,
    required: bool,
    default_value: Optional[str],
    options: Optional[list],
    sort_order: int,
    after_key: Optional[str] = None,
    ticket_number: Optional[str] = None,
    revised_by: Optional[str] = None,
    reviewer: Optional[str] = None,
) -> InstanceColumn:
    column = InstanceColumn(
        key=key,
        label=label,
        type=type_,
        required=required,
        default_value=default_value,
        options=options,
        sort_order=sort_order,
        after_key=after_key,
        ticket_number=ticket_number,
        revised_by=revised_by,
        reviewer=reviewer,
    )
    session.add(column)
    session.flush()
    return column


def update_fields(session: Session, column: InstanceColumn, **fields) -> InstanceColumn:
    for name, value in fields.items():
        setattr(column, name, value)
    session.flush()
    return column


def delete(session: Session, column: InstanceColumn) -> None:
    session.delete(column)
    session.flush()


def create_deletion(
    session: Session,
    *,
    column_key: str,
    column_label: str,
    ticket_number: str,
    revised_by: str,
    reviewer: str,
    deleted_by_user_id: Optional[int],
) -> InstanceColumnDeletion:
    """Record a column-deletion audit row (write-only trail)."""
    record = InstanceColumnDeletion(
        column_key=column_key,
        column_label=column_label,
        ticket_number=ticket_number,
        revised_by=revised_by,
        reviewer=reviewer,
        deleted_by_user_id=deleted_by_user_id,
    )
    session.add(record)
    session.flush()
    return record


# --- Bulk JSONB helpers over instances.custom_fields -----------------
def backfill_key(session: Session, *, key: str, value) -> None:
    """Set custom_fields[key] = value on EVERY instance that doesn't
    already have that key — used right after a new column is created so
    existing rows get its default. Uses a single UPDATE with jsonb_set.

    The value is cast to text before to_jsonb() — Postgres cannot infer
    the type of a bare bound parameter ("could not determine polymorphic
    type because input has type unknown"), so an explicit CAST is
    required. Stored as a JSON string, matching how custom_fields values
    are held elsewhere."""
    json_value = func.to_jsonb(cast(literal(str(value)), String))
    stmt = (
        update(Instance)
        .where(~Instance.custom_fields.has_key(key))  # noqa: W601 - SQLAlchemy JSONB .has_key()
        .values(custom_fields=func.jsonb_set(Instance.custom_fields, f"{{{key}}}", json_value))
    )
    session.execute(stmt)
    session.flush()


def remove_key(session: Session, *, key: str) -> None:
    """Strip a key from custom_fields on EVERY instance — used when a
    column is deleted. `#-` is Postgres' 'delete at path' JSONB operator."""
    stmt = update(Instance).values(custom_fields=Instance.custom_fields.op("#-")(f"{{{key}}}"))
    session.execute(stmt)
    session.flush()
