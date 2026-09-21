"""Reading the governed rows the Standardised Framework runs on.

Founder directive D-024: no regulatory number lives in code. Everything the
framework prescribes — the buckets, the six calibrations per currency, the
scalars, the caps, the thresholds — is a control-plane row that staff propose
and approve. Two consequences are tested here.

First, a missing or unreadable row REFUSES, naming the code. There is no
default anywhere: a framework run with a silently assumed shock size is worse
than no run at all, because it looks like an answer.

Second, the structure is data too. The ladder has nineteen buckets today
because the table says nineteen; a shorter table parses and the engine works on
it. Anything the code fixes is something an operator cannot correct.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import pytest

from app.domain.irr import standardised_params as sp
from tests.domain.irr.test_sf_fixtures import seed_rows, sf_parameters


def _without(code: str) -> Mapping[str, sp.GovernedValue]:
    rows = seed_rows()
    del rows[code]
    return rows


def _replaced(code: str, value_json: Mapping[str, object]) -> Mapping[str, sp.GovernedValue]:
    rows = seed_rows()
    rows[code] = sp.GovernedValue(
        param_code=code, value_json=value_json, unit="test", source_citation="test"
    )
    return rows


@pytest.mark.parametrize("code", sp.REQUIRED_CODES)
def test_a_missing_row_refuses_and_names_the_code(code: str) -> None:
    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(_without(code))

    assert caught.value.code == sp.SfParameterError.MISSING
    assert caught.value.param_code == code
    assert code in str(caught.value)


def test_an_uncalibrated_currency_takes_the_tables_own_other_column() -> None:
    """"Other" is a column of the governed table, not a code fallback."""
    params = sf_parameters()

    assert params.shock_bp("parallel", "JPY") == Decimal("325")
    assert params.shock_bp("short", "JPY") == Decimal("500")
    assert params.shock_bp("long", "JPY") == Decimal("300")


def test_a_table_with_no_other_column_refuses_rather_than_guessing() -> None:
    params = sf_parameters(
        {
            sp.CODE_PARALLEL_SHOCK_BP: sp.GovernedValue(
                param_code=sp.CODE_PARALLEL_SHOCK_BP,
                value_json={"schema": "irrbb-sf-currency-bp-v1", "GHS": "450"},
                unit="bps",
                source_citation="test",
            )
        }
    )

    with pytest.raises(sp.SfParameterError) as caught:
        params.shock_bp("parallel", "JPY")

    assert caught.value.param_code == sp.CODE_PARALLEL_SHOCK_BP
    assert "JPY" in str(caught.value)


def test_an_empty_currency_table_refuses() -> None:
    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(
            _replaced(sp.CODE_PARALLEL_SHOCK_BP, {"schema": "irrbb-sf-currency-bp-v1"})
        )

    assert caught.value.param_code == sp.CODE_PARALLEL_SHOCK_BP


# --- buckets -----------------------------------------------------------------


def _bucket_table(buckets: list[dict[str, object]]) -> Mapping[str, object]:
    return {"schema": "irrbb-sf-buckets-v1", "buckets": buckets}


def test_the_number_of_buckets_is_data() -> None:
    """A different ladder parses, so the nineteen are not baked in."""
    rows = _replaced(
        sp.CODE_TIME_BUCKETS,
        _bucket_table(
            [
                {"key": "s1", "label": "Up to a year", "upper": "12M", "midpoint_years": "0.5"},
                {"key": "s2", "label": "One to five", "upper": "5Y", "midpoint_years": "3"},
                {"key": "s3", "label": "Over five", "upper": None, "midpoint_years": "10"},
            ]
        ),
    )

    params = sp.parse_parameters(rows)

    assert params.bucket_count == 3
    assert params.bucket_keys == ("s1", "s2", "s3")
    assert params.widths_years == (Decimal(1), Decimal(4), Decimal(10))


def test_the_first_buckets_midpoint_may_sit_past_its_own_upper_bound() -> None:
    """The printed overnight midpoint does exactly that, and must be honoured."""
    params = sf_parameters()

    overnight = params.upper_years[0]
    assert overnight is not None
    assert params.midpoints[0] == Decimal("0.0028")
    assert overnight == Decimal(1) / Decimal(365)
    assert params.midpoints[0] > overnight


@pytest.mark.parametrize(
    ("label", "buckets"),
    (
        (
            "the last bucket is closed",
            [{"key": "a", "upper": "1Y", "midpoint_years": "0.5"}],
        ),
        (
            "an inner bucket is open-ended",
            [
                {"key": "a", "upper": None, "midpoint_years": "0.5"},
                {"key": "b", "upper": None, "midpoint_years": "3"},
            ],
        ),
        (
            "upper bounds do not increase",
            [
                {"key": "a", "upper": "5Y", "midpoint_years": "2"},
                {"key": "b", "upper": "1Y", "midpoint_years": "3"},
                {"key": "c", "upper": None, "midpoint_years": "10"},
            ],
        ),
        (
            "midpoints do not increase",
            [
                {"key": "a", "upper": "1Y", "midpoint_years": "0.5"},
                {"key": "b", "upper": "5Y", "midpoint_years": "0.4"},
                {"key": "c", "upper": None, "midpoint_years": "10"},
            ],
        ),
        (
            "a midpoint sits below its own lower bound",
            [
                {"key": "a", "upper": "1Y", "midpoint_years": "0.5"},
                {"key": "b", "upper": "5Y", "midpoint_years": "0.9"},
                {"key": "c", "upper": None, "midpoint_years": "10"},
            ],
        ),
        (
            "bucket keys repeat",
            [
                {"key": "a", "upper": "1Y", "midpoint_years": "0.5"},
                {"key": "a", "upper": "5Y", "midpoint_years": "3"},
                {"key": "c", "upper": None, "midpoint_years": "10"},
            ],
        ),
        ("the table is empty", []),
    ),
)
def test_a_broken_bucket_table_refuses(label: str, buckets: list[dict[str, object]]) -> None:
    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(_replaced(sp.CODE_TIME_BUCKETS, _bucket_table(buckets)))

    assert caught.value.param_code == sp.CODE_TIME_BUCKETS, label


def test_a_tenor_that_is_not_a_tenor_refuses() -> None:
    with pytest.raises(sp.SfParameterError):
        sp.parse_parameters(
            _replaced(
                sp.CODE_TIME_BUCKETS,
                _bucket_table(
                    [
                        {"key": "a", "upper": "90 days", "midpoint_years": "0.25"},
                        {"key": "b", "upper": None, "midpoint_years": "10"},
                    ]
                ),
            )
        )


# --- scenario scalars and sets -----------------------------------------------


def test_every_scenario_needs_a_prepayment_and_a_redemption_scalar() -> None:
    incomplete = {
        "schema": "irrbb-sf-scenario-scalars-v1",
        "parallel_up": "0.8",
        "parallel_down": "1.2",
    }

    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(_replaced(sp.CODE_CPR_MULTIPLIERS, incomplete))

    assert caught.value.param_code == sp.CODE_CPR_MULTIPLIERS
    assert "steepener" in str(caught.value)


def test_a_scenario_list_may_only_name_shapes_the_framework_defines() -> None:
    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(
            _replaced(
                sp.CODE_OUTLIER_SCENARIO_SET,
                {"schema": "icaap-code-list-v1", "codes": ["parallel_up", "twist"]},
            )
        )

    assert caught.value.param_code == sp.CODE_OUTLIER_SCENARIO_SET
    assert "twist" in str(caught.value)


def test_an_empty_scenario_list_refuses() -> None:
    with pytest.raises(sp.SfParameterError):
        sp.parse_parameters(
            _replaced(sp.CODE_MANDATORY_SCENARIOS, {"schema": "icaap-code-list-v1", "codes": []})
        )


def test_the_mandatory_set_need_not_sit_inside_the_outlier_set() -> None:
    """Two independent governance choices, not one derived from the other."""
    params = sf_parameters(
        {
            sp.CODE_OUTLIER_SCENARIO_SET: sp.GovernedValue(
                param_code=sp.CODE_OUTLIER_SCENARIO_SET,
                value_json={"schema": "icaap-code-list-v1", "codes": ["steepener"]},
                unit="scenario_list",
                source_citation="test",
            )
        }
    )

    assert params.outlier_scenarios == ("steepener",)
    assert params.mandatory_scenarios == ("parallel_up", "parallel_down")


def test_a_scenario_list_is_ordered_the_way_the_framework_prints_it() -> None:
    params = sf_parameters(
        {
            sp.CODE_MANDATORY_SCENARIOS: sp.GovernedValue(
                param_code=sp.CODE_MANDATORY_SCENARIOS,
                value_json={
                    "schema": "icaap-code-list-v1",
                    "codes": ["short_down", "parallel_up"],
                },
                unit="scenario_list",
                source_citation="test",
            )
        }
    )

    assert params.mandatory_scenarios == ("parallel_up", "short_down")


# --- caps, profiles and modes ------------------------------------------------


@pytest.mark.parametrize(
    ("label", "caps"),
    (
        (
            "a cap above one hundred per cent",
            {"core_cap_pct": "140", "avg_maturity_cap_years": "5"},
        ),
        ("a negative cap", {"core_cap_pct": "-1", "avg_maturity_cap_years": "5"}),
        ("a zero maturity cap", {"core_cap_pct": "90", "avg_maturity_cap_years": "0"}),
    ),
)
def test_a_broken_deposit_cap_refuses(label: str, caps: Mapping[str, str]) -> None:
    rows = seed_rows()
    table = dict(rows[sp.CODE_NMD_CAPS].value_json or {})
    table["retail_transactional"] = caps
    rows[sp.CODE_NMD_CAPS] = sp.GovernedValue(
        param_code=sp.CODE_NMD_CAPS, value_json=table, unit="percent_years", source_citation="t"
    )

    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(rows)

    assert caught.value.param_code == sp.CODE_NMD_CAPS, label


def test_every_deposit_category_needs_its_caps() -> None:
    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(
            _replaced(
                sp.CODE_NMD_CAPS,
                {
                    "schema": "irrbb-sf-nmd-caps-v1",
                    "retail_transactional": {
                        "core_cap_pct": "90",
                        "avg_maturity_cap_years": "5",
                    },
                },
            )
        )

    assert "retail_non_transactional" in str(caught.value)


def test_a_profile_is_needed_for_every_position_family() -> None:
    rows = seed_rows()
    table = {
        key: value
        for key, value in dict(rows[sp.CODE_DEFAULT_CASH_FLOW_PROFILE].value_json or {}).items()
        if key != "LOAN"
    }
    rows[sp.CODE_DEFAULT_CASH_FLOW_PROFILE] = sp.GovernedValue(
        param_code=sp.CODE_DEFAULT_CASH_FLOW_PROFILE,
        value_json=table,
        unit="profile",
        source_citation="t",
    )

    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(rows)

    assert "LOAN" in str(caught.value)


def test_an_unknown_amortisation_refuses() -> None:
    rows = seed_rows()
    table = dict(rows[sp.CODE_DEFAULT_CASH_FLOW_PROFILE].value_json or {})
    table["LOAN"] = {
        "amortisation": "balloon",
        "frequency_months": "1",
        "horizonless_bucket": "b13",
    }
    rows[sp.CODE_DEFAULT_CASH_FLOW_PROFILE] = sp.GovernedValue(
        param_code=sp.CODE_DEFAULT_CASH_FLOW_PROFILE,
        value_json=table,
        unit="profile",
        source_citation="t",
    )

    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(rows)

    assert "balloon" in str(caught.value)


def test_an_unknown_time_scaling_mode_refuses() -> None:
    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(
            _replaced(sp.CODE_CPR_TIME_SCALING, {"schema": "x", "mode": "whatever"})
        )

    assert caught.value.param_code == sp.CODE_CPR_TIME_SCALING


def test_a_non_positive_short_rate_decay_refuses() -> None:
    rows = seed_rows()
    rows[sp.CODE_SHORT_DECAY_X] = sp.GovernedValue(
        param_code=sp.CODE_SHORT_DECAY_X, value=Decimal(0), unit="years", source_citation="t"
    )

    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(rows)

    assert caught.value.param_code == sp.CODE_SHORT_DECAY_X


def test_a_scalar_row_carrying_a_table_refuses() -> None:
    rows = seed_rows()
    rows[sp.CODE_NMD_HISTORY_YEARS] = sp.GovernedValue(
        param_code=sp.CODE_NMD_HISTORY_YEARS, value_json={"years": "10"}, source_citation="t"
    )

    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(rows)

    assert caught.value.param_code == sp.CODE_NMD_HISTORY_YEARS


def test_a_table_row_carrying_a_scalar_refuses() -> None:
    rows = seed_rows()
    rows[sp.CODE_ROTATION] = sp.GovernedValue(
        param_code=sp.CODE_ROTATION, value=Decimal(1), source_citation="t"
    )

    with pytest.raises(sp.SfParameterError) as caught:
        sp.parse_parameters(rows)

    assert caught.value.param_code == sp.CODE_ROTATION


# --- provenance --------------------------------------------------------------


def test_the_representative_profile_is_labelled_as_representative() -> None:
    """A platform methodology must never read as a supervisory rule."""
    params = sf_parameters()

    row = next(
        item
        for item in params.provenance
        if item.code == sp.CODE_DEFAULT_CASH_FLOW_PROFILE
    )

    assert row.representative is True
    assert params.representative_codes == (sp.CODE_DEFAULT_CASH_FLOW_PROFILE,)
    assert "representative only" in row.statement
    assert "not a published supervisory value" in row.statement
    assert row.label == "Default cash-flow profiles"


def test_a_row_the_control_plane_marks_representative_is_honoured() -> None:
    """The label can arrive from the console for a code the code never guessed."""
    params = sf_parameters(
        {
            sp.CODE_NMD_HISTORY_YEARS: sp.GovernedValue(
                param_code=sp.CODE_NMD_HISTORY_YEARS,
                value=Decimal(10),
                unit="years",
                source_citation="AequorOS platform methodology (REPRESENTATIVE)",
            )
        }
    )

    assert sp.CODE_NMD_HISTORY_YEARS in params.representative_codes


def test_pending_rows_are_reported_as_pending() -> None:
    params = sf_parameters()

    pending = params.pending_codes

    assert set(pending) == set(sp.REQUIRED_CODES)
    statement = next(
        row.statement for row in params.provenance if row.code == sp.CODE_PARALLEL_SHOCK_BP
    )
    assert statement == "Parallel rate shocks: pending confirmation with the supervisor."


def test_a_confirmed_row_says_so() -> None:
    params = sf_parameters(
        {
            sp.CODE_OUTLIER_THRESHOLD_PCT: sp.GovernedValue(
                param_code=sp.CODE_OUTLIER_THRESHOLD_PCT,
                value=Decimal(15),
                unit="percent",
                source_citation="guideline preamble",
                confirmation_status="approved",
            )
        }
    )

    row = next(
        item for item in params.provenance if item.code == sp.CODE_OUTLIER_THRESHOLD_PCT
    )

    assert row.pending_confirmation is False
    assert row.statement == "Outlier threshold: confirmed."


def test_every_consumed_code_carries_provenance() -> None:
    params = sf_parameters()

    assert tuple(row.code for row in params.provenance) == sp.REQUIRED_CODES
    assert all(row.label != row.code for row in params.provenance)
