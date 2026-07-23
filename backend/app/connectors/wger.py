"""wger exporter: lowers FitIR into wger's normalized write model.

session      -> POST /workoutsession/ + one /workoutlog/ per set (R1)
body-metric  -> weight goes to /weightentry/; other metric_keys go to
                auto-created /measurement-category/ + /measurement/ (R9)

Exercise resolution runs a configurable pipeline (data/wger-mapping.yaml):
manual (refs table / GUI) -> override (yaml) -> catalog (local match against
/exercise-translation/ + /exercisealias/; /exercise/search/ is gone in
wger >= 2.7) -> create (auto-create, always succeeds). Stale refs are
re-validated before push and on workoutlog 400.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import httpx
import yaml

from .. import config, db
from ..ir.schema import rpe_to_rir

log = logging.getLogger(__name__)

SYSTEM = "wger"

MAPPING_PATH = os.path.join(os.path.dirname(config.DB_PATH), "wger-mapping.yaml")

DEFAULT_MAPPING: dict[str, Any] = {
    "version": 1,
    "overrides": {},
    "resolvers": [
        {"type": "manual"},
        {"type": "override"},
        {"type": "catalog",
         "variants": ["paren_equipment", "singularize", "token_sort", "spaceless"]},
        {"type": "create",
         "defaults": {"language": 2, "category": 10,
                      "description": "Auto-created by hevy-bridge",
                      # wger requires >= 40 chars for description_source
                      "description_source":
                          "Automatically created by hevy-bridge from"
                          " Hevy workout data export"}},
    ],
}

ALL_VARIANTS = ["paren_equipment", "singularize", "token_sort", "spaceless"]


def load_mapping() -> dict[str, Any]:
    try:
        with open(MAPPING_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if data.get("resolvers"):
            return data
        log.warning("%s has no resolvers; using defaults", MAPPING_PATH)
    except FileNotFoundError:
        pass
    except yaml.YAMLError as exc:
        log.warning("cannot parse %s (%s); using defaults", MAPPING_PATH, exc)
    return DEFAULT_MAPPING


def _name_keys(name: str, variants: list[str]) -> list[str]:
    """Normalized lookup keys for one name (no paren expansion here)."""
    words = re.sub(r"[^a-z0-9一-鿿]+", " ", name.lower()).split()
    if "singularize" in variants:
        words = [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words]
    keys = [" ".join(words)]
    if "token_sort" in variants:
        keys.append(" ".join(sorted(words)))
    if "spaceless" in variants:
        keys.append("".join(words))
    return [k for k in dict.fromkeys(keys) if k]


def _query_names(name: str, variants: list[str]) -> list[str]:
    out = [name]
    if "paren_equipment" in variants:
        m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", name)
        if m:  # "Bench Press (Barbell)" -> "Bench Press Barbell", "Bench Press"
            out += [f"{m.group(1)} {m.group(2)}", m.group(1)]
    return out


# --- resolvers -------------------------------------------------------------

class ManualResolver:
    """refs table: GUI mappings and previously cached resolutions."""

    def __init__(self, exp: "WgerExporter", cfg: dict[str, Any]) -> None:
        pass

    def resolve(self, client: Optional[httpx.Client],
                doc: dict[str, Any]) -> Optional[str]:
        return db.find_external(SYSTEM, "exercise", doc["id"])


class OverrideResolver:
    """overrides section of wger-mapping.yaml: IR id -> wger exercise id."""

    def __init__(self, exp: "WgerExporter", cfg: dict[str, Any]) -> None:
        self.overrides = exp.mapping.get("overrides") or {}

    def resolve(self, client: Optional[httpx.Client],
                doc: dict[str, Any]) -> Optional[str]:
        wger_id = self.overrides.get(doc["id"])
        return str(wger_id) if wger_id else None


class CatalogResolver:
    """Local name match against the instance's exercise catalog."""

    def __init__(self, exp: "WgerExporter", cfg: dict[str, Any]) -> None:
        self.exp = exp
        self.variants = cfg.get("variants") or ALL_VARIANTS

    def resolve(self, client: Optional[httpx.Client],
                doc: dict[str, Any]) -> Optional[str]:
        if client is None:
            return None
        catalog = self.exp.catalog(client, self.variants)
        for name in _query_names(doc.get("name", ""), self.variants):
            for key in _name_keys(name, self.variants):
                if key in catalog:
                    return str(catalog[key])
        return None


class CreateResolver:
    """Fallback: create the exercise on the wger instance."""

    def __init__(self, exp: "WgerExporter", cfg: dict[str, Any]) -> None:
        self.exp = exp
        self.defaults = cfg.get("defaults") or {}

    def resolve(self, client: Optional[httpx.Client],
                doc: dict[str, Any]) -> Optional[str]:
        if client is None:
            return None
        name = (doc.get("name") or doc["id"]).strip()
        catalog = self.exp.catalog(client, ALL_VARIANTS)
        for key in _name_keys(name, ALL_VARIANTS):  # dedupe within/across runs
            if key in catalog:
                return str(catalog[key])
        resp = client.post("/exercise/", json={
            "category": self.defaults.get("category", 10),
        })
        resp.raise_for_status()
        exercise_id = int(resp.json()["id"])
        resp = client.post("/exercise-translation/", json={
            "exercise": exercise_id,
            "name": name,
            "language": self.defaults.get("language", 2),
            "description": self.defaults.get(
                "description", "Auto-created by hevy-bridge"),
            # wger validates >= 40 chars on description_source
            "description_source": self.defaults.get(
                "description_source",
                "Automatically created by hevy-bridge from Hevy workout"
                " data export"),
        })
        resp.raise_for_status()
        for key in _name_keys(name, ALL_VARIANTS):
            catalog.setdefault(key, exercise_id)
        self.exp.audit["created_exercises"].append(
            {"ir_id": doc["id"], "name": name, "wger_exercise_id": exercise_id})
        log.info("created wger exercise %d for %r", exercise_id, name)
        return str(exercise_id)


RESOLVERS = {
    "manual": ManualResolver,
    "override": OverrideResolver,
    "catalog": CatalogResolver,
    "create": CreateResolver,
}


class WgerExporter:
    name = SYSTEM

    def __init__(self) -> None:
        self.base_url = db.get_setting("wger_base_url").rstrip("/")
        self.api_key = db.get_setting("wger_api_key")
        self.mapping = load_mapping()  # re-read every run, no cross-run cache
        self._catalog: Optional[dict[str, int]] = None
        self.audit: dict[str, list] = {"created_exercises": [],
                                       "invalidated_refs": []}
        self._pipeline = []
        for entry in self.mapping["resolvers"]:
            cls = RESOLVERS.get(entry.get("type"))
            if cls is None:
                log.warning("unknown resolver type %r ignored", entry.get("type"))
                continue
            self._pipeline.append(cls(self, entry))

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _client(self) -> httpx.Client:
        if not self.configured():
            raise RuntimeError("wger_base_url / wger_api_key is not configured")
        return httpx.Client(
            base_url=f"{self.base_url}/api/v2",
            headers={"Authorization": f"Token {self.api_key}"},
            timeout=30,
        )

    # --- exercise catalog ---------------------------------------------------

    def catalog(self, client: httpx.Client,
                variants: list[str]) -> dict[str, int]:
        if self._catalog is not None:
            return self._catalog

        def fetch_all(path: str) -> list[dict[str, Any]]:
            results, url = [], f"{path}?limit=500"
            while url:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
                results += data["results"]
                url = data["next"]
                if url:  # absolute url; make it relative to base_url again
                    url = url.split("/api/v2", 1)[1]
            return results

        translations = fetch_all("/exercise-translation/")
        catalog: dict[str, int] = {}

        def add(name: str, exercise_id: int) -> None:
            for key in _name_keys(name, variants):
                # keep the smallest id for deterministic resolution
                catalog[key] = min(catalog.get(key, exercise_id), exercise_id)

        by_translation = {t["id"]: t["exercise"] for t in translations}
        for t in translations:
            add(t["name"], t["exercise"])
        for a in fetch_all("/exercisealias/"):
            exercise_id = by_translation.get(a.get("translation"))
            if exercise_id:
                add(a["alias"], exercise_id)
        log.info("wger catalog loaded: %d name keys", len(catalog))
        self._catalog = catalog
        return catalog

    # --- exercise resolution ------------------------------------------------

    def resolve_exercise(self, client: Optional[httpx.Client],
                         exercise_doc: dict[str, Any],
                         allow_create: bool = True) -> Optional[str]:
        for resolver in self._pipeline:
            if not allow_create and isinstance(resolver, CreateResolver):
                continue
            try:
                wger_id = resolver.resolve(client, exercise_doc)
            except httpx.HTTPError as exc:
                log.warning("%s resolver failed for %s: %s",
                            type(resolver).__name__, exercise_doc["id"], exc)
                continue
            if wger_id:
                db.put_ref(SYSTEM, "exercise", wger_id,
                           "exercise", exercise_doc["id"])
                return wger_id
        return None

    def _revalidate_refs(self, client: httpx.Client,
                         needed_ir_ids: set[str]) -> None:
        """Drop refs whose wger exercise no longer exists (deleted remotely)."""
        for ir_id in sorted(needed_ir_ids):
            wger_id = db.find_external(SYSTEM, "exercise", ir_id)
            if not wger_id:
                continue
            resp = client.get(f"/exercise/{wger_id}/")
            if resp.status_code == 404:
                db.delete_ref(SYSTEM, "exercise", ir_id)
                self.audit["invalidated_refs"].append(
                    {"ir_id": ir_id, "stale_wger_id": wger_id,
                     "reason": "exercise 404 on pre-push validation"})

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
        """Side-effect free towards wger: never creates exercises."""
        exercises = {e["id"]: e for e in db.list_docs("exercise")}
        client = None
        try:
            client = self._client()
        except RuntimeError:
            pass
        has_create = any(isinstance(r, CreateResolver) for r in self._pipeline)
        unresolved, will_create = [], []
        sessions = self._pending_sessions()
        needed = {ex["exercise_id"] for s in sessions for ex in s["exercises"]}
        for ir_id in sorted(needed):
            doc = exercises.get(ir_id)
            if doc is not None and self.resolve_exercise(
                    client, doc, allow_create=False) is not None:
                continue
            item = {"ir_id": ir_id, "name": (doc or {}).get("name", ir_id)}
            if doc is not None and has_create and client is not None:
                will_create.append(item)
            else:
                unresolved.append(item)
        if client:
            client.close()
        return {
            "configured": client is not None,
            "pending_sessions": len(sessions),
            "pending_weight_entries": len(self._pending_weights()),
            "unresolved_exercises": unresolved,
            "will_create_exercises": will_create,
        }

    def push(self) -> dict[str, Any]:
        exercises = {e["id"]: e for e in db.list_docs("exercise")}
        report: dict[str, Any] = {"sessions_exported": 0, "logs_exported": 0,
                                  "weights_exported": 0, "errors": []}
        with self._client() as client:
            pending = self._pending_sessions()
            needed = {ex["exercise_id"] for s in pending
                      for ex in s["exercises"]}
            self._revalidate_refs(client, needed)
            for session in pending:
                try:
                    self._push_session(client, session, exercises, report)
                except (httpx.HTTPError, RuntimeError) as exc:
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
        report.update(self.audit)
        return report

    def _post_log(self, client: httpx.Client, log_payload: dict[str, Any],
                  ir_exercise_id: str, exercises: dict[str, dict[str, Any]],
                  resolved: dict[str, str]) -> httpx.Response:
        resp = client.post("/workoutlog/", json=log_payload)
        if resp.status_code == 400:
            # exercise may have been deleted between validation and write:
            # drop the ref, re-resolve (worst case: create), retry once
            db.delete_ref(SYSTEM, "exercise", ir_exercise_id)
            self.audit["invalidated_refs"].append(
                {"ir_id": ir_exercise_id,
                 "stale_wger_id": resolved.get(ir_exercise_id),
                 "reason": f"workoutlog 400: {resp.text[:200]}"})
            wger_id = self.resolve_exercise(client, exercises[ir_exercise_id])
            if wger_id:
                resolved[ir_exercise_id] = wger_id
                log_payload["exercise"] = int(wger_id)
                resp = client.post("/workoutlog/", json=log_payload)
        return resp

    def _push_session(self, client: httpx.Client, session: dict[str, Any],
                      exercises: dict[str, dict[str, Any]],
                      report: dict[str, Any]) -> None:
        resolved: dict[str, str] = {}
        for ex in session["exercises"]:
            doc = exercises.get(ex["exercise_id"])
            wger_id = self.resolve_exercise(client, doc) if doc else None
            if wger_id is None:
                raise RuntimeError(
                    f"unresolved exercise {ex['exercise_id']} after pipeline")
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
                elif actual.get("duration"):
                    # wger rejects logs with neither reps nor weight; lower
                    # duration-only sets (planks etc.) to seconds (unit 3)
                    log_payload["repetitions"] = str(actual["duration"]["value"])
                    log_payload["repetition_unit"] = 3
                if actual.get("weight"):
                    log_payload["weight"] = str(actual["weight"]["value"])
                rir = self._rir(actual.get("effort"))
                if rir is not None:
                    log_payload["rir"] = str(min(9.0, round(rir * 2) / 2))
                resp = self._post_log(client, log_payload,
                                      ex["exercise_id"], exercises, resolved)
                resp.raise_for_status()
                report["logs_exported"] += 1

        db.put_ref(SYSTEM, "workoutsession", str(wger_session_id),
                   "session", session["id"])
        report["sessions_exported"] += 1
