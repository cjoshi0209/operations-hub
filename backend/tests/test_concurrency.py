"""The three concurrency mechanisms: conditional claim, optimistic versioning, idempotency keys."""
import threading

import pytest
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.models import Event, IdempotencyKey, Outbox


def _race(world, n, fn):
    """Run fn(i, client) on n threads released simultaneously; return results in thread order."""
    barrier = threading.Barrier(n)
    clients = [world.client() for _ in range(n)]

    def run(i):
        barrier.wait()
        return fn(i, clients[i])

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


def _count(world, model, **where):
    with world.db.read_session() as s:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return s.execute(stmt).scalar_one()


# 1. Two concurrent claims on the same item: exactly one succeeds -----------------------------------------
def test_concurrent_claims_exactly_one_wins(world):
    item_id = world.make_item()
    claimants = ["alice", "bob"]

    results = _race(
        world, 2, lambda i, c: c.post(f"/work-items/{item_id}/claim", headers=world.h(claimants[i]))
    )

    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409]
    winner_idx = next(i for i, r in enumerate(results) if r.status_code == 200)
    loser = results[1 - winner_idx]
    assert loser.json()["error"]["code"] == "already_claimed"
    # 409 tells the loser who owns it now
    assert loser.json()["error"]["current_owner"]["id"] == world.users[claimants[winner_idx]]
    # the database agrees, and only the winner produced an event
    assert world.item(item_id).assignee_id == world.users[claimants[winner_idx]]
    assert _count(world, Event, work_item_id=item_id, type="claimed") == 1


def test_many_concurrent_claims_still_exactly_one(world):
    item_id = world.make_item()
    team = ["alice", "bob", "carol"]
    results = _race(
        world, 12, lambda i, c: c.post(f"/work-items/{item_id}/claim", headers=world.h(team[i % 3]))
    )
    assert sum(r.status_code == 200 for r in results) == 1
    assert sum(r.status_code == 409 for r in results) == 11
    assert _count(world, Event, work_item_id=item_id, type="claimed") == 1


def test_claiming_a_claimed_item_returns_409_with_owner(world):
    item_id = world.make_item(assignee="bob")
    r = world.client().post(f"/work-items/{item_id}/claim", headers=world.h("alice"))
    assert r.status_code == 409
    assert r.json()["error"]["current_owner"] == {"id": world.users["bob"], "name": "Bob"}


# 2. Stale edits ------------------------------------------------------------------------------------------
def test_patch_with_stale_version_returns_409_and_current_item(world):
    item_id = world.make_item()
    c = world.client()
    first = c.patch(f"/work-items/{item_id}", headers=world.h("alice"),
                    json={"expected_version": 1, "title": "Edited by Alice"})
    assert first.status_code == 200 and first.json()["version"] == 2

    stale = c.patch(f"/work-items/{item_id}", headers=world.h("bob"),
                    json={"expected_version": 1, "title": "Edited by Bob"})
    assert stale.status_code == 409
    err = stale.json()["error"]
    assert err["code"] == "version_conflict"
    assert err["expected_version"] == 1 and err["current_version"] == 2
    assert err["current"]["title"] == "Edited by Alice"  # client can reconcile from this
    assert world.item(item_id).title == "Edited by Alice"  # Bob's write did not land


def test_patch_requires_expected_version(world):
    item_id = world.make_item()
    r = world.client().patch(f"/work-items/{item_id}", headers=world.h("alice"), json={"title": "x"})
    assert r.status_code == 422


def test_concurrent_patches_same_version_only_one_applies(world):
    item_id = world.make_item()
    results = _race(
        world, 6,
        lambda i, c: c.patch(f"/work-items/{item_id}", headers=world.h("alice"),
                             json={"expected_version": 1, "title": f"title {i}"}),
    )
    assert sorted(r.status_code for r in results) == [200] + [409] * 5
    assert world.item(item_id).version == 2


def test_stale_status_change_is_rejected_too(world):
    item_id = world.make_item(assignee="bob")
    c = world.client()
    assert c.patch(f"/work-items/{item_id}", headers=world.h("bob"),
                   json={"expected_version": 1, "priority": "P1"}).status_code == 200
    r = c.post(f"/work-items/{item_id}/transitions", headers=world.h("bob"),
               json={"to_status": "in_progress", "expected_version": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"


# 3. Idempotency ------------------------------------------------------------------------------------------
def test_same_idempotency_key_twice_one_event_identical_response(world):
    item_id = world.make_item(assignee="bob")
    c = world.client()
    body = {"to_status": "in_progress", "expected_version": 1}

    first = c.post(f"/work-items/{item_id}/transitions", headers=world.h("bob", "key-123"), json=body)
    second = c.post(f"/work-items/{item_id}/transitions", headers=world.h("bob", "key-123"), json=body)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()  # byte-for-byte the stored response
    assert second.headers.get("Idempotent-Replay") == "true"
    assert "Idempotent-Replay" not in first.headers
    assert _count(world, Event, work_item_id=item_id, type="status_changed") == 1
    assert _count(world, Outbox) == 1
    assert world.item(item_id).version == 2  # applied once, not twice


def test_idempotent_replay_survives_a_later_state_change(world):
    """The replay returns what the original request returned, not the item's current state."""
    item_id = world.make_item()
    c = world.client()
    first = c.post(f"/work-items/{item_id}/claim", headers=world.h("alice", "k1"))
    c.patch(f"/work-items/{item_id}", headers=world.h("alice"), json={"expected_version": 2, "title": "Later"})
    replay = c.post(f"/work-items/{item_id}/claim", headers=world.h("alice", "k1"))
    assert replay.status_code == 200 and replay.json() == first.json()
    assert replay.json()["title"] != "Later"


def test_idempotency_key_reused_for_different_request_is_rejected(world):
    a = world.make_item()
    b = world.make_item(title="Another")
    c = world.client()
    assert c.post(f"/work-items/{a}/claim", headers=world.h("alice", "same")).status_code == 200
    r = c.post(f"/work-items/{b}/claim", headers=world.h("alice", "same"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "idempotency_key_reuse"
    assert world.item(b).assignee_id is None


def test_idempotency_keys_are_scoped_per_user(world):
    a = world.make_item()
    b = world.make_item(title="Another")
    c = world.client()
    assert c.post(f"/work-items/{a}/claim", headers=world.h("alice", "shared")).status_code == 200
    # Bob using the same literal key for a different request is a different (user, key) pair.
    assert c.post(f"/work-items/{b}/claim", headers=world.h("bob", "shared")).status_code == 200


def test_concurrent_requests_with_same_key_create_one_event(world):
    item_id = world.make_item()
    results = _race(
        world, 6,
        lambda i, c: c.post(f"/work-items/{item_id}/comments", headers=world.h("alice", "dup-comment"),
                            json={"body": "hello"}),
    )
    assert all(r.status_code == 201 for r in results)
    assert len({r.text for r in results}) == 1
    assert _count(world, Event, work_item_id=item_id, type="comment_added") == 1


def test_failed_request_is_not_cached_so_retry_with_same_key_works(world):
    item_id = world.make_item(assignee="bob")
    c = world.client()
    bad = c.post(f"/work-items/{item_id}/transitions", headers=world.h("bob", "retry-me"),
                 json={"to_status": "in_progress", "expected_version": 99})
    assert bad.status_code == 409
    good = c.post(f"/work-items/{item_id}/transitions", headers=world.h("bob", "retry-me"),
                  json={"to_status": "in_progress", "expected_version": 1})
    assert good.status_code == 200
    assert _count(world, IdempotencyKey) == 1


# The HTTP races above are serialised by SQLite's write lock, so on their own they could not tell a real
# conditional UPDATE from a naive read-then-write. These tests remove that crutch: the service is handed a
# STALE in-memory row (what a second transaction on Postgres would have read before the first one committed)
# and the SQL itself must still refuse the write.
def test_claim_is_guarded_by_the_sql_not_by_the_earlier_read(world, monkeypatch):
    from app import services
    from app.auth import Principal
    from app.errors import ApiError
    from app.models import User

    item_id = world.make_item()
    with world.db.read_session() as s:
        stale = s.get(type(world.item(item_id)), item_id)  # read while still unassigned
        assert stale.assignee_id is None
    assert world.client().post(f"/work-items/{item_id}/claim", headers=world.h("alice")).status_code == 200

    monkeypatch.setattr(services, "get_visible_item", lambda db, p, i: stale)
    with world.db.write_session() as s:
        bob = Principal(user=s.get(User, world.users["bob"]), roles={world.teams["Ops"]: "member"})
        with pytest.raises(ApiError) as exc:
            services.claim_item(s, bob, item_id)
    assert exc.value.status_code == 409 and exc.value.code == "already_claimed"
    assert exc.value.extra["current_owner"]["id"] == world.users["alice"]


def test_version_guard_is_in_the_sql_not_just_the_early_check(world, monkeypatch):
    from app import services
    from app.auth import Principal
    from app.errors import ApiError
    from app.models import User
    from app.schemas import ItemPatch

    item_id = world.make_item()
    with world.db.read_session() as s:
        stale = s.get(type(world.item(item_id)), item_id)  # version 1
    assert world.client().patch(f"/work-items/{item_id}", headers=world.h("alice"),
                                json={"expected_version": 1, "title": "winner"}).status_code == 200

    monkeypatch.setattr(services, "get_visible_item", lambda db, p, i: stale)  # in-memory version still 1
    with world.db.write_session() as s:
        bob = Principal(user=s.get(User, world.users["bob"]), roles={world.teams["Ops"]: "member"})
        with pytest.raises(ApiError) as exc:
            services.update_item(s, bob, item_id, ItemPatch(expected_version=1, title="loser"))
    assert exc.value.code == "version_conflict" and exc.value.extra["current"]["title"] == "winner"
    assert world.item(item_id).title == "winner"
