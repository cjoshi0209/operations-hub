from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import queries, services
from ..auth import (
    Principal,
    get_read_db,
    get_write_db,
    hash_password,
    issue_token,
    read_principal,
    verify_password,
    write_principal,
)
from ..errors import ApiError
from ..idempotency import run_idempotent
from ..models import Comment, Notification, Team, TeamMembership, User
from ..schemas import (
    ApprovalDecisionIn,
    ApprovalRequestIn,
    CommentIn,
    CommentPage,
    EventPage,
    ItemCreate,
    ItemDetail,
    ItemPage,
    ItemPatch,
    LoginIn,
    LoginOut,
    MeOut,
    NotificationOut,
    NotificationPage,
    TeamOut,
    TransitionIn,
)
from ..serializers import comment_out, event_out, single_item_out, user_ref

router = APIRouter()
IdemKey = Header(None, alias="Idempotency-Key", description="Makes the mutation safe to retry (24h).")


# ---------- auth ----------
@router.post("/auth/login", response_model=LoginOut, tags=["auth"])
def login(body: LoginIn, request: Request, db: Session = Depends(get_write_db)):
    settings = request.app.state.settings
    user = db.execute(select(User).where(User.email == body.email.strip().lower())).scalar_one_or_none()
    # Verify even for unknown emails so response time does not reveal which accounts exist.
    stored = user.password_hash if user else hash_password("x", settings.password_iterations, salt=b"0" * 16)
    ok = verify_password(body.password, stored)
    if not (user and ok and user.is_active):
        raise ApiError(401, "invalid_credentials", "Wrong email or password.")
    token, row = issue_token(db, user, settings.token_ttl_hours)
    db.commit()
    return LoginOut(token=token, expires_at=row.expires_at, user=user_ref(user))


@router.get("/auth/me", response_model=MeOut, tags=["auth"])
def me(p: Principal = Depends(read_principal), db: Session = Depends(get_read_db)):
    rows = db.execute(
        select(TeamMembership, Team).join(Team, Team.id == TeamMembership.team_id).where(TeamMembership.user_id == p.id)
    ).all()
    return MeOut(
        user=user_ref(p.user),
        email=p.user.email,
        memberships=[{"team": {"id": t.id, "name": t.name}, "role": m.role} for m, t in rows],
    )


@router.get("/teams", response_model=list[TeamOut], tags=["teams"])
def teams(p: Principal = Depends(read_principal), db: Session = Depends(get_read_db)):
    """Team directory, used by pickers (assignee, approver, team filter)."""
    out: dict[int, TeamOut] = {}
    for t in db.execute(select(Team).order_by(Team.name)).scalars():
        out[t.id] = TeamOut(id=t.id, name=t.name, members=[])
    rows = db.execute(
        select(TeamMembership.team_id, User).join(User, User.id == TeamMembership.user_id).order_by(User.name)
    ).all()
    for team_id, u in rows:
        out[team_id].members.append(user_ref(u))
    return list(out.values())


# ---------- work items: reads ----------
@router.get("/work-items", response_model=ItemPage, tags=["work-items"])
def list_work_items(
    status: Optional[list[str]] = Query(None),
    priority: Optional[list[str]] = Query(None),
    team_id: Optional[int] = None,
    assignee: Optional[str] = Query(None, description="'me', 'none' or a user id"),
    needs_attention: bool = False,
    q: Optional[str] = Query(None, max_length=200, description="case-insensitive title search"),
    sort: str = "updated_at",
    order: str = "desc",
    limit: int = Query(25, ge=1, le=queries.MAX_LIMIT),
    cursor: Optional[str] = None,
    p: Principal = Depends(read_principal),
    db: Session = Depends(get_read_db),
):
    items, next_cursor, total = queries.list_items(
        db, p, statuses=status, priorities=priority, team_id=team_id, assignee=assignee,
        needs_attention=needs_attention, q=q, sort=sort, order=order, limit=limit, cursor=cursor,
    )
    return ItemPage(items=items, next_cursor=next_cursor, total=total)


@router.get("/work-items/{item_id}", response_model=ItemDetail, tags=["work-items"])
def get_work_item(
    item_id: int,
    events_limit: int = Query(20, ge=1, le=queries.MAX_LIMIT),
    p: Principal = Depends(read_principal),
    db: Session = Depends(get_read_db),
):
    item = services.get_visible_item(db, p, item_id)
    events, next_cursor = queries.list_events(db, item.id, limit=events_limit)
    base = single_item_out(db, p, item)
    return ItemDetail(**base.model_dump(), events=EventPage(items=[event_out(e) for e in events], next_cursor=next_cursor))


@router.get("/work-items/{item_id}/events", response_model=EventPage, tags=["work-items"])
def get_events(
    item_id: int,
    limit: int = Query(20, ge=1, le=queries.MAX_LIMIT),
    cursor: Optional[str] = None,
    p: Principal = Depends(read_principal),
    db: Session = Depends(get_read_db),
):
    services.get_visible_item(db, p, item_id)
    events, next_cursor = queries.list_events(db, item_id, limit=limit, cursor=cursor)
    return EventPage(items=[event_out(e) for e in events], next_cursor=next_cursor)


@router.get("/work-items/{item_id}/comments", response_model=CommentPage, tags=["comments"])
def get_comments(
    item_id: int,
    limit: int = Query(20, ge=1, le=queries.MAX_LIMIT),
    cursor: Optional[str] = None,
    p: Principal = Depends(read_principal),
    db: Session = Depends(get_read_db),
):
    services.get_visible_item(db, p, item_id)
    stmt = select(Comment).where(Comment.work_item_id == item_id)
    if cursor:
        c = queries.decode_cursor(cursor)
        if not isinstance(c.get("id"), int):
            raise ApiError(400, "bad_cursor", "Malformed pagination cursor.")
        stmt = stmt.where(Comment.id < c["id"])
    rows = db.execute(stmt.order_by(Comment.id.desc()).limit(limit + 1)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = queries.encode_cursor({"id": rows[-1].id})
    return CommentPage(items=[comment_out(c) for c in rows], next_cursor=next_cursor)


# ---------- work items: writes (all idempotent-capable) ----------
def _idem(request: Request, db: Session, p: Principal, key: Optional[str], payload, action):
    return run_idempotent(
        db,
        user_id=p.id,
        key=key,
        method=request.method,
        path=request.url.path,
        payload=payload,
        ttl_hours=request.app.state.settings.idempotency_ttl_hours,
        action=action,
    )


@router.post("/work-items", status_code=201, response_model=None, tags=["work-items"])
def create_work_item(
    body: ItemCreate,
    request: Request,
    idempotency_key: Optional[str] = IdemKey,
    p: Principal = Depends(write_principal),
    db: Session = Depends(get_write_db),
):
    return _idem(request, db, p, idempotency_key, body.model_dump(mode="json"),
                 lambda: (201, services.create_item(db, p, body).model_dump(mode="json")))


@router.patch("/work-items/{item_id}", response_model=None, tags=["work-items"])
def patch_work_item(
    item_id: int,
    body: ItemPatch,
    request: Request,
    idempotency_key: Optional[str] = IdemKey,
    p: Principal = Depends(write_principal),
    db: Session = Depends(get_write_db),
):
    payload = body.model_dump(mode="json", exclude_unset=True)
    return _idem(request, db, p, idempotency_key, payload,
                 lambda: (200, services.update_item(db, p, item_id, body).model_dump(mode="json")))


@router.post("/work-items/{item_id}/claim", response_model=None, tags=["work-items"])
def claim_work_item(
    item_id: int,
    request: Request,
    idempotency_key: Optional[str] = IdemKey,
    p: Principal = Depends(write_principal),
    db: Session = Depends(get_write_db),
):
    return _idem(request, db, p, idempotency_key, None,
                 lambda: (200, services.claim_item(db, p, item_id).model_dump(mode="json")))


@router.post("/work-items/{item_id}/transitions", response_model=None, tags=["work-items"])
def transition_work_item(
    item_id: int,
    body: TransitionIn,
    request: Request,
    idempotency_key: Optional[str] = IdemKey,
    p: Principal = Depends(write_principal),
    db: Session = Depends(get_write_db),
):
    return _idem(request, db, p, idempotency_key, body.model_dump(mode="json"),
                 lambda: (200, services.transition_item(db, p, item_id, body).model_dump(mode="json")))


@router.post("/work-items/{item_id}/comments", status_code=201, response_model=None, tags=["comments"])
def post_comment(
    item_id: int,
    body: CommentIn,
    request: Request,
    idempotency_key: Optional[str] = IdemKey,
    p: Principal = Depends(write_principal),
    db: Session = Depends(get_write_db),
):
    return _idem(request, db, p, idempotency_key, body.model_dump(mode="json"),
                 lambda: (201, comment_out(services.add_comment(db, p, item_id, body.body)).model_dump(mode="json")))


@router.post("/work-items/{item_id}/approval", status_code=201, response_model=None, tags=["approvals"])
def request_approval(
    item_id: int,
    body: ApprovalRequestIn,
    request: Request,
    idempotency_key: Optional[str] = IdemKey,
    p: Principal = Depends(write_principal),
    db: Session = Depends(get_write_db),
):
    return _idem(request, db, p, idempotency_key, body.model_dump(mode="json"),
                 lambda: (201, services.request_approval(db, p, item_id, body.approver_id).model_dump(mode="json")))


@router.post("/work-items/{item_id}/approval/decision", response_model=None, tags=["approvals"])
def decide_approval(
    item_id: int,
    body: ApprovalDecisionIn,
    request: Request,
    idempotency_key: Optional[str] = IdemKey,
    p: Principal = Depends(write_principal),
    db: Session = Depends(get_write_db),
):
    return _idem(request, db, p, idempotency_key, body.model_dump(mode="json"),
                 lambda: (200, services.decide_approval(db, p, item_id, body.decision, body.note).model_dump(mode="json")))


# ---------- notifications ----------
@router.get("/notifications", response_model=NotificationPage, tags=["notifications"])
def list_notifications(
    limit: int = Query(20, ge=1, le=queries.MAX_LIMIT),
    cursor: Optional[str] = None,
    p: Principal = Depends(read_principal),
    db: Session = Depends(get_read_db),
):
    stmt = select(Notification).where(Notification.user_id == p.id)
    if cursor:
        c = queries.decode_cursor(cursor)
        if not isinstance(c.get("id"), int):
            raise ApiError(400, "bad_cursor", "Malformed pagination cursor.")
        stmt = stmt.where(Notification.id < c["id"])
    rows = db.execute(stmt.order_by(Notification.id.desc()).limit(limit + 1)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = queries.encode_cursor({"id": rows[-1].id})
    return NotificationPage(
        items=[
            NotificationOut(id=n.id, work_item_id=n.work_item_id, message=n.message, created_at=n.created_at)
            for n in rows
        ],
        next_cursor=next_cursor,
    )
