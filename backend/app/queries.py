"""Read side: filtered, searched, sorted, keyset-paginated work-item listing and cursor helpers."""
import base64
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from .auth import Principal
from .errors import ApiError
from .models import Approval, Event, UTCDateTime, WorkItem
from .permissions import visible_clause
from .schemas import ItemOut
from .serializers import items_out
from .workflow import PRIORITIES, STATUSES, TERMINAL

MAX_LIMIT = 100
FAR_FUTURE = datetime(9999, 12, 31, tzinfo=timezone.utc)

# sort key -> (SQL expression used for ORDER BY and keyset comparison, python value extractor)
_SORTS = {
    "updated_at": (WorkItem.updated_at, lambda i: i.updated_at.isoformat()),
    "created_at": (WorkItem.created_at, lambda i: i.created_at.isoformat()),
    "priority": (WorkItem.priority, lambda i: i.priority),
    # NULL due dates sort last in ascending order.
    "due_at": (
        func.coalesce(WorkItem.due_at, literal(FAR_FUTURE, UTCDateTime()), type_=UTCDateTime()),
        lambda i: (i.due_at or FAR_FUTURE).isoformat(),
    ),
}


def encode_cursor(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except Exception:  # noqa: BLE001 - any malformed cursor is a client error
        raise ApiError(400, "bad_cursor", "Malformed pagination cursor.")


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def list_items(
    db: Session,
    p: Principal,
    *,
    statuses: Optional[list[str]] = None,
    priorities: Optional[list[str]] = None,
    team_id: Optional[int] = None,
    assignee: Optional[str] = None,  # "me" | "none" | "<user id>"
    needs_attention: bool = False,
    q: Optional[str] = None,
    sort: str = "updated_at",
    order: str = "desc",
    limit: int = 25,
    cursor: Optional[str] = None,
) -> tuple[list[ItemOut], Optional[str], int]:
    if sort not in _SORTS:
        raise ApiError(422, "bad_sort", f"sort must be one of {sorted(_SORTS)}.")
    if order not in ("asc", "desc"):
        raise ApiError(422, "bad_order", "order must be 'asc' or 'desc'.")
    limit = max(1, min(limit, MAX_LIMIT))

    where = [visible_clause(p)]
    if statuses:
        bad = [s for s in statuses if s not in STATUSES]
        if bad:
            raise ApiError(422, "bad_status", f"Unknown status: {bad[0]}.")
        where.append(WorkItem.status.in_(statuses))
    if priorities:
        bad = [s for s in priorities if s not in PRIORITIES]
        if bad:
            raise ApiError(422, "bad_priority", f"Unknown priority: {bad[0]}.")
        where.append(WorkItem.priority.in_(priorities))
    if team_id is not None:
        where.append(WorkItem.owning_team_id == team_id)
    if assignee:
        if assignee == "me":
            where.append(WorkItem.assignee_id == p.id)
        elif assignee == "none":
            where.append(WorkItem.assignee_id.is_(None))
        elif assignee.isdigit():
            where.append(WorkItem.assignee_id == int(assignee))
        else:
            raise ApiError(422, "bad_assignee", "assignee must be 'me', 'none' or a user id.")
    if q and q.strip():
        where.append(WorkItem.title.ilike(f"%{_escape_like(q.strip())}%", escape="\\"))
    if needs_attention:
        open_ = WorkItem.status.notin_(TERMINAL)
        awaiting_me = exists().where(
            Approval.work_item_id == WorkItem.id, Approval.approver_id == p.id, Approval.status == "pending"
        )
        where.append(
            and_(
                open_,
                or_(
                    WorkItem.assignee_id == p.id,
                    awaiting_me,
                    and_(WorkItem.priority == "P1", WorkItem.owning_team_id.in_(p.team_ids or [-1])),
                ),
            )
        )

    total = db.execute(select(func.count()).select_from(WorkItem).where(*where)).scalar_one()

    expr, extract = _SORTS[sort]
    desc = order == "desc"
    page_where = list(where)
    if cursor:
        c = decode_cursor(cursor)
        if c.get("sort") != sort or c.get("order") != order or "v" not in c or "id" not in c:
            raise ApiError(400, "bad_cursor", "Cursor does not match the requested sort.")
        v = c["v"]
        if sort in ("updated_at", "created_at", "due_at"):
            try:
                v = datetime.fromisoformat(v)
            except (TypeError, ValueError):
                raise ApiError(400, "bad_cursor", "Malformed pagination cursor.")
        cmp = (lambda a, b: a < b) if desc else (lambda a, b: a > b)
        page_where.append(or_(cmp(expr, v), and_(expr == v, cmp(WorkItem.id, c["id"]))))

    order_by = (expr.desc(), WorkItem.id.desc()) if desc else (expr.asc(), WorkItem.id.asc())
    rows = db.execute(select(WorkItem).where(*page_where).order_by(*order_by).limit(limit + 1)).scalars().all()

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor({"sort": sort, "order": order, "v": extract(last), "id": last.id})
    return items_out(db, p, list(rows)), next_cursor, total


def list_events(
    db: Session, item_id: int, *, limit: int = 20, cursor: Optional[str] = None
) -> tuple[list[Event], Optional[str]]:
    """Newest first. Keyset on the autoincrement id, which is monotonic for an append-only table."""
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(Event).where(Event.work_item_id == item_id)
    if cursor:
        c = decode_cursor(cursor)
        if not isinstance(c.get("id"), int):
            raise ApiError(400, "bad_cursor", "Malformed pagination cursor.")
        stmt = stmt.where(Event.id < c["id"])
    rows = db.execute(stmt.order_by(Event.id.desc()).limit(limit + 1)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor({"id": rows[-1].id})
    return list(rows), next_cursor
