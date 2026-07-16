"""Translators: percent -> fraction, agency mapping, canonical record shapes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.adapters.market_data.refinitiv.extractors.credit_data import RatingObservation
from app.adapters.market_data.refinitiv.extractors.curves import CurveObservation
from app.adapters.market_data.refinitiv.extractors.fx import FxObservation
from app.adapters.market_data.refinitiv.translators import (
    curve_to_bundle,
    fx_to_bundle,
    percent_to_fraction,
    ratings_to_bundle,
    tenor_label,
)
from app.adapters.market_data.scope_taxonomy import DataScope

DEFAULT_RATING_DATE = date(2026, 7, 15)


def _curve_observation(ric: str, tenor: int, percent: str) -> CurveObservation:
    return CurveObservation(
        ric=ric, field="TR.MidYield", tenor_months=tenor, value_percent=Decimal(percent)
    )


def _rating_observation(
    field: str,
    rating: str,
    watch: str | None = "stable",
    rating_date: date | None = date(2026, 3, 27),
) -> RatingObservation:
    return RatingObservation(
        ric="GH=", field=field, rating=rating, watch_status=watch, rating_date=rating_date
    )


def test_percent_to_fraction() -> None:
    assert percent_to_fraction(Decimal("15.80")) == Decimal("0.158")
    assert percent_to_fraction(Decimal("24.335")) == Decimal("0.24335")
    assert percent_to_fraction(Decimal("0")) == Decimal("0")
    # Quantized to the canonical 8-dp rate precision, half-up.
    assert percent_to_fraction(Decimal("12.3456789")) == Decimal("0.12345679")


def test_tenor_labels() -> None:
    assert [tenor_label(m) for m in (1, 3, 6, 12, 24, 60, 120)] == [
        "1M",
        "3M",
        "6M",
        "1Y",
        "2Y",
        "5Y",
        "10Y",
    ]


def test_curve_bundle_shape_and_samples() -> None:
    observations = [
        _curve_observation("GH3M=", 3, "15.80"),
        _curve_observation("GH1M=", 1, "15.10"),
    ]
    bundle = curve_to_bundle(DataScope.YIELD_CURVE_GHS, observations)
    assert len(bundle.curves) == 1
    curve = bundle.curves[0]
    assert curve.currency == "GHS"
    assert curve.curve_name == "GHS_SOVEREIGN"
    assert curve.curve_type == "sovereign"
    # Points sorted by tenor; rates are decimal fractions, never percents.
    assert [p.tenor_months for p in curve.points] == [1, 3]
    assert [p.rate for p in curve.points] == [Decimal("0.151"), Decimal("0.158")]
    # source_reference names the contributing RICs (§13.2).
    assert curve.source_reference == "GH1M=,GH3M="
    assert bundle.sample_values["GHS 3M"] == "15.80%"
    assert bundle.sample_values["GHS 1M"] == "15.10%"
    assert not bundle.warnings


def test_fx_bundle_follows_the_usd_ghs_convention() -> None:
    observation = FxObservation(ric="USDGHS=R", field="TR.MidPrice", value=Decimal("12.85"))
    bundle = fx_to_bundle(DataScope.FX_SPOT_USD_GHS, [observation])
    assert len(bundle.fx_rates) == 1
    fx = bundle.fx_rates[0]
    # FX_SPOT_USD_GHS: base USD, quote GHS, rate = GHS per 1 USD (§9.2 sample).
    assert (fx.base_currency, fx.quote_currency) == ("USD", "GHS")
    assert fx.rate_type == "spot"
    assert fx.tenor_months is None
    assert fx.rate == Decimal("12.85")
    assert fx.source_reference == "USDGHS=R"
    assert bundle.sample_values["USD/GHS"] == "12.85"


def test_rating_bundle_maps_agencies_and_issuer() -> None:
    observations = [
        _rating_observation("TR.MoodysIssuerRating", "Caa1", "stable"),
        _rating_observation("TR.SPIssuerRating", "CCC+", "positive"),
        _rating_observation("TR.FitchIssuerRating", "CCC", "stable"),
    ]
    bundle = ratings_to_bundle(
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN, observations, DEFAULT_RATING_DATE
    )
    assert len(bundle.ratings) == 3
    by_agency = {r.agency: r for r in bundle.ratings}
    assert set(by_agency) == {"moodys", "sp", "fitch"}
    assert all(r.issuer == "GHANA_SOVEREIGN" for r in bundle.ratings)
    assert all(r.source_reference == "GH=" for r in bundle.ratings)
    assert by_agency["moodys"].rating == "Caa1"
    assert by_agency["sp"].rating == "CCC+"
    assert by_agency["fitch"].rating == "CCC"
    assert bundle.sample_values["Ghana sovereign (S&P)"] == "CCC+"
    assert bundle.sample_values["Ghana sovereign (Moody's)"] == "Caa1"


def test_rating_watch_status_is_normalized_and_validated() -> None:
    observations = [
        _rating_observation("TR.MoodysIssuerRating", "Caa1", "Stable"),
        _rating_observation("TR.SPIssuerRating", "CCC+", "ON-WATCH-WEIRD"),
        _rating_observation("TR.FitchIssuerRating", "CCC", None),
    ]
    bundle = ratings_to_bundle(
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN, observations, DEFAULT_RATING_DATE
    )
    by_agency = {r.agency: r for r in bundle.ratings}
    assert by_agency["moodys"].watch_status == "stable"
    assert by_agency["sp"].watch_status is None  # unrecognized -> omitted, warned
    assert by_agency["fitch"].watch_status is None
    assert len(bundle.warnings) == 1
    # Warnings are bank-facing: no vendor field names or raw values.
    assert "TR." not in bundle.warnings[0]
    assert "ON-WATCH-WEIRD" not in bundle.warnings[0]


def test_rating_date_falls_back_to_the_pull_business_date() -> None:
    observations = [_rating_observation("TR.MoodysIssuerRating", "Caa1", rating_date=None)]
    bundle = ratings_to_bundle(
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN, observations, DEFAULT_RATING_DATE
    )
    assert bundle.ratings[0].rating_date == DEFAULT_RATING_DATE


def test_unrecognized_rating_field_is_skipped_with_clean_warning() -> None:
    observations = [
        _rating_observation("TR.MoodysIssuerRating", "Caa1"),
        _rating_observation("TR.SomeNewRatingField", "B-"),
    ]
    bundle = ratings_to_bundle(
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN, observations, DEFAULT_RATING_DATE
    )
    assert len(bundle.ratings) == 1
    assert len(bundle.warnings) == 1
    assert "TR." not in bundle.warnings[0]  # vendor mnemonics never surface (§12.3)
