"""Transactional outbox: atomic with the event, retried on failure, never delivered twice."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import services
from app.auth import Principal
from app.models import Event, Notification, Outbox, User, utcnow
from app.outbox import deliver_notifications, process_batch
from app.schemas import TransitionIn


def _rows(world, model):
    with world.db.read_session() as s:
        return s.execute(select(model).order_by(model.id)).scalars().all()


def _mutate(world):
    """alice (lead) moves bob's item -> bob (assignee) and alice's creator-side recipients get notified."""
    item_id = world.make_item(assignee="bob", creator="carol")
    r = world.client().post(f"/work-items/{item_id}/transitions", headers=world.h("alice"),
                            json={"to_status": "in_progress", "expected_version": 1})
    assert r.status_code == 200
    return item_id


def test_event_and_outbox_row_are_written_together(world):
    item_id = _mutate(world)
    events, outbox = _rows(world, Event), _rows(world, Outbox)
    assert len(events) == len(outbox) == 1
    assert outbox[0].event_id == events[0].id
    assert outbox[0].status == "pending" and outbox[0].attempts == 0
    # recipients are bob (assignee) and carol (creator); the actor (alice) is excluded
    assert outbox[0].payload["recipients"] == sorted([world.users["bob"], world.users["carol"]])
    assert _rows(world, Notification) == []  # nothing is sent inline


def test_failed_transaction_leaves_neither_event_nor_outbox(world):
    """Rule violation mid-request: the whole transaction rolls back, so no orphan outbox row exists."""
    item_id = world.make_item(status="open")
    r = world.client().post(f"/work-items/{item_id}/transitions", headers=world.h("alice"),
                            json={"to_status": "resolved", "expected_version": 1})
    assert r.status_code == 409
    assert _rows(world, Event) == [] and _rows(world, Outbox) == []


def test_event_and_outbox_roll_back_together_even_after_both_were_flushed(world):
    item_id = world.make_item(status="open", assignee="bob")
    with world.db.write_session() as s:
        p = Principal(user=s.get(User, world.users["alice"]), roles={world.teams["Ops"]: "lead"})
        services.transition_item(s, p, item_id, TransitionIn(to_status="in_progress", expected_version=1))
        # Event + Outbox are flushed inside this open transaction...
        assert s.execute(select(func.count()).select_from(Outbox)).scalar_one() == 1
        s.rollback()  # ...and vanish together if the transaction does not commit.
    assert _rows(world, Event) == [] and _rows(world, Outbox) == []
    assert world.item(item_id).status == "open"


# 6. A failing notification retries and is not double-delivered -------------------------------------------
def test_failing_notification_is_retried_then_delivered_exactly_once(world):
    _mutate(world)
    calls = {"n": 0}

    def flaky(db, row):
        calls["n"] += 1
        deliver_notifications(db, row)  # writes the notifications...
        if calls["n"] < 3:
            raise RuntimeError("smtp down")  # ...then blows up: the whole attempt must roll back

    t0 = utcnow()
    # attempt 1 fails
    res = process_batch(world.db, deliver=flaky, now=t0)
    assert (res.delivered, res.retried) == (0, 1)
    row = _rows(world, Outbox)[0]
    assert row.status == "pending" and row.attempts == 1 and "smtp down" in row.last_error
    assert _rows(world, Notification) == []  # partial delivery was rolled back, not left behind

    # not retried before its backoff elapses
    res = process_batch(world.db, deliver=flaky, now=t0 + timedelta(milliseconds=500))
    assert (res.delivered, res.retried) == (0, 0) and calls["n"] == 1

    # attempt 2 fails, attempt 3 succeeds
    assert process_batch(world.db, deliver=flaky, now=t0 + timedelta(minutes=1)).retried == 1
    assert process_batch(world.db, deliver=flaky, now=t0 + timedelta(minutes=5)).delivered == 1

    row = _rows(world, Outbox)[0]
    assert row.status == "done" and row.attempts == 3 and row.processed_at is not None
    assert len(_rows(world, Notification)) == 2  # one per recipient

    # polling again later (or from another worker) never re-delivers a done row
    for minutes in (10, 20):
        res = process_batch(world.db, deliver=flaky, now=t0 + timedelta(minutes=minutes))
        assert (res.delivered, res.retried, res.failed) == (0, 0, 0)
    assert calls["n"] == 3
    assert len(_rows(world, Notification)) == 2


def test_row_is_marked_failed_after_max_attempts(world):
    _mutate(world)

    def always_fails(db, row):
        raise RuntimeError("permanent")

    t = utcnow()
    results = []
    for i in range(4):
        results.append(process_batch(world.db, deliver=always_fails, now=t + timedelta(hours=i + 1), max_attempts=3))
    assert [r.retried for r in results[:2]] == [1, 1]
    assert results[2].failed == 1
    assert results[3].retried == results[3].failed == 0  # dead rows are left alone
    row = _rows(world, Outbox)[0]
    assert row.status == "failed" and row.attempts == 3
    assert _rows(world, Notification) == []


def test_delivery_itself_is_idempotent(world):
    """Simulates a worker that crashed after delivering but before marking the row done."""
    _mutate(world)
    row_id = _rows(world, Outbox)[0].id
    for _ in range(3):
        with world.db.write_session() as s:
            deliver_notifications(s, s.get(Outbox, row_id))
            s.commit()
    assert len(_rows(world, Notification)) == 2


def test_crashed_worker_row_is_reclaimed_after_the_lease_expires_without_duplicates(world):
    _mutate(world)
    row_id = _rows(world, Outbox)[0].id
    t0 = utcnow()
    # worker A delivers and commits notifications, then "crashes" leaving the row in 'processing'
    with world.db.write_session() as s:
        row = s.get(Outbox, row_id)
        row.status, row.locked_at, row.attempts = "processing", t0, 1
        deliver_notifications(s, row)
        s.commit()
    # inside the lease nobody touches it
    assert process_batch(world.db, now=t0 + timedelta(seconds=10), lease_seconds=60).delivered == 0
    # after the lease, worker B reclaims it and finishes; the unique constraint prevents a second notification
    assert process_batch(world.db, now=t0 + timedelta(seconds=120), lease_seconds=60).delivered == 1
    assert _rows(world, Outbox)[0].status == "done"
    assert len(_rows(world, Notification)) == 2


def test_two_workers_never_both_claim_the_same_row(world):
    for _ in range(5):
        _mutate(world)
    seen = []

    def spy(db, row):
        seen.append(row.id)
        deliver_notifications(db, row)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: process_batch(world.db, deliver=spy), range(4)))
    assert sorted(seen) == sorted(set(seen)) and len(seen) == 5  # each row delivered by exactly one worker
    assert len(_rows(world, Notification)) == 10


def test_notifications_are_visible_through_the_api(world):
    _mutate(world)
    process_batch(world.db)
    mine = world.client().get("/notifications", headers=world.h("bob")).json()
    assert len(mine["items"]) == 1 and "moved" in mine["items"][0]["message"]
    assert world.client().get("/notifications", headers=world.h("alice")).json()["items"] == []  # actor not notified


def test_events_are_append_only(world):
    _mutate(world)
    from sqlalchemy import delete, update

    with world.db.write_session() as s:
        with pytest.raises(Exception):
            s.execute(update(Event).values(type="tampered"))
            s.commit()
    with world.db.write_session() as s:
        with pytest.raises(Exception):
            s.execute(delete(Event))
            s.commit()
    assert _rows(world, Event)[0].type == "status_changed"
