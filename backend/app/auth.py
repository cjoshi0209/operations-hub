"""Simple token auth: seeded users log in with email + password and get an opaque bearer token.

Only a SHA-256 of the token is stored, so a database leak does not leak live sessions.
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import ApiError
from .models import AuthToken, TeamMembership, User, utcnow


def hash_password(password: str, iterations: int, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user: User, ttl_hours: int) -> tuple[str, "AuthToken"]:
    token = secrets.token_urlsafe(32)
    row = AuthToken(token_hash=_hash_token(token), user_id=user.id, expires_at=utcnow() + timedelta(hours=ttl_hours))
    db.add(row)
    db.flush()
    return token, row


@dataclass
class Principal:
    user: User
    roles: dict[int, str] = field(default_factory=dict)  # team_id -> "member" | "lead"

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def team_ids(self) -> list[int]:
        return list(self.roles)

    def is_member(self, team_id: int) -> bool:
        return team_id in self.roles

    def is_lead(self, team_id: int) -> bool:
        return self.roles.get(team_id) == "lead"


def load_principal(db: Session, authorization: str | None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthenticated", "Missing bearer token.")
    token = authorization[7:].strip()
    row = db.execute(select(AuthToken).where(AuthToken.token_hash == _hash_token(token))).scalar_one_or_none()
    if row is None or row.expires_at <= utcnow():
        raise ApiError(401, "unauthenticated", "Invalid or expired token.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "unauthenticated", "User is not active.")
    memberships = db.execute(select(TeamMembership).where(TeamMembership.user_id == user.id)).scalars().all()
    return Principal(user=user, roles={m.team_id: m.role for m in memberships})


# FastAPI dependencies --------------------------------------------------------------------------------------
def get_read_db(request: Request):
    db = request.app.state.db.read_session()
    try:
        yield db
    finally:
        db.close()


def get_write_db(request: Request):
    db = request.app.state.db.write_session()
    try:
        yield db
    finally:
        db.close()


def read_principal(request: Request, db: Session = Depends(get_read_db)) -> Principal:
    return load_principal(db, request.headers.get("authorization"))


def write_principal(request: Request, db: Session = Depends(get_write_db)) -> Principal:
    return load_principal(db, request.headers.get("authorization"))


class LoginThrottle:
    """Sliding-window limiter on *failed* logins per key (email + client IP). In-process, so per worker:
    enough to blunt password guessing in a single-node deployment; use a shared store (Redis) to scale out."""

    def __init__(self, max_failures: int = 8, window_seconds: int = 300):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._failures[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed (0 = allowed now)."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) < self.max_failures:
                return 0
            return max(1, int(self.window - (now - q[0])))

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
