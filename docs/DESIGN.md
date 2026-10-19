# Operations Hub: design (written before the code)

## Data model

```
users(id, name, email*, password_hash, is_active)
teams(id, name*)
team_memberships(id, user_id, team_id, role[member|lead])           unique(user_id, team_id)
work_items(id, title, description, type, priority, status, owning_team_id, assignee_id?,
           created_by, created_at, updated_at, due_at?, version, resolution_note?)
events(id, work_item_id, actor_id, type, payload JSON, created_at)  append-only
comments(id, work_item_id, author_id, body, created_at)
approvals(id, work_item_id, requested_by, approver_id, status[pending|approved|rejected],
          created_at, decided_at?, decision_note?)                   one *pending* per item (partial unique index)
outbox(id, event_id*, payload JSON, status[pending|processing|done|failed], attempts, last_error,
       next_attempt_at, locked_at?, processed_at?, created_at)
notifications(id, user_id, outbox_id, event_id, work_item_id, message, created_at)
                                                                     unique(outbox_id, user_id)  <- exactly-once effect
idempotency_keys(id, key, user_id, method, path, request_hash, status_code, response_body, created_at)
                                                                     unique(user_id, key)
auth_tokens(id, token_hash*, user_id, created_at, expires_at)
```

`work_items` indexes: `(owning_team_id, status)`, `(assignee_id, status)`, `status`, `priority`, `created_at`, `updated_at`, `due_at`.
Every column type is portable SQLAlchemy; SQLite-only things (WAL pragma, `BEGIN IMMEDIATE`, append-only triggers) are
guarded by a dialect check so the same schema runs on Postgres.

## Endpoints (all JSON, `Authorization: Bearer <token>`)

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | email + password -> bearer token |
| GET | `/auth/me` | current user, memberships |
| GET | `/teams` | teams + members (for pickers) |
| GET | `/work-items` | filters, `q` title search, sort, keyset pagination, `total` |
| POST | `/work-items` | create (member of owning team) |
| GET | `/work-items/{id}` | item + first page of events |
| GET | `/work-items/{id}/events` | cursor-paginated history |
| PATCH | `/work-items/{id}` | edit fields; **requires `expected_version`** |
| POST | `/work-items/{id}/claim` | conditional UPDATE |
| POST | `/work-items/{id}/transitions` | status change; requires `expected_version` |
| GET/POST | `/work-items/{id}/comments` | |
| POST | `/work-items/{id}/approval` | request approval |
| POST | `/work-items/{id}/approval/decision` | approve / reject (designated approver only) |
| GET | `/notifications` | the async-delivered notifications for the caller |

Every response item carries `allowed_actions` computed server-side for the caller.

## Concurrency approach

1. **Claim**: `UPDATE work_items SET assignee_id=:me, version=version+1 WHERE id=:id AND assignee_id IS NULL AND status<>'closed'`.
   `rowcount == 0` -> reload, 409 `already_claimed` with the current owner.
2. **Stale edits**: all rule checks run against a row read at version *V*; the write is
   `UPDATE ... WHERE id=:id AND version=:V` and bumps `version`. Because *every* mutation bumps `version`, a successful
   conditional update proves nothing changed between check and write. Zero rows -> 409 `version_conflict` with the
   current item.
3. **Idempotency**: `Idempotency-Key` header; (user, key) unique row stores status + body, written in the same
   transaction as the side effects. Replays return the stored body and create nothing. 24h TTL.

SQLite specifics: write requests open `BEGIN IMMEDIATE` (a dedicated write engine), so writers queue on the file lock
instead of failing on read->write lock upgrades; reads use a plain deferred transaction (WAL, so they never block).
On Postgres the same guarded UPDATEs and unique constraints provide the guarantees under READ COMMITTED.

## Async processing

Event + Outbox rows are inserted in the same transaction as the state change. A worker polls the outbox, claims a row with a
conditional UPDATE (`pending -> processing`, attempts+1), writes notifications and flips the row to `done` in **one**
transaction. A failure rolls that transaction back, records `last_error` and reschedules with backoff; after
`max_attempts` the row is `failed`. `unique(outbox_id, user_id)` on notifications means even a crash between delivery and
bookkeeping cannot produce a second notification.
