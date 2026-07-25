"""Sync orchestration: scheduled + manual pulls through registered importers."""
from __future__ import annotations

import json
import logging
import threading

from .. import db
from ..connectors import EXPORTERS, IMPORTERS

log = logging.getLogger(__name__)
_sync_lock = threading.Lock()


def run_sync(connector: str = "hevy", full: bool = False) -> dict:
    if connector not in IMPORTERS:
        raise ValueError(f"unknown importer: {connector}")
    if not _sync_lock.acquire(blocking=False):
        return {"status": "already_running"}
    run_id = db.start_run(connector)
    try:
        if full:  # re-baseline: forget the watermark, pull everything again
            db.put_setting(f"{connector}_last_sync", "")
        summary = IMPORTERS[connector]().pull()
        db.finish_run(run_id, "success", json.dumps(summary))
        return {"status": "success", "summary": summary}
    except Exception as exc:  # noqa: BLE001 — report, don't crash the scheduler
        log.exception("sync failed")
        db.finish_run(run_id, "error", str(exc))
        return {"status": "error", "error": str(exc)}
    finally:
        _sync_lock.release()


def run_export(exporter: str = "wger", force: bool = False) -> dict:
    """Push through a registered exporter; audit report into sync_runs."""
    if exporter not in EXPORTERS:
        raise ValueError(f"unknown exporter: {exporter}")
    if not _sync_lock.acquire(blocking=False):
        return {"status": "already_running"}
    run_id = db.start_run(exporter)
    try:
        if force:  # re-baseline: everything ref'd turns "changed"
            db.clear_export_state(exporter)
        report = EXPORTERS[exporter]().push()
        status = "success" if not report.get("errors") else "partial"
        db.finish_run(run_id, status, json.dumps(report, ensure_ascii=False))
        return {"status": status, "report": report}
    except Exception as exc:  # noqa: BLE001 — report, don't crash the scheduler
        log.exception("export failed")
        db.finish_run(run_id, "error", str(exc))
        return {"status": "error", "error": str(exc)}
    finally:
        _sync_lock.release()


def scheduled_sync() -> None:
    if not db.get_setting("hevy_api_key"):
        log.info("skipping scheduled sync: hevy_api_key not configured")
        return
    run_sync("hevy")
    # fully automated bridge: push straight to wger after every pull
    if EXPORTERS["wger"]().configured():
        run_export("wger")
    else:
        log.info("skipping scheduled export: wger not configured")
