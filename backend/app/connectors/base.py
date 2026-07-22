"""Connector protocols (architecture.md §3).

Adding a new app = one module implementing Importer and/or Exporter,
registered in connectors/__init__.py. Nothing else changes (ADR-STR-001).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Importer(Protocol):
    """Pulls from an external app and lowers into FitIR documents in the store."""

    name: str

    def pull(self) -> dict[str, Any]:
        """Run one sync cycle. Returns a summary dict for the sync_runs log."""
        ...


@runtime_checkable
class Exporter(Protocol):
    """Reads FitIR documents and pushes them to an external app."""

    name: str

    def preview(self) -> dict[str, Any]:
        """Dry-run: what would be exported, incl. unresolved mappings."""
        ...

    def push(self) -> dict[str, Any]:
        """Perform the export. Returns a report dict."""
        ...
