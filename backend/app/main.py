import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .api.routes import router
from .scheduler import DEFAULT_CRON, apply_cron, scheduler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def _legacy_interval_cron(minutes: int) -> str:
    if minutes < 1:
        return DEFAULT_CRON
    if minutes < 60:
        return f"*/{minutes} * * * *"
    if minutes % 60 == 0 and minutes // 60 <= 23:
        hours = minutes // 60
        return DEFAULT_CRON if hours == 1 else f"0 */{hours} * * *"
    return DEFAULT_CRON


def _startup_cron() -> str:
    cron = db.get_setting("sync_cron", "")
    if cron:
        return cron
    legacy = db.get_setting("sync_interval_minutes", "")
    if legacy.isdigit():
        cron = _legacy_interval_cron(int(legacy))
        log.info("migrated sync_interval_minutes=%s to sync_cron=%r", legacy, cron)
    cron = cron or DEFAULT_CRON
    db.put_setting("sync_cron", cron)
    return cron


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    cron = _startup_cron()
    try:
        apply_cron(cron)
    except ValueError:
        log.warning("invalid sync_cron %r; falling back to %r", cron, DEFAULT_CRON)
        apply_cron(DEFAULT_CRON)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="hevy-bridge", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router)
