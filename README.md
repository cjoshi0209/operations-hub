# Operations Hub

An internal web app for teams to coordinate operational work items: queues, ownership, a status workflow with
approvals, an append-only history, and async notifications.

* **Backend:** Python · FastAPI · SQLAlchemy 2 · SQLite (schema is Postgres-compatible — see below)
* **Frontend:** React · Vite · TypeScript · TanStack Query
* **Tests:** pytest (backend, 80 tests) · Vitest (frontend units, 17 tests)

Design notes live in [`docs/DESIGN.md`](docs/DESIGN.md) (written first), the trade-offs in
[`ENGINEERING_DECISIONS.md`](ENGINEERING_DECISIONS.md).

## Prerequisites

* Python 3.11+ (developed on 3.14)
* Node 18+ and npm

## Setup, seed, run, test

All commands are run from the repository root. Pick the block for your shell.

### macOS / Linux / Git Bash

```bash
# --- backend ---
cd backend
python -m venv .venv
source .venv/bin/activate            # Git Bash on Windows: source .venv/Scripts/activate
pip install -r requirements.txt

python -m app.seed --reset           # 4 teams, 12 users, 2,000 work items (prints the logins)
python -m pytest                     # backend tests
python -m uvicorn app.main:create_app --factory --port 8000
                                     # API on http://localhost:8000 (docs: /docs); also runs the outbox worker

# --- frontend (second terminal) ---
cd frontend
npm install
npm test                             # Vitest unit tests
npm run dev                          # UI on http://localhost:5173
```

### Windows PowerShell

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m app.seed --reset
python -m pytest
python -m uvicorn app.main:create_app --factory --port 8000

# second terminal
cd frontend
npm install
npm test
npm run dev
```

Open <http://localhost:5173> and sign in. The login form is pre-filled with `alice@example.com`.

### Demo users

All demo users share the password **`password123`** (demo data only, created by `python -m app.seed`).

| Email | Name | Teams |
|---|---|---|
| alice@example.com | Alice Nguyen | Platform Ops (**lead**) |
| bob@example.com | Bob Martins | Platform Ops |
| carla@example.com | Carla Diaz | Platform Ops, Customer Support |
| dev@example.com | Dev Patel | Security & Compliance (**lead**) |
| elena@example.com | Elena Rossi | Security & Compliance |
| farid@example.com | Farid Khan | Security & Compliance |
| grace@example.com | Grace Lee | Customer Support (**lead**) |
| hiro@example.com | Hiro Tanaka | Customer Support |
| isla@example.com | Isla Murphy | Customer Support |
| jon@example.com | Jon Okafor | Finance Ops (**lead**) |
| kira@example.com | Kira Novak | Finance Ops |
| liam@example.com | Liam Brooks | Finance Ops, Security & Compliance |

### Things worth trying

* Open two browsers as `alice` and `bob`, open the same unassigned item, and click **Take ownership** in both —
  exactly one wins, the other gets a clear "already owned by …" message.
* Change an item as Bob while Alice has it open: Alice sees an "updated by someone else (v3 → v4)" banner.
* Sign in as `elena` and open a Platform Ops item by URL (e.g. `/items/18`): you get "doesn't exist or you
  don't have access" (the API returns 404, not 403).
* Approval-type items cannot be resolved until the approver approves; the Resolve button is not offered until then.
* Click the bell: notifications are written by the background outbox worker, not inline in the request.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///backend/operations_hub.db` | SQLAlchemy URL |
| `RUN_WORKER` | `1` | run the outbox worker inside the API process (`0` to run `python -m app.worker` separately) |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | allowed browser origins |
| `PASSWORD_ITERATIONS` | `120000` | PBKDF2 cost |
| `VITE_API_URL` (frontend) | `http://localhost:8000` | API base URL |

To run the worker as its own process: `RUN_WORKER=0 python -m uvicorn app.main:create_app --factory` in one
terminal and `python -m app.worker` in another.

## Postgres compatibility

The schema uses only portable SQLAlchemy types and constraints (check constraints, composite indexes, a partial
unique index with both `sqlite_where` and `postgresql_where`, JSON columns). SQLite-specific behaviour — WAL
pragmas, `BEGIN IMMEDIATE`, the append-only triggers on `events` — is guarded by a dialect check. To try Postgres,
install `psycopg[binary]` and set `DATABASE_URL=postgresql+psycopg://user:pass@host/db`. The concurrency
guarantees do not depend on SQLite's single-writer lock: they come from the guarded `UPDATE … WHERE` statements and
unique constraints (see ENGINEERING_DECISIONS.md). The test suite is run against SQLite only.

## Project layout

```
backend/app/
  models.py        tables, indexes, constraints, append-only guards
  workflow.py      status transition table + approval/closing rules (single source of truth)
  permissions.py   visibility + allowed_actions (server-side authorization)
  services.py      all mutations: authorize -> check rules -> guarded UPDATE -> Event + Outbox
  queries.py       filtered / searched / sorted / keyset-paginated listing
  idempotency.py   Idempotency-Key handling (24h)
  outbox.py        outbox claim/deliver/retry + worker loop
  routers/api.py   HTTP layer (thin)
  seed.py          deterministic demo data
backend/tests/     pytest suite
frontend/src/      React app (api/ client+hooks, pages/, components/, lib/ pure helpers + tests)
```

## API quick reference

Interactive docs at `/docs` once the API is running. Errors are always
`{"error": {"code", "message", ...extras}}`; `409` responses carry what you need to reconcile
(`current` item on a stale version, `current_owner` on a lost claim).

| | |
|---|---|
| `POST /auth/login` → `{token}` | `Authorization: Bearer <token>` on everything else |
| `GET /work-items?status=&priority=&team_id=&assignee=me\|none\|<id>&needs_attention=true&q=&sort=&order=&limit=&cursor=` | keyset pagination, `total` included, `limit` ≤ 100 |
| `GET /work-items/{id}` | item + first page of events (`?events_limit=`); `GET …/events?cursor=` for more |
| `PATCH /work-items/{id}` | body must include `expected_version` |
| `POST /work-items/{id}/claim` | conditional update; 409 + `current_owner` if taken |
| `POST /work-items/{id}/transitions` | `{to_status, expected_version, resolution_note?}` |
| `POST /work-items/{id}/comments`, `GET …/comments` | |
| `POST /work-items/{id}/approval`, `POST …/approval/decision` | request / decide |
| `GET /notifications` | notifications delivered by the worker |

Every mutating endpoint accepts an `Idempotency-Key` header.

## Known limitations

* **SQLite is single-writer.** Writes queue on a file lock; fine for this scale, not for a busy deployment
  (use Postgres — the code is written for it, but only SQLite is exercised in tests).
* **Title search is `LIKE '%term%'`.** Correct and escaped, but it scans the visible rows (no index can serve a
  leading wildcard). Fine at thousands of rows; at scale use Postgres `pg_trgm` or full-text search.
* **The "send" in the outbox is a row in `notifications`.** No email/Slack/websocket delivery, and no read/unread
  state on the server (the bell remembers what you have seen in `localStorage`).
* **Real-time is polling.** The detail page polls every 8 s and on window focus; there is no websocket/SSE push.
* **Auth is deliberately minimal:** opaque tokens, no refresh, no rate limiting or lockout, no password change, no
  SSO. Demo password is shared and documented. Do not expose this as-is.
* **No admin UI.** Teams, users and memberships are managed via the seed script / database.
* **Only successful responses are cached for idempotency.** A request that failed (e.g. 409) is not stored, so
  retrying the same key re-evaluates it — by design, but different from some payment APIs.
* **Approvers see items they approve, even from other teams.** This extends the stated visibility rule on purpose
  (otherwise they could not decide); see ENGINEERING_DECISIONS.md.
* **Frontend tests are unit-level** (API client, pure helpers). There are no component or browser E2E tests; the UI
  was exercised manually in a browser.
* **Seeded history is thin** (a few events per item) and seed items have no outbox rows.
