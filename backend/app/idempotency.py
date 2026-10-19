"""Idempotency-Key support for mutating endpoints.

The stored response is written in the *same transaction* as the side effects, so "key exists" implies
"the effects were committed", and a crash can never leave an effect without its key (or vice versa).
Only successful responses are stored; a failed request rolls back and may be retried with the same key.
"""
import hashlib
import json
from datetime import timedelta
from typing import Callable

from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import ApiError
from .models import IdempotencyKey, utcnow

MAX_KEY_LENGTH = 128


def _fingerprint(method: str, path: str, payload: dict | None) -> str:
    raw = json.dumps([method, path, payload or {}], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _replay(row: IdempotencyKey) -> JSONResponse:
    return JSONResponse(
        status_code=row.status_code,
        content=json.loads(row.response_body),
        headers={"Idempotent-Replay": "true"},
    )


def _find(db: Session, user_id: int, key: str, ttl_hours: int) -> IdempotencyKey | None:
    row = db.execute(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
    ).scalar_one_or_none()
    if row is not None and row.created_at <= utcnow() - timedelta(hours=ttl_hours):
        db.execute(delete(IdempotencyKey).where(IdempotencyKey.id == row.id))
        return None
    return row


def run_idempotent(
    db: Session,
    *,
    user_id: int,
    key: str | None,
    method: str,
    path: str,
    payload: dict | None,
    ttl_hours: int,
    action: Callable[[], tuple[int, dict]],
) -> JSONResponse:
    """Run ``action`` (returns ``(status, json-able body)``), commit, and remember the response under ``key``."""
    if key is None:
        status, body = action()
        db.commit()
        return JSONResponse(status_code=status, content=body)

    if not key.strip() or len(key) > MAX_KEY_LENGTH:
        raise ApiError(422, "bad_idempotency_key", f"Idempotency-Key must be 1-{MAX_KEY_LENGTH} characters.")
    fingerprint = _fingerprint(method, path, payload)

    existing = _find(db, user_id, key, ttl_hours)
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise ApiError(
                422, "idempotency_key_reuse", "This Idempotency-Key was already used for a different request."
            )
        return _replay(existing)

    status, body = action()
    db.add(
        IdempotencyKey(
            key=key,
            user_id=user_id,
            method=method,
            path=path,
            request_hash=fingerprint,
            status_code=status,
            response_body=json.dumps(body),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent request using the same key (possible on Postgres, where writers do not
        # serialise on a file lock). Our whole transaction - effects included - is rolled back; replay the winner.
        db.rollback()
        winner = _find(db, user_id, key, ttl_hours)
        if winner is None or winner.request_hash != fingerprint:
            raise ApiError(409, "idempotency_conflict", "A concurrent request with this key is in progress.")
        return _replay(winner)
    return JSONResponse(status_code=status, content=body)
