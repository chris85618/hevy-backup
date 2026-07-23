from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, db
from ..connectors import EXPORTERS, IMPORTERS
from ..connectors.wger import WgerExporter
from ..ir.schema import DOC_TYPES, FITIR_VERSION
from ..scheduler import apply_cron
from ..services.sync import run_export, run_sync

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "fitir": FITIR_VERSION}


@router.get("/status")
def status() -> dict[str, Any]:
    settings = db.all_settings()
    return {
        "fitir": FITIR_VERSION,
        "documents": db.count_docs(),
        "recent_runs": db.recent_runs(),
        "last_sync": settings.get("hevy_last_sync", ""),
        "connectors": {"importers": list(IMPORTERS), "exporters": list(EXPORTERS)},
        "configured": {
            "hevy": bool(settings.get("hevy_api_key")),
            "wger": bool(settings.get("wger_base_url") and settings.get("wger_api_key")),
        },
    }


@router.post("/sync/run")
def sync_now(connector: str = "hevy") -> dict[str, Any]:
    if connector not in IMPORTERS:
        raise HTTPException(404, f"unknown importer: {connector}")
    return run_sync(connector)


@router.get("/documents/{kind}")
def list_documents(kind: str, page: int = 1, page_size: int = 20,
                   q: Optional[str] = None) -> dict[str, Any]:
    if kind not in DOC_TYPES:
        raise HTTPException(404, f"unknown kind: {kind}")
    docs = db.list_docs(kind)
    if q:
        needle = q.lower()
        docs = [d for d in docs
                if needle in str(d.get("name", "")).lower()
                or needle in str(d.get("title", "")).lower()
                or needle in str(d.get("metric_key", "")).lower()]
    total = len(docs)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size,
            "items": docs[start:start + page_size]}


@router.get("/documents/{kind}/{doc_id}")
def get_document(kind: str, doc_id: str) -> dict[str, Any]:
    if kind not in DOC_TYPES:
        raise HTTPException(404, f"unknown kind: {kind}")
    doc = db.get_doc(kind, doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return doc


@router.get("/export/ir")
def export_ir_bundle() -> dict[str, Any]:
    """Full FitIR bundle — the clean egress path (architecture.md §1)."""
    return {
        "fitir": FITIR_VERSION,
        "exported_at": db.now_iso(),
        "documents": {kind: db.list_docs(kind, include_deleted=True)
                      for kind in DOC_TYPES},
    }


@router.get("/export/wger/preview")
def wger_preview() -> dict[str, Any]:
    return WgerExporter().preview()


@router.post("/export/wger")
def wger_push() -> dict[str, Any]:
    result = run_export("wger")
    if result["status"] == "error":
        raise HTTPException(400, result["error"])
    if result["status"] == "already_running":
        raise HTTPException(409, "a sync/export run is already in progress")
    return result["report"]


class ExerciseMapping(BaseModel):
    ir_id: str
    wger_exercise_id: int


@router.post("/mappings/exercise")
def map_exercise(mapping: ExerciseMapping) -> dict[str, str]:
    if db.get_doc("exercise", mapping.ir_id) is None:
        raise HTTPException(404, f"unknown exercise: {mapping.ir_id}")
    db.put_ref("wger", "exercise", str(mapping.wger_exercise_id),
               "exercise", mapping.ir_id)
    return {"status": "mapped"}


class SettingsUpdate(BaseModel):
    hevy_api_key: Optional[str] = None
    wger_base_url: Optional[str] = None
    wger_api_key: Optional[str] = None
    sync_cron: Optional[str] = None


@router.get("/settings")
def get_settings() -> dict[str, str]:
    settings = db.all_settings()
    result = {}
    for key in config.ENV_SETTING_DEFAULTS:
        value = settings.get(key, "")
        if key in config.SECRET_SETTINGS and value:
            value = value[:4] + "****"
        result[key] = value
    return result


@router.put("/settings")
def put_settings(update: SettingsUpdate) -> dict[str, str]:
    data = update.model_dump(exclude_none=True)
    cron = data.get("sync_cron")
    if cron:
        try:
            apply_cron(cron)  # validates the expression and reschedules live
        except ValueError as exc:
            raise HTTPException(400, f"invalid crontab expression: {exc}")
    for key, value in data.items():
        if value and not value.endswith("****"):
            db.put_setting(key, value)
    return get_settings()
