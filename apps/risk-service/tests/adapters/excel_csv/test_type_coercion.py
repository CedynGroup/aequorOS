from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.adapters.excel_csv.type_coercion import (
    CoercionError,
    coerce_date,
    coerce_int,
    coerce_money,
    coerce_rate,
    coerce_string,
    excel_serial_for,
)


class TestMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("GHS 1,500,000.50", Decimal("1500000.50")),
            ("$74,000", Decimal("74000")),
            ("(2,500.00)", Decimal("-2500.00")),
            ("1500000.5", Decimal("1500000.5")),
            (1500000.5, Decimal("1500000.5")),
            (250000, Decimal("250000")),
            (Decimal("99.999999"), Decimal("99.999999")),
            ("-1,234.56", Decimal("-1234.56")),
        ],
    )
    def test_parses(self, value: object, expected: Decimal) -> None:
        assert coerce_money(value) == expected

    @pytest.mark.parametrize("value", [None, "", "-", "N/A", "TBC", "n.a."])
    def test_null_placeholders(self, value: object) -> None:
        assert coerce_money(value) is None

    @pytest.mark.parametrize("value", ["approx fifty", "GHS", date(2026, 1, 1)])
    def test_rejects_garbage(self, value: object) -> None:
        with pytest.raises(CoercionError):
            coerce_money(value)


class TestRate:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("24.5%", Decimal("0.245")),
            ("24.5", Decimal("0.245")),
            (24.5, Decimal("0.245")),
            ("0.245", Decimal("0.245")),
            (0.245, Decimal("0.245")),
            (Decimal("0.245"), Decimal("0.245")),
            ("0.5%", Decimal("0.005")),
            (1.5, Decimal("1.5")),  # boundary: never divided
            (2, Decimal("0.02")),
        ],
    )
    def test_all_notations_agree(self, value: object, expected: Decimal) -> None:
        assert coerce_rate(value) == expected

    def test_null_placeholder(self) -> None:
        assert coerce_rate("N/A") is None

    def test_rejects_text(self) -> None:
        with pytest.raises(CoercionError):
            coerce_rate("prime plus two")


class TestDate:
    def test_passthrough_and_datetime(self) -> None:
        assert coerce_date(date(2026, 6, 30)) == date(2026, 6, 30)
        assert coerce_date(datetime(2026, 6, 30, 14, 5)) == date(2026, 6, 30)

    def test_excel_serial_round_trip(self) -> None:
        serial = excel_serial_for(date(2031, 3, 15))
        assert coerce_date(serial) == date(2031, 3, 15)

    @pytest.mark.parametrize(
        ("value", "dayfirst", "expected"),
        [
            ("2026-06-30", True, date(2026, 6, 30)),
            ("15/03/2031", True, date(2031, 3, 15)),
            ("03/15/2031", True, date(2031, 3, 15)),  # day>12 disambiguates
            ("03/04/2031", False, date(2031, 3, 4)),
            ("03/04/2031", True, date(2031, 4, 3)),
        ],
    )
    def test_string_formats(self, value: str, dayfirst: bool, expected: date) -> None:
        assert coerce_date(value, dayfirst=dayfirst) == expected

    def test_null_placeholder(self) -> None:
        assert coerce_date("-") is None

    @pytest.mark.parametrize("value", ["soon", "31/31/2031", 12.5, 1_000_000])
    def test_rejects_garbage(self, value: object) -> None:
        with pytest.raises(CoercionError):
            coerce_date(value)


class TestIntAndString:
    def test_int_forms(self) -> None:
        assert coerce_int("2") == 2
        assert coerce_int(2.0) == 2
        assert coerce_int(None) is None

    def test_int_rejects_fraction_and_bool(self) -> None:
        with pytest.raises(CoercionError):
            coerce_int(2.5)
        with pytest.raises(CoercionError):
            coerce_int(True)

    def test_string_strips_and_nulls(self) -> None:
        assert coerce_string("  LN-0001  ") == "LN-0001"
        assert coerce_string("N/A") is None
        assert coerce_string(1000) == "1000"
