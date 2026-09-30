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
from collections.abc import Callable
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
    #: Field → the longest value the platform can STORE for it, for fields a
    #: downstream mart carries verbatim into a bounded column (audit A360 H3 /
    #: A8-01). Checked here, at the door, so an unstorable value is refused with a
    #: message naming the limit rather than accepted and then failing the tenant's
    #: whole nightly mart build on Postgres — which SQLite, ignoring VARCHAR
    #: lengths, can never show. A test per register pins each width against the
    #: mart model's own ``String(n)``; the schema module itself stays free of
    #: ``app.models``.
    max_lengths: dict[str, int] = field(default_factory=dict)
    #: one row per … (documentation)
    grain: str = ""
    #: The kind's own extra rules, when the declarative fields above cannot
    #: express them — "the period must be the last day of the grain it names",
    #: "a field spelt both ways must agree with itself". Set through
    #: ``dataclasses.replace`` after the function is defined, because the rules
    #: need the schema they belong to. :meth:`problems_for`, not
    #: :meth:`validate_row`, is what a caller should ask.
    row_validator: Callable[[dict], list[str]] | None = None

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
        for name, limit in self.max_lengths.items():
            value = row.get(name)
            if value in (None, ""):
                continue
            length = len(str(value).strip())
            if length > limit:
                problems.append(
                    f"field '{name}' is {length} characters long, above the {limit}-character "
                    f"limit the platform can store; send the identifier or name as your "
                    f"system holds it"
                )
        return problems

    def problems_for(self, row: dict) -> list[str]:
        """EVERY problem with one row — the declarative checks plus this kind's
        own rules. This is the question the ingestion path asks.

        :meth:`validate_row` is the declarative half only, and the extra rules
        call it themselves, so asking it directly silently skips them. That is
        exactly how the period-end and alias-conflict rules came to be written,
        tested, and never run on a real push (audit A7-07 / H-027).
        """
        if self.row_validator is not None:
            return self.row_validator(row)
        return self.validate_row(row)

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
