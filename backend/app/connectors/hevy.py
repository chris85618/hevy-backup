"""Hevy importer: lowers Hevy API payloads into FitIR (ir-spec.md §4).

Sync strategy (FEA-041): first run does a full paginated pull of workouts,
routines, exercise templates and body measurements; later runs use
/v1/workouts/events?since= for incremental workout updates/deletes, plus a
cheap re-pull of the small collections.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator, Optional

import httpx

from .. import db
from ..ir.schema import FITIR_VERSION, exercise_id_from_name

log = logging.getLogger(__name__)

BASE_URL = "https://api.hevyapp.com/v1"
SYSTEM = "hevy"

# ir-spec.md §4.3
BODY_METRIC_FIELDS = {
    "weight_kg": ("weight", "kg"),
    "fat_percent": ("body_fat", "percent"),
    "lean_mass_kg": ("lean_mass", "kg"),
    "neck_cm": ("neck", "cm"),
    "shoulder_cm": ("shoulders", "cm"),
    "chest_cm": ("chest", "cm"),
    "left_bicep_cm": ("bicep_left", "cm"),
    "right_bicep_cm": ("bicep_right", "cm"),
    "left_forearm_cm": ("forearm_left", "cm"),
    "right_forearm_cm": ("forearm_right", "cm"),
    "abdomen": ("abdomen", "cm"),
    "waist": ("waist", "cm"),
    "hips": ("hips", "cm"),
    "left_thigh": ("thigh_left", "cm"),
    "right_thigh": ("thigh_right", "cm"),
    "left_calf": ("calf_left", "cm"),
    "right_calf": ("calf_right", "cm"),
}

SET_TAGS = {"normal", "warmup", "dropset", "failure"}
METRIC_KINDS = {
    "weight_reps": "weight_reps", "reps_only": "reps",
    "bodyweight_reps": "bodyweight_reps",
    "bodyweight_assisted_reps": "assisted_reps",
    "duration": "duration", "weight_duration": "weight_duration",
    "distance_duration": "distance_duration",
    "short_distance_weight": "weight_distance",
}


class HevyImporter:
    name = SYSTEM

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or db.get_setting("hevy_api_key")

    def _client(self) -> httpx.Client:
        if not self.api_key:
            raise RuntimeError("hevy_api_key is not configured")
        return httpx.Client(
            base_url=BASE_URL, headers={"api-key": self.api_key}, timeout=30
        )

    def _paged(self, client: httpx.Client, path: str, item_key: str,
               page_size: int = 10, **params: Any) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            resp = client.get(path, params={"page": page, "pageSize": page_size, **params})
            resp.raise_for_status()
            data = resp.json()
            yield from data.get(item_key, [])
            if page >= int(data.get("page_count", 1)):
                return
            page += 1

    # --- lowering ---------------------------------------------------------

    def _exercise_ir_id(self, template: dict[str, Any]) -> str:
        """Merge-or-create the exercise dictionary entry (ir-spec.md §3.1).

        Existing docs are refreshed from the template so metadata edits in
        Hevy (name, muscles, equipment) reach the IR — and, via updated_at,
        downstream exporters. The workout lowering path passes a stub with
        only id/title; fields the template doesn't carry stay untouched."""
        ir_id = db.find_ref(SYSTEM, "exercise_template", template["id"])
        doc = db.get_doc("exercise", ir_id) if ir_id else None
        if doc is None:
            ir_id = exercise_id_from_name(
                template.get("title", ""), fallback=template.get("id", ""))
            doc = db.get_doc("exercise", ir_id)
        if doc is None:
            doc = {
                "fitir": FITIR_VERSION, "kind": "exercise", "id": ir_id,
                "refs": [], "ext": {}, "name": "", "aliases": [],
                "metric_kind": "weight_reps", "primary_muscles": [],
                "secondary_muscles": [], "equipment_category": "none",
                "is_custom": False,
            }
        if template.get("title"):
            doc["name"] = template["title"]
        if template.get("type"):
            doc["metric_kind"] = METRIC_KINDS.get(template["type"], "weight_reps")
        if "primary_muscle_group" in template:
            doc["primary_muscles"] = [
                m for m in [template.get("primary_muscle_group")] if m]
        if "secondary_muscle_groups" in template:
            doc["secondary_muscles"] = template.get("secondary_muscle_groups") or []
        if "equipment_category" in template:
            doc["equipment_category"] = template.get("equipment_category") or "none"
        if "is_custom" in template:
            doc["is_custom"] = bool(template.get("is_custom"))
        refs = [r for r in doc.get("refs", []) if not (
            r["system"] == SYSTEM and r["kind"] == "exercise_template")]
        refs.append({"system": SYSTEM, "id": template["id"], "kind": "exercise_template"})
        doc["refs"] = refs
        doc["updated_at"] = db.now_iso()
        db.put_doc_if_changed(doc)
        db.put_ref(SYSTEM, "exercise_template", template["id"], "exercise", ir_id)
        return ir_id

    def _exercise_id_for_title(self, template_id: str, title: str) -> str:
        return (db.find_ref(SYSTEM, "exercise_template", template_id)
                or self._exercise_ir_id({"id": template_id, "title": title}))

    def _lower_set_actual(self, s: dict[str, Any]) -> dict[str, Any]:
        actual: dict[str, Any] = {"extra_metrics": {}}
        if s.get("reps") is not None:
            actual["reps"] = s["reps"]
        if s.get("weight_kg") is not None:
            actual["weight"] = {"value": s["weight_kg"], "unit": "kg"}
        if s.get("duration_seconds") is not None:
            actual["duration"] = {"value": s["duration_seconds"], "unit": "s"}
        if s.get("distance_meters") is not None:
            actual["distance"] = {"value": s["distance_meters"], "unit": "m"}
        if s.get("rpe") is not None:
            actual["effort"] = {"scale": "rpe", "value": s["rpe"]}
        if s.get("custom_metric") is not None:
            actual["extra_metrics"]["custom"] = s["custom_metric"]
        return actual

    def _tag(self, raw: Optional[str]) -> tuple[str, dict[str, Any]]:
        if raw in SET_TAGS:
            return raw, {}
        return "normal", ({"hevy": {"set_type": raw}} if raw else {})

    def lower_workout(self, w: dict[str, Any]) -> dict[str, Any]:
        ir_id = f"ses_{w['id']}"
        exercises = []
        for ex in w.get("exercises", []):
            sets = []
            for s in ex.get("sets", []):
                tag, _ext = self._tag(s.get("type"))
                sets.append({
                    "order": s.get("index", 0), "tag": tag,
                    "actual": self._lower_set_actual(s),
                    "prescription": None,
                })
            exercises.append({
                "order": ex.get("index", 0),
                "exercise_id": self._exercise_id_for_title(
                    ex.get("exercise_template_id", ""), ex.get("title", "")),
                "group_key": (None if ex.get("superset_id") is None
                              else f"ss{ex['superset_id']}"),
                "notes": ex.get("notes") or "",
                "sets": sets,
            })
        plan_id = None
        if w.get("routine_id"):
            plan_id = db.find_ref(SYSTEM, "routine", w["routine_id"])
        return {
            "fitir": FITIR_VERSION, "kind": "session", "id": ir_id,
            "refs": [{"system": SYSTEM, "id": w["id"], "kind": "workout"}],
            "ext": {}, "title": w.get("title") or "",
            "plan_id": plan_id,
            "started_at": w.get("start_time"), "ended_at": w.get("end_time"),
            "notes": w.get("description") or "", "mood": None,
            "exercises": exercises,
            "created_at": w.get("created_at"),
            "updated_at": w.get("updated_at") or db.now_iso(),
        }

    def lower_routine(self, r: dict[str, Any], folders: dict[Any, str]) -> dict[str, Any]:
        ir_id = f"pln_{r['id']}"
        entries = []
        for ex in r.get("exercises", []):
            sets = []
            for s in ex.get("sets", []):
                tag, _ext = self._tag(s.get("type"))
                target: dict[str, Any] = {}
                rep_range = s.get("rep_range") or {}
                if s.get("reps") is not None or rep_range.get("start") is not None:
                    lo = rep_range.get("start", s.get("reps"))
                    hi = rep_range.get("end", s.get("reps"))
                    target["reps"] = {"min": lo, "max": hi}
                if s.get("weight_kg") is not None:
                    target["weight"] = {"value": s["weight_kg"], "unit": "kg"}
                if s.get("duration_seconds") is not None:
                    target["duration"] = {"value": s["duration_seconds"], "unit": "s"}
                if s.get("distance_meters") is not None:
                    target["distance"] = {"value": s["distance_meters"], "unit": "m"}
                if s.get("rpe") is not None:
                    target["effort"] = {"scale": "rpe", "value": s["rpe"]}
                sets.append({"order": s.get("index", 0), "tag": tag,
                             "target": target or None})
            rest = ex.get("rest_seconds")
            entries.append({
                "order": ex.get("index", 0),
                "exercise_id": self._exercise_id_for_title(
                    ex.get("exercise_template_id", ""), ex.get("title", "")),
                "group_key": (None if ex.get("superset_id") is None
                              else f"ss{ex['superset_id']}"),
                "rest": ({"value": float(rest), "unit": "s"} if rest else None),
                "notes": ex.get("notes") or "",
                "sets": sets, "progression": [],
            })
        tags = []
        folder = folders.get(r.get("folder_id"))
        if folder:
            tags.append(f"folder:{folder}")
        return {
            "fitir": FITIR_VERSION, "kind": "plan", "id": ir_id,
            "refs": [{"system": SYSTEM, "id": r["id"], "kind": "routine"}],
            "ext": {}, "name": r.get("title") or "", "description": "",
            "tags": tags,
            "days": [{"name": r.get("title") or "", "order": 0,
                      "is_rest": False, "entries": entries}],
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at") or db.now_iso(),
        }

    def lower_body_measurement(self, m: dict[str, Any]) -> list[dict[str, Any]]:
        docs = []
        date = m["date"]
        for field, (key, unit) in BODY_METRIC_FIELDS.items():
            value = m.get(field)
            if value is None:
                continue
            docs.append({
                "fitir": FITIR_VERSION, "kind": "body-metric",
                "id": f"bm_{date}_{key}",
                "refs": [{"system": SYSTEM, "id": f"{date}", "kind": "body_measurement"}],
                "ext": {}, "metric_key": key, "at": date,
                "quantity": {"value": float(value), "unit": unit},
                "notes": "", "updated_at": db.now_iso(),
            })
        return docs

    # --- sync -------------------------------------------------------------

    def pull(self) -> dict[str, Any]:
        summary = {"workouts": 0, "deleted": 0, "routines": 0,
                   "exercises": 0, "body_metrics": 0, "mode": "full"}
        last_sync = db.get_setting("hevy_last_sync")
        sync_started = db.now_iso()
        with self._client() as client:
            for t in self._paged(client, "/exercise_templates", "exercise_templates",
                                 page_size=100):
                db.archive_raw(SYSTEM, "exercise_template", t["id"], t)
                self._exercise_ir_id(t)
                summary["exercises"] += 1

            folders = {}
            for f in self._paged(client, "/routine_folders", "routine_folders"):
                folders[f["id"]] = f.get("title", "")
            for r in self._paged(client, "/routines", "routines"):
                db.archive_raw(SYSTEM, "routine", r["id"], r)
                doc = self.lower_routine(r, folders)
                db.put_doc_if_changed(doc)
                db.put_ref(SYSTEM, "routine", r["id"], "plan", doc["id"])
                summary["routines"] += 1

            if last_sync:
                summary["mode"] = "incremental"
                self._pull_workout_events(client, last_sync, summary)
            else:
                for w in self._paged(client, "/workouts", "workouts"):
                    self._store_workout(w)
                    summary["workouts"] += 1

            for m in self._paged(client, "/body_measurements", "measurements"):
                db.archive_raw(SYSTEM, "body_measurement", m["date"], m)
                for doc in self.lower_body_measurement(m):
                    db.put_doc_if_changed(doc)
                    summary["body_metrics"] += 1

        db.put_setting("hevy_last_sync", sync_started)
        return summary

    def _store_workout(self, w: dict[str, Any]) -> None:
        db.archive_raw(SYSTEM, "workout", w["id"], w)
        doc = self.lower_workout(w)
        db.put_doc_if_changed(doc)
        db.put_ref(SYSTEM, "workout", w["id"], "session", doc["id"])

    def _pull_workout_events(self, client: httpx.Client, since: str,
                             summary: dict[str, Any]) -> None:
        for event in self._paged(client, "/workouts/events", "events", since=since):
            if event.get("type") == "updated":
                self._store_workout(event["workout"])
                summary["workouts"] += 1
            elif event.get("type") == "deleted":
                ir_id = db.find_ref(SYSTEM, "workout", event["id"])
                if ir_id:
                    doc = db.get_doc("session", ir_id)
                    if doc and not doc.get("deleted_at"):
                        doc["deleted_at"] = event.get("deleted_at") or db.now_iso()
                        # bump updated_at so exporters see the tombstone
                        doc["updated_at"] = db.now_iso()
                        db.put_doc(doc)
                        summary["deleted"] += 1
