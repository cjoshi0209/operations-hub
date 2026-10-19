import { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Priority, Status, WorkItem } from "../api/types";
import { formatDue, timeAgo } from "../lib/format";
import { reasonLabel, statusLabel } from "../lib/actions";

export const StatusBadge = ({ status }: { status: Status }) => (
  <span className={`badge status-${status}`}>{statusLabel(status)}</span>
);

export const PriorityBadge = ({ priority }: { priority: Priority }) => (
  <span className={`badge prio-${priority}`}>{priority}</span>
);

export function Alert({
  kind,
  children,
  onDismiss,
}: {
  kind: "error" | "warn" | "info";
  children: ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div className={`alert alert-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <div>{children}</div>
      {onDismiss && (
        <button className="link" onClick={onDismiss} aria-label="Dismiss">
          Dismiss
        </button>
      )}
    </div>
  );
}

export function ItemRow({ item, showReasons }: { item: WorkItem; showReasons?: boolean }) {
  const due = formatDue(item.due_at);
  return (
    <li className="row">
      <div className="row-main">
        <Link to={`/items/${item.id}`} className="row-title">
          #{item.id} {item.title}
        </Link>
        <div className="row-meta">
          <PriorityBadge priority={item.priority} />
          <StatusBadge status={item.status} />
          <span>{item.type}</span>
          <span>{item.owning_team.name}</span>
          <span>{item.assignee ? `👤 ${item.assignee.name}` : "Unassigned"}</span>
          {item.due_at && <span className={due.overdue ? "overdue" : ""}>Due {due.text}</span>}
          <span title={item.updated_at}>Updated {timeAgo(item.updated_at)}</span>
        </div>
        {showReasons && item.attention_reasons.length > 0 && (
          <div className="row-meta">
            {item.attention_reasons.map((r) => (
              <span key={r} className="chip">
                {reasonLabel(r)}
              </span>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return <p className="muted">{label}</p>;
}
