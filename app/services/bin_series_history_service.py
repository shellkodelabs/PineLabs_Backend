"""
Service layer for the Bin Series Version History (read-only) API —
backs GET /api/v1/bin-series/version-history (renamed from /history by
the API Naming task). Orchestrates
app.repositories.revision_repository and maps `Revision` ORM objects to
app.schemas.bin_series_history.BinSeriesHistoryEntryResponse — no
SQLAlchemy usage, no HTTP-layer concerns.

SCOPE: GLOBAL to the whole Bin Series table, not per-record. Confirmed
by frontend inspection (BinTable.jsx): the "Version History" button
opens VersionHistoryModal with the table's ENTIRE changeLog — there is
no per-row history trigger anywhere (RowActionsMenu.jsx has no history
action at all). So this queries revisions across every bin_record AND
bin_custom_column event, not one specific record's history — hence
GET /api/v1/bin-series/version-history, not
GET /api/v1/bin-series/{binRecordId}/version-history.

ENTITY TYPES: exactly ("bin_record", "bin_custom_column") — the two
`revisions.entity_type` values Bin Series ever writes. "merchant",
"sop_sheet", "sop_row", "user" revisions are out of scope here (they
already have their own home: GET /api/v1/revisions).

ACTION VOCABULARY: the frontend's own local changeLog (useChangeLog.js)
uses SIX event types for its icon/label lookup (VersionHistoryModal.jsx's
actionMeta, ChangePreviewModal.jsx's typeMeta): create | update | delete
| upload | column | rename — confirmed by reading BinTable.jsx's
addRow/saveEdit/saveCell/confirmDelete/addColumn/renameColumn, each of
which calls log() with one of those six literal type strings. The
persisted `revisions` table only has FOUR action_type values
(create/update/delete/upload) shared across ALL entities, so a
bin_custom_column "create" and a bin_record "create" are
indistinguishable by action_type alone — `_ACTION_LABELS` below
reconstructs the frontend's 6-way vocabulary from the (entity_type,
action_type) PAIR: a bin_custom_column create becomes "column" (matches
addColumn() -> log('column', ...), never log('create', ...)), and a
bin_custom_column update (the only kind that exists — rename) becomes
"rename" (matches renameColumn() -> log('rename', ...)).

NO SEPARATE DETAIL ENDPOINT: confirmed by frontend inspection that
ChangePreviewModal.jsx (opened when a history entry's eye icon is
clicked) reads `entry.preview` — the SAME entry object already in the
list, never a second fetch. So every entry returned here already
carries its full before/after snapshot; there is nothing a detail
endpoint would add that the list response doesn't already have.

NOT A FULL PAGINATION SCHEME: the frontend has no "load more" UI at
all — VersionHistoryModal.jsx always just slices `entries.slice(0,
limit)` (default limit=5) out of an array it already holds in full, no
matter how many changes exist. `list_bin_series_history` mirrors that
with a `limit` (query-scoped LIMIT, not a page/pageSize envelope) —
`total` is returned as informational context (how many matching
revisions exist in total), not as a page count.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.revision import Revision
from app.repositories import revision_repository
from app.schemas.bin_series_history import (
    BinSeriesHistoryEntryResponse,
    BinSeriesHistoryResponse,
    HistoryActorResponse,
)

_BIN_SERIES_ENTITY_TYPES = ("bin_record", "bin_custom_column")

_ACTION_LABELS = {
    ("bin_record", "create"): "create",
    ("bin_record", "update"): "update",
    ("bin_record", "delete"): "delete",
    ("bin_record", "upload"): "upload",
    ("bin_custom_column", "create"): "column",
    ("bin_custom_column", "update"): "rename",
}


def _to_entry(revision: Revision) -> BinSeriesHistoryEntryResponse:
    action = _ACTION_LABELS.get((revision.entity_type, revision.action_type), revision.action_type)
    metadata = revision.metadata_ or {}
    actor: Optional[HistoryActorResponse] = None
    if revision.user is not None:
        actor = HistoryActorResponse(id=revision.user.id, name=revision.user.name)

    return BinSeriesHistoryEntryResponse(
        revisionId=revision.id,
        action=action,
        entityType=revision.entity_type,
        entityId=revision.entity_id,
        occurredAt=revision.occurred_at,
        actor=actor,
        targetLabel=revision.target_label,
        changeDescription=revision.change_description,
        fieldsChanged=metadata.get("fieldsChanged"),
        # Deliberately `metadata.get(...)`, NOT `metadata.get(..., default)`
        # then falling back to anything derived from current DB state —
        # a revision written before this task shipped simply has no
        # "before"/"after" key, so this is None, never fabricated.
        before=metadata.get("before"),
        after=metadata.get("after"),
    )


def list_bin_series_history(db: Session, *, limit: int) -> BinSeriesHistoryResponse:
    revisions = revision_repository.list_for_entity_types(db, entity_types=_BIN_SERIES_ENTITY_TYPES, limit=limit)
    total = revision_repository.count_for_entity_types(db, entity_types=_BIN_SERIES_ENTITY_TYPES)
    return BinSeriesHistoryResponse(items=[_to_entry(r) for r in revisions], total=total)
