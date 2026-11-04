import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import api, pipeline
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.providers import get_providers

logger = logging.getLogger(__name__)
_stop = threading.Event()


def tick() -> None:
    """One round of work: send due deliveries, then fire due nudges. Safe to run twice."""
    with SessionLocal() as db:
        pipeline.run_nudges(db)
        pipeline.deliver(db, get_providers())


def _loop() -> None:
    interval = get_settings().WORKER_INTERVAL_SECONDS
    while not _stop.wait(interval):
        try:
            tick()
        except Exception:
            logger.exception("worker round failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)       # for a quick start; for production run Alembic migrations (see README)
    worker = None
    if get_settings().WORKER_ENABLED:
        _stop.clear()
        worker = threading.Thread(target=_loop, name="notification-worker", daemon=True)
        worker.start()
    yield
    _stop.set()
    if worker:
        worker.join(timeout=5)


app = FastAPI(title="Notification service", version="1.0.0", lifespan=lifespan,
              description="Notifications and nudges for every module. Send events with POST /v1/events; read a person's inbox with GET /v1/users/{id}/notifications.")
app.include_router(api.router)


@app.get("/health", tags=["ops"], summary="Is the service up")
def health():
    return {"status": "ok"}
