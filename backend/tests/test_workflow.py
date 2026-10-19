"""Workflow rules are enforced by the service layer."""

import pytest

from app.workflow import STATUSES, TRANSITIONS


def _transition(world, item_id, to, who="alice", version=None, note=None):
    version = version if version is not None else world.item(item_id).version
    body = {"to_status": to, "expected_version": version}
    if note is not None:
        body["resolution_note"] = note
    return world.client().post(f"/work-items/{item_id}/transitions", headers=world.h(who), json=body)


# 5a. Invalid transitions are rejected --------------------------------------------------------------------
def test_invalid_transition_rejected_with_409(world):
    item_id = world.make_item(status="open")
    r = _transition(world, item_id, "resolved")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_transition"
    assert r.json()["error"]["allowed"] == ["in_progress", "blocked"]
    assert world.item(item_id).status == "open"


@pytest.mark.parametrize(
    "src,dst",
    [(s, d) for s in STATUSES for d in STATUSES if d not in TRANSITIONS[s] and s != "closed" and d != "closed"],
)
def test_every_transition_outside_the_table_is_rejected(world, src, dst):
    item_id = world.make_item(status=src)
    r = _transition(world, item_id, dst)
    assert r.status_code == 409, f"{src}->{dst} should be rejected"


def test_closed_is_terminal(world):
    item_id = world.make_item(status="closed")
    r = _transition(world, item_id, "in_progress")
    assert r.status_code == 409


def test_happy_path_through_the_whole_lifecycle(world):
    item_id = world.make_item()
    for to in ["in_progress", "blocked", "in_progress", "resolved"]:
        assert _transition(world, item_id, to).status_code == 200, to
    done = _transition(world, item_id, "closed", note="Rotated and verified.")
    assert done.status_code == 200
    assert done.json()["status"] == "closed" and done.json()["resolution_note"] == "Rotated and verified."
    assert done.json()["version"] == 6


# 5b. Closing needs a resolution note ---------------------------------------------------------------------
@pytest.mark.parametrize("note", [None, "", "   "])
def test_closing_without_resolution_note_rejected(world, note):
    item_id = world.make_item(status="resolved")
    r = _transition(world, item_id, "closed", note=note)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "resolution_note_required"
    assert world.item(item_id).status == "resolved"


def test_closing_with_note_succeeds(world):
    item_id = world.make_item(status="resolved")
    assert _transition(world, item_id, "closed", note="All good").status_code == 200


# 5c. Approval-gated resolution ---------------------------------------------------------------------------
def test_approval_item_cannot_be_resolved_until_approved(world):
    item_id = world.make_item(type="approval", status="in_progress", approver="dave")
    blocked = _transition(world, item_id, "resolved")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "approval_required"
    assert world.item(item_id).status == "in_progress"
    # the UI is told not to offer it either
    actions = world.client().get(f"/work-items/{item_id}", headers=world.h("alice")).json()["allowed_actions"]
    assert "transition:resolved" not in actions

    decide = world.client().post(
        f"/work-items/{item_id}/approval/decision", headers=world.h("dave"), json={"decision": "approved"}
    )
    assert decide.status_code == 200

    assert _transition(world, item_id, "resolved").status_code == 200
    actions = world.client().get(f"/work-items/{item_id}", headers=world.h("alice")).json()["allowed_actions"]
    assert "transition:closed" in actions


def test_rejected_approval_still_blocks_resolution_until_re_requested_and_approved(world):
    item_id = world.make_item(type="approval", status="in_progress", approver="dave", approval_status="rejected")
    assert _transition(world, item_id, "resolved").status_code == 409
    c = world.client()
    actions = c.get(f"/work-items/{item_id}", headers=world.h("alice")).json()["allowed_actions"]
    assert "request_approval" in actions
    r = c.post(f"/work-items/{item_id}/approval", headers=world.h("alice"), json={"approver_id": world.users["erin"]})
    assert r.status_code == 201 and r.json()["approval"]["status"] == "pending"
    assert (
        c.post(
            f"/work-items/{item_id}/approval/decision", headers=world.h("erin"), json={"decision": "approved"}
        ).status_code
        == 200
    )
    assert _transition(world, item_id, "resolved").status_code == 200


def test_approval_item_with_no_approval_at_all_cannot_resolve(world):
    item_id = world.make_item(type="approval", status="in_progress")
    assert _transition(world, item_id, "resolved").status_code == 409


def test_non_approval_items_do_not_need_approval(world):
    item_id = world.make_item(type="action", status="in_progress")
    assert _transition(world, item_id, "resolved").status_code == 200


def test_creating_an_approval_item_requires_an_approver_and_creates_the_approval(world):
    c = world.client()
    base = {"title": "Approve spend", "type": "approval", "priority": "P2", "owning_team_id": world.teams["Ops"]}
    assert c.post("/work-items", headers=world.h("alice"), json=base).status_code == 422
    r = c.post("/work-items", headers=world.h("alice"), json={**base, "approver_id": world.users["dave"]})
    assert r.status_code == 201
    assert r.json()["approval"]["status"] == "pending"
    assert r.json()["approval"]["approver"]["id"] == world.users["dave"]
    # nobody approves their own request
    r = c.post("/work-items", headers=world.h("alice"), json={**base, "approver_id": world.users["alice"]})
    assert r.status_code == 422


def test_every_mutation_bumps_version_and_writes_an_event(world):
    item_id = world.make_item()
    c = world.client()
    assert c.post(f"/work-items/{item_id}/claim", headers=world.h("alice")).json()["version"] == 2
    assert (
        c.patch(
            f"/work-items/{item_id}", headers=world.h("alice"), json={"expected_version": 2, "priority": "P1"}
        ).json()["version"]
        == 3
    )
    assert _transition(world, item_id, "in_progress").json()["version"] == 4
    events = c.get(f"/work-items/{item_id}", headers=world.h("alice")).json()["events"]["items"]
    assert [e["type"] for e in events] == ["status_changed", "updated", "claimed"]
    assert events[1]["payload"]["changes"]["priority"] == {"from": "P3", "to": "P1"}


def test_patch_validates_assignee_belongs_to_team(world):
    item_id = world.make_item()
    c = world.client()
    bad = c.patch(
        f"/work-items/{item_id}",
        headers=world.h("alice"),
        json={"expected_version": 1, "assignee_id": world.users["erin"]},
    )
    assert bad.status_code == 422
    ok = c.patch(
        f"/work-items/{item_id}",
        headers=world.h("alice"),
        json={"expected_version": 1, "assignee_id": world.users["bob"]},
    )
    assert ok.status_code == 200 and ok.json()["assignee"]["id"] == world.users["bob"]
    cleared = c.patch(
        f"/work-items/{item_id}", headers=world.h("alice"), json={"expected_version": 2, "assignee_id": None}
    )
    assert cleared.status_code == 200 and cleared.json()["assignee"] is None


def test_noop_patch_does_not_bump_version_or_write_events(world):
    item_id = world.make_item(title="same")
    r = world.client().patch(
        f"/work-items/{item_id}", headers=world.h("alice"), json={"expected_version": 1, "title": "same"}
    )
    assert r.status_code == 200 and r.json()["version"] == 1
    assert world.client().get(f"/work-items/{item_id}", headers=world.h("alice")).json()["events"]["items"] == []


def test_closed_items_cannot_be_claimed_or_edited(world):
    item_id = world.make_item(status="closed")
    c = world.client()
    assert c.post(f"/work-items/{item_id}/claim", headers=world.h("alice")).status_code == 409
    assert (
        c.patch(
            f"/work-items/{item_id}", headers=world.h("alice"), json={"expected_version": 1, "title": "x"}
        ).status_code
        == 409
    )
