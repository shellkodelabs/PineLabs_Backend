"""
Reusable audit/revision-creation service.

`create_revision()` is the single place that inserts a row into
`revisions`. Every write-operation service (user_service today; future
BIN/merchant/SOP/upload write services) calls this instead of
constructing a Revision object itself, so revision-creation logic is
never duplicated across services.

ATOMICITY: this function only ever session.add()/flush()es — it NEVER
commits and NEVER opens its own transaction/savepoint. That is a
deliberate design choice: it means create_revision() automatically
participates in whatever transaction or SAVEPOINT the CALLER has
already opened (e.g. user_service's `with db.begin_nested():` block),
giving "business write + audit record, atomically" for free, without
this module needing any awareness of nested transactions, or of
whichever mechanism (Part 10's get_db() commit-on-success, or a test's
shared session) ultimately governs the outer transaction. If the
caller's block raises after calling this function, the flushed revision
row is rolled back along with everything else in that block; if
create_revision() itself fails, the caller's block raises and rolls
back too — either direction gets the same "succeed or fail together"
guarantee the caller wired up for its own writes.

TEMPORARY ACTOR HANDLING: authentication doesn't exist yet (Part 3 §15,
still unresolved), so every caller must pass `actor_user_id` explicitly
— the id of the user PERFORMING the write, never the id of the entity
being written (see revisions.user_id's documented meaning in
app/models/revision.py and Part 13's completion report). This function
does not invent, default, or hardcode an actor — see
app/api/v1/users.py for where that value currently comes from (an
explicit, temporary `actorUserId` query parameter) and how it is meant
to be swapped for `Depends(get_current_user)` later without this
function's signature changing at all.
"""
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.revision import ACTION_TYPE_VALUES, ENTITY_TYPE_VALUES, Revision


def create_revision(
    db: Session,
    *,
    actor_user_id: int,
    action_type: str,
    entity_type: str,
    entity_id: Optional[int],
    target_label: str,
    change_description: str,
    metadata: Optional[dict] = None,
) -> Revision:
    """
    Inserts and flushes one revision row. `action_type`/`entity_type`
    are validated against the model's own allowed-value constants — this
    is a defensive assertion against a programming error in a CALLING
    service (which always passes hardcoded literals it controls, e.g.
    action_type="create"), not a user-input validation path, hence a
    plain ValueError rather than a domain/HTTP exception.
    """
    if action_type not in ACTION_TYPE_VALUES:
        raise ValueError(f"action_type must be one of {ACTION_TYPE_VALUES}, got {action_type!r}")
    if entity_type not in ENTITY_TYPE_VALUES:
        raise ValueError(f"entity_type must be one of {ENTITY_TYPE_VALUES}, got {entity_type!r}")

    revision = Revision(
        user_id=actor_user_id,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        target_label=target_label,
        change_description=change_description,
        metadata_=metadata,
    )
    db.add(revision)
    db.flush()
    return revision
