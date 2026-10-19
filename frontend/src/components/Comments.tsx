import { FormEvent, useState } from "react";
import { useAddComment, useComments } from "../api/hooks";
import { timeAgo } from "../lib/format";
import { Alert, Spinner } from "./common";

export default function Comments({ itemId, canComment }: { itemId: number; canComment: boolean }) {
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useComments(itemId);
  const add = useAddComment(itemId);
  const [body, setBody] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    add.mutate(body, { onSuccess: () => setBody("") });
  }

  const comments = data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <section className="card">
      <h2>Comments</h2>
      {canComment && (
        <form onSubmit={submit} className="comment-form">
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={2}
            placeholder="Add a comment…"
            aria-label="New comment"
          />
          <button className="primary" disabled={add.isPending || !body.trim()}>
            {add.isPending ? "Posting…" : "Comment"}
          </button>
        </form>
      )}
      {add.error && <Alert kind="error">{(add.error as Error).message}</Alert>}
      {isLoading && <Spinner />}
      {!isLoading && comments.length === 0 && <p className="muted">No comments yet.</p>}
      <ul className="comments">
        {comments.map((c) => (
          <li key={c.id}>
            <strong>{c.author.name}</strong> <span className="muted small">{timeAgo(c.created_at)}</span>
            <p>{c.body}</p>
          </li>
        ))}
      </ul>
      {hasNextPage && (
        <button onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
          {isFetchingNextPage ? "Loading…" : "Older comments"}
        </button>
      )}
    </section>
  );
}
