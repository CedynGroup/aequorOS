"""Per-form line maps: official input cell → AequorOS source.

Each ``linemaps/<form>.py`` module exposes ``LINES: dict[sheet_name,
tuple[LineSpec, ...]]``. Most maps are built by :func:`_common.leaf_lines`, which
binds EVERY leaf (input) row of an official sheet — a row with a source resolves
from platform data; a row without one is emitted at its official cell as
``input_required`` with a note saying what the bank must supply. That is the
Guide-honest way to keep the full official structure while data catches up.

The registry below is import-driven: adding ``linemaps/bsd4.py`` is all it takes
for BSD4's inputs to start filling.
"""

from __future__ import annotations

import importlib
from functools import cache

from ..spec import LineSpec

_MODULES: dict[str, str] = {
    "BSD1": "bsd1",
    "BSD1A": "bsd1a",
    "BSD1B": "bsd1b",
    "BSD2": "bsd2",
    "BSD2A": "bsd2a",
    "BSD3A": "bsd3a",
    "BSD3B": "bsd3b",
    "BSD4": "bsd4",
    "BSD5A": "bsd5a",
    "BSD5B": "bsd5b",
    "BSD6": "bsd6",
    "BSD7A": "bsd7a",
    "BSD7B": "bsd7b",
    "BSD8": "bsd8",
    "BSD9": "bsd9",
    "BSD10": "bsd10",
    "BSD11": "bsd11",
    "BSD13": "bsd13",
    "BSD14": "bsd14",
    "BSD15A": "bsd15a",
    "BSD15B": "bsd15b",
    "BSD16": "bsd16",
    "BSD17": "bsd17",
}


@cache
def line_maps_for(form_code: str) -> dict[str, tuple[LineSpec, ...]]:
    module_name = _MODULES.get(form_code)
    if module_name is None:
        return {}
    try:
        module = importlib.import_module(f"{__name__}.{module_name}")
    except ModuleNotFoundError as exc:
        # Only swallow the absence of THIS line-map module, never a broken import
        # inside it (which would silently turn a mapped form into structure-only).
        if exc.name == f"{__name__}.{module_name}":
            return {}
        raise
    return dict(getattr(module, "LINES", {}))


def mapped_forms() -> tuple[str, ...]:
    return tuple(code for code in _MODULES if line_maps_for(code))
