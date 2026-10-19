import type { Status, WorkItem } from "../api/types";

/**
 * The server decides what the current user may do (`allowed_actions`). These helpers only *read* that list;
 * there is deliberately no role/ownership logic anywhere in the UI.
 */
export function can(item: Pick<WorkItem, "allowed_actions">, action: string): boolean {
  return item.allowed_actions.includes(action);
}

export function transitionTargets(item: Pick<WorkItem, "allowed_actions">): Status[] {
  return item.allowed_actions
    .filter((a) => a.startsWith("transition:"))
    .map((a) => a.slice("transition:".length) as Status);
}

export const STATUS_LABEL: Record<Status, string> = {
  open: "Open",
  in_progress: "In progress",
  blocked: "Blocked",
  awaiting_approval: "Awaiting approval",
  resolved: "Resolved",
  closed: "Closed",
};

export const TRANSITION_LABEL: Record<Status, string> = {
  open: "Reopen",
  in_progress: "Start / resume work",
  blocked: "Mark blocked",
  awaiting_approval: "Request approval review",
  resolved: "Resolve",
  closed: "Close…",
};

export const REASON_LABEL: Record<string, string> = {
  assigned_to_me: "Assigned to me",
  awaiting_my_approval: "Awaiting my approval",
  p1_in_my_team: "P1 in my team",
};

export function statusLabel(s: string): string {
  return (STATUS_LABEL as Record<string, string>)[s] ?? s;
}

export function reasonLabel(r: string): string {
  return REASON_LABEL[r] ?? r;
}

/** One-line description of a timeline event. */
export function describeEvent(type: string, payload: Record<string, any>): string {
  switch (type) {
    case "created":
      return "created this item";
    case "claimed":
      return "took ownership";
    case "status_changed":
      return `moved it from ${statusLabel(payload.from)} to ${statusLabel(payload.to)}${
        payload.resolution_note ? ` — “${payload.resolution_note}”` : ""
      }`;
    case "updated":
      return `edited ${Object.keys(payload.changes ?? {})
        .map((k) => k.replace("_id", "").replace("_", " "))
        .join(", ")}`;
    case "comment_added":
      return `commented: “${payload.excerpt ?? ""}”`;
    case "approval_requested":
      return "requested an approval";
    case "approval_decided":
      return `${payload.decision} the approval${payload.note ? ` — “${payload.note}”` : ""}`;
    default:
      return type.replace(/_/g, " ");
  }
}

/**
 * "Changed underneath you" detection. `acknowledged` is the version the user has seen (initial load, or the
 * response to their own last action). A newer server version means somebody else changed the item.
 */
export function changedUnderneath(serverVersion: number | undefined, acknowledged: number | undefined): boolean {
  return serverVersion !== undefined && acknowledged !== undefined && serverVersion > acknowledged;
}
