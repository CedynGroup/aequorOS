"""Parser behavior: strict coercion, percent conversion, scope mapping, and
row-level problem collection that never aborts the file."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters.market_data.manual_upload.parser import (
    ManualUploadParseError,
    parse_upload,
)
from app.adapters.market_data.scope_taxonomy import DataScope
from tests.adapters.market_data.manual_upload.fixtures import (
    FIXTURE_AS_OF,
    build_full_coverage_workbook,
    build_yield_curve_workbook,
)

AS_OF = FIXTURE_AS_OF.isoformat()


# -- happy paths ----------------------------------------------------------------


def test_full_coverage_workbook_parses_every_scope() -> None:
    parsed = parse_upload(build_full_coverage_workbook(), "full.xlsx", expected_as_of=FIXTURE_AS_OF)
    assert parsed.problems == []
    assert set(parsed.kinds) == {
        "yield_curve",
        "fx_rates",
        "credit_ratings",
        "macro_forecasts",
    }
    expected_scopes = {
        scope for scope in DataScope if not scope.value.startswith("SECURITY_MASTER_")
    }
    assert set(parsed.scopes) == expected_scopes


def test_csv_yield_curve_happy_path() -> None:
    content = (
        "currency,curve_name,as_of_date,tenor_months,rate_percent\n"
        f"GHS,GHS_GOV_BOND,{AS_OF},3,15.80\n"
        f"GHS,GHS_GOV_BOND,{AS_OF},6,16.40\n"
    ).encode()
    parsed = parse_upload(content, "curves.csv", expected_as_of=FIXTURE_AS_OF)
    assert parsed.problems == []
    assert parsed.kinds == ("yield_curve",)
    curve = parsed.scopes[DataScope.YIELD_CURVE_GHS].bundle.curves[0]
    assert [point.rate for point in curve.points] == [Decimal("0.158"), Decimal("0.164")]


def test_percent_is_converted_to_decimal_fraction() -> None:
    content = build_yield_curve_workbook([["GHS", "GHS_GOV_BOND", AS_OF, 3, 15.80]])
    parsed = parse_upload(content, "curves.xlsx")
    point = parsed.scopes[DataScope.YIELD_CURVE_GHS].bundle.curves[0].points[0]
    assert point.rate == Decimal("0.158")
    assert parsed.scopes[DataScope.YIELD_CURVE_GHS].bundle.sample_values == {"GHS 3M": "15.80%"}


def test_raw_rows_are_preserved_per_scope() -> None:
    content = build_yield_curve_workbook([["GHS", "GHS_GOV_BOND", AS_OF, 3, 15.80]])
    parsed = parse_upload(content, "curves.xlsx")
    raw = parsed.scopes[DataScope.YIELD_CURVE_GHS].raw_rows
    assert raw == [
        {
            "sheet": "yield_curve",
            "row": 2,
            "currency": "GHS",
            "curve_name": "GHS_GOV_BOND",
            "as_of_date": AS_OF,
            "tenor_months": 3,
            "rate_percent": 15.8,
        }
    ]


def test_column_order_does_not_matter() -> None:
    content = (
        "rate_percent,tenor_months,as_of_date,curve_name,currency\n"
        f"15.80,3,{AS_OF},GHS_GOV_BOND,GHS\n"
    ).encode()
    parsed = parse_upload(content, "curves.csv")
    assert set(parsed.scopes) == {DataScope.YIELD_CURVE_GHS}
    assert parsed.problems == []


# -- row problems collected, file never aborted -----------------------------------


def test_bad_rows_are_collected_with_row_numbers() -> None:
    content = build_yield_curve_workbook(
        [
            ["GHS", "GHS_GOV_BOND", AS_OF, 3, 15.80],  # row 2: good
            ["GHS", "GHS_GOV_BOND", AS_OF, "abc", 16.40],  # row 3: bad tenor
            ["GHS", "GHS_GOV_BOND", AS_OF, 6, "n/a"],  # row 4: bad rate
            ["GHS", "GHS_GOV_BOND", AS_OF, 12, 17.10],  # row 5: good
        ]
    )
    parsed = parse_upload(content, "curves.xlsx")
    assert [(problem.row_number, problem.sheet) for problem in parsed.problems] == [
        (3, "yield_curve"),
        (4, "yield_curve"),
    ]
    assert "tenor_months" in parsed.problems[0].message
    assert "rate_percent" in parsed.problems[1].message
    curve = parsed.scopes[DataScope.YIELD_CURVE_GHS].bundle.curves[0]
    assert [point.tenor_months for point in curve.points] == [3, 12]


def test_unsupported_currency_is_a_problem_not_a_crash() -> None:
    content = build_yield_curve_workbook(
        [
            ["XXX", "XXX_GOV_BOND", AS_OF, 3, 9.10],
            ["GHS", "GHS_GOV_BOND", AS_OF, 3, 15.80],
        ]
    )
    parsed = parse_upload(content, "curves.xlsx")
    assert len(parsed.problems) == 1
    assert "unsupported currency" in parsed.problems[0].message
    assert set(parsed.scopes) == {DataScope.YIELD_CURVE_GHS}


def test_unsupported_issuer_is_a_problem_not_a_crash() -> None:
    content = (
        b"issuer,agency,rating,watch_status,rating_date\n"
        b"KENYA_SOVEREIGN,fitch,B,,2026-06-15\n"
        b"GHANA_SOVEREIGN,fitch,B-,stable,2026-06-15\n"
    )
    parsed = parse_upload(content, "ratings.csv")
    assert len(parsed.problems) == 1
    assert "unsupported issuer" in parsed.problems[0].message
    assert set(parsed.scopes) == {DataScope.CREDIT_RATING_GHANA_SOVEREIGN}


def test_unsupported_fx_pair_and_index_code_are_problems() -> None:
    fx_content = (
        "base_currency,quote_currency,rate_type,tenor_months,rate,as_of_date\n"
        f"USD,KES,spot,,132.50,{AS_OF}\n"
    ).encode()
    parsed = parse_upload(fx_content, "fx.csv")
    assert parsed.scopes == {}
    assert "no scope FX_SPOT_USD_KES exists" in parsed.problems[0].message

    macro_content = (
        "index_code,value,scenario,horizon_months,as_of_date\n"
        f"GHANA_UNEMPLOYMENT_FORECAST,4.2,base,12,{AS_OF}\n"
    ).encode()
    parsed = parse_upload(macro_content, "macro.csv")
    assert parsed.scopes == {}
    assert "unsupported index_code" in parsed.problems[0].message


def test_spot_rate_with_tenor_is_a_problem() -> None:
    content = (
        "base_currency,quote_currency,rate_type,tenor_months,rate,as_of_date\n"
        f"USD,GHS,spot,3,12.85,{AS_OF}\n"
    ).encode()
    parsed = parse_upload(content, "fx.csv")
    assert parsed.scopes == {}
    assert "blank for spot" in parsed.problems[0].message


def test_forward_without_tenor_is_a_problem() -> None:
    content = (
        "base_currency,quote_currency,rate_type,tenor_months,rate,as_of_date\n"
        f"USD,GHS,forward,,13.10,{AS_OF}\n"
    ).encode()
    parsed = parse_upload(content, "fx.csv")
    assert "required for forward" in parsed.problems[0].message


def test_duplicate_tenor_on_curve_is_a_problem() -> None:
    content = build_yield_curve_workbook(
        [
            ["GHS", "GHS_GOV_BOND", AS_OF, 3, 15.80],
            ["GHS", "GHS_GOV_BOND", AS_OF, 3, 15.90],
        ]
    )
    parsed = parse_upload(content, "curves.xlsx")
    assert "duplicate tenor 3" in parsed.problems[0].message
    curve = parsed.scopes[DataScope.YIELD_CURVE_GHS].bundle.curves[0]
    assert [point.rate for point in curve.points] == [Decimal("0.158")]


def test_percent_outside_plausible_band_is_a_problem() -> None:
    content = build_yield_curve_workbook([["GHS", "GHS_GOV_BOND", AS_OF, 3, 158.0]])
    parsed = parse_upload(content, "curves.xlsx")
    assert parsed.scopes == {}
    assert "[-100, 100]" in parsed.problems[0].message


def test_as_of_mismatch_is_a_problem_when_expected_is_given() -> None:
    content = build_yield_curve_workbook(
        [
            ["GHS", "GHS_GOV_BOND", "2026-06-29", 3, 15.80],
            ["GHS", "GHS_GOV_BOND", AS_OF, 6, 16.40],
        ]
    )
    parsed = parse_upload(content, "curves.xlsx", expected_as_of=FIXTURE_AS_OF)
    assert len(parsed.problems) == 1
    assert "does not match the upload as-of date" in parsed.problems[0].message
    curve = parsed.scopes[DataScope.YIELD_CURVE_GHS].bundle.curves[0]
    assert [point.tenor_months for point in curve.points] == [6]


def test_unrecognized_headers_are_a_sheet_problem() -> None:
    content = b"name,amount\nsomething,1\n"
    parsed = parse_upload(content, "mystery.csv")
    assert parsed.scopes == {}
    assert parsed.problems[0].row_number == 1
    assert "do not match any manual upload template" in parsed.problems[0].message


def test_duplicate_fx_row_for_same_scope_is_a_problem() -> None:
    content = (
        "base_currency,quote_currency,rate_type,tenor_months,rate,as_of_date\n"
        f"USD,GHS,spot,,12.85,{AS_OF}\n"
        f"USD,GHS,spot,,12.90,{AS_OF}\n"
    ).encode()
    parsed = parse_upload(content, "fx.csv")
    assert "duplicate row" in parsed.problems[0].message
    assert parsed.scopes[DataScope.FX_SPOT_USD_GHS].bundle.fx_rates[0].rate == Decimal("12.85")


# -- unreadable files -----------------------------------------------------------


def test_unsupported_suffix_raises_parse_error() -> None:
    with pytest.raises(ManualUploadParseError, match="Unsupported file type"):
        parse_upload(b"whatever", "notes.txt")


def test_corrupt_xlsx_raises_parse_error() -> None:
    with pytest.raises(ManualUploadParseError, match="not a valid .xlsx"):
        parse_upload(b"this is not a zip archive", "curves.xlsx")


def test_non_utf8_csv_raises_parse_error() -> None:
    with pytest.raises(ManualUploadParseError, match="not valid UTF-8"):
        parse_upload(b"\xff\xfe\x00bad", "curves.csv")


def test_expected_as_of_none_skips_the_date_check() -> None:
    content = build_yield_curve_workbook([["GHS", "GHS_GOV_BOND", "2020-01-31", 3, 15.80]])
    parsed = parse_upload(content, "curves.xlsx")
    assert parsed.problems == []
    assert set(parsed.scopes) == {DataScope.YIELD_CURVE_GHS}


def test_date_cells_from_excel_are_accepted() -> None:
    content = build_yield_curve_workbook([["GHS", "GHS_GOV_BOND", date(2026, 6, 30), 3, 15.80]])
    parsed = parse_upload(content, "curves.xlsx", expected_as_of=FIXTURE_AS_OF)
    assert parsed.problems == []
