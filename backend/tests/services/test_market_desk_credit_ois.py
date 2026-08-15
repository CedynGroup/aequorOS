"""Stage 3 credit curve and Stage 4 OIS mode."""

from __future__ import annotations

from datetime import date

from app.services.market_desk.calculation import CORP_CODE, run_pipeline
from app.services.market_desk import register

COB = date(2026, 8, 7)
PARAMS = dict(register.DEFAULT_METHODOLOGY_PARAMETERS_V1)


def _base_snapshot() -> list[dict[str, str]]:
    return [
        {"series_code": "GHS.MPR", "as_of_date": "2026-07-22", "value": "15.00"},
        {"series_code": "GHS.INTERBANK.ON", "as_of_date": "2026-08-07", "value": "10.23"},
        {"series_code": "GHS.TBILL.91.DISCOUNT", "as_of_date": "2026-08-03", "value": "5.68"},
        {"series_code": "GHS.TBILL.182.DISCOUNT", "as_of_date": "2026-08-03", "value": "7.37"},
        {"series_code": "GHS.TBILL.364.DISCOUNT", "as_of_date": "2026-08-03", "value": "11.48"},
        {"series_code": "GHS.USDGHS.MID", "as_of_date": "2026-08-07", "value": "12.50"},
        {"series_code": "GHS.GRR", "as_of_date": "2024-07-01", "value": "29.40"},
    ]


def test_credit_curve_built_from_corporate_gfim_yields() -> None:
    snapshot = _base_snapshot() + [
        {
            "series_code": "GHS.GFIM.GH000A1XXXX1.YIELD",
            "as_of_date": "2026-08-06",
            "value": "18.50",
            "security_type": "corporate",
            "maturity_date": "2028-08-07",
            "trades": "5",
        },
        {
            "series_code": "GHS.GFIM.GH000A1XXXX2.YIELD",
            "as_of_date": "2026-08-06",
            "value": "19.25",
            "security_type": "corporate",
            "maturity_date": "2030-08-07",
            "trades": "3",
        },
    ]
    derived, qa = run_pipeline(snapshot, PARAMS, COB)
    assert CORP_CODE in derived["curves"]
    assert derived["credit_curve_present"] is True
    assert qa["gates"]["credit_curve"] == "pass"
    assert len(derived["curves"][CORP_CODE]["points"]) >= 2


def test_credit_curve_skipped_without_liquid_corporates() -> None:
    derived, qa = run_pipeline(_base_snapshot(), PARAMS, COB)
    assert CORP_CODE not in derived["curves"]
    assert derived["credit_curve_present"] is False
    assert qa["gates"]["credit_curve"] == "skipped"


def test_ois_bootstrap_mode_with_instruments() -> None:
    params = {**PARAMS, "discounting_mode": "ois_bootstrap"}
    snapshot = _base_snapshot() + [
        {"series_code": "GHS.OIS.1M", "as_of_date": "2026-08-07", "value": "14.50"},
        {"series_code": "GHS.OIS.3M", "as_of_date": "2026-08-07", "value": "14.75"},
    ]
    derived, qa = run_pipeline(snapshot, params, COB)
    assert derived["discounting_mode"] == "ois_bootstrap"
    assert qa["discounting_mode"] == "ois_bootstrap"
    assert derived["curves"]["AEQ.GHS.OIS"]["stage"] == 4
