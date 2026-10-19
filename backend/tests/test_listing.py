"""List endpoint: filters, search, sorting, keyset pagination, and "needs my attention"."""
from datetime import timedelta

from sqlalchemy import update

from app.models import WorkItem, utcnow


def _get(world, who, qs=""):
    r = world.client().get(f"/work-items{qs}", headers=world.h(who))
    assert r.status_code == 200, r.text
    return r.json()


def _paginate(world, who, qs, limit):
    pages, cursor = [], None
    while True:
        suffix = f"&cursor={cursor}" if cursor else ""
        body = _get(world, who, f"?{qs}&limit={limit}{suffix}")
        pages.append(body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            return pages


def _spread_timestamps(world, ids):
    """Give each item a distinct updated_at/created_at (and some deliberate ties)."""
    base = utcnow() - timedelta(days=30)
    with world.db.write_session() as s:
        for n, item_id in enumerate(ids):
            ts = base + timedelta(hours=n // 2)  # pairs share a timestamp -> exercises the id tiebreaker
            s.execute(update(WorkItem).where(WorkItem.id == item_id).values(updated_at=ts, created_at=ts))
        s.commit()


def test_pagination_returns_every_item_exactly_once(world):
    ids = [world.make_item(title=f"item {n}") for n in range(23)]
    _spread_timestamps(world, ids)
    for sort in ("updated_at", "created_at", "priority", "due_at"):
        for order in ("asc", "desc"):
            pages = _paginate(world, "alice", f"sort={sort}&order={order}", limit=5)
            flat = [i["id"] for page in pages for i in page]
            assert sorted(flat) == sorted(ids), (sort, order)
            assert len(flat) == len(set(flat)), (sort, order)
            assert all(len(p) == 5 for p in pages[:-1])


def test_pagination_respects_sort_order(world):
    ids = [world.make_item(title=f"item {n}") for n in range(12)]
    _spread_timestamps(world, ids)
    pages = _paginate(world, "alice", "sort=updated_at&order=desc", limit=4)
    stamps = [i["updated_at"] for page in pages for i in page]
    assert stamps == sorted(stamps, reverse=True)


def test_total_is_reported_but_only_one_page_is_loaded(world):
    for n in range(30):
        world.make_item(title=f"item {n}")
    body = _get(world, "alice", "?limit=10")
    assert body["total"] == 30 and len(body["items"]) == 10 and body["next_cursor"]


def test_limit_is_capped(world):
    for n in range(3):
        world.make_item()
    r = world.client().get("/work-items?limit=100000", headers=world.h("alice"))
    assert r.status_code == 422  # the server refuses to hand out the whole dataset


def test_filters(world):
    world.make_item(title="a", status="open", priority="P1")
    world.make_item(title="b", status="blocked", priority="P2", assignee="bob")
    world.make_item(title="c", status="blocked", priority="P1", assignee="alice")
    world.make_item(title="d", status="closed", priority="P4")
    titles = lambda qs, who="alice": sorted(i["title"] for i in _get(world, who, qs)["items"])  # noqa: E731

    assert titles("?status=blocked") == ["b", "c"]
    assert titles("?status=blocked&status=closed") == ["b", "c", "d"]
    assert titles("?priority=P1") == ["a", "c"]
    assert titles("?status=blocked&priority=P1") == ["c"]
    assert titles("?assignee=me") == ["c"]
    assert titles("?assignee=me", who="bob") == ["b"]
    assert titles("?assignee=none") == ["a", "d"]
    assert titles(f"?assignee={world.users['bob']}") == ["b"]
    assert titles(f"?team_id={world.teams['Ops']}") == ["a", "b", "c", "d"]


def test_title_search_is_case_insensitive_and_escapes_wildcards(world):
    world.make_item(title="Payment gateway outage")
    world.make_item(title="Rotate payment keys")
    world.make_item(title="100% uptime review")
    world.make_item(title="Unrelated")
    titles = lambda q: sorted(i["title"] for i in _get(world, "alice", f"?q={q}")["items"])  # noqa: E731
    assert titles("PAYMENT") == ["Payment gateway outage", "Rotate payment keys"]
    assert titles("%25") == ["100% uptime review"]  # a literal %, not "match everything"
    assert titles("_") == []                         # a literal underscore, not "any one char"
    assert titles("nomatch") == []


def test_bad_params_are_client_errors(world):
    c, h = world.client(), world.h("alice")
    assert c.get("/work-items?sort=password", headers=h).status_code == 422
    assert c.get("/work-items?order=sideways", headers=h).status_code == 422
    assert c.get("/work-items?status=nope", headers=h).status_code == 422
    assert c.get("/work-items?cursor=garbage", headers=h).status_code == 400
    page = _get(world, "alice", "?sort=priority&limit=1")
    world.make_item()
    world.make_item()
    mismatch = c.get(f"/work-items?sort=updated_at&limit=1&cursor={page['next_cursor'] or 'x'}", headers=h)
    assert mismatch.status_code == 400


def test_needs_my_attention(world):
    mine = world.make_item(title="assigned to alice", assignee="alice", priority="P3")
    awaiting = world.make_item(title="needs alice approval", type="approval", creator="bob", approver="alice",
                               priority="P3")
    p1 = world.make_item(title="p1 in my team", priority="P1")
    world.make_item(title="p1 elsewhere", team="Sec", creator="dave", priority="P1")
    world.make_item(title="p3 nobody", priority="P3")
    world.make_item(title="assigned but resolved", assignee="alice", status="resolved")
    world.make_item(title="p1 but closed", priority="P1", status="closed")
    world.make_item(title="already decided", type="approval", creator="bob", approver="alice",
                    approval_status="approved")

    body = _get(world, "alice", "?needs_attention=true")
    by_title = {i["title"]: i for i in body["items"]}
    assert set(by_title) == {"assigned to alice", "needs alice approval", "p1 in my team"}
    assert by_title["assigned to alice"]["attention_reasons"] == ["assigned_to_me"]
    assert by_title["needs alice approval"]["attention_reasons"] == ["awaiting_my_approval"]
    assert by_title["p1 in my team"]["attention_reasons"] == ["p1_in_my_team"]
    assert body["total"] == 3
    assert mine and awaiting and p1


def test_event_history_is_paginated(world):
    item_id = world.make_item(assignee="bob")
    c = world.client()
    for n in range(7):
        r = c.post(f"/work-items/{item_id}/comments", headers=world.h("bob"), json={"body": f"c{n}"})
        assert r.status_code == 201
    detail = c.get(f"/work-items/{item_id}?events_limit=3", headers=world.h("bob")).json()
    assert len(detail["events"]["items"]) == 3 and detail["events"]["next_cursor"]
    seen = [e["id"] for e in detail["events"]["items"]]
    cursor = detail["events"]["next_cursor"]
    while cursor:
        page = c.get(f"/work-items/{item_id}/events?limit=3&cursor={cursor}", headers=world.h("bob")).json()
        seen += [e["id"] for e in page["items"]]
        cursor = page["next_cursor"]
    assert len(seen) == 7 and seen == sorted(seen, reverse=True)


def test_comments_are_paginated_and_visible_only_to_viewers(world):
    item_id = world.make_item()
    c = world.client()
    for n in range(5):
        c.post(f"/work-items/{item_id}/comments", headers=world.h("alice"), json={"body": f"note {n}"})
    page = c.get(f"/work-items/{item_id}/comments?limit=2", headers=world.h("bob")).json()
    assert [x["body"] for x in page["items"]] == ["note 4", "note 3"] and page["next_cursor"]
    assert c.get(f"/work-items/{item_id}/comments", headers=world.h("erin")).status_code == 404
