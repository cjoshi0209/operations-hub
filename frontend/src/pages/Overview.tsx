import { useEffect, useState } from "react";
import { useItems, useTeams } from "../api/hooks";
import type { ListFilters, Priority, Status } from "../api/types";
import { useAuth } from "../auth";
import { Alert, ItemRow, Spinner } from "../components/common";
import NewItemDialog from "../components/NewItemDialog";
import { STATUS_LABEL } from "../lib/actions";

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Attention() {
  const { data, isLoading, error, fetchNextPage, hasNextPage, isFetchingNextPage } = useItems(
    { needs_attention: true, sort: "priority", order: "asc" },
    8,
  );
  const total = data?.pages[0]?.total ?? 0;
  return (
    <section className="card">
      <h2>
        Needs my attention <span className="count">{isLoading ? "" : total}</span>
      </h2>
      <p className="muted small">Assigned to me · awaiting my approval · P1 in my teams</p>
      {error && <Alert kind="error">{(error as Error).message}</Alert>}
      {isLoading && <Spinner />}
      {data && total === 0 && <p className="muted">Nothing needs you right now. 🎉</p>}
      <ul className="rows">
        {data?.pages.flatMap((p) => p.items).map((i) => <ItemRow key={i.id} item={i} showReasons />)}
      </ul>
      {hasNextPage && (
        <button onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
          {isFetchingNextPage ? "Loading…" : "Show more"}
        </button>
      )}
    </section>
  );
}

function Queue() {
  const { me } = useAuth();
  const teams = useTeams();
  const myTeams = me?.memberships.map((m) => m.team) ?? [];
  const [teamId, setTeamId] = useState<number | "">("");
  const [status, setStatus] = useState<Status | "">("");
  const [priority, setPriority] = useState<Priority | "">("");
  const [assignee, setAssignee] = useState<"" | "me" | "none">("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<ListFilters["sort"]>("updated_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const q = useDebounced(search);

  const filters: ListFilters = {
    team_id: teamId === "" ? undefined : teamId,
    status: status ? [status] : undefined,
    priority: priority ? [priority] : undefined,
    assignee: assignee || undefined,
    q: q.trim() || undefined,
    sort,
    order,
  };
  const { data, isLoading, isFetching, error, fetchNextPage, hasNextPage, isFetchingNextPage } = useItems(filters);
  const items = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages[0]?.total ?? 0;

  return (
    <section className="card">
      <h2>Team queue</h2>
      <div className="filters">
        <select value={teamId} onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : "")} aria-label="Team">
          <option value="">All my teams</option>
          {myTeams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
          {/* items I can see through assignment/approval from other teams */}
          {(teams.data ?? [])
            .filter((t) => !myTeams.some((m) => m.id === t.id))
            .map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} (shared with me)
              </option>
            ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value as Status | "")} aria-label="Status">
          <option value="">Any status</option>
          {Object.entries(STATUS_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <select value={priority} onChange={(e) => setPriority(e.target.value as Priority | "")} aria-label="Priority">
          <option value="">Any priority</option>
          {["P1", "P2", "P3", "P4"].map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
        <select value={assignee} onChange={(e) => setAssignee(e.target.value as "" | "me" | "none")} aria-label="Assignee">
          <option value="">Anyone</option>
          <option value="me">Assigned to me</option>
          <option value="none">Unassigned</option>
        </select>
        <input
          type="search"
          placeholder="Search titles…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search titles"
        />
        <select value={sort} onChange={(e) => setSort(e.target.value as ListFilters["sort"])} aria-label="Sort by">
          <option value="updated_at">Last updated</option>
          <option value="created_at">Created</option>
          <option value="priority">Priority</option>
          <option value="due_at">Due date</option>
        </select>
        <button onClick={() => setOrder(order === "desc" ? "asc" : "desc")} aria-label="Toggle sort order">
          {order === "desc" ? "↓ Desc" : "↑ Asc"}
        </button>
      </div>

      {error && <Alert kind="error">{(error as Error).message}</Alert>}
      {isLoading ? (
        <Spinner />
      ) : (
        <>
          <p className="muted small">
            Showing {items.length} of {total.toLocaleString()} {isFetching && !isFetchingNextPage ? "· updating…" : ""}
          </p>
          {items.length === 0 && <p className="muted">No items match these filters.</p>}
          <ul className="rows">
            {items.map((i) => (
              <ItemRow key={i.id} item={i} />
            ))}
          </ul>
          {hasNextPage && (
            <button onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
              {isFetchingNextPage ? "Loading…" : "Load more"}
            </button>
          )}
        </>
      )}
    </section>
  );
}

export default function Overview() {
  const [creating, setCreating] = useState(false);
  return (
    <div className="stack">
      <div className="page-head">
        <h1>Overview</h1>
        <button className="primary" onClick={() => setCreating(true)}>
          + New work item
        </button>
      </div>
      {creating && <NewItemDialog onClose={() => setCreating(false)} />}
      <Attention />
      <Queue />
    </div>
  );
}
