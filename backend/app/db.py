"""SQLite document store for FitIR (ADR-STR-002).

Documents are stored as JSON bodies at their original fitir version and
lazily migrated on read (ir/migrate.py).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from . import config
from .ir.migrate import upgrade

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  kind TEXT NOT NULL, id TEXT NOT NULL, fitir_version TEXT NOT NULL,
  body TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(kind, id));
CREATE TABLE IF NOT EXISTS refs(
  system TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
  ir_kind TEXT NOT NULL, ir_id TEXT NOT NULL,
  PRIMARY KEY(system, kind, external_id));
CREATE INDEX IF NOT EXISTS refs_by_ir ON refs(ir_kind, ir_id);
CREATE TABLE IF NOT EXISTS raw_archive(
  system TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
  fetched_at TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(system, kind, external_id));
CREATE TABLE IF NOT EXISTS sync_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, connector TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init() -> None:
    global _conn
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        _conn.executescript(_SCHEMA)
        for key, value in config.ENV_SETTING_DEFAULTS.items():
            if value:
                _conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                    (key, value),
                )
        _conn.commit()


def _db() -> sqlite3.Connection:
    assert _conn is not None, "db.init() not called"
    return _conn


# --- documents -------------------------------------------------------------

def put_doc(body: dict[str, Any]) -> None:
    with _lock:
        _db().execute(
            "INSERT OR REPLACE INTO documents(kind, id, fitir_version, body, updated_at)"
            " VALUES(?,?,?,?,?)",
            (body["kind"], body["id"], body.get("fitir", "1.0"),
             json.dumps(body, ensure_ascii=False), body.get("updated_at") or now_iso()),
        )
        _db().commit()


def get_doc(kind: str, doc_id: str) -> Optional[dict[str, Any]]:
    row = _db().execute(
        "SELECT body FROM documents WHERE kind=? AND id=?", (kind, doc_id)
    ).fetchone()
    return upgrade(json.loads(row["body"])) if row else None


def list_docs(kind: str, include_deleted: bool = False) -> list[dict[str, Any]]:
    rows = _db().execute(
        "SELECT body FROM documents WHERE kind=? ORDER BY updated_at DESC", (kind,)
    ).fetchall()
    docs = [upgrade(json.loads(r["body"])) for r in rows]
    if not include_deleted:
        docs = [d for d in docs if not d.get("deleted_at")]
    return docs


def count_docs() -> dict[str, int]:
    rows = _db().execute(
        "SELECT kind, COUNT(*) AS n FROM documents GROUP BY kind"
    ).fetchall()
    return {r["kind"]: r["n"] for r in rows}


# --- refs ------------------------------------------------------------------

def put_ref(system: str, kind: str, external_id: str, ir_kind: str, ir_id: str) -> None:
    with _lock:
        _db().execute(
            "INSERT OR REPLACE INTO refs(system, kind, external_id, ir_kind, ir_id)"
            " VALUES(?,?,?,?,?)",
            (system, kind, str(external_id), ir_kind, ir_id),
        )
        _db().commit()


def find_ref(system: str, kind: str, external_id: str) -> Optional[str]:
    row = _db().execute(
        "SELECT ir_id FROM refs WHERE system=? AND kind=? AND external_id=?",
        (system, kind, str(external_id)),
    ).fetchone()
    return row["ir_id"] if row else None


def delete_ref(system: str, kind: str, ir_id: str) -> None:
    with _lock:
        _db().execute(
            "DELETE FROM refs WHERE system=? AND kind=? AND ir_id=?",
            (system, kind, ir_id),
        )
        _db().commit()


def find_external(system: str, kind: str, ir_id: str) -> Optional[str]:
    row = _db().execute(
        "SELECT external_id FROM refs WHERE system=? AND kind=? AND ir_id=?",
        (system, kind, ir_id),
    ).fetchone()
    return row["external_id"] if row else None


# --- raw archive (provenance only, never a read path) ----------------------

def archive_raw(system: str, kind: str, external_id: str, payload: Any) -> None:
    with _lock:
        _db().execute(
            "INSERT OR REPLACE INTO raw_archive(system, kind, external_id, fetched_at, payload)"
            " VALUES(?,?,?,?,?)",
            (system, kind, str(external_id), now_iso(),
             json.dumps(payload, ensure_ascii=False)),
        )
        _db().commit()


# --- sync runs -------------------------------------------------------------

def start_run(connector: str) -> int:
    with _lock:
        cur = _db().execute(
            "INSERT INTO sync_runs(connector, started_at, status) VALUES(?,?,'running')",
            (connector, now_iso()),
        )
        _db().commit()
        return int(cur.lastrowid)


def finish_run(run_id: int, status: str, detail: str) -> None:
    with _lock:
        _db().execute(
            "UPDATE sync_runs SET finished_at=?, status=?, detail=? WHERE id=?",
            (now_iso(), status, detail, run_id),
        )
        _db().commit()


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    rows = _db().execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- settings --------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    row = _db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def put_setting(key: str, value: str) -> None:
    with _lock:
        _db().execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value)
        )
        _db().commit()


def all_settings() -> dict[str, str]:
    rows = _db().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}
