"""The shape every Pillar 2 method answers in, and the refusal it raises.

A method is a pure function: bank figures and RESOLVED governed parameters go
in, a :class:`MethodResult` comes out. The result carries the canonical amount,
the basis it was converted from, every intermediate as a string (so a digest
over it is value-based), the machine reasons it is not complete, and the
parameter CODES it rested on — never the parameter values' provenance rows,
which the service attaches, and never a sentence, which the service renders.

Two states deserve naming because they are not failures:

* ``not_capitalised`` — the risk is assessed and managed but carries no capital
  line at all (liquidity, under the LRMD);
* ``interim_non_sf`` — a computed figure from a method the platform itself
  labels as interim, so nobody reads it as the standardised framework (D-013).

A required governed parameter that resolves to nothing raises
:class:`MissingParameter`, which the service maps to a typed ``missing_parameter``
refusal naming the code. There is no fallback value anywhere in this package
(D-024).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from app.domain.icaap.units import Basis


class MissingParameter(LookupError):
    """A governed value this method needs was not resolved.

    The service turns this into ``missing_parameter`` with the code, so the
    operator is told which console row to fill rather than being handed a
    number nobody approved.
    """

    def __init__(self, param_code: str, *, detail: str | None = None):
        self.param_code = param_code
        self.detail = detail
        super().__init__(param_code if detail is None else f"{param_code}: {detail}")


class MethodStatus(StrEnum):
    """How far a method got."""

    COMPUTED = "computed"
    #: Computed, but by a method the platform declares interim (D-013).
    INTERIM_NON_SF = "interim_non_sf"
    #: Some required input is absent; the figure would be understated.
    INCOMPLETE = "incomplete"
    #: The method cannot apply to this cycle's data at all.
    NOT_COMPUTABLE = "not_computable"
    #: Assessed deliberately without a capital line.
    NOT_CAPITALISED = "not_capitalised"


#: How a stressed figure was arrived at. ``method`` = recomputed on stressed
#: denominators; ``same_as_baseline`` / ``same_as_stressed`` = a declared,
#: visible choice; ``not_assessed`` = the stressed side is genuinely absent.
Derivation = Literal[
    "method",
    "adopted",
    "same_as_baseline",
    "same_as_stressed",
    "max_of_baseline_and_scenario",
    "not_assessed",
    "not_applicable",
]

MethodPhase = Literal["P2", "P5"]
InputMode = Literal["bound_blocks", "manual_with_evidence"]


@dataclass(frozen=True)
class ParameterUse:
    """A governed code a result rested on. Codes only — no values, no rows."""

    code: str
    #: What it was used for, e.g. ``single_name:hhi``. Free-form, machine-ish.
    role: str | None = None


@dataclass(frozen=True)
class MethodResult:
    """One Pillar 2 quantification, in the canonical unit (D-009)."""

    method: str
    method_version: str
    status: MethodStatus
    basis: Basis | None = None
    basis_value: Decimal | None = None
    baseline_amount: Decimal | None = None
    stressed_amount: Decimal | None = None
    baseline_derivation: Derivation = "method"
    stressed_derivation: Derivation = "not_assessed"
    scenario_definition: Mapping[str, Any] | None = None
    #: Every intermediate, as a string, so a digest over the result is
    #: value-based and reproducible.
    detail: Mapping[str, str | None] = field(default_factory=lambda: MappingProxyType({}))
    #: Machine codes, e.g. ``required_scenario_missing:parallel_up_450``.
    reasons: tuple[str, ...] = ()
    parameters_used: tuple[ParameterUse, ...] = ()

    @property
    def computed(self) -> bool:
        return self.status in (MethodStatus.COMPUTED, MethodStatus.INTERIM_NON_SF)


@dataclass(frozen=True)
class MethodSpec:
    """What a method needs, for the service to check before it computes."""

    key: str
    label: str
    phase: MethodPhase
    scenario_based: bool
    supported_input_modes: tuple[InputMode, ...]
    requires_blocks: tuple[str, ...]


def detail_of(pairs: Mapping[str, str | None]) -> Mapping[str, str | None]:
    """Freeze a detail mapping so a result cannot be edited after the fact."""
    return MappingProxyType(dict(pairs))


def text(value: Decimal | int | str | None) -> str | None:
    """A detail value as a string, keeping ``None`` distinct from zero."""
    return None if value is None else str(value)


def codes(uses: Sequence[ParameterUse]) -> tuple[str, ...]:
    """The distinct codes in ``uses``, in first-seen order."""
    seen: list[str] = []
    for use in uses:
        if use.code not in seen:
            seen.append(use.code)
    return tuple(seen)


__all__ = [
    "Derivation",
    "InputMode",
    "MethodPhase",
    "MethodResult",
    "MethodSpec",
    "MethodStatus",
    "MissingParameter",
    "ParameterUse",
    "codes",
    "detail_of",
    "text",
]
