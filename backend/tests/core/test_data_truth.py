from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.data_truth.boundary import (
    parse_basis_points,
    parse_decimal,
    parse_money,
    parse_optional,
    parse_percentage,
    parse_rate,
    parse_ratio,
    parse_result,
    parse_status,
    result_read,
)
from app.core.data_truth.types import (
    BasisPoints,
    CalculationResult,
    Money,
    NotApplicable,
    NumericKind,
    Percentage,
    Rate,
    Ratio,
    ResultStatus,
    Unavailable,
    Value,
    map_result,
)


@pytest.mark.parametrize("raw", ["123.4500", Decimal("123.4500"), 123])
def test_exact_boundary_numbers(raw: object) -> None:
    assert parse_money(raw, currency="USD").amount == Decimal(str(raw))


@pytest.mark.parametrize("raw", [1.25, True, False, None, "NaN", "Infinity", "-Infinity", "bad"])
def test_boundary_rejects_unknown_or_lossy_numbers(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_decimal(raw)


@pytest.mark.parametrize("raw", [1.25, 1, True, Decimal("NaN"), Decimal("Infinity")])
def test_direct_construction_cannot_bypass_decimal_validation(raw: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        Money(cast(Decimal, raw), "USD")
    for kind in (Rate, Ratio, Percentage, BasisPoints):
        with pytest.raises((TypeError, ValueError)):
            kind(cast(Decimal, raw))


@pytest.mark.parametrize("currency", ["", "usd", "US", "USDD", "123", "ÅBC"])
def test_currency_is_required_and_has_no_default(currency: str) -> None:
    with pytest.raises(ValueError):
        parse_money("1", currency=currency)


def test_currency_arithmetic_and_explicit_scales_preserve_complete_values() -> None:
    amount = Money(Decimal("123.4500"), "USD")
    other = Money(Decimal("76.5500"), "USD")
    assert amount + other == Money(Decimal("200.0000"), "USD")
    assert amount - other == Money(Decimal("46.9000"), "USD")
    assert amount.amount.as_tuple().exponent == -4
    rate = Rate(Decimal("0.0525"))
    assert rate.to_percentage() == Percentage(Decimal("5.25"))
    assert rate.to_basis_points() == BasisPoints(Decimal("525"))
    assert rate.to_percentage().to_rate() == rate.to_basis_points().to_rate() == rate
    assert amount.scale(rate) == Money(amount.amount * Decimal("0.0525"), "USD")
    assert amount.scale(Ratio(Decimal("0.5"))).amount == amount.amount / 2
    assert Percentage(Decimal("50")).to_ratio() == Ratio(Decimal("0.5"))
    assert Ratio(Decimal("0.5")).to_percentage() == Percentage(Decimal("50"))
    with pytest.raises(ValueError, match="matching currencies"):
        _ = amount + Money(Decimal("1"), "EUR")
    with pytest.raises(TypeError):
        _ = amount + cast(Money, Ratio(Decimal("1")))
    with pytest.raises(TypeError):
        amount.scale(cast(Rate, Percentage(Decimal("5"))))


def test_missing_db_value_is_unavailable_but_zero_is_a_real_value() -> None:
    def money(raw: object) -> Money:
        return parse_money(raw, currency="USD")

    assert parse_optional(None, money, missing_reason="Balance not supplied") == Unavailable(
        "Balance not supplied"
    )
    assert parse_optional("0", money, missing_reason="Balance not supplied") == Value(
        Money(Decimal(0), "USD")
    )
    with pytest.raises(ValueError):
        parse_optional("bad", money, missing_reason="Balance not supplied")


def test_zero_denominator_is_unavailable_with_reason() -> None:
    amount = Money(Decimal("2"), "USD")
    assert amount.ratio_to(Money(Decimal(0), "USD"), zero_reason="No denominator") == Unavailable(
        "No denominator"
    )
    assert amount.ratio_to(amount, zero_reason="No denominator") == Value(Ratio(Decimal(1)))


@pytest.mark.parametrize("state", [Unavailable, NotApplicable])
@pytest.mark.parametrize("reason", ["", "  \n "])
def test_absent_states_require_meaningful_reason(
    state: type[Unavailable | NotApplicable], reason: str
) -> None:
    with pytest.raises(ValueError):
        state(reason)


@pytest.mark.parametrize(
    "result",
    [
        Value(Money(Decimal("123.4500"), "USD")),
        Value(Rate(Decimal("0.05"))),
        Value(Ratio(Decimal("1.02"))),
        Value(Percentage(Decimal("5"))),
        Value(BasisPoints(Decimal("500"))),
        Unavailable("Missing balance"),
        NotApplicable("Instrument is outside scope"),
    ],
)
def test_api_and_export_contract_round_trips_all_kinds_and_states(
    result: CalculationResult[NumericKind],
) -> None:
    wire = TypeAdapter(dict[str, object]).validate_json(result_read(result).model_dump_json())
    assert parse_result(wire) == result
    if isinstance(result, (Unavailable, NotApplicable)):
        assert wire == {"status": result.status.value, "reason": result.reason}
    else:
        value = TypeAdapter(dict[str, str]).validate_python(wire["value"])
        assert value["kind"] in {"money", "rate", "ratio", "percentage", "basis_points"}


@pytest.mark.parametrize(
    "wire",
    [
        None,
        0,
        {"status": "unknown", "reason": "x"},
        {"status": "unavailable", "reason": "x", "value": 0},
        {"status": "not_applicable", "reason": " "},
        {"status": "unavailable"},
        {"status": "value", "value": None},
        {"status": "value", "value": {"kind": "money", "amount": "0"}},
        {"status": "value", "value": {"kind": "rate", "fraction": 0.05}},
        {"status": "value", "value": {"kind": "ratio", "fraction": "NaN"}},
        {"status": "value", "value": {"kind": "percentage", "fraction": "5"}},
    ],
)
def test_invalid_result_payload_cannot_render_as_a_figure(wire: object) -> None:
    with pytest.raises(ValidationError):
        parse_result(wire)


def test_availability_propagates_without_calling_arithmetic() -> None:
    calls: list[Rate] = []

    def percentage(rate: Rate) -> Percentage:
        calls.append(rate)
        return rate.to_percentage()

    assert map_result(Unavailable("Missing rate"), percentage) == Unavailable("Missing rate")
    assert map_result(NotApplicable("No rate exposure"), percentage) == NotApplicable(
        "No rate exposure"
    )
    assert calls == []
    assert map_result(Value(Rate(Decimal("0.05"))), percentage) == Value(Percentage(Decimal(5)))
    assert calls == [Rate(Decimal("0.05"))]


def test_boundary_parsers_retain_numeric_kinds() -> None:
    assert parse_rate("0.05") == Rate(Decimal("0.05"))
    assert parse_ratio("0.05") == Ratio(Decimal("0.05"))
    assert parse_percentage("5").to_rate() == parse_basis_points("500").to_rate()


@pytest.mark.parametrize("status", list(ResultStatus))
def test_recorded_status_is_parsed_to_closed_enum(status: ResultStatus) -> None:
    assert parse_status(status.value, ResultStatus) is status


@pytest.mark.parametrize("raw", [None, 0, True, "draft", ""])
def test_unknown_recorded_status_has_no_fallback(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_status(raw, ResultStatus)


@pytest.mark.parametrize("raw", [None, 0, Decimal("NaN")])
def test_value_cannot_wrap_a_missing_or_untyped_number(raw: object) -> None:
    with pytest.raises(TypeError):
        Value(cast(Money, raw))
