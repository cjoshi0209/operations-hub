# Engineering decisions

Each decision lists what was chosen, why, and what it costs. See [`docs/DESIGN.md`](docs/DESIGN.md) for the data
model and endpoint list.

## 1. Concurrency: guarded writes in SQL, not "check then write" in Python

**Chosen.** Three independent mechanisms, each enforced by the database statement itself:

* **Claim** — `UPDATE … SET assignee_id=:me, version=version+1 WHERE id=:id AND assignee_id IS NULL AND status<>'closed'`.
  `rowcount == 0` means someone else got there first; the handler reloads and returns `409 already_claimed` with the
  current owner. No lock, no retry loop.
* **Stale edits (optimistic concurrency)** — every state-changing request is validated against a row read at
  version *V*, and the write is `UPDATE … WHERE id=:id AND version=:V` with `version = version + 1`. Because *every*
  mutation (edit, claim, status change, approval decision) bumps `version`, a successful guarded update proves that
  nothing changed between the rules check and the write. `PATCH` and status transitions require `expected_version`
  (422 if absent); a mismatch returns `409 version_conflict` **with the current item**, so the client can
  reconcile without another round trip. An early comparison gives a cheap fast-path, but the SQL guard is the
  authority — a test hands the service a deliberately stale row to prove it (`test_*_guard_is_in_the_sql…`).
* **Idempotency** — `Idempotency-Key` + user → stored status/body, written *in the same transaction* as the effects,
  with a 24 h TTL. A replay returns the stored body (flagged `Idempotent-Replay: true`) and creates no event. The same
  key on a different request is a `422`, not a silent replay of the wrong thing. Keys are per-user.

**Why not row locks / `SELECT … FOR UPDATE`?** SQLite has none, and the guarded UPDATE is portable and needs no
lock-ordering discipline. **SQLite detail:** the write engine opens every transaction with `BEGIN IMMEDIATE`, so
concurrent writers queue on the file lock instead of failing with `SQLITE_BUSY_SNAPSHOT` when a read transaction
upgrades to a write; reads use plain deferred transactions under WAL and never block.

**Trade-offs.** Optimistic concurrency surfaces conflicts to users (a 409 and a refetch) rather than silently
last-write-wins; that is the point, but it is more UI work (banner, kept drafts). Comments deliberately do *not* bump
the item version (they do not change item state, and bumping would raise false "changed underneath you" banners).
On Postgres two same-key requests can race past the lookup; the `unique(user_id, key)` constraint makes one commit
fail, and the loser rolls back and replays the winner (implemented, but only exercised on SQLite).

## 2. Authorization: server-side, resource-level, 404 over 403, `allowed_actions` as the contract

**Chosen.** One `visible_clause(principal)` is AND-ed into every read of work items: owning team ∈ my teams, **or**
assigned to me, **or** I am a designated approver. Anything outside it is a `404` — existence is never leaked, and the
404 body is identical to a genuinely missing id (tested). Inside the visible set, a second check gives `403`:

| Action | Who |
|---|---|
| edit | member of owning team |
| claim | member of owning team (item unassigned) |
| change status | the assignee, or a lead of the owning team |
| decide approval | the designated approver |
| comment | anyone who can see the item |

The server computes `allowed_actions` per user for every item (list and detail) from the *same* predicates the
endpoints use, and the UI renders buttons purely from that list — there is no role or ownership logic in the frontend
(`lib/actions.ts` only reads the list). It even omits `transition:resolved` when an approval is outstanding.

**Deviation, on purpose.** The spec's visibility rule is "my teams + assigned to me". I added "+ items I am the
approver of": without it a cross-team approver gets a 404 on the very item they were asked to approve. They can see
and comment but not edit/claim. Cost: visibility is a three-way `OR` (one `EXISTS` subquery, backed by an index on
`approvals(approver_id, status)`), slightly more expensive than a pure team filter.

**Trade-offs.** Permissions are code, not data (no policy engine, no per-item ACLs); adding a role means editing
`permissions.py`. `GET /teams` exposes the team directory to any authenticated user so pickers work — acceptable
for an internal tool, not for a multi-tenant one.

## 3. Outbox vs. inline notifications

**Chosen.** The `Event` row and its `Outbox` row are inserted in the **same transaction** as the state change, so a
notification can never exist for a change that rolled back, and a committed change can never lose its notification.
A worker (an in-process thread by default, or `python -m app.worker`) polls and delivers.

* **Claim** is a conditional `UPDATE … status='processing', attempts=attempts+1`, so two workers can never own a row.
* **Delivery and the `done` flip commit in one transaction.** A failure rolls both back, records `last_error`, and
  reschedules with exponential backoff; after `max_attempts` the row is `failed` (dead-letter, visible, not retried).
* **A row is never delivered twice:** `unique(outbox_id, user_id)` on `notifications` makes a repeated delivery a
  no-op, so even a worker that crashes after committing notifications but before marking the row done (the lease
  expires and the row is reclaimed) cannot notify anyone twice. This is "at-least-once attempts, exactly-once
  effect" — the realistic guarantee for a pull-based outbox.

**Why not inline?** Inline sending couples request latency and availability to the notification channel and has no
good answer to "the DB commit succeeded but the send failed" (or the reverse). **Trade-offs:** notifications are
eventually consistent (≈1 s poll); a polling table needs an index (`status, next_attempt_at`) and housekeeping; and
the "send" here is only a table insert — a real channel would also need its own idempotency key downstream
(the outbox id is the natural one).

## 4. Append-only history

**Chosen.** `events` is only ever inserted into. It is enforced in three layers: the service layer only calls
`add`; ORM `before_update`/`before_delete` hooks raise; and on SQLite, `BEFORE UPDATE/DELETE` triggers abort the
statement (tested with raw SQL). Payloads record *what changed* (`{"changes": {"priority": {"from","to"}}}`,
status `from`/`to`, resolution notes), so the timeline is self-explanatory and auditable. Event ids are monotonic,
which makes `id < cursor` a stable keyset for paging history newest-first.

**Trade-offs.** `work_items` holds current state and `events` holds history — it is an audit log, **not** event
sourcing: the item row is the source of truth and is not rebuilt from events. Corrections are new events, never
edits. There is no retention/archival policy, and Postgres would need its own trigger (or revoked privileges) to
match the SQLite triggers.

## 5. What I deliberately did not build

Given a ~4 hour budget, "a smaller system that behaves well" won over breadth:

* **No real notification channels, read/unread state, or websockets/SSE** — polling and a `notifications` table.
* **No SSO, refresh tokens, password management, or admin UI** — seeded users, opaque bearer tokens. Login has an
  in-process failed-attempt throttle only (a shared store would be needed across workers).
* **No Alembic migrations** — `create_all` plus a `--reset` seed. Fine for a take-home; a real service needs migrations.
* **No full-text search** — escaped `ILIKE` on titles; `pg_trgm`/FTS is the upgrade path.
* **No per-item ACLs, delegation, or SLA timers**; approvals are a single approver per request.
* **No component/E2E frontend tests** — Vitest covers the API client (including idempotency-key reuse on retry),
  and the pure helpers; the UI was checked by hand in a browser.
* **No Postgres in CI** — the code is written to be portable and documented as such, but only SQLite is tested.

## Smaller choices worth knowing

* **Status workflow lives in one table** (`workflow.TRANSITIONS`); invalid moves are `409` with the allowed
  targets. Missing resolution note is `422` (it is a bad request, not a state conflict); approval-gated resolve is
  `409 approval_required`.
* **Keyset (cursor) pagination**, not offset: stable under concurrent writes and constant-cost per page. The cursor
  encodes `(sort value, id)` and is rejected if it doesn't match the requested sort. `total` is returned for the UI;
  `limit` is capped at 100 so no client can pull the whole dataset.
* **Tokens are stored hashed** (SHA-256) and passwords with PBKDF2-SHA256; login does the same work for unknown
  emails to avoid a trivial user-enumeration timing signal.
* **Approval requests cannot name the requester as approver** (separation of duties).
