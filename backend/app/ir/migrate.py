"""FitIR version migration registry (ir-spec.md §5, ADR-STR-004).

Stored documents are never rewritten; migration happens lazily on read via
`upgrade()`, chaining registered pure functions until the current version.

To ship FitIR 2.0: bump FITIR_VERSION in schema.py and register

    @migration("1.1", "2.0")
    def _1_1_to_2_0(body: dict) -> dict: ...
"""
from __future__ import annotations

from typing import Any, Callable

from .schema import FITIR_VERSION

Migration = Callable[[dict[str, Any]], dict[str, Any]]

_MIGRATIONS: dict[str, tuple[str, Migration]] = {}


def migration(src: str, dst: str) -> Callable[[Migration], Migration]:
    def register(fn: Migration) -> Migration:
        _MIGRATIONS[src] = (dst, fn)
        return fn

    return register


def upgrade(body: dict[str, Any]) -> dict[str, Any]:
    """Chain-migrate a document dict to FITIR_VERSION."""
    version = str(body.get("fitir", "1.0"))
    guard = 0
    while version != FITIR_VERSION:
        if version.split(".")[0] == FITIR_VERSION.split(".")[0]:
            # Same major: forward-compatible by the additive-only rule.
            body = {**body, "fitir": FITIR_VERSION}
            break
        if version not in _MIGRATIONS:
            raise ValueError(f"no migration path from fitir {version}")
        version, fn = _MIGRATIONS[version][0], _MIGRATIONS[version][1]
        body = {**fn(body), "fitir": version}
        guard += 1
        if guard > 100:
            raise ValueError("migration cycle detected")
    return body
