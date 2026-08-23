"""Fail-closed outcome states for material calculations (primitive P3).

A material regulatory figure is either *computed from an authoritative input
set* or it is **not a number at all**. Before this module the only way a
calculation could report "I cannot do this" was to fall back to a default, a
zero, or a code constant — and a zero is indistinguishable, downstream, from a
genuine zero. Every such fallback silently manufactures a filed figure.

This module gives every engine a typed vocabulary to return *instead of*
inventing a value:

``NOT_COMPUTABLE``
    The metric is undefined for this input set (e.g. a ratio whose denominator
    is legitimately zero, or a metric outside the institution's regime).
``MISSING_REQUIRED_INPUT``
    A canonical input the authority requires was not ingested.
``POLICY_UNRESOLVED``
    No governed parameter / threshold resolved for this
    jurisdiction x regime x institution class x effective date.
``DATA_QUALITY_BLOCK``
    The inputs exist but failed a quality gate (unbalanced, stale, unmapped
    classification, negative where impossible).
``RECONCILIATION_FAILED``
    Two authoritative sources that must agree do not agree within the declared
    tolerance (engine vs template, package vs source run, Y0 vs point-in-time).

Every state carries a machine code, a human reason, the *specific* items that
are missing or failing, and whether the condition blocks regulatory filing.

Deliberately dependency-light: no SQLAlchemy, no FastAPI, no ``app.services``
imports, so ``app/domain`` stays pure and importable from anywhere (including
the pure engines and the test suite) without a database.

Usage — the two supported shapes:

    from app.domain.authority.outcomes import (
        CalculationOutcome,
        NotComputable,
        OutcomeState,
        outcome,
    )

    # 1. Return a result wrapper (preferred inside pure engines):
    if not hqla_facts:
        return CalculationOutcome.blocked(
            OutcomeState.MISSING_REQUIRED_INPUT,
            metric_id="lcr_pct",
            reason="No HQLA facts for the reporting period.",
            items=("fact:hqla_level1", "fact:hqla_level2a"),
        )
    return CalculationOutcome.computed(Decimal("134.2"), metric_id="lcr_pct")

    # 2. Raise (preferred at a service boundary that must abort a run):
    raise NotComputable(
        outcome(
            OutcomeState.POLICY_UNRESOLVED,
            metric_id="car_pct",
            reason="No car_min parameter for GH / SDI / 2026-06-30.",
            items=("param:car_min",),
        )
    )
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

__all__ = [
    "BLOCKING_STATES",
    "CalculationOutcome",
    "NotComputable",
    "OutcomeDetail",
    "OutcomeState",
    "Severity",
    "outcome",
]


class OutcomeState(StrEnum):
    """The five declared fail-closed states.

    ``COMPUTED`` is not one of them — a successful calculation is represented by
    ``CalculationOutcome.computed(...)``, whose ``state`` is ``None``.
    """

    NOT_COMPUTABLE = "not_computable"
    MISSING_REQUIRED_INPUT = "missing_required_input"
    POLICY_UNRESOLVED = "policy_unresolved"
    DATA_QUALITY_BLOCK = "data_quality_block"
    RECONCILIATION_FAILED = "reconciliation_failed"


class Severity(StrEnum):
    """How hard a state stops the workflow it appears in."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


# Every one of the five states blocks filing by default. A deployment may
# downgrade a *specific occurrence* to advisory (see ``OutcomeDetail.advisory``)
# but the default direction is always fail-closed: an engine that cannot
# establish a figure must not let that figure be filed.
BLOCKING_STATES: frozenset[OutcomeState] = frozenset(OutcomeState)


_STATE_TITLES: Mapping[OutcomeState, str] = {
    OutcomeState.NOT_COMPUTABLE: "Not computable",
    OutcomeState.MISSING_REQUIRED_INPUT: "Missing required input",
    OutcomeState.POLICY_UNRESOLVED: "Policy unresolved",
    OutcomeState.DATA_QUALITY_BLOCK: "Data quality block",
    OutcomeState.RECONCILIATION_FAILED: "Reconciliation failed",
}


@dataclass(frozen=True, slots=True)
class OutcomeDetail:
    """One fail-closed condition against one metric.

    ``items`` names the *specific* offending things, not a category. Use a
    stable ``kind:name`` form so downstream surfaces can group them without
    parsing prose — for example ``fact:hqla_level1``, ``param:car_min``,
    ``cell:BSD3!G9``, ``run:liquidity``, ``register:ecl-assumptions``.
    """

    state: OutcomeState
    metric_id: str
    reason: str
    items: tuple[str, ...] = ()
    advisory: bool = False
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metric_id:
            raise ValueError("OutcomeDetail.metric_id must be a non-empty metric identifier")
        if not self.reason:
            raise ValueError("OutcomeDetail.reason must be a non-empty human explanation")

    @property
    def code(self) -> str:
        """Machine code: ``<state>:<metric_id>`` — stable, greppable, loggable."""
        return f"{self.state.value}:{self.metric_id}"

    @property
    def blocks_filing(self) -> bool:
        """True when this condition must prevent a regulatory filing.

        Fail-closed: every declared state blocks unless the *occurrence* was
        explicitly raised as advisory by the caller that knows the metric is
        not filed (e.g. a dashboard-only indicator).
        """
        return self.state in BLOCKING_STATES and not self.advisory

    @property
    def severity(self) -> Severity:
        return Severity.BLOCKING if self.blocks_filing else Severity.ADVISORY

    @property
    def title(self) -> str:
        return _STATE_TITLES[self.state]

    @property
    def message(self) -> str:
        """Single-line human summary, safe for logs and bank-facing surfaces."""
        base = f"{self.title} for {self.metric_id}: {self.reason}"
        if self.items:
            return f"{base} ({', '.join(self.items)})"
        return base

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form. Keys are stable wire contract — do not rename."""
        return {
            "state": self.state.value,
            "code": self.code,
            "metric_id": self.metric_id,
            "reason": self.reason,
            "items": list(self.items),
            "blocks_filing": self.blocks_filing,
            "severity": self.severity.value,
            "advisory": self.advisory,
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        """Rebuild from :meth:`to_dict`. Derived keys are ignored."""
        raw_items = payload.get("items") or ()
        raw_context = payload.get("context") or {}
        return cls(
            state=OutcomeState(payload["state"]),
            metric_id=str(payload["metric_id"]),
            reason=str(payload["reason"]),
            items=tuple(str(item) for item in raw_items),
            advisory=bool(payload.get("advisory", False)),
            context=dict(raw_context),
        )


def outcome(  # noqa: PLR0913 - keyword-only constructor, each field is required context
    state: OutcomeState,
    *,
    metric_id: str,
    reason: str,
    items: Sequence[str] = (),
    advisory: bool = False,
    context: Mapping[str, Any] | None = None,
) -> OutcomeDetail:
    """Construct an :class:`OutcomeDetail` with keyword clarity at call sites."""
    return OutcomeDetail(
        state=state,
        metric_id=metric_id,
        reason=reason,
        items=tuple(items),
        advisory=advisory,
        context=dict(context or {}),
    )


@dataclass(frozen=True, slots=True)
class CalculationOutcome[ValueT]:
    """Either a computed value, or one or more fail-closed conditions.

    Never both: ``value`` is ``None`` whenever ``details`` is non-empty. This is
    the shape engines return so a caller cannot accidentally read a number that
    was never established.
    """

    metric_id: str
    value: ValueT | None = None
    details: tuple[OutcomeDetail, ...] = ()

    def __post_init__(self) -> None:
        if not self.metric_id:
            raise ValueError("CalculationOutcome.metric_id must be non-empty")
        if self.details and self.value is not None:
            raise ValueError(
                "CalculationOutcome cannot carry both a value and fail-closed details; "
                "a blocked calculation has no number"
            )

    # -- constructors ----------------------------------------------------

    @classmethod
    def computed(cls, value: ValueT, *, metric_id: str) -> CalculationOutcome[ValueT]:
        if value is None:
            raise ValueError(
                "CalculationOutcome.computed requires a value; use .blocked(...) with an "
                "explicit OutcomeState instead of returning None"
            )
        return cls(metric_id=metric_id, value=value, details=())

    @classmethod
    def blocked(  # noqa: PLR0913 - mirrors outcome(); keyword-only
        cls,
        state: OutcomeState,
        *,
        metric_id: str,
        reason: str,
        items: Sequence[str] = (),
        advisory: bool = False,
        context: Mapping[str, Any] | None = None,
    ) -> CalculationOutcome[ValueT]:
        return cls(
            metric_id=metric_id,
            value=None,
            details=(
                outcome(
                    state,
                    metric_id=metric_id,
                    reason=reason,
                    items=items,
                    advisory=advisory,
                    context=context,
                ),
            ),
        )

    @classmethod
    def from_details(
        cls, details: Sequence[OutcomeDetail], *, metric_id: str
    ) -> CalculationOutcome[ValueT]:
        if not details:
            raise ValueError("CalculationOutcome.from_details requires at least one detail")
        return cls(metric_id=metric_id, value=None, details=tuple(details))

    # -- inspection ------------------------------------------------------

    @property
    def ok(self) -> bool:
        return not self.details

    @property
    def blocks_filing(self) -> bool:
        return any(detail.blocks_filing for detail in self.details)

    @property
    def states(self) -> tuple[OutcomeState, ...]:
        return tuple(detail.state for detail in self.details)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(detail.code for detail in self.details)

    def unwrap(self) -> ValueT:
        """Return the value, or raise :class:`NotComputable`.

        Use at a boundary that genuinely cannot proceed without the number.
        """
        if self.details:
            raise NotComputable(*self.details)
        if self.value is None:  # pragma: no cover - guarded by __post_init__
            raise NotComputable(
                outcome(
                    OutcomeState.NOT_COMPUTABLE,
                    metric_id=self.metric_id,
                    reason="Outcome carries neither a value nor a declared state.",
                )
            )
        return self.value

    def merge(self, other: CalculationOutcome[ValueT]) -> CalculationOutcome[ValueT]:
        """Combine two outcomes for the same metric; any block wins."""
        if other.metric_id != self.metric_id:
            raise ValueError(
                f"cannot merge outcomes for different metrics: "
                f"{self.metric_id!r} vs {other.metric_id!r}"
            )
        details = self.details + other.details
        if details:
            return CalculationOutcome(metric_id=self.metric_id, value=None, details=details)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "ok": self.ok,
            "value": self.value,
            "blocks_filing": self.blocks_filing,
            "details": [detail.to_dict() for detail in self.details],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CalculationOutcome[Any]:
        details = tuple(OutcomeDetail.from_dict(item) for item in (payload.get("details") or ()))
        return CalculationOutcome(
            metric_id=str(payload["metric_id"]),
            value=None if details else payload.get("value"),
            details=details,
        )


class NotComputable(Exception):
    """Raised when a material figure cannot be established.

    Carries the full :class:`OutcomeDetail` set so a service layer can persist
    the reason against a run / package rather than logging a bare string.
    """

    def __init__(self, *details: OutcomeDetail) -> None:
        if not details:
            raise ValueError("NotComputable requires at least one OutcomeDetail")
        self.details: tuple[OutcomeDetail, ...] = tuple(details)
        super().__init__("; ".join(detail.message for detail in self.details))

    @property
    def state(self) -> OutcomeState:
        """The first (primary) state — convenient for single-detail raises."""
        return self.details[0].state

    @property
    def code(self) -> str:
        return self.details[0].code

    @property
    def blocks_filing(self) -> bool:
        return any(detail.blocks_filing for detail in self.details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "not_computable",
            "blocks_filing": self.blocks_filing,
            "details": [detail.to_dict() for detail in self.details],
        }
