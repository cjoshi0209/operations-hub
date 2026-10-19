"""Standalone outbox worker:  python -m app.worker   (the API also runs one in-process unless RUN_WORKER=0)."""

import logging
import signal

from .config import Settings
from .db import Database
from .outbox import OutboxWorker


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    db = Database(settings.database_url)
    worker = OutboxWorker(
        db,
        settings.worker_poll_seconds,
        max_attempts=settings.outbox_max_attempts,
        lease_seconds=settings.outbox_lease_seconds,
    )
    signal.signal(signal.SIGINT, lambda *_: worker._stop.set())
    signal.signal(signal.SIGTERM, lambda *_: worker._stop.set())
    logging.info("outbox worker started")
    worker.run_forever()


if __name__ == "__main__":
    main()
