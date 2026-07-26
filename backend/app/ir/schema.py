"""FitIR core schema — executable form of docs/ir-spec.md.

This module must not import any connector code (ADR-STR-001).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

FITIR_VERSION = "1.0"

Unit = Literal["kg", "m", "s", "count", "cm", "percent"]
SetTag = Literal[
    "normal", "warmup", "dropset", "failure",
    "myo", "partial", "forced", "tut", "iso", "jump",
]
MetricKind = Literal[
    "weight_reps", "reps", "bodyweight_reps", "assisted_reps",
    "duration", "weight_duration", "distance_duration", "weight_distance",
]
EffortScale = Literal["rpe", "rir"]
Kind = Literal["exercise", "plan", "session", "body-metric"]


class Quantity(BaseModel):
    value: float
    unit: Unit


class Effort(BaseModel):
    scale: EffortScale
    value: float


class Ref(BaseModel):
    system: str
    id: str
    kind: str


class Envelope(BaseModel):
    """Shared document envelope (ir-spec.md §1). Unknown fields are ignored
    on read (minor-version forward compatibility)."""

    model_config = ConfigDict(extra="ignore")

    fitir: str = FITIR_VERSION
    kind: Kind
    id: str
    refs: list[Ref] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None
    ext: dict[str, Any] = Field(default_factory=dict)


class RepRange(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None


class SetTarget(BaseModel):
    reps: Optional[RepRange] = None
    weight: Optional[Quantity] = None
    duration: Optional[Quantity] = None
    distance: Optional[Quantity] = None
    effort: Optional[Effort] = None


class PlanSet(BaseModel):
    order: int = 0
    tag: SetTag = "normal"
    target: Optional[SetTarget] = None


class ProgressionRule(BaseModel):
    param: Literal["weight", "reps", "sets", "rest", "effort"]
    iteration: int = 1
    op: Literal["add", "subtract", "replace"] = "add"
    step: Literal["abs", "percent"] = "abs"
    value: float = 0
    repeat: bool = False
    condition: Optional[dict[str, Any]] = None


class PlanEntry(BaseModel):
    order: int = 0
    exercise_id: str
    group_key: Optional[str] = None
    rest: Optional[Quantity] = None
    notes: str = ""
    sets: list[PlanSet] = Field(default_factory=list)
    progression: list[ProgressionRule] = Field(default_factory=list)


class PlanDay(BaseModel):
    name: str = ""
    order: int = 0
    is_rest: bool = False
    entries: list[PlanEntry] = Field(default_factory=list)


class PlanDoc(Envelope):
    kind: Literal["plan"] = "plan"
    name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    days: list[PlanDay] = Field(default_factory=list)


class SetActual(BaseModel):
    reps: Optional[float] = None
    weight: Optional[Quantity] = None
    duration: Optional[Quantity] = None
    distance: Optional[Quantity] = None
    effort: Optional[Effort] = None
    extra_metrics: dict[str, float] = Field(default_factory=dict)


class SessionSet(BaseModel):
    order: int = 0
    tag: SetTag = "normal"
    actual: SetActual = Field(default_factory=SetActual)
    prescription: Optional[SetTarget] = None


class SessionExercise(BaseModel):
    order: int = 0
    exercise_id: str
    group_key: Optional[str] = None
    notes: str = ""
    sets: list[SessionSet] = Field(default_factory=list)


class SessionDoc(Envelope):
    kind: Literal["session"] = "session"
    title: str = ""
    plan_id: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    notes: str = ""
    mood: Optional[int] = None
    exercises: list[SessionExercise] = Field(default_factory=list)


class ExerciseDoc(Envelope):
    kind: Literal["exercise"] = "exercise"
    name: str
    aliases: list[str] = Field(default_factory=list)
    metric_kind: MetricKind = "weight_reps"
    primary_muscles: list[str] = Field(default_factory=list)
    secondary_muscles: list[str] = Field(default_factory=list)
    equipment_category: str = "none"
    is_custom: bool = False


class BodyMetricDoc(Envelope):
    kind: Literal["body-metric"] = "body-metric"
    metric_key: str
    at: str
    quantity: Quantity
    notes: str = ""


DOC_TYPES: dict[str, type[Envelope]] = {
    "exercise": ExerciseDoc,
    "plan": PlanDoc,
    "session": SessionDoc,
    "body-metric": BodyMetricDoc,
}


def parse_doc(body: dict[str, Any]) -> Envelope:
    """Validate a raw dict as a FitIR document (after migration)."""
    return DOC_TYPES[body["kind"]].model_validate(body)


def slugify(name: str) -> str:
    """ASCII slug (kept for non-exercise uses). Exercise IDs use exercise_id_from_name."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return text or "unnamed"


def exercise_id_from_name(name: str, fallback: str = "") -> str:
    """Stable merge key for exercise docs (ir-spec.md §3.1).

    Hash of NFKC-normalized name so any Unicode script works and two
    importers that agree on the name produce the same IR id.  When the
    name is empty the Hevy template id is used as a fallback so there is
    never a collision on the empty string."""
    key = unicodedata.normalize("NFKC", (name or "").strip()).lower()
    if not key:
        key = f"__fallback:{fallback}"
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"exr_{h}"


def rpe_to_rir(rpe: float) -> float:
    return max(0.0, 10.0 - rpe)


def rir_to_rpe(rir: float) -> float:
    return max(0.0, 10.0 - rir)
