from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    DDL,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .workflow import APPROVAL_STATUSES, PRIORITIES, STATUSES, TYPES


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, returns timezone-aware UTC. Behaves the same on SQLite and Postgres."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Optional[datetime], dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(default=True)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class TeamMembership(Base):
    __tablename__ = "team_memberships"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    role: Mapped[str] = mapped_column(String(10), default="member")

    user: Mapped[User] = relationship()
    team: Mapped[Team] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "team_id", name="uq_membership"),
        CheckConstraint(_in("role", ("member", "lead")), name="ck_membership_role"),
        Index("ix_membership_team", "team_id"),
    )


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class WorkItem(Base):
    __tablename__ = "work_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(20))
    priority: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(20), default="open")
    owning_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    team: Mapped[Team] = relationship(lazy="joined", innerjoin=True)
    assignee: Mapped[Optional[User]] = relationship(foreign_keys=[assignee_id], lazy="joined")
    creator: Mapped[User] = relationship(foreign_keys=[created_by], lazy="joined", innerjoin=True)

    __table_args__ = (
        CheckConstraint(_in("type", TYPES), name="ck_item_type"),
        CheckConstraint(_in("priority", PRIORITIES), name="ck_item_priority"),
        CheckConstraint(_in("status", STATUSES), name="ck_item_status"),
        Index("ix_items_team_status", "owning_team_id", "status"),
        Index("ix_items_assignee_status", "assignee_id", "status"),
        Index("ix_items_status", "status"),
        Index("ix_items_priority", "priority"),
        Index("ix_items_created_at", "created_at"),
        Index("ix_items_updated_at", "updated_at"),
        Index("ix_items_due_at", "due_at"),
    )


class Event(Base):
    """Append-only history. Never updated, never deleted (enforced in ORM and, on SQLite, by triggers)."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    work_item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    actor: Mapped[User] = relationship(lazy="joined", innerjoin=True)

    __table_args__ = (Index("ix_events_item", "work_item_id", "id"),)


@event.listens_for(Event, "before_update")
def _events_are_immutable(*_args):
    raise RuntimeError("events are append-only")


@event.listens_for(Event, "before_delete")
def _events_cannot_be_deleted(*_args):
    raise RuntimeError("events are append-only")


event.listen(
    Event.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
        "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    Event.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER events_no_delete BEFORE DELETE ON events "
        "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
    ).execute_if(dialect="sqlite"),
)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    work_item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    author: Mapped[User] = relationship(lazy="joined", innerjoin=True)

    __table_args__ = (Index("ix_comments_item", "work_item_id", "id"),)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    work_item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id"))
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    requester: Mapped[User] = relationship(foreign_keys=[requested_by], lazy="joined", innerjoin=True)
    approver: Mapped[User] = relationship(foreign_keys=[approver_id], lazy="joined", innerjoin=True)

    __table_args__ = (
        CheckConstraint(_in("status", APPROVAL_STATUSES), name="ck_approval_status"),
        Index("ix_approvals_item", "work_item_id", "id"),
        Index("ix_approvals_approver", "approver_id", "status"),
        # At most one pending approval per item.
        Index(
            "uq_approvals_one_pending",
            "work_item_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    locked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        CheckConstraint(_in("status", ("pending", "processing", "done", "failed")), name="ck_outbox_status"),
        Index("ix_outbox_poll", "status", "next_attempt_at"),
    )


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    outbox_id: Mapped[int] = mapped_column(ForeignKey("outbox.id"))
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    work_item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id"))
    message: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        # The delivery guarantee: one notification per (outbox row, recipient), whatever the worker does.
        UniqueConstraint("outbox_id", "user_id", name="uq_notification_once"),
        Index("ix_notifications_user", "user_id", "id"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(300))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
        Index("ix_idempotency_created", "created_at"),
    )
