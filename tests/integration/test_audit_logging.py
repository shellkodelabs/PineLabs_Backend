"""
Tests for automatic audit/revision logging on User write APIs (Part 13;
actor now sourced from authentication, Part 14):
  POST   /api/v1/users
  PUT    /api/v1/users/{userId}
  DELETE /api/v1/users/{userId}

Verifies that each successful write creates exactly one, correctly
shaped revision row; that the revision and the business write are
atomic in both directions (a business-write failure leaves no revision,
and a deliberately injected audit-write failure leaves no business
change either — see the FAILURE INJECTION section, which patches
app.services.audit_service.create_revision rather than weakening any
real constraint); and that revision.user_id always represents the ACTOR
performing the write, never the entity being acted on — critical for
DELETE, where the target user row is gone by the time anything could
reference it, so the revision instead references the (still-existing)
actor while entity_id (a plain soft reference, not a foreign key —
see app/models/revision.py) carries the deleted user's former id.

Part 14: the actor is now whoever is authenticated, via the `act_as`
fixture (tests/integration/conftest.py), which overrides
get_current_user to a specific real user — NOT a query parameter
(actorUserId has been removed entirely; see test_auth.py for tests
proving it no longer has any effect).

Uses its own synthetic "Test Audit " users/merchants throughout,
independent of tests/integration/test_users_api.py's fixtures — no
dependency on the real Part 6 seed, and no shared state with other
test files (each test's own transaction is isolated, per the
established db_session/api_client pattern).
"""
import pytest
from sqlalchemy import func, select

import app.services.audit_service as audit_service_module
from app.models import Merchant, Revision, SopSheet, User


def _make_user(db_session, *, name, email, mobile=None, role="SME", status="Active"):
    user = User(name=name, email=email, mobile=mobile, role=role, status=status)
    db_session.add(user)
    db_session.flush()
    return user


def _revisions_for_entity(db_session, entity_type, entity_id):
    stmt = select(Revision).where(Revision.entity_type == entity_type, Revision.entity_id == entity_id)
    return db_session.execute(stmt).scalars().all()


def _count_revisions(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Revision))


def _raise_simulated_audit_failure(*args, **kwargs):
    raise RuntimeError("Simulated audit failure for atomicity test")


@pytest.fixture()
def actor(db_session):
    return _make_user(db_session, name="Test Audit Actor", email="test.audit.actor@example.invalid", role="Admin", status="Active")


@pytest.fixture()
def merchant_alpha(db_session):
    """Sheets: block, poc."""
    merchant = Merchant(name="Test Audit Merchant Alpha", classification="Reward Card")
    db_session.add(merchant)
    db_session.flush()
    for key, name in [("block", "Block"), ("poc", "POC")]:
        db_session.add(SopSheet(merchant_id=merchant.id, key=key, name=name))
    db_session.flush()
    return merchant


# =====================================================================
# CREATE — items 1-8
# =====================================================================


def test_create_user_creates_exactly_one_revision(api_client, db_session, act_as, actor):
    act_as(actor)
    before = _count_revisions(db_session)
    resp = api_client.post(
        "/api/v1/users",
        json={"name": "Test Audit NewUser", "email": "test.audit.newuser@example.invalid", "role": "SME"},
    )
    assert resp.status_code == 201
    assert _count_revisions(db_session) == before + 1


def test_create_user_revision_has_correct_shape(api_client, db_session, act_as, actor):
    """Covers: action=create, entity=user, entity_id=created id,
    user_id=actor, target contains the created user's name, metadata
    contains role/status (items 2-7)."""
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={"name": "Test Audit ShapeCheck", "email": "test.audit.shapecheck@example.invalid", "role": "SME"},
    )
    new_id = resp.json()["id"]

    [revision] = _revisions_for_entity(db_session, "user", new_id)
    assert revision.action_type == "create"
    assert revision.entity_type == "user"
    assert revision.entity_id == new_id
    assert revision.user_id == actor.id
    assert "Test Audit ShapeCheck" in revision.target_label
    assert revision.metadata_ == {"role": "SME", "status": "Invited"}
    # no secrets/sensitive data — the model has no password field at
    # all, but explicitly confirm nothing password-like leaked into metadata
    assert "password" not in str(revision.metadata_).lower()


def test_create_and_revision_are_atomic(api_client, db_session, act_as, actor):
    """A failure partway through the transaction (bad access reference,
    reached AFTER the user row was already written inside the same
    savepoint) must roll back both the user AND leave no revision."""
    act_as(actor)
    before = _count_revisions(db_session)
    email = "test.audit.atomicfail@example.invalid"
    resp = api_client.post(
        "/api/v1/users",
        json={
            "name": "Test Audit AtomicFail",
            "email": email,
            "role": "SME",
            "access": {"Totally Fake Merchant For Audit Atomic Test": ["block"]},
        },
    )
    assert resp.status_code == 422
    assert _count_revisions(db_session) == before
    created = db_session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    assert created is None


# =====================================================================
# UPDATE — items 9-16
# =====================================================================


def test_update_creates_exactly_one_revision_when_something_changes(api_client, db_session, act_as, actor):
    act_as(actor)
    target = _make_user(db_session, name="Test Audit UpdateCount", email="test.audit.updatecount@example.invalid", status="Invited")
    before = _count_revisions(db_session)

    resp = api_client.put(f"/api/v1/users/{target.id}", json={"status": "Active"})
    assert resp.status_code == 200
    assert _count_revisions(db_session) == before + 1


def test_update_revision_has_correct_shape(api_client, db_session, act_as, actor):
    """Covers: action=update, entity=user, entity_id=updated id,
    user_id=actor (items 10-13)."""
    act_as(actor)
    target = _make_user(db_session, name="Test Audit UpdateShape", email="test.audit.updateshape@example.invalid", status="Invited")

    resp = api_client.put(f"/api/v1/users/{target.id}", json={"status": "Active"})
    assert resp.status_code == 200

    [revision] = _revisions_for_entity(db_session, "user", target.id)
    assert revision.action_type == "update"
    assert revision.entity_type == "user"
    assert revision.entity_id == target.id
    assert revision.user_id == actor.id


def test_update_change_description_accurately_describes_changed_fields(api_client, db_session, act_as, actor):
    act_as(actor)
    target = _make_user(
        db_session, name="Test Audit ChangeDesc", email="test.audit.changedesc@example.invalid",
        role="SME", status="Invited",
    )

    single = api_client.put(f"/api/v1/users/{target.id}", json={"status": "Active"})
    assert single.status_code == 200
    [revision1] = _revisions_for_entity(db_session, "user", target.id)
    assert revision1.change_description == "Status changed from Invited to Active"

    multi = api_client.put(
        f"/api/v1/users/{target.id}",
        json={"role": "Admin", "status": "Inactive"},
    )
    assert multi.status_code == 200
    revisions = _revisions_for_entity(db_session, "user", target.id)
    latest = max(revisions, key=lambda r: r.id)
    assert latest.change_description == "Role changed from SME to Admin; status changed from Active to Inactive"


def test_no_revision_when_nothing_actually_changes(api_client, db_session, act_as, actor):
    act_as(actor)
    target = _make_user(
        db_session, name="Test Audit NoChange", email="test.audit.nochange@example.invalid",
        role="SME", status="Active",
    )
    before = _count_revisions(db_session)

    # resending the SAME current values — not a real change.
    resp = api_client.put(f"/api/v1/users/{target.id}", json={"role": "SME", "status": "Active"})
    assert resp.status_code == 200
    assert _count_revisions(db_session) == before
    assert _revisions_for_entity(db_session, "user", target.id) == []


def test_update_and_revision_are_atomic(api_client, db_session, act_as, actor):
    act_as(actor)
    target = _make_user(
        db_session, name="Test Audit UpdateAtomic", email="test.audit.updateatomic@example.invalid",
        role="SME", status="Active",
    )
    before = _count_revisions(db_session)

    resp = api_client.put(
        f"/api/v1/users/{target.id}",
        json={"status": "Inactive", "access": {"Totally Fake Merchant For Update Atomic": ["block"]}},
    )
    assert resp.status_code == 422
    assert _count_revisions(db_session) == before

    db_session.refresh(target)
    assert target.status == "Active"  # rolled back, not left half-applied


# =====================================================================
# DELETE — items 17-21, and the explicit "IMPORTANT DELETE TEST" scenario
# =====================================================================


def test_delete_creates_audit_revision_performed_by_actor(api_client, db_session, act_as, actor):
    act_as(actor)
    target = _make_user(db_session, name="Test Audit DeleteShape", email="test.audit.deleteshape@example.invalid")

    resp = api_client.delete(f"/api/v1/users/{target.id}")
    assert resp.status_code == 200

    [revision] = _revisions_for_entity(db_session, "user", target.id)
    assert revision.action_type == "delete"
    assert revision.entity_type == "user"
    assert revision.entity_id == target.id  # item 18
    assert revision.user_id == actor.id  # item 19
    assert revision.target_label == "Test Audit DeleteShape"


def test_delete_revision_survives_deletion_of_target_user(api_client, db_session, act_as, actor):
    act_as(actor)
    target = _make_user(db_session, name="Test Audit DeleteSurvives", email="test.audit.deletesurvives@example.invalid")
    target_id = target.id

    resp = api_client.delete(f"/api/v1/users/{target_id}")
    assert resp.status_code == 200

    assert db_session.get(User, target_id) is None  # target truly gone

    [revision] = _revisions_for_entity(db_session, "user", target_id)
    assert revision is not None  # revision persists despite the target's removal
    # its user_id (the actor) still resolves to a real, existing row —
    # this is exactly why user_id must be the actor and not the target.
    assert db_session.get(User, revision.user_id) is not None


def test_delete_and_revision_are_atomic(api_client, db_session, act_as, actor, monkeypatch):
    act_as(actor)
    target = _make_user(db_session, name="Test Audit DeleteAtomic", email="test.audit.deleteatomic@example.invalid")
    before = _count_revisions(db_session)

    monkeypatch.setattr(audit_service_module, "create_revision", _raise_simulated_audit_failure)

    # Starlette's TestClient (default raise_server_exceptions=True)
    # re-raises unhandled exceptions into the test for debuggability,
    # even though a real deployed server would return a clean 500 via
    # the registered @app.exception_handler(Exception) — see this
    # file's FAILURE INJECTION section header for the full explanation.
    with pytest.raises(RuntimeError):
        api_client.delete(f"/api/v1/users/{target.id}")

    assert db_session.get(User, target.id) is not None  # delete rolled back
    assert _count_revisions(db_session) == before


def test_actor_deletes_another_user_important_scenario(api_client, db_session, act_as, actor):
    """The explicit critical scenario from the task:
      Actor = User A, Target = User B, A deletes B.
    After: B no longer exists; a revision exists with
    user_id=A.id, entity_type='user', entity_id=B.id — because
    revisions.user_id (NOT NULL, FK to users) could never reference the
    just-deleted B."""
    user_a = actor
    user_b = _make_user(db_session, name="Test Audit UserB", email="test.audit.userb@example.invalid")
    assert user_a.id != user_b.id

    act_as(user_a)
    resp = api_client.delete(f"/api/v1/users/{user_b.id}")
    assert resp.status_code == 200

    assert db_session.get(User, user_b.id) is None

    [revision] = _revisions_for_entity(db_session, "user", user_b.id)
    assert revision.user_id == user_a.id
    assert revision.user_id != user_b.id
    assert revision.entity_type == "user"
    assert revision.entity_id == user_b.id


# =====================================================================
# FAILURE INJECTION — items 22-24
#
# Patches app.services.audit_service.create_revision to raise, rather
# than weakening any real constraint (per the task's explicit
# instruction). user_service.py accesses it as `audit_service.create_
# revision(...)` (module-level attribute lookup on every call), so
# monkeypatching the module attribute is picked up correctly.
#
# Each call is wrapped in `pytest.raises(RuntimeError)` rather than
# asserting on a response status code: Starlette's TestClient (default
# raise_server_exceptions=True) re-raises unhandled exceptions into the
# test for debuggability, even though a real deployed server would
# return a clean 500 via the registered @app.exception_handler(Exception)
# — this is a TestClient debugging behavior, not something specific to
# this app. What actually matters for these tests — that the exception
# happened AND nothing was left half-written — is verified via db_session
# afterward either way.
# =====================================================================


def test_revision_insertion_failure_rolls_back_user_creation(api_client, db_session, act_as, actor, monkeypatch):
    act_as(actor)
    monkeypatch.setattr(audit_service_module, "create_revision", _raise_simulated_audit_failure)

    email = "test.audit.injectcreate@example.invalid"
    with pytest.raises(RuntimeError):
        api_client.post(
            "/api/v1/users",
            json={"name": "Test Audit InjectCreate", "email": email, "role": "SME"},
        )

    created = db_session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    assert created is None


def test_revision_insertion_failure_rolls_back_user_update(api_client, db_session, act_as, actor, monkeypatch):
    target = _make_user(
        db_session, name="Test Audit InjectUpdate", email="test.audit.injectupdate@example.invalid", status="Active"
    )
    act_as(actor)
    monkeypatch.setattr(audit_service_module, "create_revision", _raise_simulated_audit_failure)

    with pytest.raises(RuntimeError):
        api_client.put(f"/api/v1/users/{target.id}", json={"status": "Inactive"})

    db_session.refresh(target)
    assert target.status == "Active"  # original value preserved, not half-applied


def test_revision_insertion_failure_rolls_back_user_deletion(api_client, db_session, act_as, actor, monkeypatch):
    target = _make_user(db_session, name="Test Audit InjectDelete", email="test.audit.injectdelete@example.invalid")
    act_as(actor)
    monkeypatch.setattr(audit_service_module, "create_revision", _raise_simulated_audit_failure)

    with pytest.raises(RuntimeError):
        api_client.delete(f"/api/v1/users/{target.id}")

    assert db_session.get(User, target.id) is not None  # still exists — delete did not go through


# =====================================================================
# GET /api/v1/revisions reflects the new audit entries
# =====================================================================


def test_revisions_api_reflects_newly_created_audit_entry(api_client, act_as, actor):
    act_as(actor)
    resp = api_client.post(
        "/api/v1/users",
        json={"name": "Test Audit ViaRevisionsApi", "email": "test.audit.viarevisionsapi@example.invalid", "role": "SME"},
    )
    assert resp.status_code == 201

    revisions_resp = api_client.get("/api/v1/revisions", params={"search": "Test Audit ViaRevisionsApi"})
    body = revisions_resp.json()
    assert body["total"] == 1

    item = body["items"][0]
    assert item["action"] == "create"
    assert item["entity"] == "user"
    assert item["target"] == "Test Audit ViaRevisionsApi"
    assert item["user"]["id"] == actor.id
    assert item["user"]["name"] == "Test Audit Actor"
