"""Connector registry (architecture.md §3).

To add an app: implement base.Importer / base.Exporter in a new module and
register the factory here. The API and GUI pick it up automatically.
"""
from __future__ import annotations

from typing import Callable

from .hevy import HevyImporter
from .wger import WgerExporter

IMPORTERS: dict[str, Callable[[], object]] = {
    "hevy": HevyImporter,
}

EXPORTERS: dict[str, Callable[[], object]] = {
    "wger": WgerExporter,
}
