"""Parse external values once; preserve kinds and availability in API/export payloads.

Decimal strings, integers and database Decimals are accepted. Binary floats,
booleans, non-finite numbers and malformed tagged results are rejected.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal, assert_never

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from app.core.data_truth.types import (
    BasisPoints,
    CalculationResult,
    Money,
    NotApplicable,
    NumericKind,
    Percentage,
    Rate,
    Ratio,
    Unavailable,
    Value,
)


def parse_decimal(raw: object) -> Decimal:
    """Never repair a binary float by wrapping it in str or Decimal."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        raise ValueError("A financial number requires a decimal string, integer or Decimal.")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal value.") from exc
    if not value.is_finite():
        raise ValueError("A financial number must be finite.")
    return value


type DecimalInput = Annotated[Decimal, BeforeValidator(parse_decimal)]
type Reason = Annotated[str, Field(min_length=1, pattern=r"\S")]
type Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MoneyRead(_ClosedModel):
    kind: Literal["money"] = "money"
    amount: DecimalInput
    currency: Currency


class RateRead(_ClosedModel):
    kind: Literal["rate"] = "rate"
    fraction: DecimalInput


class RatioRead(_ClosedModel):
    kind: Literal["ratio"] = "ratio"
    fraction: DecimalInput


class PercentageRead(_ClosedModel):
    kind: Literal["percentage"] = "percentage"
    points: DecimalInput


class BasisPointsRead(_ClosedModel):
    kind: Literal["basis_points"] = "basis_points"
    points: DecimalInput


type NumericRead = Annotated[
    MoneyRead | RateRead | RatioRead | PercentageRead | BasisPointsRead, Field(discriminator="kind")
]


class ValueRead(_ClosedModel):
    status: Literal["value"] = "value"
    value: NumericRead


class UnavailableRead(_ClosedModel):
    status: Literal["unavailable"] = "unavailable"
    reason: Reason


class NotApplicableRead(_ClosedModel):
    status: Literal["not_applicable"] = "not_applicable"
    reason: Reason


type ResultRead = Annotated[
    ValueRead | UnavailableRead | NotApplicableRead, Field(discriminator="status")
]
_RESULT: TypeAdapter[ResultRead] = TypeAdapter(ResultRead)


def parse_money(raw: object, *, currency: str) -> Money:
    """The caller resolves currency from the bank or instrument, never a fallback."""
    return Money(parse_decimal(raw), currency)


def parse_rate(raw: object) -> Rate:
    return Rate(parse_decimal(raw))


def parse_ratio(raw: object) -> Ratio:
    return Ratio(parse_decimal(raw))


def parse_percentage(raw: object) -> Percentage:
    return Percentage(parse_decimal(raw))


def parse_basis_points(raw: object) -> BasisPoints:
    return BasisPoints(parse_decimal(raw))


def parse_status[E: StrEnum](raw: object, status_type: type[E]) -> E:
    """Decode a recorded status into its closed enum, without a fallback state."""
    if not isinstance(raw, str):
        raise ValueError("A recorded status requires a string.")
    return status_type(raw)


def parse_optional[T: NumericKind](
    raw: object, parser: Callable[[object], T], *, missing_reason: str
) -> CalculationResult[T]:
    """Map a nullable DB/ingestion field to unavailability; invalid data still fails."""
    if raw is None:
        return Unavailable(missing_reason)
    return Value(parser(raw))


def _parse_kind(value: NumericRead) -> NumericKind:
    match value:
        case MoneyRead():
            return Money(value.amount, value.currency)
        case RateRead():
            return Rate(value.fraction)
        case RatioRead():
            return Ratio(value.fraction)
        case PercentageRead():
            return Percentage(value.points)
        case BasisPointsRead():
            return BasisPoints(value.points)
        case _:
            assert_never(value)


def parse_result(raw: object) -> CalculationResult[NumericKind]:
    """API/persisted result parsing requires an explicit status and numeric kind."""
    result = _RESULT.validate_python(raw)
    match result:
        case ValueRead():
            return Value(_parse_kind(result.value))
        case UnavailableRead():
            return Unavailable(result.reason)
        case NotApplicableRead():
            return NotApplicable(result.reason)
        case _:
            assert_never(result)


def _kind_read(value: NumericKind) -> NumericRead:
    match value:
        case Money():
            return MoneyRead(amount=value.amount, currency=value.currency)
        case Rate():
            return RateRead(fraction=value.fraction)
        case Ratio():
            return RatioRead(fraction=value.fraction)
        case Percentage():
            return PercentageRead(points=value.points)
        case BasisPoints():
            return BasisPointsRead(points=value.points)
        case _:
            assert_never(value)


def result_read(result: CalculationResult[NumericKind]) -> ResultRead:
    """Shared API/export representation: absence never has a numeric value field."""
    match result:
        case Value():
            return ValueRead(value=_kind_read(result.value))
        case Unavailable():
            return UnavailableRead(reason=result.reason)
        case NotApplicable():
            return NotApplicableRead(reason=result.reason)
        case _:
            assert_never(result)
