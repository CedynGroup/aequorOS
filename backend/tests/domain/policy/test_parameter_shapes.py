"""A governed table is only governance if its shape is checked.

D-037: the operator console can now write the table-valued parameters the ICAAP
Pillar 2 engine reads. The danger those introduce is not a wrong number — a
wrong number is visible and arguable — but a MALFORMED table, because a band
table with a gap returns no add-on for the exposures that fall in the gap and
nothing downstream can tell that from a genuine zero.

These are the accept/reject cases for every registered shape. The seed
catalogue's own rows are held to them in
``tests/services/test_regulatory_parameters_icaap_p2.py``.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

import pytest

from app.domain.policy import parameter_shapes
from app.domain.policy.parameter_shapes import ParameterShapeError, validate
from app.models.icaap import ICAAP_BASES as WORKSPACE_BASES
from app.models.icaap_risk_capital import ICAAP_BASES


def _band_table(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": "icaap-band-table-v1",
        "metric": "hhi",
        "dimension": "single_name",
        "scale": "unit_interval",
        "mode": "step",
        "basis": "pct_pillar1_credit_capital",
        "bands": [
            {"lower": "0", "upper": "0.02", "addon": "0"},
            {"lower": "0.02", "upper": None, "addon": "5"},
        ],
    }
    body.update(overrides)
    return body


def _fx_shocks() -> dict[str, Any]:
    return {
        "schema": "icaap-fx-shocks-v1",
        "horizon": "one_year",
        "depreciation": {"default": "30", "USD": "40"},
        "appreciation": {"default": "10"},
    }


def _severity_map() -> dict[str, Any]:
    return {
        "schema": "icaap-severity-map-v1",
        "basis": "pct_annual_gross_income",
        "scenarios": {"cyber_data_corruption": "12"},
    }


def _haircut_grid() -> dict[str, Any]:
    return {
        "schema": "icaap-haircut-grid-v1",
        "tenor_buckets": [
            {"key": "up_to_1y", "max_years": "1"},
            {"key": "over_1y", "max_years": None},
        ],
        "currency_kinds": ["reporting", "foreign"],
        "haircut_pct": {
            "reporting": {"up_to_1y": "10", "over_1y": "25"},
            "foreign": {"up_to_1y": "15", "over_1y": "35"},
        },
    }


def _score_bands() -> dict[str, Any]:
    return {
        "schema": "icaap-score-bands-v1",
        "bands": [
            {"key": "low", "label": "Low", "min_score": 1, "max_score": 4},
            {"key": "high", "label": "High", "min_score": 5, "max_score": 25},
        ],
    }


def _code_list() -> dict[str, Any]:
    return {
        "schema": "icaap-code-list-v1",
        "codes": ["parallel_up_450", "parallel_down_450"],
        "required": ["parallel_up_450"],
    }


def _metric_set() -> dict[str, Any]:
    return {
        "schema": "icaap-ccr-metric-set-v1",
        "single_name": ["hhi", "crn"],
        "sector": ["hhi"],
    }


# --- the registry itself --------------------------------------------------


def test_an_unregistered_code_is_left_alone() -> None:
    """Registering a code is a deliberate act; everything else is unchanged.

    ``sdi_rwa_composition`` is the pre-existing structural parameter, and it
    must keep behaving exactly as it did before shapes existed.
    """
    validate("sdi_rwa_composition", None, {"anything": "at all"})
    validate("car_min", Decimal("13"), None)
    validate("not_a_code_at_all", None, None)


def test_the_band_table_basis_vocabulary_matches_the_model() -> None:
    """The pure module may not import the model, so the two are pinned equal."""
    assert parameter_shapes.BAND_TABLE_BASES == ICAAP_BASES


def test_the_two_icaap_bases_vocabularies_are_not_the_same_thing() -> None:
    """``ICAAP_BASES`` means two different things in two model modules.

    ``app.models.icaap`` has the REPORTING basis (solo / consolidated); the
    risk & capital module has the AMOUNT basis a Pillar 2 figure is expressed
    in. Both names come from the design. Neither is re-exported from
    ``app.models``, so nobody can pick one up by accident — but a reader who
    greps for the name should land here and see why there are two.
    """
    assert set(WORKSPACE_BASES) & set(ICAAP_BASES) == set()
    assert WORKSPACE_BASES == ("solo", "consolidated")


def test_is_structural_names_the_table_valued_codes() -> None:
    structural = {code for code in parameter_shapes.SHAPES if parameter_shapes.is_structural(code)}
    assert structural == {
        "icaap_materiality_rating_bands",
        "ccr_metric_set",
        "ccr_name_bands_hhi",
        "ccr_name_bands_gini",
        "ccr_name_bands_crn",
        "ccr_sector_bands_hhi",
        "icaap_irrbb_interim_scenarios",
        "fx_p2_shock_pct",
        "op_p2_scenario_severity_pct_gross_income",
        "sov_p2_haircut_pct",
        "sov_p2_exposure_categories",
        # ICAAP P3: the first as-of date an ICAAP report may be filed for is a
        # governed DATE, which has no scalar form (202609190058).
        "icaap_report_first_as_of_date",
        # P5: the IRRBB Standardised Framework's calibration is mostly tables —
        # a bucket ladder, three per-currency shock grids, the rotation
        # weights, two scenario-scalar rows, the deposit caps, the scenario
        # sets, the time-scaling mode, the default cash-flow profiles and the
        # commencement date (202609190061).
        "irrbb_sf_time_buckets",
        "irrbb_sf_parallel_shock_bp",
        "irrbb_sf_short_shock_bp",
        "irrbb_sf_long_shock_bp",
        "irrbb_sf_rotation_coefficients",
        "irrbb_sf_cpr_multipliers",
        "irrbb_sf_tdrr_scalars",
        "irrbb_sf_nmd_caps",
        "irrbb_sf_outlier_scenario_set",
        "irrbb_sf_mandatory_scenarios",
        "irrbb_sf_cpr_time_scaling",
        "irrbb_sf_default_cash_flow_profile",
        "irrbb_sf_mandatory_from_as_of",
        # P5: the granularity adjustment's correlations, maturity function,
        # proxy-PD table and segment map (202609190061).
        "ga_asset_correlation",
        "ga_maturity_adjustment",
        "ga_proxy_pd_by_rw_code",
        "ga_counterparty_segment_map",
    }
    assert parameter_shapes.shape_for("ccr_name_bands_hhi") is not None
    assert parameter_shapes.shape_for("car_min") is None


# --- scalar / structural exclusivity --------------------------------------


def test_a_structural_code_refuses_a_number() -> None:
    with pytest.raises(ParameterShapeError) as exc:
        validate("ccr_name_bands_hhi", Decimal("5"), None)
    assert exc.value.path == "value_json"

    with pytest.raises(ParameterShapeError) as both:
        validate("ccr_name_bands_hhi", Decimal("5"), _band_table())
    assert both.value.path == "value_numeric"


def test_a_scalar_code_refuses_a_table() -> None:
    with pytest.raises(ParameterShapeError) as exc:
        validate("ccr_name_cr_n", None, {"bands": []})
    assert exc.value.path == "value_numeric"

    with pytest.raises(ParameterShapeError) as both:
        validate("ccr_name_cr_n", Decimal("20"), {"bands": []})
    assert both.value.path == "value_json"


@pytest.mark.parametrize(
    ("code", "good", "bad"),
    [
        ("icaap_diversification_benefit_allowed", "1", "2"),
        ("icaap_car_min_includes_ccb1", "0", "0.5"),
        ("ccr_name_cr_n", "20", "0"),
        ("icaap_review_max_months", "12", "12.5"),
        ("irrbb_outlier_threshold_pct_tier1", "15", "101"),
        ("icaap_pillar2_source_tolerance_pct", "0", "-1"),
        ("ccr_name_hhi_coeff", "0.5", "1.5"),
    ],
)
def test_scalar_bounds(code: str, good: str, bad: str) -> None:
    validate(code, Decimal(good), None)
    with pytest.raises(ParameterShapeError) as exc:
        validate(code, Decimal(bad), None)
    assert exc.value.path == "value_numeric"


# --- band tables ----------------------------------------------------------


def test_a_well_formed_band_table_is_accepted() -> None:
    validate("ccr_name_bands_hhi", None, _band_table())


def test_a_band_table_must_declare_the_metric_and_dimension_of_its_code() -> None:
    with pytest.raises(ParameterShapeError) as metric:
        validate("ccr_name_bands_crn", None, _band_table())
    assert metric.value.path == "value_json.metric"

    with pytest.raises(ParameterShapeError) as dimension:
        validate("ccr_sector_bands_hhi", None, _band_table())
    assert dimension.value.path == "value_json.dimension"


def test_a_band_table_must_start_at_zero() -> None:
    body = _band_table(
        bands=[
            {"lower": "0.01", "upper": "0.02", "addon": "0"},
            {"lower": "0.02", "upper": None, "addon": "5"},
        ]
    )
    with pytest.raises(ParameterShapeError) as exc:
        validate("ccr_name_bands_hhi", None, body)
    assert exc.value.path == "value_json.bands[0].lower"


def test_a_gap_between_bands_is_refused() -> None:
    """The case this validator exists for: exposures in the gap get no add-on."""
    body = _band_table(
        bands=[
            {"lower": "0", "upper": "0.02", "addon": "0"},
            {"lower": "0.05", "upper": None, "addon": "5"},
        ]
    )
    with pytest.raises(ParameterShapeError) as exc:
        validate("ccr_name_bands_hhi", None, body)
    assert exc.value.path == "value_json.bands[1].lower"


def test_overlapping_bands_are_refused() -> None:
    body = _band_table(
        bands=[
            {"lower": "0", "upper": "0.05", "addon": "0"},
            {"lower": "0.02", "upper": None, "addon": "5"},
        ]
    )
    with pytest.raises(ParameterShapeError):
        validate("ccr_name_bands_hhi", None, body)


def test_only_the_last_band_may_be_open_ended_and_it_must_be() -> None:
    open_first = _band_table(
        bands=[
            {"lower": "0", "upper": None, "addon": "0"},
            {"lower": "0.02", "upper": None, "addon": "5"},
        ]
    )
    with pytest.raises(ParameterShapeError) as early:
        validate("ccr_name_bands_hhi", None, open_first)
    assert early.value.path == "value_json.bands[0].upper"

    closed_last = _band_table(
        bands=[
            {"lower": "0", "upper": "0.02", "addon": "0"},
            {"lower": "0.02", "upper": "0.5", "addon": "5"},
        ]
    )
    with pytest.raises(ParameterShapeError) as late:
        validate("ccr_name_bands_hhi", None, closed_last)
    assert late.value.path == "value_json.bands[1].upper"


def test_a_falling_add_on_is_refused() -> None:
    body = _band_table(
        bands=[
            {"lower": "0", "upper": "0.02", "addon": "5"},
            {"lower": "0.02", "upper": None, "addon": "1"},
        ]
    )
    with pytest.raises(ParameterShapeError) as exc:
        validate("ccr_name_bands_hhi", None, body)
    assert exc.value.path == "value_json.bands[1].addon"


def test_an_empty_band_table_and_an_unknown_basis_are_refused() -> None:
    with pytest.raises(ParameterShapeError) as empty:
        validate("ccr_name_bands_hhi", None, _band_table(bands=[]))
    assert empty.value.path == "value_json.bands"

    with pytest.raises(ParameterShapeError) as basis:
        validate("ccr_name_bands_hhi", None, _band_table(basis="pct_of_something"))
    assert basis.value.path == "value_json.basis"

    with pytest.raises(ParameterShapeError) as schema:
        validate("ccr_name_bands_hhi", None, _band_table(schema="icaap-band-table-v2"))
    assert schema.value.path == "value_json.schema"


# --- score bands ----------------------------------------------------------


def test_score_bands_must_cover_the_scale_from_one_without_gaps() -> None:
    validate("icaap_materiality_rating_bands", None, _score_bands())

    gapped = copy.deepcopy(_score_bands())
    gapped["bands"][1]["min_score"] = 6
    with pytest.raises(ParameterShapeError) as exc:
        validate("icaap_materiality_rating_bands", None, gapped)
    assert exc.value.path == "value_json.bands[1].min_score"

    duplicate = copy.deepcopy(_score_bands())
    duplicate["bands"][1]["key"] = "low"
    with pytest.raises(ParameterShapeError) as dupe:
        validate("icaap_materiality_rating_bands", None, duplicate)
    assert dupe.value.path == "value_json.bands[1].key"

    inverted = copy.deepcopy(_score_bands())
    inverted["bands"][0]["max_score"] = 0
    with pytest.raises(ParameterShapeError):
        validate("icaap_materiality_rating_bands", None, inverted)


# --- the remaining structural shapes --------------------------------------


def test_a_code_list_refuses_a_required_code_it_does_not_contain() -> None:
    validate("icaap_irrbb_interim_scenarios", None, _code_list())
    body = copy.deepcopy(_code_list())
    body["required"] = ["parallel_up_999"]
    with pytest.raises(ParameterShapeError) as exc:
        validate("icaap_irrbb_interim_scenarios", None, body)
    assert exc.value.path == "value_json.required[0]"


def test_a_metric_set_names_known_metrics_for_both_dimensions() -> None:
    validate("ccr_metric_set", None, _metric_set())
    body = copy.deepcopy(_metric_set())
    body["sector"] = ["entropy"]
    with pytest.raises(ParameterShapeError) as exc:
        validate("ccr_metric_set", None, body)
    assert exc.value.path == "value_json.sector[0]"

    missing = copy.deepcopy(_metric_set())
    del missing["sector"]
    with pytest.raises(ParameterShapeError) as absent:
        validate("ccr_metric_set", None, missing)
    assert absent.value.path == "value_json.sector"


def test_fx_shocks_need_a_default_and_iso_currency_keys() -> None:
    validate("fx_p2_shock_pct", None, _fx_shocks())

    no_default = copy.deepcopy(_fx_shocks())
    no_default["depreciation"] = {"USD": "40"}
    with pytest.raises(ParameterShapeError) as exc:
        validate("fx_p2_shock_pct", None, no_default)
    assert exc.value.path == "value_json.depreciation"

    bad_currency = copy.deepcopy(_fx_shocks())
    bad_currency["appreciation"]["dollars"] = "10"
    with pytest.raises(ParameterShapeError) as currency:
        validate("fx_p2_shock_pct", None, bad_currency)
    assert currency.value.path == "value_json.appreciation.dollars"

    negative = copy.deepcopy(_fx_shocks())
    negative["depreciation"]["default"] = "-1"
    with pytest.raises(ParameterShapeError):
        validate("fx_p2_shock_pct", None, negative)


def test_a_severity_map_needs_a_known_basis_and_at_least_one_scenario() -> None:
    validate("op_p2_scenario_severity_pct_gross_income", None, _severity_map())

    empty = copy.deepcopy(_severity_map())
    empty["scenarios"] = {}
    with pytest.raises(ParameterShapeError) as exc:
        validate("op_p2_scenario_severity_pct_gross_income", None, empty)
    assert exc.value.path == "value_json.scenarios"

    basis = copy.deepcopy(_severity_map())
    basis["basis"] = "pct_total_assets"
    with pytest.raises(ParameterShapeError) as wrong_basis:
        validate("op_p2_scenario_severity_pct_gross_income", None, basis)
    assert wrong_basis.value.path == "value_json.basis"


def test_a_haircut_grid_must_be_complete() -> None:
    """Every declared currency kind x tenor bucket needs a cell.

    A missing cell is the sovereign equivalent of a band-table gap: the
    exposures in it would be haircut by nothing.
    """
    validate("sov_p2_haircut_pct", None, _haircut_grid())

    missing_cell = copy.deepcopy(_haircut_grid())
    del missing_cell["haircut_pct"]["foreign"]["over_1y"]
    with pytest.raises(ParameterShapeError) as cell:
        validate("sov_p2_haircut_pct", None, missing_cell)
    assert cell.value.path == "value_json.haircut_pct.foreign.over_1y"

    missing_kind = copy.deepcopy(_haircut_grid())
    del missing_kind["haircut_pct"]["foreign"]
    with pytest.raises(ParameterShapeError) as kind:
        validate("sov_p2_haircut_pct", None, missing_kind)
    assert kind.value.path == "value_json.haircut_pct.foreign"

    unordered = copy.deepcopy(_haircut_grid())
    unordered["tenor_buckets"] = [
        {"key": "a", "max_years": "5"},
        {"key": "b", "max_years": "1"},
        {"key": "c", "max_years": None},
    ]
    with pytest.raises(ParameterShapeError) as order:
        validate("sov_p2_haircut_pct", None, unordered)
    assert order.value.path == "value_json.tenor_buckets[1].max_years"


def test_a_non_object_body_and_a_non_numeric_cell_are_typed_errors_not_crashes() -> None:
    """The console is an attacker-reachable writer: never a TypeError at a 500."""
    with pytest.raises(ParameterShapeError) as not_object:
        validate("ccr_name_bands_hhi", None, {"schema": "icaap-band-table-v1", "bands": "lots"})
    assert not_object.value.path in {"value_json.bands", "value_json.metric"}

    body = _band_table()
    body["bands"][0]["addon"] = True
    with pytest.raises(ParameterShapeError):
        validate("ccr_name_bands_hhi", None, body)

    body = _band_table()
    body["bands"][0]["addon"] = "not a number"
    with pytest.raises(ParameterShapeError):
        validate("ccr_name_bands_hhi", None, body)
