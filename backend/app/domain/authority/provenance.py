"""Calculation provenance + versioning (primitive P4/P5).

This module does **not** introduce a second provenance store. The repository
already has one and only one authoritative record of "how was this number
produced": :class:`app.models.regulatory_run.RegulatoryRun`. What was missing
was a *formal interface* over it — a typed, dependency-free view that pure
domain code, reporting generators, exports and tests can all agree on, and a
single place that says which fields together constitute complete provenance.

So this module:

* declares :class:`RunLike` — a structural protocol matching the fields
  ``RegulatoryRun`` already has (no SQLAlchemy import, no DB session);
* declares :class:`CalculationProvenance` — the formalised read-model, built by
  :meth:`CalculationProvenance.from_run`;
* preserves byte-compatibility with the existing wire shape via
  :meth:`CalculationProvenance.source_run_entry`, which reproduces exactly the
  ``{module, run_id, input_hash, engine_version}`` dict that
  ``regulatory_reporting.generation._source_run_entry`` already writes into
  ``RegulatoryPackage.source_runs``. Nothing downstream has to change to adopt
  this module.

Field mapping — what the audit asked for, and where it already lives
--------------------------------------------------------------------

===========================  ==================================================
Required provenance element  Existing ``RegulatoryRun`` source
===========================  ==================================================
source run id                ``id``
input hash                   ``input_hash`` (value-based; see CLAUDE.md)
canonical fact versions      ``input_schema_version`` (e.g. ``bank-facts-v3``)
                             — the fact *values* are sealed inside
                             ``input_hash``; ``inputs["facts"]`` holds them
policy resolution            ``inputs["jurisdiction_code"]``, ``inputs["currency"]``
parameter set + version      ``inputs["parameters"]`` (+ ``parameter_digest``,
                             derived here, never stored twice)
parameter ROW identity       ``parameter_provenance`` — which governed
                             ``regulatory_parameter`` rows the run resolved
                             (audit D-18; migration ``202608230039``)
engine + calculation version ``engine_version``, ``output_schema_version``
scenario + version           ``scenario_code`` (+ ``inputs["shocks"]`` where the
                             module carries shocks)
timestamp                    ``completed_at`` (fallback ``started_at``)
actor                        ``created_by``
===========================  ==================================================

**Honest gap (repo reality vs the directive's assumed model):** ``RegulatoryRun``
has *no* explicit scenario-version column. Scenario identity is the
``scenario_code`` string. Rather than inventing a column,
:class:`CalculationProvenance` derives a ``parameter_digest`` from
``inputs["parameters"]`` using the same value-based, sort-keys JSON discipline as
``input_hash`` — it is a *view* of data already sealed in the run, not new stored
state.

**Closed 2026-08-22 (audit D-18):** ``parameter_digest`` identifies the parameter
*values*, and until ``202608230039`` that was the whole story — a reader could
see that a run used 13%, never that the approved row said 13% at the time. The
run now also carries ``parameter_provenance``: the ``regulatory_parameter`` ROWS
it resolved, each with its id, scope key, effective window, confirmation status,
four-eyes evidence and ``updated_at`` version marker. It is read here as
``parameter_rows`` and fingerprinted as ``parameter_rows_digest``, and it lives
BESIDE the hashed snapshot, never inside it — row ids and timestamps are
identity, not values, and ``input_hash`` is value-based by contract. ``None``
means the run predates the column; ``()`` means the run resolved no governed
parameter, which is a different and positive statement.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "REQUIRED_PROVENANCE_FIELDS",
    "CalculationProvenance",
    "ProvenanceIncomplete",
    "RunLike",
    "parameter_digest",
]


@runtime_checkable
class RunLike(Protocol):
    """Structural view of the fields ``RegulatoryRun`` already exposes.

    Deliberately a Protocol: ``app/domain`` must not import ``app/models``.
    Any object carrying these attributes — the ORM row, a test double, a
    deserialised snapshot — satisfies it, and ``isinstance(run, RunLike)``
    verifies that at runtime.

    **Why every member is typed ``Any``, not ``str`` / ``datetime``:** the ORM
    declares these as SQLAlchemy ``Mapped[T]`` descriptors. Attribute *access*
    resolves to ``T``, but a static checker matches a Protocol against the
    *declared* type, so ``Mapped[str]`` is reported as not assignable to
    ``str`` — for property members and plain annotations alike. Narrowing the
    Protocol would therefore make every real call site a type error. The real
    contract lives one layer down, on :class:`CalculationProvenance`, whose
    fields ARE precisely typed and which normalises everything through
    ``str()`` in :meth:`CalculationProvenance.from_run`.
    """

    @property
    def id(self) -> Any: ...
    @property
    def organization_id(self) -> Any: ...
    @property
    def bank_id(self) -> Any: ...
    @property
    def reporting_period_id(self) -> Any: ...
    @property
    def module(self) -> Any: ...
    @property
    def scenario_code(self) -> Any: ...
    @property
    def status(self) -> Any: ...
    @property
    def engine_version(self) -> Any: ...
    @property
    def input_schema_version(self) -> Any: ...
    @property
    def output_schema_version(self) -> Any: ...
    @property
    def input_hash(self) -> Any: ...
    @property
    def inputs(self) -> Any: ...
    @property
    def metrics(self) -> Any: ...
    @property
    def started_at(self) -> Any: ...
    @property
    def completed_at(self) -> Any: ...
    @property
    def created_by(self) -> Any: ...


class ProvenanceIncomplete(ValueError):
    """A run does not carry every element required to file from it."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing: tuple[str, ...] = missing
        super().__init__("incomplete calculation provenance; missing: " + ", ".join(missing))


#: Every element that must be present before a computed figure may be filed.
#: The equality of this tuple with :meth:`CalculationProvenance.to_dict` keys is
#: asserted by ``tests/domain/authority/test_provenance.py``.
REQUIRED_PROVENANCE_FIELDS: tuple[str, ...] = (
    "run_id",
    "organization_id",
    "bank_id",
    "reporting_period_id",
    "module",
    "input_hash",
    "input_schema_version",
    "output_schema_version",
    "engine_version",
    "parameter_digest",
    "scenario_code",
    "computed_at",
    "actor_id",
)


def _canonical_json(payload: Any) -> str:
    """Value-based canonical JSON — the same discipline as ``input_hash``."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def parameter_digest(parameters: Mapping[str, Any] | None) -> str:
    """SHA-256 over the run's governed-parameter block.

    Derived, never stored a second time: the parameters are already inside the
    run's hashed ``inputs``. This gives callers a short, comparable identity for
    "which parameter set produced this" without a new table.
    """
    payload = dict(parameters or {})
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CalculationProvenance:
    """Formalised provenance for one computed regulatory figure set."""

    run_id: str
    organization_id: str
    bank_id: str
    reporting_period_id: str
    module: str
    input_hash: str
    input_schema_version: str
    output_schema_version: str
    engine_version: str
    parameter_digest: str
    scenario_code: str
    computed_at: datetime | None
    actor_id: str
    status: str = "succeeded"
    jurisdiction_code: str | None = None
    base_currency: str | None = None
    as_of_date: str | None = None
    shocks: Mapping[str, Any] | None = None
    #: The governed ``regulatory_parameter`` rows the run resolved (audit D-18).
    #: ``None`` = the run predates ``202608230039`` and cannot say;
    #: ``()`` = the run resolved no governed parameter.
    parameter_rows: tuple[Mapping[str, Any], ...] | None = None

    # -- construction ----------------------------------------------------

    @classmethod
    def from_run(cls, run: RunLike) -> CalculationProvenance:
        """Build from the existing ``RegulatoryRun`` row. No new state."""
        inputs: Mapping[str, Any] = run.inputs or {}
        shocks = inputs.get("shocks")
        # ``getattr``, not a Protocol member: ``RunLike`` is matched structurally
        # and several test doubles and deserialised snapshots predate the column.
        # A double that cannot say reads as "unrecorded", never as "none used".
        rows = getattr(run, "parameter_provenance", None)
        return cls(
            run_id=str(run.id),
            organization_id=str(run.organization_id),
            bank_id=str(run.bank_id),
            reporting_period_id=str(run.reporting_period_id),
            module=str(run.module),
            input_hash=str(run.input_hash),
            input_schema_version=str(run.input_schema_version),
            output_schema_version=str(run.output_schema_version),
            engine_version=str(run.engine_version),
            parameter_digest=parameter_digest(inputs.get("parameters")),
            scenario_code=str(run.scenario_code),
            computed_at=run.completed_at or run.started_at,
            actor_id=str(run.created_by),
            status=str(run.status),
            jurisdiction_code=inputs.get("jurisdiction_code"),
            base_currency=inputs.get("currency"),
            as_of_date=inputs.get("as_of_date"),
            shocks=dict(shocks) if isinstance(shocks, Mapping) else None,
            parameter_rows=None if rows is None else tuple(dict(row) for row in rows),
        )

    # -- inspection ------------------------------------------------------

    def missing_fields(self) -> tuple[str, ...]:
        """Required elements that are absent or empty on this record."""
        missing: list[str] = []
        for name in REQUIRED_PROVENANCE_FIELDS:
            value = getattr(self, name, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(name)
        return tuple(missing)

    @property
    def parameter_rows_digest(self) -> str | None:
        """SHA-256 over the governed rows this run resolved, or ``None``.

        ``None`` when the run predates ``202608230039``. It is deliberately NOT
        derived from an empty list in that case: absence of a record and a record
        of absence are different claims, and only the second may be asserted.
        """
        if self.parameter_rows is None:
            return None
        payload = [dict(row) for row in self.parameter_rows]
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields()

    @property
    def is_filable(self) -> bool:
        """Complete provenance *and* a succeeded run."""
        return self.is_complete and self.status == "succeeded"

    def require_complete(self) -> CalculationProvenance:
        """Return self, or raise :class:`ProvenanceIncomplete`."""
        missing = self.missing_fields()
        if missing:
            raise ProvenanceIncomplete(missing)
        return self

    # -- interop ---------------------------------------------------------

    def source_run_entry(self) -> dict[str, str]:
        """The EXACT existing ``RegulatoryPackage.source_runs`` entry shape.

        Byte-compatible with
        ``regulatory_reporting.generation._source_run_entry``; adopting this
        module requires no migration and no snapshot-hash change.
        """
        return {
            "module": self.module,
            "run_id": self.run_id,
            "input_hash": self.input_hash,
            "engine_version": self.engine_version,
        }

    def to_dict(self) -> dict[str, Any]:
        """Full provenance record. Required keys first, in declared order."""
        payload: dict[str, Any] = {name: getattr(self, name) for name in REQUIRED_PROVENANCE_FIELDS}
        payload["computed_at"] = (
            self.computed_at.isoformat() if self.computed_at is not None else None
        )
        payload["status"] = self.status
        payload["jurisdiction_code"] = self.jurisdiction_code
        payload["base_currency"] = self.base_currency
        payload["as_of_date"] = self.as_of_date
        payload["shocks"] = dict(self.shocks) if self.shocks else None
        payload["parameter_rows"] = (
            None if self.parameter_rows is None else [dict(row) for row in self.parameter_rows]
        )
        payload["parameter_rows_digest"] = self.parameter_rows_digest
        payload["is_complete"] = self.is_complete
        payload["is_filable"] = self.is_filable
        return payload

    def digest(self) -> str:
        """Stable identity of this provenance view (for equivalence tests)."""
        seed = {name: getattr(self, name) for name in REQUIRED_PROVENANCE_FIELDS}
        return hashlib.sha256(_canonical_json(seed).encode("utf-8")).hexdigest()
