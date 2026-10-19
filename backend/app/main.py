import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import LoginThrottle
from .config import Settings
from .db import Database
from .errors import ApiError
from .outbox import OutboxWorker
from .routers import api

log = logging.getLogger("ops_hub")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.database_url)
    db.create_all()
    worker = OutboxWorker(
        db,
        settings.worker_poll_seconds,
        max_attempts=settings.outbox_max_attempts,
        lease_seconds=settings.outbox_lease_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.run_worker:
            worker.start()
        try:
            yield
        finally:
            if settings.run_worker:
                worker.stop()
            db.dispose()

    app = FastAPI(title="Operations Hub API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.worker = worker
    app.state.login_throttle = LoginThrottle(settings.login_max_failures, settings.login_window_seconds)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Idempotent-Replay", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Correlate every request: honour an inbound X-Request-ID (or make one), log it, echo it back."""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "%s %s -> %s %.0fms rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )
        return response

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()
        ]
        first = fields[0] if fields else {"field": "", "message": "Invalid request"}
        label = f"{first['field']}: " if first["field"] else ""
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": f"{label}{first['message']}", "fields": fields}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": str(exc.detail)}})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None)
        log.exception("unhandled error rid=%s", rid, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Unexpected server error.", "request_id": rid}},
        )

    @app.get("/health", tags=["meta"])
    def health():
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/ready", tags=["meta"])
    def ready():
        """Readiness: the database answers."""
        try:
            with db.read_session() as s:
                s.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    app.include_router(api.router)
    return app
