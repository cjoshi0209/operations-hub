"""Seed data:  python -m app.seed [--items 2000] [--reset]

4 teams, 12 users, ~2,000 work items (with events, a few comments, approvals). Deterministic (fixed RNG seed).
All demo users share the password in DEMO_PASSWORD. This is demo data only.
"""

import argparse
import random
from datetime import timedelta

from sqlalchemy import insert, text

from .auth import hash_password
from .config import Settings
from .db import Base, Database
from .models import Approval, Comment, Event, Team, TeamMembership, User, WorkItem, utcnow

DEMO_PASSWORD = "password123"

TEAMS = ["Platform Ops", "Security & Compliance", "Customer Support", "Finance Ops"]

# (name, email, [(team index, role), ...])
USERS = [
    ("Alice Nguyen", "alice@example.com", [(0, "lead")]),
    ("Bob Martins", "bob@example.com", [(0, "member")]),
    ("Carla Diaz", "carla@example.com", [(0, "member"), (2, "member")]),  # multi-team
    ("Dev Patel", "dev@example.com", [(1, "lead")]),
    ("Elena Rossi", "elena@example.com", [(1, "member")]),
    ("Farid Khan", "farid@example.com", [(1, "member")]),
    ("Grace Lee", "grace@example.com", [(2, "lead")]),
    ("Hiro Tanaka", "hiro@example.com", [(2, "member")]),
    ("Isla Murphy", "isla@example.com", [(2, "member")]),
    ("Jon Okafor", "jon@example.com", [(3, "lead")]),
    ("Kira Novak", "kira@example.com", [(3, "member")]),
    ("Liam Brooks", "liam@example.com", [(3, "member"), (1, "member")]),  # multi-team
]

SUBJECTS = [
    "payment gateway",
    "login service",
    "billing export",
    "vendor invoice",
    "SOC2 evidence",
    "database failover",
    "refund backlog",
    "access review",
    "VPN certificate",
    "data retention policy",
    "chargeback dispute",
    "onboarding checklist",
    "audit log pipeline",
    "customer escalation",
    "API rate limits",
    "backup restore test",
    "expense report",
    "firewall rule change",
    "ticket queue",
    "SLA breach",
    "password reset flow",
    "tax filing",
    "disk usage alert",
    "GDPR request",
    "deploy pipeline",
    "reconciliation job",
    "phishing report",
    "license renewal",
]
VERBS = {
    "investigation": ["Investigate", "Triage", "Root-cause", "Analyse"],
    "action": ["Update", "Rotate", "Migrate", "Clean up", "Document"],
    "incident": ["Outage in", "Degraded", "Errors spiking in", "Failing"],
    "approval": ["Approve change to", "Sign off on", "Approve spend for", "Authorise"],
    "compliance": ["Review", "Attest", "Evidence collection for", "Quarterly check of"],
}
TYPE_WEIGHTS = {"investigation": 25, "action": 35, "incident": 10, "approval": 15, "compliance": 15}
PRIORITY_WEIGHTS = {"P1": 8, "P2": 22, "P3": 45, "P4": 25}
STATUS_WEIGHTS = {
    "open": 28,
    "in_progress": 27,
    "blocked": 8,
    "awaiting_approval": 7,
    "resolved": 12,
    "closed": 18,
}
COMMENTS = [
    "Looking into this now.",
    "Blocked on the vendor, chasing.",
    "Can someone confirm the scope?",
    "Fix is in staging, verifying.",
    "Adding evidence to the shared folder.",
    "Needs sign-off before we proceed.",
]


def wipe(db: Database) -> None:
    Base.metadata.drop_all(db.write_engine)
    db.create_all()


def seed(db: Database, n_items: int = 2000, password_iterations: int = 120_000, rng_seed: int = 42) -> dict:
    rng = random.Random(rng_seed)
    now = utcnow()
    with db.write_session() as s:
        team_ids = []
        for name in TEAMS:
            t = Team(name=name)
            s.add(t)
            s.flush()
            team_ids.append(t.id)
        pw = hash_password(DEMO_PASSWORD, password_iterations)
        user_ids = []
        members: dict[int, list[int]] = {tid: [] for tid in team_ids}
        leads: dict[int, int] = {}
        for name, email, memberships in USERS:
            u = User(name=name, email=email, password_hash=pw)
            s.add(u)
            s.flush()
            user_ids.append(u.id)
            for team_idx, role in memberships:
                tid = team_ids[team_idx]
                s.add(TeamMembership(user_id=u.id, team_id=tid, role=role))
                members[tid].append(u.id)
                if role == "lead":
                    leads[tid] = u.id
        s.flush()

        items, events, approvals, comments = [], [], [], []
        types = list(TYPE_WEIGHTS)
        statuses = list(STATUS_WEIGHTS)
        prios = list(PRIORITY_WEIGHTS)
        for i in range(1, n_items + 1):
            tid = rng.choice(team_ids)
            type_ = rng.choices(types, weights=list(TYPE_WEIGHTS.values()))[0]
            status = rng.choices(statuses, weights=list(STATUS_WEIGHTS.values()))[0]
            prio = rng.choices(prios, weights=list(PRIORITY_WEIGHTS.values()))[0]
            creator = rng.choice(members[tid])
            assignee = rng.choice(members[tid]) if (status != "open" or rng.random() < 0.35) else None
            created = now - timedelta(days=rng.uniform(0, 90), hours=rng.uniform(0, 12))
            updated = created + (now - created) * rng.random()
            due = created + timedelta(days=rng.randint(1, 30)) if rng.random() < 0.7 else None
            subject = rng.choice(SUBJECTS)
            title = f"{rng.choice(VERBS[type_])} {subject} #{i}"
            version = rng.randint(1, 6)
            items.append(
                dict(
                    id=i,
                    title=title,
                    description=f"Auto-generated demo item about the {subject}.",
                    type=type_,
                    priority=prio,
                    status=status,
                    owning_team_id=tid,
                    assignee_id=assignee,
                    created_by=creator,
                    created_at=created,
                    updated_at=updated,
                    due_at=due,
                    version=version,
                    resolution_note="Done and verified." if status == "closed" else None,
                )
            )
            events.append(
                dict(
                    work_item_id=i,
                    actor_id=creator,
                    type="created",
                    payload={"type": type_, "priority": prio},
                    created_at=created,
                )
            )
            if assignee and status != "open":
                events.append(
                    dict(
                        work_item_id=i,
                        actor_id=assignee,
                        type="claimed",
                        payload={"assignee_id": assignee},
                        created_at=created + timedelta(hours=1),
                    )
                )
                events.append(
                    dict(
                        work_item_id=i,
                        actor_id=assignee,
                        type="status_changed",
                        payload={"from": "open", "to": status},
                        created_at=updated,
                    )
                )
            if rng.random() < 0.15:
                comments.append(
                    dict(
                        work_item_id=i,
                        author_id=rng.choice(members[tid]),
                        body=rng.choice(COMMENTS),
                        created_at=updated,
                    )
                )
            if type_ == "approval":
                approver_pool = [u for u in user_ids if u != creator and u in (leads.get(tid), *members[tid])]
                approver = leads[tid] if leads[tid] != creator else rng.choice(approver_pool)
                if status in ("resolved", "closed"):
                    a_status = "approved"
                elif status == "awaiting_approval":
                    a_status = "pending"
                else:
                    a_status = rng.choices(["pending", "rejected", "none"], weights=[40, 10, 50])[0]
                if a_status != "none":
                    approvals.append(
                        dict(
                            work_item_id=i,
                            requested_by=creator,
                            approver_id=approver,
                            status=a_status,
                            created_at=created,
                            decided_at=None if a_status == "pending" else updated,
                        )
                    )
        # Core bulk inserts: fast, and seed history needs no outbox rows (nothing to notify about).
        s.execute(insert(WorkItem), items)
        s.execute(insert(Event), events)
        if comments:
            s.execute(insert(Comment), comments)
        if approvals:
            s.execute(insert(Approval), approvals)
        s.commit()
    return {"teams": len(TEAMS), "users": len(USERS), "items": n_items}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", type=int, default=2000)
    ap.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
    args = ap.parse_args()
    settings = Settings()
    db = Database(settings.database_url)
    db.create_all()
    if args.reset:
        wipe(db)
    else:
        with db.read_session() as s:
            if s.execute(text("SELECT COUNT(*) FROM users")).scalar_one() > 0:
                raise SystemExit("Database already has data. Re-run with --reset to start over.")
    stats = seed(db, args.items, settings.password_iterations)
    print(f"Seeded {stats['teams']} teams, {stats['users']} users, {stats['items']} work items.")
    print(f"Log in with any of these emails and password '{DEMO_PASSWORD}':")
    for name, email, memberships in USERS:
        teams = ", ".join(f"{TEAMS[t]} ({r})" for t, r in memberships)
        print(f"  {email:<20} {name:<14} {teams}")


if __name__ == "__main__":
    main()
