import { useMemo, useState } from "react";
import { fetchOlderEvents } from "../api/hooks";
import type { ItemDetail, ItemEvent } from "../api/types";
import { describeEvent } from "../lib/actions";
import { timeAgo } from "../lib/format";
import { Alert } from "./common";

/**
 * The first page of events arrives with the item (and refreshes whenever the item does); older pages are
 * fetched on demand and merged by id.
 */
export default function Timeline({ item }: { item: ItemDetail }) {
  const [older, setOlder] = useState<ItemEvent[]>([]);
  const [olderCursor, setOlderCursor] = useState<string | null | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const events = useMemo(() => {
    const byId = new Map<number, ItemEvent>();
    [...item.events.items, ...older].forEach((e) => byId.set(e.id, e));
    return [...byId.values()].sort((a, b) => b.id - a.id);
  }, [item.events.items, older]);

  // undefined = never paged; fall back to the cursor that came with the item
  const cursor = olderCursor === undefined ? item.events.next_cursor : olderCursor;

  async function loadOlder() {
    if (!cursor) return;
    setLoading(true);
    setError(null);
    try {
      const page = await fetchOlderEvents(item.id, cursor);
      setOlder((prev) => [...prev, ...page.items]);
      setOlderCursor(page.next_cursor);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card">
      <h2>Timeline</h2>
      {events.length === 0 && <p className="muted">No history yet.</p>}
      <ol className="timeline">
        {events.map((e) => (
          <li key={e.id}>
            <span className="dot" />
            <div>
              <strong>{e.actor.name}</strong> {describeEvent(e.type, e.payload)}
              <div className="muted small" title={e.created_at}>
                {timeAgo(e.created_at)}
              </div>
            </div>
          </li>
        ))}
      </ol>
      {error && <Alert kind="error">{error}</Alert>}
      {cursor && (
        <button onClick={loadOlder} disabled={loading}>
          {loading ? "Loading…" : "Load older events"}
        </button>
      )}
    </section>
  );
}
