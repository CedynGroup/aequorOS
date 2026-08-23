"""Hand-verified tests for the pure macro→risk-parameter translation layer.

Every expected shock is derived independently here with explicit Decimal
literals against the documented elasticity register (docs/stress.md §3.2), so the
goldens are never self-referential. A separate test proves the emitted keys are a
subset of the engines' live ``SHOCK_VOCABULARY`` (the only place this pure
domain module is checked against the service layer).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.stress.translation import (
    DEFAULT_ELASTICITIES,
    TRANSLATION_MODULES,
    ElasticityTerm,
    MacroPathPoint,
    ShockMapping,
    frac_change,
    signed_peak_delta,
    translate,
)
from app.services.scenario_catalog import SHOCK_VOCABULARY, validate_shocks


def _p(variable: str, year: int, base: str, stress: str) -> MacroPathPoint:
    return MacroPathPoint(
        variable=variable,
        year_index=year,
        base_value=Decimal(base),
        stress_value=Decimal(stress),
    )


# A severe 3-year scenario with unambiguous peak years (year 1 is the trough).
SEVERE_PATHS: tuple[MacroPathPoint, ...] = (
    _p("interest_rate", 0, "0.20", "0.20"),
    _p("interest_rate", 1, "0.20", "0.25"),  # peak Δ = +0.05
    _p("interest_rate", 2, "0.20", "0.24"),
    _p("inflation", 0, "0.15", "0.15"),
    _p("inflation", 1, "0.15", "0.21"),  # peak Δ = +0.06
    _p("inflation", 2, "0.15", "0.19"),
    _p("gdp_growth", 0, "0.05", "0.05"),
    _p("gdp_growth", 1, "0.05", "0.00"),  # peak Δ = −0.05
    _p("gdp_growth", 2, "0.05", "0.02"),
    _p("unemployment", 1, "0.06", "0.09"),  # peak Δ = +0.03
    _p("fx_usd_ghs", 0, "12.5", "12.5"),
    _p("fx_usd_ghs", 1, "12.5", "15.0"),  # peak frac = +0.20
    _p("fx_usd_ghs", 2, "12.5", "14.0"),
    _p("gse_index", 1, "5000", "3500"),  # peak frac = −0.30
    _p("gog_yield", 1, "0.22", "0.26"),  # peak Δ = +0.04
)


def _num(shocks: dict[str, Decimal]) -> dict[str, Decimal]:
    return shocks


def test_peak_delta_and_frac_change_are_order_independent() -> None:
    assert signed_peak_delta(SEVERE_PATHS, "interest_rate") == Decimal("0.05")
    assert signed_peak_delta(SEVERE_PATHS, "gdp_growth") == Decimal("-0.05")
    assert signed_peak_delta(SEVERE_PATHS, "policy_rate") == Decimal(0)  # absent
    assert frac_change(SEVERE_PATHS, "fx_usd_ghs") == Decimal("0.20")
    assert frac_change(SEVERE_PATHS, "gse_index") == Decimal("-0.30")
    # Reversing the sequence must not change the peak selection.
    assert signed_peak_delta(tuple(reversed(SEVERE_PATHS)), "interest_rate") == Decimal("0.05")


def test_irr_parallel_shift() -> None:
    assert translate(SEVERE_PATHS, "irr") == {"parallel_bp": Decimal("500.0000")}


def test_ftp_curve_and_funding_spread() -> None:
    result = translate(SEVERE_PATHS, "ftp")
    assert result["curve_shift_bp"] == Decimal("500")
    assert result["funding_spread_add_bps"] == Decimal("200")
    assert set(result) == {"curve_shift_bp", "funding_spread_add_bps"}


def test_fx_depreciation_shock() -> None:
    assert translate(SEVERE_PATHS, "fx") == {"ghs_usd_shock_pct": Decimal("20.00")}


def test_capital_ecl_multipliers() -> None:
    result = translate(SEVERE_PATHS, "capital")
    # PD = 1 + (−0.05 × −3.0) + (0.03 × 2.0) = 1.21
    assert result["ecl_pd_multiplier"] == Decimal("1.21")
    # LGD = 1 + (−0.05 × −1.5) + (−0.30 × −0.4) = 1.195
    assert result["ecl_lgd_multiplier"] == Decimal("1.195")
    assert set(result) == {"ecl_pd_multiplier", "ecl_lgd_multiplier"}


def test_liquidity_full_shock_set() -> None:
    result = translate(SEVERE_PATHS, "liquidity")
    # NMD runoff term structure: front-loaded (2%/pp rate + 1%/pp inflation).
    assert result["nmd_runoff:h1"] == Decimal("16")  # 0.05×200 + 0.06×100
    assert result["nmd_runoff:h2"] == Decimal("12")  # 0.05×150 + 0.06×75
    assert result["nmd_runoff:h3"] == Decimal("8")  # 0.05×100 + 0.06×50
    assert result["nmd_runoff:h4"] == Decimal("4.8")  # 0.05×60 + 0.06×30
    assert result["inflow_multiplier"] == Decimal("0.8")  # 1 + (−0.05 × 4.0)
    assert result["hqla_securities_haircut_pct"] == Decimal("17.5")  # 0.05 × 350
    assert result["fx_depreciation_pct"] == Decimal("20")  # 0.20 × 100


def test_base_scenario_translates_to_nothing() -> None:
    # stress == base everywhere ⇒ no shocks in any module.
    base_paths = tuple(
        MacroPathPoint(p.variable, p.year_index, p.base_value, p.base_value)
        for p in SEVERE_PATHS
    )
    for module in TRANSLATION_MODULES:
        assert translate(base_paths, module) == {}


def test_no_op_shocks_are_omitted_but_real_ones_kept() -> None:
    # A rate FALL: parallel shift is a real negative shock (kept); the
    # duration haircut and runoff floor at 0 and drop; a GDP rise lifts the
    # inflow multiplier to its 1.0 cap (neutral) and drops.
    # inflation and fx_usd_ghs are read by the liquidity register and are
    # authored FLAT (zero contribution) so the scenario is complete under the
    # P0-9 fail-closed rule; every assertion below is unchanged.
    paths = (
        _p("interest_rate", 1, "0.20", "0.17"),  # Δ = −0.03
        _p("gdp_growth", 1, "0.05", "0.08"),  # Δ = +0.03
        _p("inflation", 1, "0.15", "0.15"),  # flat
        _p("fx_usd_ghs", 1, "12.5", "12.5"),  # flat
    )
    liquidity = translate(paths, "liquidity")
    assert liquidity == {}  # haircut→0, runoff→0, inflow_multiplier→1.0 (cap)
    assert translate(paths, "irr") == {"parallel_bp": Decimal("-300.0000")}


def test_overrides_replace_the_module_register() -> None:
    override = {
        "fx": (
            ShockMapping(
                "ghs_usd_shock_pct",
                "additive",
                (ElasticityTerm("fx_usd_ghs", "frac_change", Decimal("50")),),
                floor=Decimal(0),
            ),
        )
    }
    assert translate(SEVERE_PATHS, "fx", overrides=override) == {
        "ghs_usd_shock_pct": Decimal("10.00")  # 0.20 × 50
    }
    # The default register is untouched by the override call.
    assert translate(SEVERE_PATHS, "fx") == {"ghs_usd_shock_pct": Decimal("20.00")}


def test_unknown_module_raises() -> None:
    with pytest.raises(ValueError, match="Unknown stress module"):
        translate(SEVERE_PATHS, "operational")


def _is_valid_key(module: str, key: str) -> bool:
    vocabulary = SHOCK_VOCABULARY[module]
    return key in vocabulary.exact_keys or any(
        key.startswith(prefix) for prefix in vocabulary.prefixes
    )


def test_every_emitted_key_is_in_the_engine_vocabulary() -> None:
    for module, mappings in DEFAULT_ELASTICITIES.items():
        for mapping in mappings:
            assert _is_valid_key(module, mapping.shock_key), (
                f"{module}:{mapping.shock_key} is not in SHOCK_VOCABULARY"
            )


def test_translated_output_passes_engine_validation() -> None:
    # The service-level validator (completeness rules included) accepts every
    # module's translated output — capital's ECL-only set does not trip the
    # four-key completeness rule.
    for module in TRANSLATION_MODULES:
        validate_shocks(module, _num(translate(SEVERE_PATHS, module)))
