"""Pure financial kinds. Conversions name the scale; no implicit float or unit coercion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Literal, assert_never


def _finite(value: object) -> None:
    if not isinstance(value, Decimal):
        raise TypeError("Financial values require Decimal.")
    if not value.is_finite():
        raise ValueError("Financial values must be finite.")


def _reason(reason: object) -> None:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("An unavailable or inapplicable figure requires a reason.")


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        _finite(self.amount)
        if len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isalpha():
            raise ValueError("Currency must be a three-letter code resolved at the boundary.")
        if self.currency != self.currency.upper():
            raise ValueError("Currency must be uppercase.")

    def _same_currency(self, other: object) -> None:
        if not isinstance(other, Money):
            raise TypeError("Money arithmetic requires Money.")
        if self.currency != other.currency:
            raise ValueError("Money arithmetic requires matching currencies.")

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def scale(self, factor: Ratio | Rate) -> Money:
        return Money(self.amount * _fraction(factor), self.currency)

    def ratio_to(self, denominator: Money, *, zero_reason: str) -> CalculationResult[Ratio]:
        self._same_currency(denominator)
        if denominator.amount == 0:
            return Unavailable(zero_reason)
        return Value(Ratio(self.amount / denominator.amount))


@dataclass(frozen=True, slots=True)
class Rate:
    """Interest or discount rate as a fraction: 0.05 means 5 percent."""

    fraction: Decimal

    def __post_init__(self) -> None:
        _finite(self.fraction)

    def to_percentage(self) -> Percentage:
        return Percentage(self.fraction * Decimal(100))

    def to_basis_points(self) -> BasisPoints:
        return BasisPoints(self.fraction * Decimal(10_000))


@dataclass(frozen=True, slots=True)
class Ratio:
    """Dimensionless fraction, distinct from a rate and percentage points."""

    fraction: Decimal

    def __post_init__(self) -> None:
        _finite(self.fraction)

    def to_percentage(self) -> Percentage:
        return Percentage(self.fraction * Decimal(100))


@dataclass(frozen=True, slots=True)
class Percentage:
    """Percentage points: 5 means 5 percent; conversion to a fraction is explicit."""

    points: Decimal

    def __post_init__(self) -> None:
        _finite(self.points)

    def to_rate(self) -> Rate:
        return Rate(self.points / Decimal(100))

    def to_ratio(self) -> Ratio:
        return Ratio(self.points / Decimal(100))


@dataclass(frozen=True, slots=True)
class BasisPoints:
    """Basis points: 100 means one percentage point."""

    points: Decimal

    def __post_init__(self) -> None:
        _finite(self.points)

    def to_rate(self) -> Rate:
        return Rate(self.points / Decimal(10_000))


type NumericKind = Money | Rate | Ratio | Percentage | BasisPoints


def _fraction(value: object) -> Decimal:
    if not isinstance(value, (Ratio, Rate)):
        raise TypeError("Convert percentages or basis points to a fraction before scaling.")
    return value.fraction


def _numeric(value: object) -> None:
    if not isinstance(value, (Money, Rate, Ratio, Percentage, BasisPoints)):
        raise TypeError("A figure value requires a validated financial kind.")


class ResultStatus(StrEnum):
    VALUE = "value"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class Value[T: NumericKind]:
    value: T
    status: Literal[ResultStatus.VALUE] = field(default=ResultStatus.VALUE, init=False)

    def __post_init__(self) -> None:
        _numeric(self.value)


@dataclass(frozen=True, slots=True)
class Unavailable:
    reason: str
    status: Literal[ResultStatus.UNAVAILABLE] = field(default=ResultStatus.UNAVAILABLE, init=False)

    def __post_init__(self) -> None:
        _reason(self.reason)


@dataclass(frozen=True, slots=True)
class NotApplicable:
    reason: str
    status: Literal[ResultStatus.NOT_APPLICABLE] = field(
        default=ResultStatus.NOT_APPLICABLE, init=False
    )

    def __post_init__(self) -> None:
        _reason(self.reason)


type CalculationResult[T: NumericKind] = Value[T] | Unavailable | NotApplicable


def map_result[T: NumericKind, U: NumericKind](
    result: CalculationResult[T], transform: Callable[[T], U]
) -> CalculationResult[U]:
    """Apply arithmetic only to a value, preserving the other states and their reasons."""
    match result:
        case Value():
            return Value(transform(result.value))
        case Unavailable() | NotApplicable():
            return result
        case _:
            assert_never(result)
