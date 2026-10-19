from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Principal
from .models import Approval, Comment, Event, User, WorkItem
from .permissions import allowed_actions
from .schemas import ApprovalOut, CommentOut, EventOut, ItemOut, UserRef
from .workflow import TERMINAL


def user_ref(u: User | None) -> UserRef | None:
    return UserRef(id=u.id, name=u.name) if u else None


def latest_approvals(db: Session, item_ids: Iterable[int]) -> dict[int, Approval]:
    """Latest approval per item (a pending one always wins; there is at most one)."""
    ids = list(item_ids)
    if not ids:
        return {}
    rows = (
        db.execute(
            select(Approval)
            .where(Approval.work_item_id.in_(ids))
            .order_by(Approval.id)
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    out: dict[int, Approval] = {}
    for a in rows:  # ascending id: later rows overwrite earlier ones
        out[a.work_item_id] = a
    return out


def approval_out(a: Approval | None) -> ApprovalOut | None:
    if a is None:
        return None
    return ApprovalOut(
        id=a.id,
        status=a.status,
        approver=user_ref(a.approver),
        requested_by=user_ref(a.requester),
        created_at=a.created_at,
        decided_at=a.decided_at,
        decision_note=a.decision_note,
    )


def attention_reasons(p: Principal, item: WorkItem, approval: Approval | None) -> list[str]:
    if item.status in TERMINAL:
        return []
    reasons = []
    if item.assignee_id == p.id:
        reasons.append("assigned_to_me")
    if approval is not None and approval.status == "pending" and approval.approver_id == p.id:
        reasons.append("awaiting_my_approval")
    if item.priority == "P1" and p.is_member(item.owning_team_id):
        reasons.append("p1_in_my_team")
    return reasons


def item_out(p: Principal, item: WorkItem, approval: Approval | None) -> ItemOut:
    return ItemOut(
        id=item.id,
        title=item.title,
        description=item.description or "",
        type=item.type,
        priority=item.priority,
        status=item.status,
        owning_team={"id": item.team.id, "name": item.team.name},
        assignee=user_ref(item.assignee),
        created_by=user_ref(item.creator),
        created_at=item.created_at,
        updated_at=item.updated_at,
        due_at=item.due_at,
        version=item.version,
        resolution_note=item.resolution_note,
        approval=approval_out(approval),
        allowed_actions=allowed_actions(p, item, approval),
        attention_reasons=attention_reasons(p, item, approval),
    )


def items_out(db: Session, p: Principal, items: list[WorkItem]) -> list[ItemOut]:
    approvals = latest_approvals(db, [i.id for i in items])
    return [item_out(p, i, approvals.get(i.id)) for i in items]


def single_item_out(db: Session, p: Principal, item: WorkItem) -> ItemOut:
    return items_out(db, p, [item])[0]


def event_out(e: Event) -> EventOut:
    return EventOut(id=e.id, type=e.type, actor=user_ref(e.actor), payload=e.payload or {}, created_at=e.created_at)


def comment_out(c: Comment) -> CommentOut:
    return CommentOut(id=c.id, author=user_ref(c.author), body=c.body, created_at=c.created_at)
