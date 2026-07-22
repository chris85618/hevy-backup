"""Sync orchestration: scheduled + manual pulls through registered importers."""
from __future__ import annotations

import json
import logging
import threading

from .. import db
from ..connectors import IMPORTERS

log = logging.getLogger(__name__)
_sync_lock = threading.Lock()


def run_sync(connector: str = "hevy") -> dict:
    if connector not in IMPORTERS:
        raise ValueError(f"unknown importer: {connector}")
    if not _sync_lock.acquire(blocking=False):
        return {"status": "already_running"}
    run_id = db.start_run(connector)
    try:
        summary = IMPORTERS[connector]().pull()
        db.finish_run(run_id, "success", json.dumps(summary))
        return {"status": "success", "summary": summary}
    except Exception as exc:  # noqa: BLE001 — report, don't crash the scheduler
        log.exception("sync failed")
        db.finish_run(run_id, "error", str(exc))
        return {"status": "error", "error": str(exc)}
    finally:
        _sync_lock.release()


def scheduled_sync() -> None:
    if not db.get_setting("hevy_api_key"):
        log.info("skipping scheduled sync: hevy_api_key not configured")
        return
    run_sync("hevy")
