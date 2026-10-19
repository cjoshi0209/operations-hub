import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'operations_hub.db').as_posix()}"
        )
    )
    run_worker: bool = field(default_factory=lambda: os.environ.get("RUN_WORKER", "1") == "1")
    worker_poll_seconds: float = 1.0
    outbox_max_attempts: int = 5
    outbox_lease_seconds: int = 60
    idempotency_ttl_hours: int = 24
    token_ttl_hours: int = 12
    login_max_failures: int = 8
    login_window_seconds: int = 300
    # PBKDF2 cost; tests lower it so the suite stays fast.
    password_iterations: int = field(default_factory=lambda: int(os.environ.get("PASSWORD_ITERATIONS", "120000")))
    cors_origins: list[str] = field(
        default_factory=lambda: os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    )
