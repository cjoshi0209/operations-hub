"""Business logic. Rules live here (not in routers, not in the UI).

Every mutation follows the same shape:
    load (visibility-filtered)  ->  authorize  ->  check rules against the row at version V
    ->  guarded UPDATE ... WHERE version = V  ->  Event + Outbox in the same transaction.
The caller commits. Raising ApiError aborts the whole transaction, so there are no partial writes.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .auth import Principal
from .errors import ApiError, not_found
from .models import Approval, Comment, Event, Outbox, TeamMembership, User, WorkItem, utcnow
from .permissions import can_change_status, can_decide_approval, can_edit, visible_clause
from .schemas import ItemCreate, ItemOut, ItemPatch, TransitionIn
from .serializers import latest_approvals, single_item_out, user_ref
from .workflow import check_transition


# ---------- helpers ----------
def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def reload_item(db: Session, item_id: int) -> WorkItem:
    return db.execute(
        select(WorkItem).where(WorkItem.id == item_id).execution_options(populate_existing=True)
    ).scalar_one()


def get_visible_item(db: Session, p: Principal, item_id: int) -> WorkItem:
    """404 (not 403) for anything the caller may not see, so existence is never leaked."""
    item = db.execute(select(WorkItem).where(WorkItem.id == item_id, visible_clause(p))).scalar_one_or_none()
    if item is None:
        raise not_found()
    return item


def _forbidden(message: str) -> ApiError:
    return ApiError(403, "forbidden", message)


def _current_approval(db: Session, item_id: int) -> Approval | None:
    return latest_approvals(db, [item_id]).get(item_id)


def _is_team_member(db: Session, user_id: int, team_id: int) -> bool:
    return (
        db.execute(
            select(TeamMembership.id).where(TeamMembership.user_id == user_id, TeamMembership.team_id == team_id)
        ).first()
        is not None
    )


def record_event(
    db: Session,
    item: WorkItem,
    actor: User,
    type_: str,
    payload: dict,
    recipients: Iterable[int | None],
    message: str,
) -> Event:
    """Event + Outbox in the caller's transaction: either both exist or neither does."""
    now = utcnow()
    ev = Event(work_item_id=item.id, actor_id=actor.id, type=type_, payload=payload, created_at=now)
    db.add(ev)
    db.flush()
    targets = sorted({r for r in recipients if r is not None and r != actor.id})
    db.add(
        Outbox(
            event_id=ev.id,
            payload={
                "event_type": type_,
                "work_item_id": item.id,
                "title": item.title,
                "actor_id": actor.id,
                "recipients": targets,
                "message": message,
            },
            status="pending",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
        )
    )
    db.flush()
    return ev


def _conflict(db: Session, p: Principal, item_id: int, expected: int) -> ApiError:
    current = single_item_out(db, p, reload_item(db, item_id))
    return ApiError(
        409,
        "version_conflict",
        "This item was changed by someone else. Review the latest version and retry.",
        expected_version=expected,
        current_version=current.version,
        current=current.model_dump(mode="json"),
    )


def _check_version(db: Session, p: Principal, item: WorkItem, expected: int) -> None:
    if item.version != expected:
        raise _conflict(db, p, item.id, expected)


def guarded_update(db: Session, p: Principal, item: WorkItem, expected: int, values: dict) -> WorkItem:
    """The only way item fields change after creation: UPDATE ... WHERE version = :expected, version += 1."""
    res = db.execute(
        update(WorkItem)
        .where(WorkItem.id == item.id, WorkItem.version == expected)
        .values(**values, version=WorkItem.version + 1, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        raise _conflict(db, p, item.id, expected)
    return reload_item(db, item.id)


# ---------- create ----------
def create_item(db: Session, p: Principal, data: ItemCreate) -> ItemOut:
    if not p.is_member(data.owning_team_id):
        raise _forbidden("You can only create items for teams you belong to.")
    if data.type == "approval" and data.approver_id is None:
        raise ApiError(422, "approver_required", "Approval items need an approver_id.")
    if data.assignee_id is not None and not _is_team_member(db, data.assignee_id, data.owning_team_id):
        raise ApiError(422, "invalid_assignee", "The assignee must belong to the owning team.")
    approver = None
    if data.approver_id is not None:
        approver = db.get(User, data.approver_id)
        if approver is None or not approver.is_active:
            raise ApiError(422, "invalid_approver", "Unknown approver.")
        if approver.id == p.id:
            raise ApiError(422, "invalid_approver", "You cannot approve your own request.")

    now = utcnow()
    item = WorkItem(
        title=data.title,
        description=data.description,
        type=data.type,
        priority=data.priority,
        status="open",
        owning_team_id=data.owning_team_id,
        assignee_id=data.assignee_id,
        created_by=p.id,
        created_at=now,
        updated_at=now,
        due_at=data.due_at,
        version=1,
    )
    db.add(item)
    db.flush()
    record_event(
        db,
        item,
        p.user,
        "created",
        {"type": data.type, "priority": data.priority},
        [data.assignee_id],
        f"{p.user.name} created #{item.id} {item.title}",
    )
    if data.type == "approval" and approver is not None:
        db.add(Approval(work_item_id=item.id, requested_by=p.id, approver_id=approver.id, status="pending"))
        db.flush()
        record_event(
            db,
            item,
            p.user,
            "approval_requested",
            {"approver_id": approver.id},
            [approver.id],
            f"{p.user.name} asked you to approve #{item.id} {item.title}",
        )
    return single_item_out(db, p, reload_item(db, item.id))


# ---------- claim ----------
def claim_item(db: Session, p: Principal, item_id: int) -> ItemOut:
    item = get_visible_item(db, p, item_id)
    if not p.is_member(item.owning_team_id):
        raise _forbidden("Only members of the owning team can take ownership.")
    # The atomic gate: only one concurrent claimant can match `assignee_id IS NULL`.
    res = db.execute(
        update(WorkItem)
        .where(WorkItem.id == item.id, WorkItem.assignee_id.is_(None), WorkItem.status != "closed")
        .values(assignee_id=p.id, version=WorkItem.version + 1, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        current = reload_item(db, item.id)
        if current.assignee_id is not None:
            raise ApiError(
                409,
                "already_claimed",
                f"{current.assignee.name} already owns this item.",
                current_owner=user_ref(current.assignee).model_dump(),
                current_version=current.version,
            )
        raise ApiError(409, "item_closed", "Closed items cannot be claimed.")
    item = reload_item(db, item.id)
    record_event(
        db,
        item,
        p.user,
        "claimed",
        {"assignee_id": p.id},
        [item.created_by],
        f"{p.user.name} took ownership of #{item.id} {item.title}",
    )
    return single_item_out(db, p, item)


# ---------- edit ----------
_EDITABLE = ("title", "description", "priority", "due_at", "assignee_id")


def update_item(db: Session, p: Principal, item_id: int, data: ItemPatch) -> ItemOut:
    item = get_visible_item(db, p, item_id)
    if not p.is_member(item.owning_team_id):
        raise _forbidden("Only members of the owning team can edit this item.")
    _check_version(db, p, item, data.expected_version)
    if not can_edit(p, item):
        raise ApiError(409, "item_closed", "Closed items cannot be edited.")

    provided = data.model_fields_set - {"expected_version"}
    changes: dict[str, dict] = {}
    values: dict[str, Any] = {}
    for field in _EDITABLE:
        if field not in provided:
            continue
        new = getattr(data, field)
        if field in ("title", "priority") and new is None:
            raise ApiError(422, "invalid_field", f"{field} cannot be null.")
        if field == "title":
            new = new.strip()
            if not new:
                raise ApiError(422, "invalid_field", "title must not be blank.")
        if field == "description" and new is None:
            new = ""
        old = getattr(item, field)
        if new == old:
            continue
        if field == "assignee_id" and new is not None and not _is_team_member(db, new, item.owning_team_id):
            raise ApiError(422, "invalid_assignee", "The assignee must belong to the owning team.")
        changes[field] = {"from": _jsonable(old), "to": _jsonable(new)}
        values[field] = new
    if not values:
        return single_item_out(db, p, item)

    old_assignee = item.assignee_id
    item = guarded_update(db, p, item, data.expected_version, values)
    record_event(
        db,
        item,
        p.user,
        "updated",
        {"changes": changes},
        [item.assignee_id, old_assignee, item.created_by],
        f"{p.user.name} updated #{item.id} {item.title}",
    )
    return single_item_out(db, p, item)


# ---------- status ----------
def transition_item(db: Session, p: Principal, item_id: int, data: TransitionIn) -> ItemOut:
    item = get_visible_item(db, p, item_id)
    if not can_change_status(p, item):
        raise _forbidden("Only the assignee or a team lead can change the status.")
    _check_version(db, p, item, data.expected_version)
    approval = _current_approval(db, item.id)
    note = (data.resolution_note or "").strip() or None
    check_transition(
        current=item.status,
        target=data.to_status,
        item_type=item.type,
        approval_status=approval.status if approval else None,
        resolution_note=note,
    )
    previous = item.status
    values: dict[str, Any] = {"status": data.to_status}
    if data.to_status == "closed":
        values["resolution_note"] = note
    item = guarded_update(db, p, item, data.expected_version, values)
    payload = {"from": previous, "to": data.to_status}
    if data.to_status == "closed":
        payload["resolution_note"] = note
    record_event(
        db,
        item,
        p.user,
        "status_changed",
        payload,
        [
            item.assignee_id,
            item.created_by,
            approval.approver_id if approval and approval.status == "pending" else None,
        ],
        f"{p.user.name} moved #{item.id} {item.title} to {data.to_status}",
    )
    return single_item_out(db, p, item)


# ---------- comments ----------
def add_comment(db: Session, p: Principal, item_id: int, body: str) -> Comment:
    item = get_visible_item(db, p, item_id)
    comment = Comment(work_item_id=item.id, author_id=p.id, body=body, created_at=utcnow())
    db.add(comment)
    db.flush()
    approval = _current_approval(db, item.id)
    record_event(
        db,
        item,
        p.user,
        "comment_added",
        {"comment_id": comment.id, "excerpt": body[:140]},
        [
            item.assignee_id,
            item.created_by,
            approval.approver_id if approval and approval.status == "pending" else None,
        ],
        f"{p.user.name} commented on #{item.id} {item.title}",
    )
    db.refresh(comment)
    return comment


# ---------- approvals ----------
def request_approval(db: Session, p: Principal, item_id: int, approver_id: int) -> ItemOut:
    item = get_visible_item(db, p, item_id)
    if not p.is_member(item.owning_team_id):
        raise _forbidden("Only members of the owning team can request approval.")
    if item.type != "approval":
        raise ApiError(409, "not_an_approval_item", "Only approval-type items have approvals.")
    if item.status == "closed":
        raise ApiError(409, "item_closed", "Closed items cannot be changed.")
    current = _current_approval(db, item.id)
    if current is not None and current.status != "rejected":
        raise ApiError(409, "approval_exists", f"An approval is already {current.status}.")
    approver = db.get(User, approver_id)
    if approver is None or not approver.is_active or approver.id == p.id:
        raise ApiError(422, "invalid_approver", "Pick a different, active approver.")
    db.add(Approval(work_item_id=item.id, requested_by=p.id, approver_id=approver.id, status="pending"))
    db.flush()
    item = guarded_update(db, p, item, item.version, {})
    record_event(
        db,
        item,
        p.user,
        "approval_requested",
        {"approver_id": approver.id},
        [approver.id],
        f"{p.user.name} asked you to approve #{item.id} {item.title}",
    )
    return single_item_out(db, p, item)


def decide_approval(db: Session, p: Principal, item_id: int, decision: str, note: str | None) -> ItemOut:
    item = get_visible_item(db, p, item_id)
    approval = _current_approval(db, item.id)
    if approval is None:
        raise ApiError(409, "no_approval", "This item has no approval to decide.")
    if approval.approver_id != p.id:
        raise _forbidden("Only the designated approver can decide this approval.")
    if not can_decide_approval(p, approval):
        raise ApiError(409, "approval_already_decided", f"This approval was already {approval.status}.")
    res = db.execute(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status == "pending")
        .values(status=decision, decided_at=utcnow(), decision_note=(note or "").strip() or None)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        raise ApiError(409, "approval_already_decided", "This approval was just decided by someone else.")
    item = guarded_update(db, p, item, item.version, {})
    record_event(
        db,
        item,
        p.user,
        "approval_decided",
        {"decision": decision, "note": note},
        [approval.requested_by, item.assignee_id, item.created_by],
        f"{p.user.name} {decision} #{item.id} {item.title}",
    )
    return single_item_out(db, p, item)
