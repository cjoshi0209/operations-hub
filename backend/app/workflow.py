"""Workflow rules: the single source of truth for statuses and allowed transitions."""
from .errors import ApiError

STATUSES = ("open", "in_progress", "blocked", "awaiting_approval", "resolved", "closed")
TYPES = ("investigation", "action", "incident", "approval", "compliance")
PRIORITIES = ("P1", "P2", "P3", "P4")
APPROVAL_STATUSES = ("pending", "approved", "rejected")
TERMINAL = ("resolved", "closed")

# from_status -> allowed target statuses. Everything not listed here is a 409.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("in_progress", "blocked"),
    "in_progress": ("blocked", "awaiting_approval", "resolved"),
    "blocked": ("in_progress",),
    "awaiting_approval": ("in_progress", "resolved"),
    "resolved": ("closed", "in_progress"),
    "closed": (),
}


def approval_blocks_resolution(item_type: str, approval_status: str | None) -> bool:
    """An approval-type item cannot be resolved until its approval is approved."""
    return item_type == "approval" and approval_status != "approved"


def check_transition(
    *,
    current: str,
    target: str,
    item_type: str,
    approval_status: str | None,
    resolution_note: str | None,
) -> None:
    if target not in STATUSES:
        raise ApiError(422, "unknown_status", f"Unknown status '{target}'.")
    if target not in TRANSITIONS.get(current, ()):
        raise ApiError(
            409,
            "invalid_transition",
            f"Cannot move from '{current}' to '{target}'.",
            allowed=list(TRANSITIONS.get(current, ())),
        )
    if target == "resolved" and approval_blocks_resolution(item_type, approval_status):
        raise ApiError(
            409,
            "approval_required",
            "This approval item cannot be resolved until its approval is approved.",
        )
    if target == "closed" and not (resolution_note and resolution_note.strip()):
        raise ApiError(422, "resolution_note_required", "Closing an item requires a resolution note.")
