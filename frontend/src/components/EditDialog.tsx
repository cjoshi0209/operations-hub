import { FormEvent, useState } from "react";
import { ApiError } from "../api/client";
import { useEditItem, useTeams } from "../api/hooks";
import type { Priority, WorkItem } from "../api/types";
import { isoToLocalInput, localInputToIso } from "../lib/format";
import { Alert } from "./common";

/**
 * Edit form. `baseVersion` is the version the user is editing against; it is sent as `expected_version`.
 * If the server answers 409 (someone else saved first) the draft is KEPT, the base moves to the server's
 * current version, and the user sees what changed and can save again deliberately.
 */
export default function EditDialog({ item, onClose }: { item: WorkItem; onClose: () => void }) {
  const edit = useEditItem(item.id);
  const teams = useTeams();
  const [baseVersion, setBaseVersion] = useState(item.version);
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description);
  const [priority, setPriority] = useState<Priority>(item.priority);
  const [due, setDue] = useState(isoToLocalInput(item.due_at));
  const [assignee, setAssignee] = useState<number | "">(item.assignee?.id ?? "");
  const [conflict, setConflict] = useState<WorkItem | null>(null);
  const members = teams.data?.find((t) => t.id === item.owning_team.id)?.members ?? [];

  function submit(e: FormEvent) {
    e.preventDefault();
    edit.mutate(
      {
        expected_version: baseVersion,
        title,
        description,
        priority,
        due_at: localInputToIso(due),
        assignee_id: assignee === "" ? null : assignee,
      },
      {
        onSuccess: () => onClose(),
        onError: (err) => {
          if (err instanceof ApiError && err.isVersionConflict && err.current) {
            setConflict(err.current);
            setBaseVersion(err.current.version); // next save is deliberate, against the new truth
          }
        },
      },
    );
  }

  const err = edit.error;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Edit work item">
      <form className="modal card" onSubmit={submit}>
        <h2>Edit #{item.id}</h2>
        {conflict && (
          <Alert kind="warn">
            <strong>Not saved — this item changed while you were editing.</strong> It is now at version{" "}
            {conflict.version} (last updated by someone else). Your draft below is untouched; review it against
            the latest values (title: “{conflict.title}”, priority {conflict.priority}) and save again, or cancel
            to discard.
          </Alert>
        )}
        {err && !conflict && <Alert kind="error">{err instanceof ApiError ? err.message : "Save failed."}</Alert>}
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
        </label>
        <label>
          Description
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={4} />
        </label>
        <div className="grid2">
          <label>
            Priority
            <select value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
              {["P1", "P2", "P3", "P4"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <label>
            Due
            <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
          <label>
            Assignee
            <select value={assignee} onChange={(e) => setAssignee(e.target.value ? Number(e.target.value) : "")}>
              <option value="">Unassigned</option>
              {members.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={edit.isPending || !title.trim()}>
            {edit.isPending ? "Saving…" : conflict ? "Save over latest" : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}
