from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .db import Database
from .errors import ApiError
from .outbox import OutboxWorker
from .routers import api


def create_app(settings: Optional[Settings] = None) -> FastAPI:
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Idempotent-Replay"],
    )

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    app.include_router(api.router)
    return app

