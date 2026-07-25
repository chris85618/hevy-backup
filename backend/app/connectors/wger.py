"""wger exporter: lowers FitIR into wger's normalized write model.

session      -> POST /workoutsession/ + one /workoutlog/ per set (R1);
                linked to its routine via plan_id (plans push first so the
                ref exists); updates PATCH the session and rebuild its
                logs; sessions deleted in Hevy get a "[deleted in Hevy]"
                notes prefix (user decision 2026-07-23: never auto-delete)
plan         -> two /routine/ trees (each /routine/ + /day/ + /slot/ +
                /slot-entry/ + *-config, wger 2.7 model): a Hevy routine
                is an agile template, not a calendar plan, so it lowers
                to a frozen wger template (is_template=True) plus one
                execution routine (is_template=False) that sessions and
                logs link to, dated over the actual training days;
                updates PATCH both in place and rebuild only their days
                — WorkoutSession.routine is on_delete=CASCADE, so
                delete-recreate would wipe every linked session and its
                logs
body-metric  -> weight goes to /weightentry/; other metric_keys go to
                auto-created /measurement-category/ + /measurement/ (R9)
exercise     -> auto-created exercises get name/muscle/equipment written
                back when their IR doc changes (shared catalog entries
                are never touched)

Change detection (ADR-STR-006/008): export_state stores the doc's
updated_at at push time and is only written after the whole doc landed;
refs are written as soon as the wger object exists. A doc is pushed when
it has no ref yet ("new") or its export_state doesn't match updated_at
("changed" — covers both edits and interrupted pushes). Every push is
idempotent (PATCH-or-recreate + rebuild), so any later successful run
converges wger onto the latest pulled snapshot (eventual consistency).

Exercise resolution runs a configurable pipeline (data/wger-mapping.yaml):
manual (refs table / GUI) -> override (yaml) -> catalog (local match against
/exercise-translation/ + /exercisealias/; /exercise/search/ is gone in
wger >= 2.7) -> create (auto-create; wger only allows it for "trustworthy"
accounts: superuser, or verified email + account age past
MIN_ACCOUNT_AGE_TO_TRUST — otherwise POST /exercise/ is 403). Stale refs
are re-validated before push and on workoutlog 400.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from typing import Any, Iterable, Optional

import httpx
import yaml

from .. import config, db
from ..ir.schema import rpe_to_rir

log = logging.getLogger(__name__)

SYSTEM = "wger"

DELETED_PREFIX = "[deleted in Hevy] "

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
        payload: dict[str, Any] = {"category": self.defaults.get("category", 10)}
        payload.update(self.exp.exercise_taxonomy_fields(client, doc))
        resp = client.post("/exercise/", json=payload)
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
        # remember our own creations: only these get metadata write-back
        db.put_ref(SYSTEM, "exercise_translation", str(resp.json()["id"]),
                   "exercise", doc["id"])
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
        self._categories: Optional[dict[str, Any]] = None
        self._muscles: Optional[dict[str, int]] = None
        self._equipment: Optional[dict[str, int]] = None
        self.resolve_failures: dict[str, list[str]] = {}
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
        failures: list[str] = []
        for resolver in self._pipeline:
            if not allow_create and isinstance(resolver, CreateResolver):
                continue
            try:
                wger_id = resolver.resolve(client, exercise_doc)
            except httpx.HTTPError as exc:
                log.warning("%s resolver failed for %s: %s",
                            type(resolver).__name__, exercise_doc["id"], exc)
                reason = f"{type(resolver).__name__}: {exc}"
                if (isinstance(resolver, CreateResolver)
                        and isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code == 403):
                    reason += (" — wger account may not create exercises;"
                               " it must be trustworthy (superuser, or"
                               " verified email + account age over"
                               " MIN_ACCOUNT_AGE_TO_TRUST); see README"
                               " 'wger 匯出前置'")
                failures.append(reason)
                continue
            if wger_id:
                db.put_ref(SYSTEM, "exercise", wger_id,
                           "exercise", exercise_doc["id"])
                return wger_id
        self.resolve_failures[exercise_doc["id"]] = failures
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

    # --- taxonomy lookups (muscle / equipment / measurement category) ------

    def _vocab(self, client: httpx.Client, path: str,
               fields: tuple[str, ...]) -> dict[str, Any]:
        resp = client.get(f"{path}?limit=100")
        resp.raise_for_status()
        vocab: dict[str, Any] = {}
        for row in resp.json()["results"]:
            for field in fields:
                name = (row.get(field) or "").strip().lower()
                if name:
                    vocab.setdefault(name, row["id"])
        return vocab

    def exercise_taxonomy_fields(self, client: httpx.Client,
                                 doc: dict[str, Any]) -> dict[str, Any]:
        """Map IR muscle/equipment names onto wger ids; unmatched names are
        skipped (Hevy vocabulary is broader than wger's)."""
        if self._muscles is None:
            self._muscles = self._vocab(client, "/muscle/", ("name_en", "name"))
        if self._equipment is None:
            self._equipment = self._vocab(client, "/equipment/", ("name",))

        def muscle_ids(names: Optional[list[str]]) -> list[int]:
            out = []
            for n in names or []:
                mid = self._muscles.get(n.replace("_", " ").strip().lower())
                if mid is not None:
                    out.append(mid)
            return out

        fields: dict[str, Any] = {}
        primary = muscle_ids(doc.get("primary_muscles"))
        secondary = muscle_ids(doc.get("secondary_muscles"))
        if primary:
            fields["muscles"] = primary
        if secondary:
            fields["muscles_secondary"] = secondary
        equip = self._equipment.get(
            (doc.get("equipment_category") or "").replace("_", " ").strip().lower())
        if equip is not None:
            fields["equipment"] = [equip]
        return fields

    # --- change detection (ADR-STR-006) ------------------------------------

    def _status(self, ir_kind: str, doc: dict[str, Any],
                ref_kind: str) -> Optional[str]:
        """None = clean, "new" = never pushed, "changed" = pushed but stale.

        A ref only proves identity (the wger object exists); export_state
        proves completion (the whole doc landed). A ref without matching
        export_state is an interrupted push — report "changed" so the
        idempotent update path re-pushes it. Adopting it as clean here
        would freeze a half-pushed doc forever (the pre-scheduler partial
        sync trap)."""
        if not db.find_external(SYSTEM, ref_kind, doc["id"]):
            return "new"
        state = db.get_export_state(SYSTEM, ir_kind, doc["id"])
        return None if state == (doc.get("updated_at") or "") else "changed"

    def _mark_pushed(self, ir_kind: str, doc: dict[str, Any]) -> None:
        db.put_export_state(SYSTEM, ir_kind, doc["id"],
                            doc.get("updated_at") or "")

    # --- work lists ---------------------------------------------------------

    def _session_work(self) -> dict[str, list[dict[str, Any]]]:
        work: dict[str, list[dict[str, Any]]] = {
            "create": [], "update": [], "mark_deleted": []}
        for s in db.list_docs("session", include_deleted=True):
            if s.get("deleted_at"):
                # no backfill here: pre-upgrade pushes must still get marked
                if (db.find_external(SYSTEM, "workoutsession", s["id"])
                        and db.get_export_state(SYSTEM, "session", s["id"])
                        != (s.get("updated_at") or "")):
                    work["mark_deleted"].append(s)
                continue
            status = self._status("session", s, "workoutsession")
            if status == "new":
                work["create"].append(s)
            elif status == "changed":
                work["update"].append(s)
        return work

    def _plan_work(self) -> list[dict[str, Any]]:
        # no template ref yet: pushed before the template/routine split,
        # re-push once so the wger template gets created
        return [p for p in db.list_docs("plan")
                if self._status("plan", p, "routine")
                or not db.find_external(SYSTEM, "template", p["id"])]

    def _body_metric_work(self) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        work: dict[str, list[tuple[str, dict[str, Any]]]] = {
            "weight": [], "measurement": []}
        for m in db.list_docs("body-metric"):
            is_weight = m["metric_key"] == "weight"
            status = self._status(
                "body-metric", m, "weightentry" if is_weight else "measurement")
            if status:
                work["weight" if is_weight else "measurement"].append((status, m))
        return work

    def _exercise_update_work(
            self, exercises: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [doc for doc in exercises.values()
                if db.find_external(SYSTEM, "exercise_translation", doc["id"])
                and self._status("exercise", doc, "exercise") == "changed"]

    @staticmethod
    def _session_exercise_ids(sessions: Iterable[dict[str, Any]]) -> set[str]:
        return {ex["exercise_id"] for s in sessions for ex in s["exercises"]}

    @staticmethod
    def _plan_exercise_ids(plans: Iterable[dict[str, Any]]) -> set[str]:
        return {e["exercise_id"] for p in plans
                for d in p["days"] for e in d["entries"]}

    # --- preview / push ----------------------------------------------------

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
        work = self._session_work()
        plans = self._plan_work()
        needed = (self._session_exercise_ids(work["create"] + work["update"])
                  | self._plan_exercise_ids(plans))
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
        metrics = self._body_metric_work()
        if client:
            client.close()
        return {
            "configured": client is not None,
            "pending_sessions": len(work["create"]),
            "updated_sessions": len(work["update"]),
            "sessions_to_mark_deleted": len(work["mark_deleted"]),
            "pending_plans": len(plans),
            "pending_weight_entries": len(metrics["weight"]),
            "pending_measurements": len(metrics["measurement"]),
            "pending_exercise_updates": len(self._exercise_update_work(exercises)),
            "unresolved_exercises": unresolved,
            "will_create_exercises": will_create,
        }

    def push(self) -> dict[str, Any]:
        exercises = {e["id"]: e for e in db.list_docs("exercise")}
        report: dict[str, Any] = {
            "sessions_exported": 0, "sessions_updated": 0,
            "sessions_marked_deleted": 0, "logs_exported": 0,
            "plans_exported": 0, "weights_exported": 0,
            "measurements_exported": 0, "exercises_updated": 0,
            "errors": [],
        }
        with self._client() as client:
            work = self._session_work()
            plans = self._plan_work()
            needed = (self._session_exercise_ids(work["create"] + work["update"])
                      | self._plan_exercise_ids(plans))
            self._revalidate_refs(client, needed)
            # plans go first: sessions link to their routine, so on a fresh
            # instance the routine refs must exist before sessions push
            for plan in plans:
                try:
                    self._push_plan(client, plan, exercises, report)
                except (httpx.HTTPError, RuntimeError) as exc:
                    report["errors"].append(f"plan {plan['id']}: {exc}")
            for session in work["create"]:
                try:
                    self._push_session(client, session, exercises, report)
                except (httpx.HTTPError, RuntimeError) as exc:
                    report["errors"].append(f"session {session['id']}: {exc}")
            for session in work["update"]:
                try:
                    self._update_session(client, session, exercises, report)
                except (httpx.HTTPError, RuntimeError) as exc:
                    report["errors"].append(f"session {session['id']}: {exc}")
            for session in work["mark_deleted"]:
                try:
                    self._mark_deleted_session(client, session, report)
                except httpx.HTTPError as exc:
                    report["errors"].append(f"session {session['id']}: {exc}")
            self._push_body_metrics(client, report)
            self._push_exercise_updates(client, exercises, report)
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

    def _resolve_all(self, client: httpx.Client, ir_ids: Iterable[str],
                     exercises: dict[str, dict[str, Any]]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for ir_id in ir_ids:
            if ir_id in resolved:
                continue
            doc = exercises.get(ir_id)
            if doc is None:
                raise RuntimeError(f"exercise {ir_id} not found in IR store")
            wger_id = self.resolve_exercise(client, doc)
            if wger_id is None:
                detail = "; ".join(self.resolve_failures.get(ir_id, []))
                raise RuntimeError(
                    f"unresolved exercise {ir_id} after pipeline"
                    + (f" ({detail})" if detail else ""))
            resolved[ir_id] = wger_id
        return resolved

    @staticmethod
    def _session_payload(session: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"date": (session.get("started_at") or "")[:10],
                                   "notes": session.get("notes") or ""}
        if session.get("plan_id"):
            routine = db.find_external(SYSTEM, "routine", session["plan_id"])
            if routine:
                payload["routine"] = routine
        if session.get("started_at") and "T" in session["started_at"]:
            payload["time_start"] = session["started_at"].split("T")[1][:8]
        if session.get("ended_at") and "T" in session["ended_at"]:
            payload["time_end"] = session["ended_at"].split("T")[1][:8]
        return payload

    @staticmethod
    def _send_session(client: httpx.Client, method: str, url: str,
                      payload: dict[str, Any]) -> httpx.Response:
        """wger enforces unique (date, user, routine): a second workout of
        the same routine on one day stays unlinked instead of failing."""
        resp = client.request(method, url, json=payload)
        if resp.status_code == 400 and "routine" in payload:
            payload = {k: v for k, v in payload.items() if k != "routine"}
            resp = client.request(method, url, json=payload)
        return resp

    def _write_logs(self, client: httpx.Client, session: dict[str, Any],
                    wger_session_id: Any,
                    exercises: dict[str, dict[str, Any]],
                    resolved: dict[str, str], report: dict[str, Any],
                    routine: Any = None) -> None:
        date_str = (session.get("started_at") or "")[:10]
        for ex in session["exercises"]:
            for s in ex["sets"]:
                actual = s.get("actual", {})
                log_payload: dict[str, Any] = {
                    "session": wger_session_id,
                    "exercise": int(resolved[ex["exercise_id"]]),
                    "date": session.get("started_at") or date_str,
                }
                if routine is not None:
                    log_payload["routine"] = routine
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

    def _push_session(self, client: httpx.Client, session: dict[str, Any],
                      exercises: dict[str, dict[str, Any]],
                      report: dict[str, Any]) -> None:
        resolved = self._resolve_all(
            client, [ex["exercise_id"] for ex in session["exercises"]], exercises)
        resp = self._send_session(client, "POST", "/workoutsession/",
                                  self._session_payload(session))
        resp.raise_for_status()
        wger_session_id = resp.json().get("id")
        # ref before logs: if a log write fails, the next run finds the ref
        # (status "changed") and heals via _update_session's log rebuild
        # instead of POSTing a duplicate that the unique (date, user,
        # routine) fallback would strip the routine link from
        db.put_ref(SYSTEM, "workoutsession", str(wger_session_id),
                   "session", session["id"])
        self._write_logs(client, session, wger_session_id, exercises, resolved,
                         report, routine=resp.json().get("routine"))
        self._mark_pushed("session", session)
        report["sessions_exported"] += 1

    def _update_session(self, client: httpx.Client, session: dict[str, Any],
                        exercises: dict[str, dict[str, Any]],
                        report: dict[str, Any]) -> None:
        """Re-sync an already-exported session: PATCH the session row, then
        rebuild its logs (delete + recreate; simpler and more robust than
        diffing set lists, and it heals partial exports — DEBT-004)."""
        wger_session_id = db.find_external(SYSTEM, "workoutsession",
                                           session["id"])
        # resolve before deleting anything so failures leave wger untouched
        resolved = self._resolve_all(
            client, [ex["exercise_id"] for ex in session["exercises"]], exercises)
        resp = self._send_session(client, "PATCH",
                                  f"/workoutsession/{wger_session_id}/",
                                  self._session_payload(session))
        if resp.status_code == 404:  # deleted on wger side: create afresh
            db.delete_ref(SYSTEM, "workoutsession", session["id"])
            self._push_session(client, session, exercises, report)
            return
        resp.raise_for_status()
        self._delete_session_logs(client, wger_session_id)
        self._write_logs(client, session, wger_session_id, exercises, resolved,
                         report, routine=resp.json().get("routine"))
        self._mark_pushed("session", session)
        report["sessions_updated"] += 1

    def _delete_session_logs(self, client: httpx.Client,
                             wger_session_id: Any) -> None:
        while True:
            resp = client.get(f"/workoutlog/?session={wger_session_id}&limit=100")
            resp.raise_for_status()
            results = resp.json()["results"]
            deleted = 0
            for entry in results:
                # never trust the server-side filter blindly before deleting
                if str(entry.get("session")) != str(wger_session_id):
                    continue
                client.delete(f"/workoutlog/{entry['id']}/").raise_for_status()
                deleted += 1
            if not results or deleted == 0:
                return

    def _mark_deleted_session(self, client: httpx.Client,
                              session: dict[str, Any],
                              report: dict[str, Any]) -> None:
        """Hevy deletion policy (user decision): keep the wger session but
        prefix its notes so it is recognizable; never auto-delete."""
        wger_session_id = db.find_external(SYSTEM, "workoutsession",
                                           session["id"])
        resp = client.get(f"/workoutsession/{wger_session_id}/")
        if resp.status_code != 404:
            resp.raise_for_status()
            notes = resp.json().get("notes") or ""
            if not notes.startswith(DELETED_PREFIX):
                client.patch(
                    f"/workoutsession/{wger_session_id}/",
                    json={"notes": DELETED_PREFIX + notes},
                ).raise_for_status()
        self._mark_pushed("session", session)
        report["sessions_marked_deleted"] += 1

    # --- plans -> wger routines (wger 2.7 model) ----------------------------

    @staticmethod
    def _slot_groups(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Consecutive entries sharing a group_key (superset) share one slot."""
        groups: list[list[dict[str, Any]]] = []
        prev_key: Any = object()
        for entry in sorted(entries, key=lambda e: e.get("order", 0)):
            key = entry.get("group_key")
            if key is not None and key == prev_key:
                groups[-1].append(entry)
            else:
                groups.append([entry])
            prev_key = key
        return groups

    def _delete_routine_days(self, client: httpx.Client,
                             routine_id: Any) -> None:
        """/day/ has no routine filter in wger 2.7, so match client-side.
        Day DELETE cascades slots/entries/configs; sessions only reference
        the routine, never a day, so they survive the rebuild."""
        day_ids, url = [], "/day/?limit=100"
        while url:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            day_ids += [d["id"] for d in data["results"]
                        if str(d.get("routine")) == str(routine_id)]
            url = data["next"]
            if url:
                url = url.split("/api/v2", 1)[1]
        for day_id in day_ids:
            client.delete(f"/day/{day_id}/").raise_for_status()

    @staticmethod
    def _exec_dates(plan_id: str) -> tuple[date, date]:
        """Window of the execution routine: back to the first logged
        session of this plan, forward 70 days so wger keeps showing it
        as the current routine. wger caps a routine at MAX_DURATION_DAYS
        (120), so the start is clamped when the history is longer — the
        log linkage itself is the routine FK, not the date window."""
        end = date.today() + timedelta(days=70)
        start = date.today()
        first = min((s["started_at"][:10] for s in db.list_docs("session")
                     if s.get("plan_id") == plan_id and s.get("started_at")),
                    default=None)
        if first:
            start = date.fromisoformat(first)
        return max(start, end - timedelta(days=119)), end

    def _upsert_routine(self, client: httpx.Client, ref_kind: str,
                        plan: dict[str, Any],
                        payload: dict[str, Any]) -> int:
        """PATCH in place when a ref exists (keeps the id stable and the
        linked sessions alive), else POST; an existing routine gets its
        days wiped for the rebuild."""
        routine_id = None
        old = db.find_external(SYSTEM, ref_kind, plan["id"])
        if old:
            resp = client.patch(f"/routine/{old}/", json=payload)
            if resp.status_code == 404:  # deleted on wger side
                db.delete_ref(SYSTEM, ref_kind, plan["id"])
            else:
                resp.raise_for_status()
                routine_id = resp.json()["id"]
                self._delete_routine_days(client, routine_id)
        if routine_id is None:
            resp = client.post("/routine/", json=payload)
            resp.raise_for_status()
            routine_id = resp.json()["id"]
        db.put_ref(SYSTEM, ref_kind, str(routine_id), "plan", plan["id"])
        return routine_id

    def _push_plan(self, client: httpx.Client, plan: dict[str, Any],
                   exercises: dict[str, dict[str, Any]],
                   report: dict[str, Any]) -> None:
        """A Hevy routine maps to a wger template (the reusable blueprint
        Hevy logs against), so each plan pushes twice: the template
        (ref kind "template", is_template=True) and the execution routine
        the workout sessions and logs link to (ref kind "routine",
        is_template=False) — mirroring wger's own template -> routine
        copy flow."""
        resolved = self._resolve_all(
            client, sorted(self._plan_exercise_ids([plan])), exercises)
        base = {
            "name": (plan.get("name") or plan["id"])[:25],
            "description": (plan.get("description") or "")[:1000],
        }
        today = date.today()
        template_id = self._upsert_routine(client, "template", plan, {
            **base, "is_template": True,
            "start": today.isoformat(),
            "end": (today + timedelta(days=70)).isoformat(),
        })
        self._push_days(client, template_id, plan, resolved)
        start, end = self._exec_dates(plan["id"])
        routine_id = self._upsert_routine(client, "routine", plan, {
            **base, "is_template": False,
            "start": start.isoformat(), "end": end.isoformat(),
        })
        self._push_days(client, routine_id, plan, resolved)
        self._mark_pushed("plan", plan)
        report["plans_exported"] += 1

    def _push_days(self, client: httpx.Client, routine_id: Any,
                   plan: dict[str, Any], resolved: dict[str, str]) -> None:
        for day in sorted(plan["days"], key=lambda d: d.get("order", 0)):
            resp = client.post("/day/", json={
                "routine": routine_id,
                "name": (day.get("name") or "Day")[:20],
                "order": day.get("order", 0) + 1,
                "is_rest": bool(day.get("is_rest")),
                "type": "custom",
            })
            resp.raise_for_status()
            day_id = resp.json()["id"]
            if day.get("is_rest"):
                continue
            for slot_order, group in enumerate(
                    self._slot_groups(day.get("entries") or []), start=1):
                resp = client.post("/slot/", json={"day": day_id,
                                                   "order": slot_order})
                resp.raise_for_status()
                slot_id = resp.json()["id"]
                for pos, entry in enumerate(group, start=1):
                    self._push_slot_entry(client, slot_id, pos, entry, resolved)

    def _push_slot_entry(self, client: httpx.Client, slot_id: int, order: int,
                         entry: dict[str, Any],
                         resolved: dict[str, str]) -> None:
        """Lossy lowering: wger configs are per-entry, Hevy targets are
        per-set, so the first set with a target provides the numbers."""
        sets = entry.get("sets") or []
        target = next((s.get("target") for s in sets if s.get("target")), None) or {}
        reps = target.get("reps") or {}
        duration = target.get("duration")
        payload: dict[str, Any] = {
            "slot": slot_id,
            "exercise": int(resolved[entry["exercise_id"]]),
            "order": order,
            "comment": (entry.get("notes") or "")[:100],
        }
        if reps.get("min") is None and reps.get("max") is None and duration:
            payload["repetition_unit"] = 3  # Seconds, like duration-only logs
        resp = client.post("/slot-entry/", json=payload)
        resp.raise_for_status()
        entry_id = resp.json()["id"]

        def config(endpoint: str, value: Any) -> None:
            client.post(f"/{endpoint}/", json={
                "slot_entry": entry_id, "iteration": 1, "value": str(value),
                "operation": "r", "step": "na",
            }).raise_for_status()

        if sets:
            config("sets-config", len(sets))
        if reps.get("min") is not None:
            config("repetitions-config", reps["min"])
            if reps.get("max") is not None and reps["max"] != reps["min"]:
                config("max-repetitions-config", reps["max"])
        elif duration:
            config("repetitions-config", duration["value"])
        if target.get("weight"):
            config("weight-config", target["weight"]["value"])
        if entry.get("rest"):
            config("rest-config", entry["rest"]["value"])
        rir = self._rir(target.get("effort"))
        if rir is not None:
            config("rir-config", min(9.0, round(rir * 2) / 2))

    # --- body metrics -------------------------------------------------------

    def _measurement_categories(self, client: httpx.Client) -> dict[str, Any]:
        if self._categories is None:
            self._categories = self._vocab(
                client, "/measurement-category/", ("name",))
        return self._categories

    def _push_body_metrics(self, client: httpx.Client,
                           report: dict[str, Any]) -> None:
        work = self._body_metric_work()
        for status, m in work["weight"]:
            try:
                payload = {"date": m["at"],
                           "weight": str(m["quantity"]["value"])}
                ext = db.find_external(SYSTEM, "weightentry", m["id"])
                if status == "changed" and ext:
                    resp = client.patch(f"/weightentry/{ext}/", json=payload)
                    if resp.status_code == 404:
                        db.delete_ref(SYSTEM, "weightentry", m["id"])
                        resp = client.post("/weightentry/", json=payload)
                else:
                    resp = client.post("/weightentry/", json=payload)
                resp.raise_for_status()
                db.put_ref(SYSTEM, "weightentry",
                           str(resp.json().get("id", m["at"])),
                           "body-metric", m["id"])
                self._mark_pushed("body-metric", m)
                report["weights_exported"] += 1
            except httpx.HTTPError as exc:
                report["errors"].append(f"weight {m['id']}: {exc}")
        for status, m in work["measurement"]:
            try:
                categories = self._measurement_categories(client)
                cat_id = categories.get(m["metric_key"].lower())
                if cat_id is None:
                    resp = client.post("/measurement-category/", json={
                        "name": m["metric_key"][:100],
                        "unit": m["quantity"]["unit"][:30],
                    })
                    resp.raise_for_status()
                    cat_id = resp.json()["id"]
                    categories[m["metric_key"].lower()] = cat_id
                payload = {"category": cat_id, "date": m["at"],
                           "value": str(m["quantity"]["value"]),
                           "notes": m.get("notes") or ""}
                ext = db.find_external(SYSTEM, "measurement", m["id"])
                if status == "changed" and ext:
                    resp = client.patch(f"/measurement/{ext}/", json=payload)
                    if resp.status_code == 404:
                        db.delete_ref(SYSTEM, "measurement", m["id"])
                        resp = client.post("/measurement/", json=payload)
                else:
                    resp = client.post("/measurement/", json=payload)
                resp.raise_for_status()
                db.put_ref(SYSTEM, "measurement", str(resp.json()["id"]),
                           "body-metric", m["id"])
                self._mark_pushed("body-metric", m)
                report["measurements_exported"] += 1
            except httpx.HTTPError as exc:
                report["errors"].append(f"measurement {m['id']}: {exc}")

    # --- exercise metadata write-back ---------------------------------------

    def _push_exercise_updates(self, client: httpx.Client,
                               exercises: dict[str, dict[str, Any]],
                               report: dict[str, Any]) -> None:
        """Only exercises this bridge created (exercise_translation ref)
        are written back; shared catalog entries stay untouched."""
        for doc in self._exercise_update_work(exercises):
            try:
                translation_id = db.find_external(
                    SYSTEM, "exercise_translation", doc["id"])
                resp = client.patch(
                    f"/exercise-translation/{translation_id}/",
                    json={"name": doc.get("name") or doc["id"]})
                if resp.status_code == 404:  # translation gone remotely
                    db.delete_ref(SYSTEM, "exercise_translation", doc["id"])
                    self._mark_pushed("exercise", doc)
                    continue
                resp.raise_for_status()
                wger_id = db.find_external(SYSTEM, "exercise", doc["id"])
                fields = self.exercise_taxonomy_fields(client, doc)
                if fields and wger_id:
                    client.patch(f"/exercise/{wger_id}/",
                                 json=fields).raise_for_status()
                self._mark_pushed("exercise", doc)
                report["exercises_updated"] += 1
            except httpx.HTTPError as exc:
                report["errors"].append(f"exercise {doc['id']}: {exc}")
