"""Code-defined default macro scenarios for the Enterprise Stress Workbench.

These definitions are platform reference data, not tenant-authored governance
rows. Stable UUIDv5 identifiers make them addressable through the existing API,
while official runs resolve the immutable definitions directly. A tenant may
clone a definition into a normal draft; the clone then follows the unchanged
maker-checker lifecycle in :mod:`app.services.macro_scenarios`.

The Bank of Ghana stress-testing exposure draft names the Appendix III Table 6
drivers and acceptable data sources, but does not prescribe a numeric macro
path. The base, adverse, and severe values below are therefore explicitly
labelled AequorOS calibrations. The six rate scenarios derive from the same
``parameter_register.IRRBB_SHOCKS`` catalogue that seeds the governed register.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid5

from app.models.stress import MACRO_VARIABLES
from app.services.institution_types import SEED_TYPES
from app.services.parameter_register import IRRBB_SHOCKS

_NAMESPACE = UUID("ae2d2ed5-27c4-4bdd-bd4f-82417ad4b527")
SYSTEM_CREATED_AT = datetime(2026, 9, 17, tzinfo=UTC)
SYSTEM_VERSION = 1
ALL_INSTITUTION_TYPES = tuple(spec.type_code for spec in SEED_TYPES)

BOG_STRESS_SOURCE = (
    "Bank of Ghana, Guideline on Stress Testing (Exposure Draft, February 2026), "
    "Appendix III Table 6; IMF World Economic Outlook, April 2026 (Ghana: 2026 "
    "real GDP 4.8%, consumer prices 5.8%); Bank of Ghana MPC, July 2026 "
    "(policy rate 14%); AfDB Ghana Economic Outlook, June 2026 (fiscal deficit "
    "2.6% of GDP in 2026, 2.2% in 2027). Market level anchors observed "
    "17 September 2026 are recorded in docs/stress.md §3.1."
)
IRRBB_SOURCE = (
    "BCBS, Interest rate risk in the banking book (2016; shock recalibration "
    "2024), implemented by the AequorOS IRRBB parameter register. Bank of Ghana "
    "IRRBB Guideline Exposure Draft (February 2026), Appendix II-III Tables 5-6, "
    "is the documented GHS local calibration source; see docs/stress.md §3.1."
)


@dataclass(frozen=True)
class DefaultMacroPath:
    variable: str
    year_index: int
    quarter_index: int
    base_value: Decimal
    stress_value: Decimal


@dataclass(frozen=True)
class DefaultMacroScenario:
    id: UUID
    code: str
    name: str
    description: str
    scenario_type: str
    severity: str | None
    narrative: str
    source: str
    institution_type_applicability: tuple[str, ...]
    paths: tuple[DefaultMacroPath, ...]
    runnable: bool = True
    horizon_years: int = 3
    version: int = SYSTEM_VERSION


def _id(code: str) -> UUID:
    return uuid5(_NAMESPACE, f"aequoros:default-macro-scenario:{code}:v{SYSTEM_VERSION}")


def _d(value: str | int | Decimal) -> Decimal:
    return Decimal(str(value))


def _interpolate(start: Decimal, end: Decimal, step: int, steps: int = 4) -> Decimal:
    return start + (end - start) * Decimal(step) / Decimal(steps)


_CURRENT = {
    "gog_yield": _d("0.18"),
    "gdp_growth": _d("0.048"),
    "policy_rate": _d("0.14"),
    "interest_rate": _d("0.18"),
    "inflation": _d("0.058"),
    "unemployment": _d("0.136"),
    "fx_usd_ghs": _d("11.45"),
    "fx_gbp_ghs": _d("15.36"),
    "fx_eur_ghs": _d("13.20"),
    "gse_index": _d("15076.3"),
    "fiscal_deficit": _d("-0.026"),
    "cocoa_price": _d("5979"),
    "gold_price": _d("4491"),
}

_BASE_ENDPOINTS = {
    "gog_yield": ("0.18", "0.17", "0.16"),
    "gdp_growth": ("0.048", "0.050", "0.052"),
    "policy_rate": ("0.14", "0.12", "0.10"),
    "interest_rate": ("0.18", "0.16", "0.14"),
    "inflation": ("0.058", "0.065", "0.075"),
    "unemployment": ("0.136", "0.130", "0.125"),
    "fx_usd_ghs": ("11.908", "12.384", "12.879"),
    "fx_gbp_ghs": ("15.974", "16.613", "17.277"),
    "fx_eur_ghs": ("13.728", "14.277", "14.848"),
    "gse_index": ("15830.115", "16621.621", "17452.702"),
    "fiscal_deficit": ("-0.026", "-0.022", "-0.020"),
    "cocoa_price": ("5979", "5800", "5700"),
    "gold_price": ("4491", "4300", "4200"),
}

_ADVERSE_ENDPOINTS = {
    "gog_yield": ("0.225", "0.210", "0.190"),
    "gdp_growth": ("-0.002", "0.020", "0.035"),
    "policy_rate": ("0.185", "0.160", "0.130"),
    "interest_rate": ("0.225", "0.200", "0.170"),
    "inflation": ("0.118", "0.095", "0.080"),
    "unemployment": ("0.166", "0.155", "0.145"),
    "fx_usd_ghs": ("13.740", "13.282", "13.000"),
    "fx_gbp_ghs": ("18.432", "17.818", "17.500"),
    "fx_eur_ghs": ("15.840", "15.312", "15.000"),
    "gse_index": ("11307.225", "12500", "14000"),
    "fiscal_deficit": ("-0.056", "-0.040", "-0.030"),
    "cocoa_price": ("4783.2", "5200", "5500"),
    "gold_price": ("3817.35", "4000", "4200"),
}

_SEVERE_ENDPOINTS = {
    "gog_yield": ("0.230", "0.220", "0.200"),
    "gdp_growth": ("-0.032", "0.000", "0.020"),
    "policy_rate": ("0.190", "0.170", "0.140"),
    "interest_rate": ("0.230", "0.210", "0.180"),
    "inflation": ("0.158", "0.120", "0.090"),
    "unemployment": ("0.186", "0.170", "0.150"),
    "fx_usd_ghs": ("15.4575", "14.3125", "13.1675"),
    "fx_gbp_ghs": ("20.736", "19.200", "17.664"),
    "fx_eur_ghs": ("17.820", "16.500", "15.180"),
    "gse_index": ("9045.780", "11000", "13500"),
    "fiscal_deficit": ("-0.076", "-0.055", "-0.040"),
    "cocoa_price": ("3886.35", "4500", "5200"),
    "gold_price": ("3143.7", "3600", "4000"),
}


def _quarterly_paths(
    stress_endpoints: dict[str, tuple[str, str, str]],
) -> tuple[DefaultMacroPath, ...]:
    paths: list[DefaultMacroPath] = []
    for variable in MACRO_VARIABLES:
        base_targets = tuple(_d(value) for value in _BASE_ENDPOINTS[variable])
        stress_targets = tuple(_d(value) for value in stress_endpoints[variable])
        base_start = _CURRENT[variable]
        stress_start = _CURRENT[variable]
        for year_index in range(1, 4):
            base_end = base_targets[year_index - 1]
            stress_end = stress_targets[year_index - 1]
            for quarter_in_year in range(1, 5):
                quarter_index = (year_index - 1) * 4 + quarter_in_year
                paths.append(
                    DefaultMacroPath(
                        variable=variable,
                        year_index=year_index,
                        quarter_index=quarter_index,
                        base_value=_interpolate(base_start, base_end, quarter_in_year),
                        stress_value=_interpolate(stress_start, stress_end, quarter_in_year),
                    )
                )
            base_start = base_end
            stress_start = stress_end
    return tuple(paths)


_BASE_PATHS = _quarterly_paths(_BASE_ENDPOINTS)


def _rate_paths(
    *,
    policy_delta: str = "0",
    market_delta: str = "0",
    sovereign_delta: str = "0",
) -> tuple[DefaultMacroPath, ...]:
    deltas = {
        "policy_rate": _d(policy_delta),
        "interest_rate": _d(market_delta),
        "gog_yield": _d(sovereign_delta),
    }
    return tuple(
        DefaultMacroPath(
            variable=path.variable,
            year_index=path.year_index,
            quarter_index=path.quarter_index,
            base_value=path.base_value,
            stress_value=path.base_value + deltas.get(path.variable, Decimal(0)),
        )
        for path in _BASE_PATHS
    )


def _shock_delta(scenario_code: str, shock_key: str) -> str:
    basis_points = _d(IRRBB_SHOCKS[scenario_code].get(shock_key, "0"))
    return str(basis_points / Decimal(10_000))


def _scenario(  # noqa: PLR0913 - one immutable definition carries its metadata
    code: str,
    name: str,
    description: str,
    scenario_type: str,
    severity: str | None,
    narrative: str,
    source: str,
    paths: tuple[DefaultMacroPath, ...],
    *,
    runnable: bool = True,
) -> DefaultMacroScenario:
    return DefaultMacroScenario(
        id=_id(code),
        code=code,
        name=name,
        description=description,
        scenario_type=scenario_type,
        severity=severity,
        narrative=narrative,
        source=source,
        institution_type_applicability=ALL_INSTITUTION_TYPES,
        paths=paths,
        runnable=runnable,
    )


DEFAULT_MACRO_SCENARIOS: tuple[DefaultMacroScenario, ...] = (
    _scenario(
        "system_base_consensus",
        "Base consensus path",
        "Quarterly three-year base path anchored to published 2026 Ghana macro data.",
        "base",
        None,
        (
            "AequorOS consensus calibration. GDP and inflation start from the IMF April "
            "2026 Ghana projections; the policy rate starts from the July 2026 MPC "
            "decision. Market levels are dated anchors. The path converges gradually "
            "toward the BoG inflation target and does not represent a regulator forecast."
        ),
        BOG_STRESS_SOURCE,
        _BASE_PATHS,
    ),
    _scenario(
        "system_adverse_bog_style",
        "Adverse domestic downturn",
        "BoG-style policy-rate spike, cedi depreciation, inflation rise, and GDP slowdown.",
        "adverse",
        "moderate",
        (
            "AequorOS adverse calibration, not a BoG-prescribed numeric scenario. Year 1 "
            "applies a 450bp domestic rate shock, 20% cedi depreciation, a 6pp inflation "
            "rise, and a 5pp GDP-growth reduction, then recovers over years 2-3."
        ),
        BOG_STRESS_SOURCE,
        _quarterly_paths(_ADVERSE_ENDPOINTS),
    ),
    _scenario(
        "system_severe_stagflation",
        "Severe stagflation",
        "Sharper FX, inflation, unemployment, market, and credit-migration stress.",
        "hypothetical",
        "severe",
        (
            "AequorOS severe-but-plausible calibration, not a regulator-prescribed path. "
            "Year 1 combines a 500bp rate shock, 35% cedi depreciation, a 10pp inflation "
            "rise, an 8pp GDP-growth reduction, and pronounced equity/commodity declines."
        ),
        BOG_STRESS_SOURCE,
        _quarterly_paths(_SEVERE_ENDPOINTS),
    ),
    _scenario(
        "system_irr_parallel_up_200",
        "IRRBB parallel +200bp",
        "Three-year quarterly macro path reproducing the existing parallel-up IRRBB shock.",
        "supervisory",
        "moderate",
        "Policy, market, and sovereign rates move up together by 200bp.",
        IRRBB_SOURCE,
        _rate_paths(
            policy_delta=_shock_delta("parallel_up_200", "parallel_bp"),
            market_delta=_shock_delta("parallel_up_200", "parallel_bp"),
            sovereign_delta=_shock_delta("parallel_up_200", "parallel_bp"),
        ),
    ),
    _scenario(
        "system_irr_parallel_down_200",
        "IRRBB parallel -200bp",
        "Three-year quarterly macro path reproducing the existing parallel-down IRRBB shock.",
        "supervisory",
        "moderate",
        "Policy, market, and sovereign rates move down together by 200bp.",
        IRRBB_SOURCE,
        _rate_paths(
            policy_delta=_shock_delta("parallel_down_200", "parallel_bp"),
            market_delta=_shock_delta("parallel_down_200", "parallel_bp"),
            sovereign_delta=_shock_delta("parallel_down_200", "parallel_bp"),
        ),
    ),
    _scenario(
        "system_irr_short_up_250",
        "IRRBB short rates +250bp",
        "Three-year quarterly macro path reproducing the existing short-rate-up shock.",
        "supervisory",
        "moderate",
        "The policy-rate path rises 250bp while the market and sovereign anchors stay flat.",
        IRRBB_SOURCE,
        _rate_paths(policy_delta=_shock_delta("short_up_250", "short_bp")),
    ),
    _scenario(
        "system_irr_short_down_250",
        "IRRBB short rates -250bp",
        "Three-year quarterly macro path reproducing the existing short-rate-down shock.",
        "supervisory",
        "moderate",
        "The policy-rate path falls 250bp while the market and sovereign anchors stay flat.",
        IRRBB_SOURCE,
        _rate_paths(policy_delta=_shock_delta("short_down_250", "short_bp")),
    ),
    _scenario(
        "system_irr_steepener",
        "IRRBB steepener",
        "Three-year quarterly path reproducing the existing -65bp short/+90bp long rotation.",
        "supervisory",
        "moderate",
        "Policy rates fall 65bp and sovereign yields rise 90bp around a flat market-rate anchor.",
        IRRBB_SOURCE,
        _rate_paths(
            policy_delta=_shock_delta("steepener", "short_bp"),
            sovereign_delta=_shock_delta("steepener", "long_bp"),
        ),
    ),
    _scenario(
        "system_irr_flattener",
        "IRRBB flattener",
        "Three-year quarterly path reproducing the existing +80bp short/-60bp long rotation.",
        "supervisory",
        "moderate",
        "Policy rates rise 80bp and sovereign yields fall 60bp around a flat market-rate anchor.",
        IRRBB_SOURCE,
        _rate_paths(
            policy_delta=_shock_delta("flattener", "short_bp"),
            sovereign_delta=_shock_delta("flattener", "long_bp"),
        ),
    ),
    _scenario(
        "system_bog_supervisory_placeholder",
        "BoG supervisory scenario",
        "Reserved for a regulator-issued numeric bottom-up supervisory path.",
        "supervisory",
        "severe",
        (
            "Non-runnable placeholder. The February 2026 exposure draft identifies the "
            "drivers and source expectations but publishes no numeric supervisory path. "
            "A runnable version must wait for an official BoG calibration."
        ),
        (
            "Bank of Ghana, Guideline on Stress Testing (Exposure Draft, February "
            "2026), Appendix III. No numeric supervisory path was published."
        ),
        (),
        runnable=False,
    ),
)

DEFAULT_BY_ID = {scenario.id: scenario for scenario in DEFAULT_MACRO_SCENARIOS}
DEFAULT_BY_CODE = {scenario.code: scenario for scenario in DEFAULT_MACRO_SCENARIOS}


def get(scenario_id: UUID) -> DefaultMacroScenario | None:
    return DEFAULT_BY_ID.get(scenario_id)


def annual_paths(scenario: DefaultMacroScenario) -> tuple[DefaultMacroPath, ...]:
    """The year-end points consumed by the existing annual projection engine."""
    return tuple(path for path in scenario.paths if path.quarter_index % 4 == 0)
