"""The IRRBB standardised framework Pillar 2 method (P5-DESIGN §1.6 item 2).

The arithmetic is not under test here — the eight golden vectors in
``tests/domain/irr/`` pin every number the engine produces, and this method
re-derives none of it. What is under test is the part that decides what a
report is allowed to SAY:

* a refused run produces a refusal, under the one canonical name (D-061), and
  never a zero;
* a run with no measure produces ``incomplete``, and never a zero;
* modelling defaults arrive per marker and leave per marker, counted;
* a representative calibration the run rested on reaches the register as a
  parameter use, so the warning a reader needs is built from it;
* the governed outlier threshold has no code fallback (D-024).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.icaap.pillar2 import irrbb_sf_method as sf_method
from app.domain.icaap.pillar2.types import MethodStatus, MissingParameter
from app.domain.icaap.units import Basis, amount

THRESHOLD = Decimal("15")
MEASURE = Decimal("1234.567890")
TIER1 = Decimal("10000")


def _figures(**overrides: object) -> sf_method.SfFigures:
    body: dict[str, object] = {
        "eve_risk_measure": MEASURE,
        "tier1": TIER1,
        "pct_tier1": Decimal("12.345679"),
        "outlier": False,
        "worst_scenario": "Parallel up",
        "max_delta_nii": Decimal("500"),
        "currencies_in_scope": "GHS, USD",
        "mandatory": True,
        "run_id": "018f3a2c-9c1e-7b3d-8a44-0c1d2e3f4a5b",
        "run_input_hash": "abc123",
        "measure_set": "outlier_set",
        "scenarios_in_measure": ("parallel_up", "parallel_down"),
    }
    body.update(overrides)
    return sf_method.SfFigures(**body)  # pyright: ignore[reportArgumentType]


def test_a_refused_run_is_a_refusal_and_never_a_zero() -> None:
    result = sf_method.standardised_framework(
        figures=None,
        refusal="irrbb_sf_options_unsupported",
        outlier_threshold_pct_tier1=THRESHOLD,
    )
    assert result.status is MethodStatus.NOT_COMPUTABLE
    assert result.baseline_amount is None, "a refusal must not present an amount"
    assert result.stressed_amount is None
    assert not result.computed
    # D-061: one name, carried from the engine to the register unmapped.
    assert result.reasons == ("standardised_framework_refused:irrbb_sf_options_unsupported",)
    assert result.detail["sf_refusal_code"] == "irrbb_sf_options_unsupported"


def test_a_run_that_reports_no_measure_is_incomplete_not_zero() -> None:
    result = sf_method.standardised_framework(
        figures=_figures(eve_risk_measure=None),
        refusal=None,
        outlier_threshold_pct_tier1=THRESHOLD,
    )
    assert result.status is MethodStatus.INCOMPLETE
    assert result.baseline_amount is None
    assert result.reasons == (sf_method.REASON_MEASURE_ABSENT,)


def test_a_missing_bound_result_is_named_rather_than_guessed() -> None:
    result = sf_method.standardised_framework(
        figures=None, refusal=None, outlier_threshold_pct_tier1=THRESHOLD
    )
    assert result.status is MethodStatus.NOT_COMPUTABLE
    assert result.reasons == (sf_method.REASON_SOURCE_ABSENT,)


def test_the_measure_becomes_the_pillar2_amount_in_the_reporting_currency() -> None:
    result = sf_method.standardised_framework(
        figures=_figures(), refusal=None, outlier_threshold_pct_tier1=THRESHOLD
    )
    assert result.status is MethodStatus.COMPUTED
    assert result.basis is Basis.ABSOLUTE
    # The canonical Pillar 2 unit is 4 dp; the engine's authoritative six-place
    # figure stays in the detail, so the register and the run can be reconciled
    # without either one rounding the other away (D-062).
    assert result.baseline_amount == amount(MEASURE)
    assert result.basis_value == amount(MEASURE)
    assert result.stressed_amount == amount(MEASURE)
    assert result.stressed_derivation == "same_as_baseline"
    assert result.detail["sf_eve_risk_measure"] == str(MEASURE)
    assert result.detail["sf_run_id"] == "018f3a2c-9c1e-7b3d-8a44-0c1d2e3f4a5b"
    assert result.detail["sf_input_hash"] == "abc123"
    assert result.detail["irrbb_outlier_measure_pct"] == "12.345679"
    assert result.detail["irrbb_outlier_threshold_pct_tier1"] == "15"
    assert result.detail["outlier"] == "false"


def test_an_improving_book_does_not_become_a_negative_capital_requirement() -> None:
    result = sf_method.standardised_framework(
        figures=_figures(eve_risk_measure=Decimal("-400"), pct_tier1=Decimal("0")),
        refusal=None,
        outlier_threshold_pct_tier1=THRESHOLD,
    )
    assert result.baseline_amount == Decimal(0)


def test_a_stress_overlay_takes_the_worse_of_the_two_and_says_which() -> None:
    result = sf_method.standardised_framework(
        figures=_figures(),
        refusal=None,
        outlier_threshold_pct_tier1=THRESHOLD,
        overlay_stressed_loss=Decimal("5000"),
    )
    assert result.stressed_amount == Decimal("5000")
    assert result.stressed_derivation == "max_of_baseline_and_scenario"
    assert result.detail["overlay_stressed_loss"] == "5000"


def test_every_application_of_a_modelling_default_is_counted_not_flattened() -> None:
    """Forty applications and one application are different exposures."""
    result = sf_method.standardised_framework(
        figures=_figures(
            assumption_tallies={"profile_amortisation": 40, "no_prepayment_rate": 1}
        ),
        refusal=None,
        outlier_threshold_pct_tier1=THRESHOLD,
    )
    assert result.detail["assumption_default:profile_amortisation"] == "40"
    assert result.detail["assumption_default:no_prepayment_rate"] == "1"
    assert result.detail["assumption_defaults_applied"] == "41"


def test_a_representative_calibration_reaches_the_register_as_a_parameter_use() -> None:
    """The register's representative warning is built from ``parameters_used``."""
    result = sf_method.standardised_framework(
        figures=_figures(
            representative_parameters=("irrbb_sf_default_cash_flow_profile",),
            pending_parameters=("irrbb_sf_default_cash_flow_profile", "irrbb_sf_nmd_caps"),
        ),
        refusal=None,
        outlier_threshold_pct_tier1=THRESHOLD,
    )
    codes = {use.code for use in result.parameters_used}
    assert sf_method.PARAM_OUTLIER_THRESHOLD in codes
    assert "irrbb_sf_default_cash_flow_profile" in codes
    assert "irrbb_sf_nmd_caps" in codes
    roles = {
        use.code: use.role
        for use in result.parameters_used
        if use.code == "irrbb_sf_default_cash_flow_profile"
    }
    assert roles["irrbb_sf_default_cash_flow_profile"] == (
        "standardised framework (representative)"
    ), "a representative row must not be reported as merely pending"
    assert result.detail["representative_parameters"] == "irrbb_sf_default_cash_flow_profile"


def test_the_outlier_threshold_has_no_code_fallback() -> None:
    with pytest.raises(MissingParameter) as caught:
        sf_method.standardised_framework(
            figures=_figures(), refusal=None, outlier_threshold_pct_tier1=None
        )
    assert caught.value.param_code == sf_method.PARAM_OUTLIER_THRESHOLD


def test_a_refusal_still_names_the_governed_row_it_would_have_measured_against() -> None:
    """The refusal path must not skip the parameter check and hide a gap."""
    with pytest.raises(MissingParameter):
        sf_method.standardised_framework(
            figures=None,
            refusal="irrbb_sf_options_unsupported",
            outlier_threshold_pct_tier1=None,
        )
