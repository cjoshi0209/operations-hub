import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  useClaim,
  useDecideApproval,
  useItem,
  useRequestApproval,
  useTeams,
  useTransition,
} from "../api/hooks";
import type { Status } from "../api/types";
import { Alert, PriorityBadge, Spinner, StatusBadge } from "../components/common";
import Comments from "../components/Comments";
import EditDialog from "../components/EditDialog";
import NoteDialog from "../components/NoteDialog";
import Timeline from "../components/Timeline";
import { can, changedUnderneath, TRANSITION_LABEL, transitionTargets } from "../lib/actions";
import { formatDue, timeAgo } from "../lib/format";

type Notice = { kind: "error" | "warn" | "info"; text: string };

/** Turn any API failure into a clear, specific message. The cache has already been reconciled by the hook. */
function describeFailure(err: unknown): Notice {
  if (!(err instanceof ApiError)) return { kind: "error", text: "Something went wrong. Please try again." };
  if (err.code === "already_claimed" && err.currentOwner)
    return { kind: "warn", text: `Too late — ${err.currentOwner.name} already took ownership of this item.` };
  if (err.isVersionConflict)
    return {
      kind: "warn",
      text: `Not saved: this item was changed by someone else (you had v${err.extra.expected_version}, it is now v${err.extra.current_version}). The latest version is shown below.`,
    };
  if (err.isConflict) return { kind: "warn", text: `${err.message} The item has been refreshed.` };
  return { kind: "error", text: err.message };
}

export default function ItemDetail() {
  const id = Number(useParams().id);
  const { data: item, isLoading, error } = useItem(id);
  const teams = useTeams();
  const claim = useClaim(id);
  const transition = useTransition(id);
  const decide = useDecideApproval(id);
  const requestApproval = useRequestApproval(id);

  const [notice, setNotice] = useState<Notice | null>(null);
  const [acknowledged, setAcknowledged] = useState<number | undefined>(undefined);
  const [editing, setEditing] = useState(false);
  const [closing, setClosing] = useState(false);
  const [deciding, setDeciding] = useState<"approved" | "rejected" | null>(null);
  const [approverId, setApproverId] = useState<number | "">("");

  // The version the user has seen: the first load, then whatever their own actions produced.
  useEffect(() => {
    if (item && acknowledged === undefined) setAcknowledged(item.version);
  }, [item, acknowledged]);
  useEffect(() => {
    setAcknowledged(undefined);
    setNotice(null);
  }, [id]);

  if (isLoading) return <Spinner />;
  if (error || !item) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <div className="stack">
        <Alert kind="error">
          {notFound ? "This item doesn't exist or you don't have access to it." : (error as Error)?.message}
        </Alert>
        <Link to="/">← Back to overview</Link>
      </div>
    );
  }

  const busy = claim.isPending || transition.isPending || decide.isPending || requestApproval.isPending;
  const due = formatDue(item.due_at);
  const showChanged = changedUnderneath(item.version, acknowledged);

  const onFailure = (err: unknown) => {
    setNotice(describeFailure(err));
    if (err instanceof ApiError && err.isVersionConflict) setAcknowledged(err.current?.version);
  };
  const onOk = (v: number) => {
    setNotice(null);
    setAcknowledged((prev) => Math.max(prev ?? 0, v));
  };

  function doTransition(to: Status, note?: string) {
    setNotice(null);
    transition.mutate(
      { to_status: to, expected_version: item!.version, resolution_note: note },
      {
        onSuccess: (r) => {
          onOk(r.version);
          setClosing(false);
        },
        onError: (e) => {
          onFailure(e);
          setClosing(false);
        },
      },
    );
  }

  const approvalPending = item.approval?.status === "pending";

  return (
    <div className="stack">
      <Link to="/">← Overview</Link>

      {showChanged && (
        <Alert kind="warn" onDismiss={() => setAcknowledged(item.version)}>
          <strong>This item was updated by someone else</strong> (v{acknowledged} → v{item.version}, {timeAgo(item.updated_at)}).
          The latest values are shown.
        </Alert>
      )}
      {notice && (
        <Alert kind={notice.kind} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Alert>
      )}

      <header className="card detail-head">
        <div className="row-meta">
          <PriorityBadge priority={item.priority} />
          <StatusBadge status={item.status} />
          <span>{item.type}</span>
          <span className="muted small">v{item.version}</span>
        </div>
        <h1>
          #{item.id} {item.title}
        </h1>
        <dl className="facts">
          <div>
            <dt>Owner</dt>
            <dd>{item.assignee ? item.assignee.name : <em>Unassigned</em>}</dd>
          </div>
          <div>
            <dt>Team</dt>
            <dd>{item.owning_team.name}</dd>
          </div>
          <div>
            <dt>Created by</dt>
            <dd>{item.created_by.name}</dd>
          </div>
          <div>
            <dt>Due</dt>
            <dd className={due.overdue && item.status !== "closed" ? "overdue" : ""}>{due.text}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{timeAgo(item.updated_at)}</dd>
          </div>
        </dl>
        {item.description && <p className="description">{item.description}</p>}
        {item.resolution_note && (
          <p className="resolution">
            <strong>Resolution:</strong> {item.resolution_note}
          </p>
        )}
        {item.approval && (
          <p className={`approval approval-${item.approval.status}`}>
            Approval <strong>{item.approval.status}</strong> — approver {item.approval.approver.name}, requested by{" "}
            {item.approval.requested_by.name}
            {item.approval.decision_note ? ` (“${item.approval.decision_note}”)` : ""}
          </p>
        )}

        {/* Every button below exists only because the server listed it in allowed_actions. */}
        <div className="actions">
          {can(item, "claim") && (
            <button
              className="primary"
              disabled={busy}
              onClick={() => {
                setNotice(null);
                claim.mutate(undefined, { onSuccess: (r) => onOk(r.version), onError: onFailure });
              }}
            >
              {claim.isPending ? "Claiming…" : "Take ownership"}
            </button>
          )}
          {transitionTargets(item).map((to) => (
            <button
              key={to}
              disabled={busy}
              onClick={() => (to === "closed" ? setClosing(true) : doTransition(to))}
            >
              {transition.isPending && transition.variables?.to_status === to ? "Updating…" : TRANSITION_LABEL[to]}
            </button>
          ))}
          {can(item, "approve") && (
            <button className="good" disabled={busy} onClick={() => setDeciding("approved")}>
              Approve
            </button>
          )}
          {can(item, "reject") && (
            <button className="danger" disabled={busy} onClick={() => setDeciding("rejected")}>
              Reject
            </button>
          )}
          {can(item, "edit") && (
            <button disabled={busy} onClick={() => setEditing(true)}>
              Edit
            </button>
          )}
        </div>

        {can(item, "request_approval") && (
          <div className="inline-form">
            <select
              value={approverId}
              onChange={(e) => setApproverId(e.target.value ? Number(e.target.value) : "")}
              aria-label="Approver"
            >
              <option value="">Choose an approver…</option>
              {[...new Map((teams.data ?? []).flatMap((t) => t.members).map((u) => [u.id, u])).values()].map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
            <button
              disabled={busy || approverId === ""}
              onClick={() => {
                setNotice(null);
                requestApproval.mutate(Number(approverId), { onSuccess: (r) => onOk(r.version), onError: onFailure });
              }}
            >
              {requestApproval.isPending ? "Requesting…" : approvalPending ? "Re-request approval" : "Request approval"}
            </button>
          </div>
        )}
      </header>

      <div className="two-col">
        <Timeline item={item} />
        <Comments itemId={item.id} canComment={can(item, "comment")} />
      </div>

      {editing && <EditDialog item={item} onClose={() => setEditing(false)} />}
      {closing && (
        <NoteDialog
          title={`Close #${item.id}`}
          label="Resolution note (required)"
          required
          confirmLabel="Close item"
          pending={transition.isPending}
          onCancel={() => setClosing(false)}
          onConfirm={(note) => doTransition("closed", note)}
        />
      )}
      {deciding && (
        <NoteDialog
          title={deciding === "approved" ? "Approve this request" : "Reject this request"}
          label="Note (optional)"
          required={false}
          confirmLabel={deciding === "approved" ? "Approve" : "Reject"}
          pending={decide.isPending}
          onCancel={() => setDeciding(null)}
          onConfirm={(note) =>
            decide.mutate(
              { decision: deciding, note: note || undefined },
              {
                onSuccess: (r) => {
                  onOk(r.version);
                  setDeciding(null);
                },
                onError: (e) => {
                  onFailure(e);
                  setDeciding(null);
                },
              },
            )
          }
        />
      )}
    </div>
  );
}
