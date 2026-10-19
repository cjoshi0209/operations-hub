"""Resource-level authorization. Everything here is evaluated server-side; the UI only renders the result.

Visibility (anything else is a 404, never a 403, so existence is not leaked):
  * items owned by one of the caller's teams
  * items assigned to the caller
  * items where the caller is the designated approver (otherwise they could not decide the approval)

Edit            : member of the owning team
Claim           : member of the owning team, item unassigned
Change status   : the assignee or a lead of the owning team
Decide approval : the designated approver of the pending approval
Comment         : anyone who can see the item
"""
from sqlalchemy import exists, or_

from .auth import Principal
from .models import Approval, WorkItem
from .workflow import TRANSITIONS, approval_blocks_resolution


def visible_clause(p: Principal):
    pending_for_me = exists().where(Approval.work_item_id == WorkItem.id, Approval.approver_id == p.id)
    return or_(WorkItem.owning_team_id.in_(p.team_ids or [-1]), WorkItem.assignee_id == p.id, pending_for_me)


def can_edit(p: Principal, item: WorkItem) -> bool:
    return p.is_member(item.owning_team_id) and item.status != "closed"


def can_claim(p: Principal, item: WorkItem) -> bool:
    return p.is_member(item.owning_team_id) and item.assignee_id is None and item.status != "closed"


def can_change_status(p: Principal, item: WorkItem) -> bool:
    return item.assignee_id == p.id or p.is_lead(item.owning_team_id)


def can_decide_approval(p: Principal, approval: Approval | None) -> bool:
    return approval is not None and approval.status == "pending" and approval.approver_id == p.id


def allowed_actions(p: Principal, item: WorkItem, approval: Approval | None) -> list[str]:
    actions = ["comment"]
    if can_claim(p, item):
        actions.append("claim")
    if can_edit(p, item):
        actions.append("edit")
    if can_change_status(p, item):
        for target in TRANSITIONS.get(item.status, ()):
            if target == "resolved" and approval_blocks_resolution(
                item.type, approval.status if approval else None
            ):
                continue
            actions.append(f"transition:{target}")
    if can_decide_approval(p, approval):
        actions += ["approve", "reject"]
    if (
        item.type == "approval"
        and p.is_member(item.owning_team_id)
        and item.status != "closed"
        and (approval is None or approval.status == "rejected")
    ):
        actions.append("request_approval")
    return actions
