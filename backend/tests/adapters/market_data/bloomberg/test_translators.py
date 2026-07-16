"""Translators: percent-to-fraction conversion, pair semantics, agency mapping."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters.market_data.bloomberg.extractors.credit_data import RatingObservation
from app.adapters.market_data.bloomberg.extractors.curves import CurveFieldObservation
from app.adapters.market_data.bloomberg.extractors.fx import FxObservation
from app.adapters.market_data.bloomberg.translators.curve_to_canonical import (
    curve_bundle,
    tenor_label,
)
from app.adapters.market_data.bloomberg.translators.fx_to_canonical import fx_spot_bundle
from app.adapters.market_data.bloomberg.translators.rating_to_canonical import (
    agency_for_field,
    rating_bundle,
)
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.scope_taxonomy import DataScope

AS_OF = date(2026, 7, 15)


def _curve_observation(security: str, tenor_months: int, percent: str) -> CurveFieldObservation:
    return CurveFieldObservation(
        security=security, field="PX_LAST", tenor_months=tenor_months, value=Decimal(percent)
    )


def test_curve_bundle_converts_percents_to_decimal_fractions() -> None:
    observations = [
        _curve_observation("GHGGB3M Index", 3, "16.25"),
        _curve_observation("GHGGB1M Index", 1, "15.80"),
    ]
    bundle = curve_bundle(DataScope.YIELD_CURVE_GHS, observations)
    assert len(bundle.curves) == 1
    curve = bundle.curves[0]
    assert curve.currency == "GHS"
    assert curve.curve_name == "YIELD_CURVE_GHS"
    assert curve.curve_type == "sovereign"
    # Points are sorted by tenor regardless of vendor response order.
    assert [point.tenor_months for point in curve.points] == [1, 3]
    assert curve.points[0].rate == Decimal("0.15800000")
    assert curve.points[1].rate == Decimal("0.16250000")
    # All rates are decimal fractions, never percents.
    assert all(point.rate < 1 for point in curve.points)


def test_curve_bundle_source_reference_lists_constituent_tickers() -> None:
    observations = [
        _curve_observation("GHGGB1M Index", 1, "15.80"),
        _curve_observation("GHGGB10Y Index", 120, "21.40"),
    ]
    bundle = curve_bundle(DataScope.YIELD_CURVE_GHS, observations)
    assert bundle.curves[0].source_reference == "GHGGB1M Index,GHGGB10Y Index"


def test_curve_bundle_sample_values_are_human_readable_percents() -> None:
    observations = [
        _curve_observation("GHGGB3M Index", 3, "16.25"),
        _curve_observation("GHGGB2Y Index", 24, "19.3"),
        _curve_observation("GHGGB10Y Index", 120, "21.4"),
    ]
    bundle = curve_bundle(DataScope.YIELD_CURVE_GHS, observations)
    assert bundle.sample_values == {
        "GHS 3M": "16.25%",
        "GHS 2Y": "19.30%",
        "GHS 10Y": "21.40%",
    }


@pytest.mark.parametrize(
    ("tenor_months", "label"),
    [(1, "1M"), (3, "3M"), (12, "12M"), (18, "18M"), (24, "2Y"), (60, "5Y"), (120, "10Y")],
)
def test_tenor_labels(tenor_months: int, label: str) -> None:
    assert tenor_label(tenor_months) == label


def test_fx_spot_bundle_is_quote_per_base_without_percent_conversion() -> None:
    observation = FxObservation(security="GHSUSD Curncy", field="PX_LAST", value=Decimal("12.85"))
    bundle = fx_spot_bundle(DataScope.FX_SPOT_USD_GHS, observation)
    assert len(bundle.fx_rates) == 1
    fx = bundle.fx_rates[0]
    assert fx.base_currency == "USD"
    assert fx.quote_currency == "GHS"
    assert fx.rate_type == "spot"
    assert fx.tenor_months is None
    # A price, not a percent: ~12.85 GHS per USD, never divided by 100.
    assert fx.rate == Decimal("12.85000000")
    assert fx.source_reference == "GHSUSD Curncy"
    assert bundle.sample_values == {"USD/GHS spot": "12.8500"}


def test_fx_spot_bundle_rejects_non_spot_scopes() -> None:
    observation = FxObservation(security="GHSUSD Curncy", field="PX_LAST", value=Decimal("12.85"))
    with pytest.raises(ValueError, match="not an FX spot scope"):
        fx_spot_bundle(DataScope.YIELD_CURVE_GHS, observation)


def test_rating_bundle_maps_mnemonics_to_canonical_agencies() -> None:
    observations = [
        RatingObservation("GHANA Govt", "RTG_MDY_LT_LC_ISSUER_CREDIT", "Caa1"),
        RatingObservation("GHANA Govt", "RTG_SP_LT_LC_ISSUER_CREDIT", "CCC+"),
        RatingObservation("GHANA Govt", "RTG_FITCH_LT_LC_ISSUER_CREDIT", "CCC"),
    ]
    bundle = rating_bundle(DataScope.CREDIT_RATING_GHANA_SOVEREIGN, observations, AS_OF)
    assert [(r.agency, r.rating) for r in bundle.ratings] == [
        ("moodys", "Caa1"),
        ("sp", "CCC+"),
        ("fitch", "CCC"),
    ]
    assert all(r.issuer == "GHANA_SOVEREIGN" for r in bundle.ratings)
    # No documented watch-status or rating-action-date mnemonics (§16.4):
    # watch stays None and the rating date is the pull's as-of date.
    assert all(r.watch_status is None for r in bundle.ratings)
    assert all(r.rating_date == AS_OF for r in bundle.ratings)
    assert bundle.ratings[0].source_reference == "GHANA Govt/RTG_MDY_LT_LC_ISSUER_CREDIT"
    assert bundle.sample_values == {"Moody's": "Caa1", "S&P": "CCC+", "Fitch": "CCC"}


def test_unmapped_rating_mnemonic_classifies_as_unknown_instrument() -> None:
    with pytest.raises(MarketDataError) as excinfo:
        agency_for_field("RTG_DBRS_ISSUER", DataScope.CREDIT_RATING_GHANA_SOVEREIGN)
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.UNKNOWN_INSTRUMENT
    assert "RTG_DBRS_ISSUER" in error.internal_detail
    assert "RTG_DBRS_ISSUER" not in error.bank_facing.message


def test_bundle_record_counts() -> None:
    curve = curve_bundle(
        DataScope.YIELD_CURVE_GHS, [_curve_observation("GHGGB1M Index", 1, "15.80")]
    )
    assert curve.record_count == 2  # header + one point
    fx = fx_spot_bundle(
        DataScope.FX_SPOT_USD_GHS,
        FxObservation(security="GHSUSD Curncy", field="PX_LAST", value=Decimal("12.85")),
    )
    assert fx.record_count == 1
    ratings = rating_bundle(
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
        [RatingObservation("GHANA Govt", "RTG_MDY_LT_LC_ISSUER_CREDIT", "Caa1")],
        AS_OF,
    )
    assert ratings.record_count == 1
