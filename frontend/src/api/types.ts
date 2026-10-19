export type ItemType = "investigation" | "action" | "incident" | "approval" | "compliance";
export type Priority = "P1" | "P2" | "P3" | "P4";
export type Status = "open" | "in_progress" | "blocked" | "awaiting_approval" | "resolved" | "closed";

export interface UserRef {
  id: number;
  name: string;
}
export interface TeamRef {
  id: number;
  name: string;
}
export interface Approval {
  id: number;
  status: "pending" | "approved" | "rejected";
  approver: UserRef;
  requested_by: UserRef;
  created_at: string;
  decided_at: string | null;
  decision_note: string | null;
}
export interface WorkItem {
  id: number;
  title: string;
  description: string;
  type: ItemType;
  priority: Priority;
  status: Status;
  owning_team: TeamRef;
  assignee: UserRef | null;
  created_by: UserRef;
  created_at: string;
  updated_at: string;
  due_at: string | null;
  version: number;
  resolution_note: string | null;
  approval: Approval | null;
  /** Computed by the server for the current user. The UI renders buttons from this and decides nothing itself. */
  allowed_actions: string[];
  attention_reasons: string[];
}
export interface ItemEvent {
  id: number;
  type: string;
  actor: UserRef;
  payload: Record<string, any>;
  created_at: string;
}
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
export interface ItemPage extends Page<WorkItem> {
  total: number;
}
export interface ItemDetail extends WorkItem {
  events: Page<ItemEvent>;
}
export interface Comment {
  id: number;
  author: UserRef;
  body: string;
  created_at: string;
}
export interface Notification {
  id: number;
  work_item_id: number;
  message: string;
  created_at: string;
}
export interface Me {
  user: UserRef;
  email: string;
  memberships: { team: TeamRef; role: "member" | "lead" }[];
}
export interface Team {
  id: number;
  name: string;
  members: UserRef[];
}
export interface ListFilters {
  team_id?: number;
  status?: Status[];
  priority?: Priority[];
  assignee?: string;
  q?: string;
  sort: "updated_at" | "created_at" | "priority" | "due_at";
  order: "asc" | "desc";
  needs_attention?: boolean;
}
