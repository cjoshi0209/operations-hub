from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ItemType = Literal["investigation", "action", "incident", "approval", "compliance"]
Priority = Literal["P1", "P2", "P3", "P4"]
Status = Literal["open", "in_progress", "blocked", "awaiting_approval", "resolved", "closed"]


# ---------- responses ----------
class UserRef(BaseModel):
    id: int
    name: str


class TeamRef(BaseModel):
    id: int
    name: str


class ApprovalOut(BaseModel):
    id: int
    status: Literal["pending", "approved", "rejected"]
    approver: UserRef
    requested_by: UserRef
    created_at: datetime
    decided_at: datetime | None = None
    decision_note: str | None = None


class ItemOut(BaseModel):
    id: int
    title: str
    description: str
    type: ItemType
    priority: Priority
    status: Status
    owning_team: TeamRef
    assignee: UserRef | None = None
    created_by: UserRef
    created_at: datetime
    updated_at: datetime
    due_at: datetime | None = None
    version: int
    resolution_note: str | None = None
    approval: ApprovalOut | None = None
    allowed_actions: list[str]
    attention_reasons: list[str] = []


class EventOut(BaseModel):
    id: int
    type: str
    actor: UserRef
    payload: dict[str, Any]
    created_at: datetime


class EventPage(BaseModel):
    items: list[EventOut]
    next_cursor: str | None = None


class ItemDetail(ItemOut):
    events: EventPage


class ItemPage(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None = None
    total: int


class CommentOut(BaseModel):
    id: int
    author: UserRef
    body: str
    created_at: datetime


class CommentPage(BaseModel):
    items: list[CommentOut]
    next_cursor: str | None = None


class NotificationOut(BaseModel):
    id: int
    work_item_id: int
    message: str
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None = None


class LoginOut(BaseModel):
    token: str
    expires_at: datetime
    user: UserRef


class MembershipOut(BaseModel):
    team: TeamRef
    role: Literal["member", "lead"]


class MeOut(BaseModel):
    user: UserRef
    email: str
    memberships: list[MembershipOut]


class TeamOut(BaseModel):
    id: int
    name: str
    members: list[UserRef]


# ---------- requests ----------
class LoginIn(BaseModel):
    email: str
    password: str


class ItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    type: ItemType
    priority: Priority
    owning_team_id: int
    assignee_id: int | None = None
    due_at: datetime | None = None
    approver_id: int | None = None  # required when type == "approval"

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v


class ItemPatch(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    priority: Priority | None = None
    due_at: datetime | None = None
    assignee_id: int | None = None  # explicit null unassigns; use model_fields_set to tell absent from null


class TransitionIn(BaseModel):
    to_status: Status
    expected_version: int = Field(ge=1)
    resolution_note: str | None = Field(default=None, max_length=10_000)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("comment must not be blank")
        return v


class ApprovalRequestIn(BaseModel):
    approver_id: int


class ApprovalDecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)
