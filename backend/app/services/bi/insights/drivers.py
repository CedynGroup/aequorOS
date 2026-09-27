"""The exact ratio bridge, and the one judgement of what "better" means.

Two things live here, and they are together because the second is what makes
the first readable.

The bridge
----------
A ratio moved. ``R = scale × N / D``, so a change in ``R`` has exactly three
sources and no more::

    ΔR = scale × [   ΔN / D₀                 the numerator moving
                   − N₀ · ΔD / (D₀ · D₁)     the denominator moving
                   − ΔN · ΔD / (D₀ · D₁) ]   the two moving together

Adding those three gives ``scale × (N₁/D₁ − N₀/D₀)`` identically — not to a
tolerance, identically — which is the property the whole module exists for: a
reader may add the parts up and must land on the whole.

Two choices are worth naming.

*Three terms, not two.* The co-movement term can be folded into either leg (the
"numerator first" reading puts it in the denominator leg, and vice versa), and
the two readings disagree. Neither ordering is privileged, so folding it would
attribute to one driver a quantity that belongs to both. It is therefore
disclosed as its own part, and omitted only when it is exactly zero — which is
exactly when one of the two did not move at all.

*Exact rationals, then residual-preserving rounding.* Every term is computed in
:class:`~fractions.Fraction`, so no arithmetic here is approximate. Presentation
still has to round, and rounding each part independently would break the very
identity the bridge promises. The parts are therefore quantised by largest
remainder against the quantised TOTAL: each part is floored to a whole number of
quanta and the residue is handed out one quantum at a time, largest fractional
remainder first. The reported parts then sum to the reported change exactly, as
:class:`Decimal` values, with no tolerance anywhere.

A ratio with a zero denominator on either side has no value to bridge, and one
with a missing component has nothing to bridge; both answer
:class:`BridgeUnavailable` rather than a zero.

The judgement
-------------
:func:`favourability` answers "was that move good for the bank?" from a
favourable direction and the two values. The DIRECTIONS are not restated here:
they are the catalogue's ``MeasureDef.favourable_direction``, which
``app/domain/bi/catalogue/directions.py`` already pins, metric by metric, to
``app.services.report_comparison.favorable_direction``. What this function adds
is the same verdict keyed on the direction rather than on a run-metric key,
which is what a portfolio measure — a measure with no run-metric key at all —
needs. ``tests/services/bi/test_insights_drivers.py`` asserts the two agree for
every key in both of ``report_comparison``'s registries, in both directions, so
the judgement cannot drift from the one the comparison surfaces already apply.

The rule that is easiest to get wrong is D-013's: a ``magnitude_lower_better``
figure (a signed change in economic value, earnings at risk) is judged on
MAGNITUDE. A move from −8 to +6 is a REDUCTION in risk and must never read as
adverse merely because the number rose, and a sign flip at equal magnitude is no
change in risk at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from app.domain.bi.catalogue.members import FavourableDirection, ValueType
from app.services.report_comparison import favorability_for_disposition

__all__ = [
    "BridgeComponent",
    "BridgeLeg",
    "BridgeUnavailable",
    "DEFAULT_QUANTUM",
    "Favourability",
    "MoveDirection",
    "RatioBridge",
    "RatioPoint",
    "favourability",
    "move_direction",
    "quantise_exactly",
    "ratio_bridge",
    "scale_for",
]

Favourability = Literal["favourable", "adverse", "neutral"]
MoveDirection = Literal["up", "down", "flat"]
BridgeComponent = Literal["numerator", "denominator", "co_movement"]

#: Presentation precision for a bridged ratio. It governs how finely the parts
#: are reported, never what any figure IS: the arithmetic above is exact and a
#: coarser quantum only merges small parts, it never moves the total.
DEFAULT_QUANTUM = Decimal("0.0001")

#: Production copy for the part of a move that belongs to both components at once.
_CO_MOVEMENT_LABEL = "the two moving together"

#: A percentage measure states ``100 × N / D``; a ratio states ``N / D``.
_PERCENT_SCALE = Fraction(100)
_UNIT_SCALE = Fraction(1)

_ZERO = Decimal(0)


def scale_for(value_type: ValueType) -> Fraction:
    """The multiplier between the raw quotient and the value the bank reads."""
    return _PERCENT_SCALE if value_type == "pct" else _UNIT_SCALE


def move_direction(prior: Decimal, current: Decimal) -> MoveDirection:
    """Which way the figure itself went, before any judgement of it."""
    if current > prior:
        return "up"
    if current < prior:
        return "down"
    return "flat"


def favourability(
    direction: FavourableDirection, prior: Decimal, current: Decimal
) -> Favourability:
    """Was the move from ``prior`` to ``current`` good for the bank?

    ``neutral`` whenever the catalogue declares no favourable direction, and
    whenever the figure did not move. See the module docstring for D-013.
    """
    # ONE definition of this rule, and it lives in ``report_comparison`` because
    # the regulatory comparison surface has judged it since before BI existed.
    # Restating it here is how the two planes would come to disagree about
    # whether a move was good news. The only thing BI owns is the spelling: the
    # regulatory wire value has always been American, this module's is British,
    # and neither is free to change because both are read by a client.
    verdict = favorability_for_disposition(
        direction, prior, current, move_direction(prior, current)
    )
    return "favourable" if verdict == "favorable" else verdict


@dataclass(frozen=True, slots=True)
class RatioPoint:
    """One side of the bridge: the two components and the ratio they make."""

    numerator: Decimal
    denominator: Decimal
    value: Decimal
    """``scale × numerator / denominator``, quantised for presentation."""


@dataclass(frozen=True, slots=True)
class BridgeLeg:
    """One part of the change, and what moved to produce it."""

    component: BridgeComponent
    measure_id: str | None
    """The component measure that moved; ``None`` for the co-movement part."""
    label: str
    contribution: Decimal


@dataclass(frozen=True, slots=True)
class RatioBridge:
    """A ratio's change, split into parts that sum to it exactly."""

    measure_id: str
    numerator_measure_id: str
    denominator_measure_id: str
    prior: RatioPoint
    current: RatioPoint
    change: Decimal
    legs: tuple[BridgeLeg, ...]
    quantum: Decimal
    favourability: Favourability

    @property
    def largest_leg(self) -> BridgeLeg:
        """The part that moved the ratio most, ties broken by bridge order."""
        return max(self.legs, key=lambda leg: abs(leg.contribution))

    def sums_exactly(self) -> bool:
        """The identity this class exists to keep. Asserted by the suite."""
        return sum((leg.contribution for leg in self.legs), _ZERO) == self.change


BridgeUnavailableReason = Literal["component_missing", "denominator_zero"]


@dataclass(frozen=True, slots=True)
class BridgeUnavailable:
    """Why a ratio could not be bridged. Never a zero, never an empty bridge."""

    measure_id: str
    reason: BridgeUnavailableReason


def _to_fraction(value: Decimal) -> Fraction:
    return Fraction(value)


def quantise_exactly(
    total: Fraction, parts: tuple[Fraction, ...], quantum: Decimal
) -> tuple[Decimal, tuple[Decimal, ...]]:
    """Round ``parts`` to ``quantum`` so they still sum to the rounded ``total``.

    Largest-remainder allocation: floor each part to a whole number of quanta,
    then hand the residue out one quantum at a time, largest fractional
    remainder first and, on a tie, in the order the parts were given. The
    returned parts sum to the returned total exactly (no tolerance).
    """
    if not parts:
        return _round_half_even(total, quantum), ()
    step = Fraction(quantum)
    total_units = _round_half_even_units(total / step)
    floors = [math.floor(part / step) for part in parts]
    remainders = [(part / step) - floor for part, floor in zip(parts, floors, strict=True)]
    residue = total_units - sum(floors)
    order = sorted(range(len(parts)), key=lambda index: (-remainders[index], index))
    step_sign = 1 if residue >= 0 else -1
    for count in range(abs(residue)):
        index = order[count % len(order)] if step_sign > 0 else order[-1 - count % len(order)]
        floors[index] += step_sign
    return (
        Decimal(total_units) * quantum,
        tuple(Decimal(units) * quantum for units in floors),
    )


def _round_half_even_units(value: Fraction) -> int:
    """``round()`` on a Fraction is banker's rounding and is exact."""
    return round(value)


def _round_half_even(value: Fraction, quantum: Decimal) -> Decimal:
    return Decimal(_round_half_even_units(value / Fraction(quantum))) * quantum


def ratio_bridge(  # noqa: PLR0913 - the ratio's identity, its two sides and its precision
    *,
    measure_id: str,
    numerator_measure_id: str,
    denominator_measure_id: str,
    numerator_label: str,
    denominator_label: str,
    prior_numerator: Decimal | None,
    prior_denominator: Decimal | None,
    current_numerator: Decimal | None,
    current_denominator: Decimal | None,
    direction: FavourableDirection = "neutral",
    value_type: ValueType = "pct",
    quantum: Decimal = DEFAULT_QUANTUM,
) -> RatioBridge | BridgeUnavailable:
    """Split a ratio's change into the parts that produced it (module docstring)."""
    components = (prior_numerator, prior_denominator, current_numerator, current_denominator)
    if any(component is None for component in components):
        return BridgeUnavailable(measure_id, "component_missing")
    assert prior_numerator is not None  # noqa: S101 - narrowed by the guard above
    assert prior_denominator is not None  # noqa: S101 - narrowed by the guard above
    assert current_numerator is not None  # noqa: S101 - narrowed by the guard above
    assert current_denominator is not None  # noqa: S101 - narrowed by the guard above
    if prior_denominator == 0 or current_denominator == 0:
        return BridgeUnavailable(measure_id, "denominator_zero")

    scale = scale_for(value_type)
    n0, d0 = _to_fraction(prior_numerator), _to_fraction(prior_denominator)
    n1, d1 = _to_fraction(current_numerator), _to_fraction(current_denominator)
    delta_n, delta_d = n1 - n0, d1 - d0

    numerator_effect = scale * delta_n / d0
    denominator_effect = -scale * n0 * delta_d / (d0 * d1)
    co_movement = -scale * delta_n * delta_d / (d0 * d1)
    exact_change = scale * (n1 / d1 - n0 / d0)

    exact_parts: list[tuple[BridgeComponent, str | None, str, Fraction]] = [
        ("numerator", numerator_measure_id, numerator_label, numerator_effect),
        ("denominator", denominator_measure_id, denominator_label, denominator_effect),
    ]
    if co_movement != 0:
        exact_parts.append(("co_movement", None, _CO_MOVEMENT_LABEL, co_movement))

    change, contributions = quantise_exactly(
        exact_change, tuple(part[3] for part in exact_parts), quantum
    )
    legs = tuple(
        BridgeLeg(component=component, measure_id=member_id, label=label, contribution=amount)
        for (component, member_id, label, _), amount in zip(exact_parts, contributions, strict=True)
    )
    prior_value = _round_half_even(scale * n0 / d0, quantum)
    current_value = _round_half_even(scale * n1 / d1, quantum)
    return RatioBridge(
        measure_id=measure_id,
        numerator_measure_id=numerator_measure_id,
        denominator_measure_id=denominator_measure_id,
        prior=RatioPoint(prior_numerator, prior_denominator, prior_value),
        current=RatioPoint(current_numerator, current_denominator, current_value),
        change=change,
        legs=legs,
        quantum=quantum,
        favourability=favourability(direction, prior_value, current_value),
    )
