"""The one place a Pillar 2 figure changes units.

A Pillar 2 add-on is quoted four different ways by four different sources: the
capital plan thinks in percentage points of total RWA, a concentration band
table is written against the Pillar 1 credit charge, a supervisory letter names
an amount, and a stress overlay reports thousands. If each consumer converted
for itself, the same risk would be added twice at two sizes.

So there is one canonical unit — ``pillar2_capital_amount``, an amount in the
bank's reporting currency — and one declared ``Basis`` saying what it was
converted FROM (D-009). Every method returns both, and every reader converts
through this module or not at all.

Nothing here knows a rate, a floor or a buffer. The capital ratio used to turn
RWA into a capital requirement arrives as ``Denominators.car_min_pct``, which
the caller resolved from the governed parameter control plane at the relevant
date (D-024). A missing denominator is a typed refusal, never a substitution.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Literal

#: The base of a percentage. Not a regulatory value: it is what "per cent"
#: means.
HUNDRED = Decimal("100")
#: Money precision, matching the engine's ``money()`` quantum.
AMOUNT_QUANTUM = Decimal("0.0001")
#: Ratio precision, matching the engine's ``ratio_pct()`` quantum.
RATIO_QUANTUM = Decimal("0.000001")

ZERO = Decimal(0)


class Basis(StrEnum):
    """What a Pillar 2 figure was quoted against before conversion."""

    #: Percentage points of total RWA — the capital plan's ``add_on_pct_rwa``.
    PCT_TOTAL_RWA = "pct_total_rwa"
    #: Percentage points of credit RWA.
    PCT_CREDIT_RWA = "pct_credit_rwa"
    #: Per cent of the Pillar 1 capital requirement on credit RWA.
    PCT_PILLAR1_CREDIT_CAPITAL = "pct_pillar1_credit_capital"
    #: Already an amount in the reporting currency.
    ABSOLUTE = "absolute"


UnitConversionCode = Literal["denominator_missing", "denominator_not_positive", "negative_value"]


@dataclass(frozen=True)
class Denominators:
    """The bank figures a percentage basis is measured against.

    Every field is optional because a cycle may legitimately hold none of them
    yet; asking for a conversion that needs a missing one is the refusal, not
    holding the gap.
    """

    total_rwa: Decimal | None = None
    credit_rwa: Decimal | None = None
    #: The governed minimum capital ratio resolved at the relevant date, in
    #: percentage points. Supplied by the caller; never defaulted here.
    car_min_pct: Decimal | None = None


class UnitConversionError(ValueError):
    """A basis conversion that cannot be made honestly."""

    def __init__(self, code: UnitConversionCode, basis: Basis, *, denominator: str | None = None):
        self.code: UnitConversionCode = code
        self.basis = basis
        self.denominator = denominator
        super().__init__(f"{code}:{basis.value}" + (f":{denominator}" if denominator else ""))


def amount(value: Decimal) -> Decimal:
    """Quantize to the engine's money precision."""
    return value.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def ratio(value: Decimal) -> Decimal:
    """Quantize to the engine's ratio precision."""
    return value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_UP)


def pillar1_capital(rwa: Decimal, car_min_pct: Decimal) -> Decimal:
    """The Pillar 1 capital requirement carried by ``rwa``.

    ``car_min_pct`` is the governed minimum ratio the caller resolved; this
    function applies it, it does not know it.
    """
    return amount(rwa * car_min_pct / HUNDRED)


def _positive(
    value: Decimal | None, basis: Basis, denominator: str, *, strict: bool = True
) -> Decimal:
    if value is None:
        raise UnitConversionError("denominator_missing", basis, denominator=denominator)
    if strict and value <= ZERO:
        raise UnitConversionError("denominator_not_positive", basis, denominator=denominator)
    return value


def to_amount(basis: Basis, basis_value: Decimal, denominators: Denominators) -> Decimal:
    """Convert a figure quoted on ``basis`` into a reporting-currency amount."""
    if basis_value < ZERO:
        raise UnitConversionError("negative_value", basis)
    if basis is Basis.ABSOLUTE:
        return amount(basis_value)
    if basis is Basis.PCT_TOTAL_RWA:
        total_rwa = _positive(denominators.total_rwa, basis, "total_rwa")
        return amount(basis_value * total_rwa / HUNDRED)
    if basis is Basis.PCT_CREDIT_RWA:
        credit_rwa = _positive(denominators.credit_rwa, basis, "credit_rwa")
        return amount(basis_value * credit_rwa / HUNDRED)
    credit_rwa = _positive(denominators.credit_rwa, basis, "credit_rwa")
    car_min_pct = _positive(denominators.car_min_pct, basis, "car_min_pct")
    return amount(basis_value * pillar1_capital(credit_rwa, car_min_pct) / HUNDRED)


def from_amount(value: Decimal, basis: Basis, denominators: Denominators) -> Decimal:
    """Express an amount on ``basis`` — the exact inverse of :func:`to_amount`."""
    if basis is Basis.ABSOLUTE:
        return amount(value)
    if basis is Basis.PCT_TOTAL_RWA:
        total_rwa = _positive(denominators.total_rwa, basis, "total_rwa")
        return ratio(value * HUNDRED / total_rwa)
    if basis is Basis.PCT_CREDIT_RWA:
        credit_rwa = _positive(denominators.credit_rwa, basis, "credit_rwa")
        return ratio(value * HUNDRED / credit_rwa)
    credit_rwa = _positive(denominators.credit_rwa, basis, "credit_rwa")
    car_min_pct = _positive(denominators.car_min_pct, basis, "car_min_pct")
    return ratio(value * HUNDRED / pillar1_capital(credit_rwa, car_min_pct))


__all__ = [
    "AMOUNT_QUANTUM",
    "HUNDRED",
    "RATIO_QUANTUM",
    "ZERO",
    "Basis",
    "Denominators",
    "UnitConversionCode",
    "UnitConversionError",
    "amount",
    "from_amount",
    "pillar1_capital",
    "ratio",
    "to_amount",
]
