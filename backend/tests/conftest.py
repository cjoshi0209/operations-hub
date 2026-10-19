import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hash_password
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import Approval, Team, TeamMembership, User, WorkItem, utcnow

PASSWORD = "pw-for-tests"

# name -> list of (team name, role)
USERS = {
    "alice": [("Ops", "lead")],
    "bob": [("Ops", "member")],
    "carol": [("Ops", "member")],
    "dave": [("Sec", "lead")],
    "erin": [("Sec", "member")],
    "frank": [],  # belongs to no team at all
}


class World:
    """A tiny, explicit dataset plus helpers. IDs are looked up by name so tests read like prose."""

    def __init__(self, app, db: Database):
        self.app = app
        self.db = db
        self.users: dict[str, int] = {}
        self.teams: dict[str, int] = {}
        self._tokens: dict[str, str] = {}
        self._local = threading.local()

    # --- clients -----------------------------------------------------------------------------------------
    def client(self) -> TestClient:
        return TestClient(self.app)

    def token(self, who: str) -> str:
        if who not in self._tokens:
            r = self.client().post("/auth/login", json={"email": f"{who}@example.com", "password": PASSWORD})
            assert r.status_code == 200, r.text
            self._tokens[who] = r.json()["token"]
        return self._tokens[who]

    def h(self, who: str, key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.token(who)}"}
        if key:
            headers["Idempotency-Key"] = key
        return headers

    # --- data --------------------------------------------------------------------------------------------
    def make_item(self, *, team="Ops", creator="alice", assignee=None, status="open", type="action",
                  priority="P3", title="Rotate the signing keys", approver=None, approval_status="pending") -> int:
        with self.db.write_session() as s:
            now = utcnow()
            item = WorkItem(
                title=title, description="", type=type, priority=priority, status=status,
                owning_team_id=self.teams[team], assignee_id=self.users[assignee] if assignee else None,
                created_by=self.users[creator], created_at=now, updated_at=now, version=1,
            )
            s.add(item)
            s.flush()
            if approver:
                s.add(Approval(work_item_id=item.id, requested_by=self.users[creator],
                               approver_id=self.users[approver], status=approval_status))
            s.commit()
            return item.id

    def item(self, item_id: int) -> WorkItem:
        with self.db.read_session() as s:
            return s.get(WorkItem, item_id)


@pytest.fixture()
def world(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        run_worker=False,
        password_iterations=1000,
    )
    app = create_app(settings)
    db: Database = app.state.db
    w = World(app, db)
    with db.write_session() as s:
        for name in ("Ops", "Sec"):
            t = Team(name=name)
            s.add(t)
            s.flush()
            w.teams[name] = t.id
        pw = hash_password(PASSWORD, settings.password_iterations)
        for name, memberships in USERS.items():
            u = User(name=name.title(), email=f"{name}@example.com", password_hash=pw)
            s.add(u)
            s.flush()
            w.users[name] = u.id
            for team, role in memberships:
                s.add(TeamMembership(user_id=u.id, team_id=w.teams[team], role=role))
        s.commit()
    with TestClient(app):  # runs lifespan (worker disabled), keeps engines alive for the test
        yield w
    db.dispose()
