"""Resource-level authorization: visibility (404, never 403) and per-action permissions."""
from sqlalchemy import func, select

from app.models import Event


# 4. A user from another team gets 404 and cannot change status ------------------------------------------
def test_other_team_gets_404_on_item_and_cannot_change_status(world):
    item_id = world.make_item(team="Ops", assignee="bob")
    c = world.client()
    sec = world.h("erin")  # Security team member: no relationship to the Ops item

    assert c.get(f"/work-items/{item_id}", headers=sec).status_code == 404
    assert c.get(f"/work-items/{item_id}/events", headers=sec).status_code == 404
    assert c.get(f"/work-items/{item_id}/comments", headers=sec).status_code == 404

    r = c.post(f"/work-items/{item_id}/transitions", headers=sec,
               json={"to_status": "in_progress", "expected_version": 1})
    assert r.status_code == 404
    assert c.patch(f"/work-items/{item_id}", headers=sec, json={"expected_version": 1, "title": "x"}).status_code == 404
    assert c.post(f"/work-items/{item_id}/claim", headers=sec).status_code == 404
    assert c.post(f"/work-items/{item_id}/comments", headers=sec, json={"body": "hi"}).status_code == 404

    # nothing changed, nothing was recorded
    item = world.item(item_id)
    assert item.status == "open" and item.version == 1
    with world.db.read_session() as s:
        assert s.execute(select(func.count()).select_from(Event)).scalar_one() == 0


def test_404_body_is_identical_for_missing_and_forbidden_items(world):
    item_id = world.make_item(team="Ops")
    c = world.client()
    hidden = c.get(f"/work-items/{item_id}", headers=world.h("erin"))
    missing = c.get("/work-items/999999", headers=world.h("erin"))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


def test_hidden_items_are_excluded_from_lists_and_totals(world):
    world.make_item(team="Ops", title="ops thing")
    world.make_item(team="Sec", creator="dave", title="sec thing")
    c = world.client()
    body = c.get("/work-items", headers=world.h("erin")).json()
    assert [i["title"] for i in body["items"]] == ["sec thing"]
    assert body["total"] == 1
    # even an explicit filter for the other team's id can't reveal anything
    body = c.get(f"/work-items?team_id={world.teams['Ops']}", headers=world.h("erin")).json()
    assert body["items"] == [] and body["total"] == 0


def test_unauthenticated_requests_are_rejected(world):
    item_id = world.make_item()
    c = world.client()
    assert c.get(f"/work-items/{item_id}").status_code == 401
    assert c.get(f"/work-items/{item_id}", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.post(f"/work-items/{item_id}/claim").status_code == 401


def test_assignee_from_another_team_can_see_but_not_edit(world):
    item_id = world.make_item(team="Ops", assignee="erin")  # erin is Sec, assigned an Ops item
    c = world.client()
    got = c.get(f"/work-items/{item_id}", headers=world.h("erin"))
    assert got.status_code == 200
    assert "edit" not in got.json()["allowed_actions"]
    assert "transition:in_progress" in got.json()["allowed_actions"]  # assignee may change status
    r = c.patch(f"/work-items/{item_id}", headers=world.h("erin"), json={"expected_version": 1, "title": "x"})
    assert r.status_code == 403


def test_only_assignee_or_lead_can_change_status(world):
    item_id = world.make_item(assignee="bob")
    c = world.client()
    body = {"to_status": "in_progress", "expected_version": 1}
    # carol: team member, but neither assignee nor lead
    r = c.post(f"/work-items/{item_id}/transitions", headers=world.h("carol"), json=body)
    assert r.status_code == 403
    # alice is the lead
    assert c.post(f"/work-items/{item_id}/transitions", headers=world.h("alice"), json=body).status_code == 200


def test_assignee_can_change_status(world):
    item_id = world.make_item(assignee="bob")
    r = world.client().post(f"/work-items/{item_id}/transitions", headers=world.h("bob"),
                            json={"to_status": "in_progress", "expected_version": 1})
    assert r.status_code == 200 and r.json()["status"] == "in_progress"


def test_only_team_members_can_edit_or_claim(world):
    item_id = world.make_item(team="Ops", assignee="erin")
    c = world.client()
    assert c.patch(f"/work-items/{item_id}", headers=world.h("bob"),
                   json={"expected_version": 1, "title": "ok"}).status_code == 200
    unassigned = world.make_item(team="Ops", approver="erin", type="approval", title="needs erin")
    # erin can see it (she is the approver) but is not on the Ops team
    c2 = c.post(f"/work-items/{unassigned}/claim", headers=world.h("erin"))
    assert c2.status_code == 403


def test_allowed_actions_are_computed_per_user(world):
    item_id = world.make_item(assignee="bob")
    c = world.client()
    for who, expect_claim, expect_edit, expect_transition in [
        ("alice", False, True, True),   # lead
        ("bob", False, True, True),     # assignee
        ("carol", False, True, False),  # plain member
    ]:
        actions = c.get(f"/work-items/{item_id}", headers=world.h(who)).json()["allowed_actions"]
        assert ("claim" in actions) is expect_claim, who
        assert ("edit" in actions) is expect_edit, who
        assert any(a.startswith("transition:") for a in actions) is expect_transition, who
        assert "comment" in actions

    unassigned = world.make_item()
    assert "claim" in c.get(f"/work-items/{unassigned}", headers=world.h("carol")).json()["allowed_actions"]


def test_list_items_carry_allowed_actions_too(world):
    world.make_item()
    items = world.client().get("/work-items", headers=world.h("carol")).json()["items"]
    assert "claim" in items[0]["allowed_actions"]


def test_only_designated_approver_can_decide(world):
    item_id = world.make_item(type="approval", approver="dave")
    c = world.client()
    body = {"decision": "approved"}
    # a member of the owning team who is not the approver
    assert c.post(f"/work-items/{item_id}/approval/decision", headers=world.h("alice"), json=body).status_code == 403
    # a total outsider
    assert c.post(f"/work-items/{item_id}/approval/decision", headers=world.h("frank"), json=body).status_code == 404
    ok = c.post(f"/work-items/{item_id}/approval/decision", headers=world.h("dave"), json=body)
    assert ok.status_code == 200 and ok.json()["approval"]["status"] == "approved"
    # deciding twice is a conflict, not a silent overwrite
    again = c.post(f"/work-items/{item_id}/approval/decision", headers=world.h("dave"), json={"decision": "rejected"})
    assert again.status_code == 409


def test_approver_outside_the_team_sees_the_item_and_gets_decide_actions(world):
    item_id = world.make_item(type="approval", approver="dave")
    got = world.client().get(f"/work-items/{item_id}", headers=world.h("dave"))
    assert got.status_code == 200
    assert {"approve", "reject"} <= set(got.json()["allowed_actions"])
    assert "edit" not in got.json()["allowed_actions"]


def test_cannot_create_items_for_a_team_you_are_not_in(world):
    r = world.client().post("/work-items", headers=world.h("erin"), json={
        "title": "sneaky", "type": "action", "priority": "P3", "owning_team_id": world.teams["Ops"]})
    assert r.status_code == 403
