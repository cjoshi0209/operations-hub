import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useCreateItem, useTeams } from "../api/hooks";
import type { ItemType, Priority } from "../api/types";
import { useAuth } from "../auth";
import { Alert } from "./common";
import Modal from "./Modal";

const TYPES: ItemType[] = ["investigation", "action", "incident", "approval", "compliance"];

export default function NewItemDialog({ onClose }: { onClose: () => void }) {
  const { me } = useAuth();
  const teams = useTeams();
  const create = useCreateItem();
  const nav = useNavigate();
  const myTeams = me?.memberships.map((m) => m.team) ?? [];
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [type, setType] = useState<ItemType>("action");
  const [priority, setPriority] = useState<Priority>("P3");
  const [teamId, setTeamId] = useState<number>(myTeams[0]?.id ?? 0);
  const [approverId, setApproverId] = useState<number | "">("");
  const people = (teams.data ?? []).flatMap((t) => t.members).filter((u) => u.id !== me?.user.id);
  const unique = [...new Map(people.map((u) => [u.id, u])).values()];

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate(
      {
        title,
        description,
        type,
        priority,
        owning_team_id: teamId,
        approver_id: type === "approval" && approverId !== "" ? approverId : undefined,
      },
      {
        onSuccess: (item) => {
          onClose();
          nav(`/items/${item.id}`);
        },
      },
    );
  }

  const err = create.error;
  return (
    <Modal title="New work item" onClose={onClose} busy={create.isPending}>
      <form onSubmit={submit}>
        {err && <Alert kind="error">{err instanceof ApiError ? err.message : "Could not create the item."}</Alert>}
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
        </label>
        <label>
          Description
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
        </label>
        <div className="grid2">
          <label>
            Type
            <select value={type} onChange={(e) => setType(e.target.value as ItemType)}>
              {TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label>
            Priority
            <select value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
              {["P1", "P2", "P3", "P4"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <label>
            Owning team
            <select value={teamId} onChange={(e) => setTeamId(Number(e.target.value))}>
              {myTeams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          {type === "approval" && (
            <label>
              Approver
              <select
                value={approverId}
                onChange={(e) => setApproverId(e.target.value ? Number(e.target.value) : "")}
                required
              >
                <option value="">Choose…</option>
                {unique.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <div className="actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={create.isPending || !title.trim()}>
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
