"""wger exporter: lowers FitIR into wger's normalized write model.

session      -> POST /workoutsession/ + one /workoutlog/ per set (R1)
body-metric  -> weight goes to /weightentry/; other metric_keys go to
                auto-created /measurement-category/ + /measurement/ (R9)

Exercise resolution: FitIR exercise -> wger exercise id via refs; unresolved
names are looked up with /exercise/search/ and can be mapped manually through
the GUI (POST /api/mappings/exercise).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .. import db
from ..ir.schema import rpe_to_rir

log = logging.getLogger(__name__)

SYSTEM = "wger"


class WgerExporter:
    name = SYSTEM

    def __init__(self) -> None:
        self.base_url = db.get_setting("wger_base_url").rstrip("/")
        self.api_key = db.get_setting("wger_api_key")

    def _client(self) -> httpx.Client:
        if not self.base_url or not self.api_key:
            raise RuntimeError("wger_base_url / wger_api_key is not configured")
        return httpx.Client(
            base_url=f"{self.base_url}/api/v2",
            headers={"Authorization": f"Token {self.api_key}"},
            timeout=30,
        )

    # --- exercise resolution ----------------------------------------------

    def resolve_exercise(self, client: Optional[httpx.Client],
                         exercise_doc: dict[str, Any]) -> Optional[str]:
        ir_id = exercise_doc["id"]
        mapped = db.find_external(SYSTEM, "exercise", ir_id)
        if mapped:
            return mapped
        if client is None:
            return None
        try:
            resp = client.get("/exercise/search/",
                              params={"term": exercise_doc.get("name", ""),
                                      "language": "en"})
            resp.raise_for_status()
            suggestions = resp.json().get("suggestions", [])
        except httpx.HTTPError as exc:
            log.warning("wger exercise search failed for %s: %s", ir_id, exc)
            return None
        for s in suggestions:
            data = s.get("data", {})
            if data.get("base_id") or data.get("id"):
                wger_id = str(data.get("base_id") or data.get("id"))
                db.put_ref(SYSTEM, "exercise", wger_id, "exercise", ir_id)
                return wger_id
        return None

    # --- effort ------------------------------------------------------------

    @staticmethod
    def _rir(effort: Optional[dict[str, Any]]) -> Optional[float]:
        if not effort:
            return None
        if effort["scale"] == "rir":
            return effort["value"]
        return rpe_to_rir(effort["value"])  # derived (ir-spec.md §4.4)

    # --- preview / push ----------------------------------------------------

    def _pending_sessions(self) -> list[dict[str, Any]]:
        return [s for s in db.list_docs("session")
                if not db.find_external(SYSTEM, "workoutsession", s["id"])]

    def _pending_weights(self) -> list[dict[str, Any]]:
        return [m for m in db.list_docs("body-metric")
                if m["metric_key"] == "weight"
                and not db.find_external(SYSTEM, "weightentry", m["id"])]

    def preview(self) -> dict[str, Any]:
        exercises = {e["id"]: e for e in db.list_docs("exercise")}
        client = None
        try:
            client = self._client()
        except RuntimeError:
            pass
        unresolved = []
        sessions = self._pending_sessions()
        needed = {ex["exercise_id"] for s in sessions for ex in s["exercises"]}
        for ir_id in sorted(needed):
            doc = exercises.get(ir_id)
            if doc is None or self.resolve_exercise(client, doc) is None:
                unresolved.append({"ir_id": ir_id,
                                   "name": (doc or {}).get("name", ir_id)})
        if client:
            client.close()
        return {
            "configured": client is not None,
            "pending_sessions": len(sessions),
            "pending_weight_entries": len(self._pending_weights()),
            "unresolved_exercises": unresolved,
        }

    def push(self) -> dict[str, Any]:
        exercises = {e["id"]: e for e in db.list_docs("exercise")}
        report = {"sessions_exported": 0, "logs_exported": 0,
                  "weights_exported": 0, "skipped_sessions": [],
                  "errors": []}
        with self._client() as client:
            for session in self._pending_sessions():
                try:
                    self._push_session(client, session, exercises, report)
                except httpx.HTTPError as exc:
                    report["errors"].append(f"session {session['id']}: {exc}")
            for metric in self._pending_weights():
                try:
                    resp = client.post("/weightentry/", json={
                        "date": metric["at"],
                        "weight": str(metric["quantity"]["value"]),
                    })
                    resp.raise_for_status()
                    db.put_ref(SYSTEM, "weightentry",
                               str(resp.json().get("id", metric["at"])),
                               "body-metric", metric["id"])
                    report["weights_exported"] += 1
                except httpx.HTTPError as exc:
                    report["errors"].append(f"weight {metric['id']}: {exc}")
        return report

    def _push_session(self, client: httpx.Client, session: dict[str, Any],
                      exercises: dict[str, dict[str, Any]],
                      report: dict[str, Any]) -> None:
        resolved: dict[str, str] = {}
        for ex in session["exercises"]:
            doc = exercises.get(ex["exercise_id"])
            wger_id = self.resolve_exercise(client, doc) if doc else None
            if wger_id is None:
                report["skipped_sessions"].append({
                    "id": session["id"],
                    "reason": f"unresolved exercise {ex['exercise_id']}",
                })
                return
            resolved[ex["exercise_id"]] = wger_id

        date = (session.get("started_at") or "")[:10]
        payload: dict[str, Any] = {"date": date, "notes": session.get("notes") or ""}
        if session.get("started_at") and "T" in session["started_at"]:
            payload["time_start"] = session["started_at"].split("T")[1][:8]
        if session.get("ended_at") and "T" in session["ended_at"]:
            payload["time_end"] = session["ended_at"].split("T")[1][:8]
        resp = client.post("/workoutsession/", json=payload)
        resp.raise_for_status()
        wger_session_id = resp.json().get("id")

        for ex in session["exercises"]:
            for s in ex["sets"]:
                actual = s.get("actual", {})
                log_payload: dict[str, Any] = {
                    "session": wger_session_id,
                    "exercise": int(resolved[ex["exercise_id"]]),
                    "date": session.get("started_at") or date,
                }
                if actual.get("reps") is not None:
                    log_payload["repetitions"] = str(actual["reps"])
                if actual.get("weight"):
                    log_payload["weight"] = str(actual["weight"]["value"])
                rir = self._rir(actual.get("effort"))
                if rir is not None:
                    log_payload["rir"] = str(min(9.0, round(rir * 2) / 2))
                resp = client.post("/workoutlog/", json=log_payload)
                resp.raise_for_status()
                report["logs_exported"] += 1

        db.put_ref(SYSTEM, "workoutsession", str(wger_session_id),
                   "session", session["id"])
        report["sessions_exported"] += 1
