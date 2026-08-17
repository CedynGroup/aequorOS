"""Per-kind schemas for reference datasets (validation + CSV template hints).

Reference rows are preserved verbatim by the platform (no fixed schema at the
storage layer); a :class:`ReferenceSchema` declares what a WELL-FORMED row of a
kind looks like — required fields, numeric fields, enumerations — so the batch
validator can surface missing/invalid fields as findings, the app can offer a
CSV template, and downstream readers (bog_forms ``refs.*`` resolvers) can rely
on documented field names. One module per kind; register it in ``SCHEMAS``.
Docs: docs/data_engine/datasets/<kind>.md.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReferenceSchema:
    kind: str
    description: str
    required: tuple[str, ...]
    numeric: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)
    optional: tuple[str, ...] = ()
    #: one row per … (documentation)
    grain: str = ""

    def validate_row(self, row: dict) -> list[str]:
        """Return human-readable problems for one payload row (empty = OK)."""
        problems: list[str] = []
        for name in self.required:
            if row.get(name) in (None, ""):
                problems.append(f"missing required field '{name}'")
        for name in self.numeric:
            value = row.get(name)
            if value in (None, ""):
                continue
            try:
                float(str(value).replace(",", ""))
            except ValueError:
                problems.append(f"field '{name}' must be numeric (got {value!r})")
        for name, allowed in self.enums.items():
            value = row.get(name)
            if value not in (None, "") and str(value) not in allowed:
                problems.append(f"field '{name}' must be one of {list(allowed)} (got {value!r})")
        return problems

    @property
    def columns(self) -> tuple[str, ...]:
        seen: list[str] = []
        for name in (*self.required, *self.optional):
            if name not in seen:
                seen.append(name)
        return tuple(seen)


SCHEMAS: dict[str, ReferenceSchema] = {}


def register(schema: ReferenceSchema) -> ReferenceSchema:
    SCHEMAS[schema.kind] = schema
    return schema


def schema_for(kind: str) -> ReferenceSchema | None:
    return SCHEMAS.get(kind)


for _module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module.name}")
