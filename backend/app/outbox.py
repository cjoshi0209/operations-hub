"""Outbox processing: at-least-once *attempts*, exactly-once *effect*.

Row life cycle:  pending --claim--> processing --deliver ok--> done
                                        |  deliver fails
                                        +--> pending (attempts < max, next_attempt_at = now + backoff)
                                        +--> failed  (attempts >= max)

* Claiming is a conditional UPDATE, so two workers can never both own a row.
* Delivery (inserting notifications) and the ``done`` flip commit in ONE transaction. A failure rolls both back.
* If a worker dies mid-flight the row stays ``processing`` until its lease expires, then it is claimed again.
* ``unique(outbox_id, user_id)`` on notifications makes a repeated delivery a no-op, so even a crash between
  "notifications committed" and "row marked done" cannot notify anyone twice.
"""
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import Database
from .models import IdempotencyKey, Notification, Outbox, utcnow

log = logging.getLogger("outbox")

Deliver = Callable[[Session, Outbox], None]


def backoff_seconds(attempts: int) -> int:
    return min(2 ** attempts, 300)


def deliver_notifications(db: Session, row: Outbox) -> None:
    """Default "send": write one notification per recipient. Safe to run any number of times for the same row."""
    payload = row.payload
    for user_id in payload.get("recipients", []):
        try:
            with db.begin_nested():  # savepoint: a duplicate must not poison the surrounding transaction
                db.add(
                    Notification(
                        user_id=user_id,
                        outbox_id=row.id,
                        event_id=row.event_id,
                        work_item_id=payload["work_item_id"],
                        message=payload.get("message", ""),
                    )
                )
        except IntegrityError:
            continue  # already delivered to this user for this outbox row


@dataclass
class BatchResult:
    delivered: int = 0
    retried: int = 0
    failed: int = 0
    skipped: int = 0


def process_batch(
    db: Database,
    *,
    deliver: Deliver = deliver_notifications,
    now: Optional[datetime] = None,
    batch_size: int = 50,
    max_attempts: int = 5,
    lease_seconds: int = 60,
) -> BatchResult:
    now = now or utcnow()
    result = BatchResult()
    lease_cutoff = now - timedelta(seconds=lease_seconds)

    with db.read_session() as rs:
        candidates = (
            rs.execute(
                select(Outbox.id)
                .where(
                    or_(
                        and_(Outbox.status == "pending", Outbox.next_attempt_at <= now),
                        and_(Outbox.status == "processing", Outbox.locked_at <= lease_cutoff),
                    )
                )
                .order_by(Outbox.id)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )

    for outbox_id in candidates:
        # 1) claim (own short transaction)
        with db.write_session() as ws:
            claimed = ws.execute(
                update(Outbox)
                .where(
                    Outbox.id == outbox_id,
                    or_(
                        and_(Outbox.status == "pending", Outbox.next_attempt_at <= now),
                        and_(Outbox.status == "processing", Outbox.locked_at <= lease_cutoff),
                    ),
                )
                .values(status="processing", locked_at=now, attempts=Outbox.attempts + 1)
                .execution_options(synchronize_session=False)
            ).rowcount
            ws.commit()
        if claimed != 1:
            result.skipped += 1  # another worker got it
            continue

        # 2) deliver + mark done atomically
        try:
            with db.write_session() as ws:
                row = ws.get(Outbox, outbox_id)
                deliver(ws, row)
                ws.execute(
                    update(Outbox)
                    .where(Outbox.id == outbox_id, Outbox.status == "processing")
                    .values(status="done", processed_at=utcnow(), last_error=None)
                    .execution_options(synchronize_session=False)
                )
                ws.commit()
            result.delivered += 1
        except Exception as exc:  # noqa: BLE001 - any delivery failure is retried
            log.warning("outbox %s failed: %s", outbox_id, exc)
            with db.write_session() as ws:
                row = ws.get(Outbox, outbox_id)
                give_up = row.attempts >= max_attempts
                ws.execute(
                    update(Outbox)
                    .where(Outbox.id == outbox_id)
                    .values(
                        status="failed" if give_up else "pending",
                        last_error=str(exc)[:500],
                        next_attempt_at=now + timedelta(seconds=backoff_seconds(row.attempts)),
                        locked_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                ws.commit()
            if give_up:
                result.failed += 1
            else:
                result.retried += 1
    return result


def purge_expired_idempotency_keys(db: Database, ttl_hours: int = 24) -> int:
    with db.write_session() as ws:
        n = ws.execute(
            delete(IdempotencyKey).where(IdempotencyKey.created_at <= utcnow() - timedelta(hours=ttl_hours))
        ).rowcount
        ws.commit()
    return n


class OutboxWorker:
    """Polling loop on a daemon thread (also runnable standalone: ``python -m app.worker``)."""

    def __init__(self, db: Database, poll_seconds: float = 1.0, **kwargs):
        self.db = db
        self.poll_seconds = poll_seconds
        self.kwargs = kwargs
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def run_forever(self) -> None:
        ticks = 0
        while not self._stop.is_set():
            try:
                res = process_batch(self.db, **self.kwargs)
                ticks += 1
                if ticks % 300 == 0:
                    purge_expired_idempotency_keys(self.db)
                if res.delivered or res.retried or res.failed:
                    continue  # more work may be waiting; poll again immediately
            except Exception:  # noqa: BLE001 - the loop must survive transient DB errors
                log.exception("outbox loop error")
            self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="outbox-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
