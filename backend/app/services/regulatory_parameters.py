"""Resolver + seed catalogue for the regulatory-parameter control plane.

Engines call :func:`resolve` (never a literal) to obtain a class/type-keyed
regulatory number with full provenance (value, unit, source, confirmation status,
effective date, resolution scope) — the audit trail a regulator expects
("which parameter, which version, what source, was it confirmed?").

Resolution precedence (docs/sdi.md §7 Phase C):

    tenant board override  >  institution_type row  >  institution_class row  >  (fail-loud)

The **tenant board override** layer lives in the existing per-tenant registers
(``ParamCapitalThreshold``/``ParamLiquidityThreshold`` …, resolved by
``app.services.params.get_active_params``); those are read by each engine BEFORE
falling back here, so a tenant register value takes precedence and every current
bank read is preserved. The board override may only ever TIGHTEN: as of
2026-08-21 that is a hard, generalised clamp (:func:`clamp_overrides`) applied
across a whole register in one pass for every code in
``app.domain.policy.PARAMETER_DIRECTION`` — not the per-code, per-call-site clamp
it used to be, which covered 9 of the 25 governed codes and had two modules
disagreeing about ``car_min``. This module owns the **global default** layer: the
licence-specific (``institution_type``) row wins over the coarse
(``institution_class``) row, and an unseeded required code raises — a regulatory
number is never invented.

The chain itself (scope key, precedence, effective dating, tighten-only rules) is
pure and lives in ``app/domain/policy/resolver.py``; this module is its database
adapter. Call ``policy_scope(db, bank, as_of=...)`` when you need the whole chain.

``SEED_PARAMETERS`` is the single authoritative catalogue used by BOTH the seed
migration and the hermetic-test seed, so the two never drift.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any, NamedTuple
from weakref import WeakKeyDictionary

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.domain.authority.outcomes import OutcomeDetail
from app.domain.policy import (
    PARAMETER_DIRECTION,
    ClampReport,
    Direction,
    PolicyScope,
    PolicyUnresolvedError,
    direction_for,
    governed_codes,
    policy_unresolved,
    resolution_order,
    tighten,
)
from app.domain.policy import clamp_overrides as _clamp_values
from app.models import Bank, RegulatoryParameter
from app.services import institution_types, jurisdictions

#: Re-exported from ``app/domain/policy`` so the historic
#: ``regulatory_parameters.tighten`` / ``.PARAMETER_DIRECTION`` call sites keep
#: working while the rules themselves live in the pure resolver.
__all__ = [
    "PARAMETER_DIRECTION",
    "ClampReport",
    "Direction",
    "PolicyScope",
    "PolicyUnresolvedError",
    "PrefetchedParameterResolver",
    "RegulatoryParameterError",
    "ResolvedParameter",
    "SEED_PARAMETERS",
    "CCB_TREATMENT_PENDING_NOTE",
    "CONTROL_PLANE_REGISTER_CODES",
    "CRD_CAPITAL_FLOOR_SEEDS",
    "ICAAP_FILING_SEED_PARAMETERS",
    "ICAAP_P2_SEED_PARAMETERS",
    "FILING_PARAMETER_CODES",
    "GRANULARITY_SEED_PARAMETERS",
    "IRRBB_SF_SEED_PARAMETERS",
    "P2_PARAMETER_CODES",
    "P5_PARAMETER_CODES",
    "P5_SEED_PARAMETERS",
    "SDI_RECOGNITION_CAP_SEEDS",
    "clamp_overrides",
    "consume_parameter_provenance",
    "control_values",
    "direction_for",
    "filing_seed_rows",
    "governed_codes",
    "p2_seed_rows",
    "p5_seed_rows",
    "parameter_row_provenance",
    "policy_scope",
    "resolve",
    "resolve_class_value",
    "resolve_decimal",
    "resolve_many",
    "seed_rows",
    "seed_values",
    "tighten",
    "try_resolve",
]

#: Observability (docs/sdi.md §19). The control-plane resolver is the single seam
#: through which every class/type-keyed regulatory number reaches a calculation, so
#: it is where the two risk events are recorded as structured logs: an *unconfirmed*
#: (pending) value driving a live regulatory computation, and a mandatory parameter
#: missing (fail-loud). Both are intercepted into the JSON log stream (loguru
#: ``serialize=True``); the persistent who/when/why audit trail lives separately in
#: ``operator_audit_log``.
logger = logging.getLogger(__name__)

#: The anchor effective date for the seeded defaults — well before any reporting
#: as-of, so a seeded parameter is always active. New generations supersede it.
SEED_EFFECTIVE_FROM = date(2020, 1, 1)
#: The system actor recorded as maker+checker on the seeded defaults (the seed is
#: the authoritative platform default; operator changes go through four-eyes).
SEED_ACTOR = "platform_seed"


class ParamSpec(NamedTuple):
    """One row in the seed catalogue.

    A row carries EXACTLY one of ``value`` (a scalar) or ``value_json`` (a
    structural table — bands, shock grids, code lists). The structural arm
    arrived with the ICAAP Pillar 2 engine (D-024: every number the engine uses
    is a console row, and several of those numbers are tables). The shape of
    each structural body is validated by ``app/domain/policy/parameter_shapes``,
    which is the same validator the operator console's editor is held to, so a
    seeded table and a staff-proposed one cannot differ in form.
    """

    scope_type: str  # 'institution_class' | 'institution_type'
    scope_key: str  # 'bank'/'sdi' or a licence code
    param_code: str
    value: str | None  # Decimal-as-string (exact, no float drift); None ⇒ structural
    unit: str
    source_citation: str
    confirmation_status: str  # 'confirmed' | 'pending'
    #: The structural body, for parameters whose value is a table. Trailing with
    #: a default so every existing seven-positional call site is unchanged.
    value_json: Mapping[str, Any] | None = None
    #: Which jurisdiction's regulator governs this row. Defaulted to Ghana
    #: because every pre-P5 seed is a Ghanaian row and ``_seed_row`` wrote the
    #: literal itself; making it a column rather than a constant is what lets a
    #: second jurisdiction be seeded without a second seed function. A test
    #: proves every pre-P5 spec still emits an identical dict.
    jurisdiction_code: str = "GH"


# The 8 LMTD Table-1 prudential-ratio floors keep the register's existing code
# vocabulary (backend/app/services/liquidity_thresholds.py BANK_MINIMUM_PCT keys)
# so Phase D resolves resolve(db, bank, code) directly. Bank column is byte-verified
# against BANK_MINIMUM_PCT; SDI column per docs/sdi.md §4.1.
_LMTD_FLOORS: tuple[tuple[str, str, str], ...] = (
    # (param_code, bank_floor, sdi_floor)
    ("narrow_to_volatile", "80", "90"),
    ("broad_to_volatile", "100", "100"),
    ("narrow_to_short_term", "50", "50"),
    ("broad_to_short_term", "70", "60"),
    ("narrow_to_total_assets", "30", "30"),
    ("broad_to_total_assets", "50", "40"),
    ("narrow_to_total_deposits", "60", "60"),
    ("broad_to_total_deposits", "80", "70"),
)


def _lmtd_specs() -> list[ParamSpec]:
    specs: list[ParamSpec] = []
    for code, bank_floor, sdi_floor in _LMTD_FLOORS:
        specs.append(
            ParamSpec(
                "institution_class",
                "bank",
                code,
                bank_floor,
                "percent",
                "LMTD 2026 ¶9",
                "confirmed",
            )
        )
        specs.append(
            ParamSpec(
                "institution_class", "sdi", code, sdi_floor, "percent", "LMTD 2026 ¶9", "confirmed"
            )
        )
    return specs


#: Appended to the citation of a seed whose value awaits stakeholder
#: confirmation (founder directive D-024, 2026-09-19; the open question is the
#: regulatory audit's M20 — whether the CET1 / Tier 1 minima carry the capital
#: conservation buffer). The citation is the one text field that reaches every
#: copy of the row identically — the live catalogue, both migration paths (the
#: initial control-plane revision inserts this catalogue with ``change_rationale``
#: forced to NULL on a database built from scratch), the hermetic fixture, the
#: resolver's provenance and the operator console's row — so the reason travels
#: with the value. It is printed and API-visible, so it names no internal audit
#: reference (D-042).
CCB_TREATMENT_PENDING_NOTE = "pending stakeholder confirmation of the conservation-buffer treatment"

#: The SDI class's AT1 / Tier 2 recognition caps (D-042): the values the platform
#: applied as literals until 2026-09-19 (Appendix II Table 2 and the
#: management-action overlay used 1.5 / 2 for every institution), carried over so
#: an SDI's results do not move, ``pending`` because no SDI-specific regulatory
#: basis has been identified. Staff confirm or change them in the operator console.
SDI_RECOGNITION_CAP_CITATION = (
    "Carried from pre-2026-09-19 platform behaviour; no SDI-specific regulatory basis identified"
)
SDI_RECOGNITION_CAP_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    ("at1_cap_pct_rwa", "1.5", "pending", SDI_RECOGNITION_CAP_CITATION),
    ("tier2_cap_pct_rwa", "2", "pending", SDI_RECOGNITION_CAP_CITATION),
)

#: The capital minima the control plane SUPPLIES to a tenant register that
#: carries no row for them, per licence class (founder directive D-042). They are
#: never seeded into a tenant register: the tighten-only clamp
#: (:func:`clamp_overrides`) takes the governed value when the register has no row,
#: so a value staff raise OR lower in the console reaches every such tenant. A
#: board row, where one exists, still stands only when it is stricter. An SDI has
#: none here: its s.29 floor is read from the control plane by its own path.
CONTROL_PLANE_REGISTER_CODES: dict[str, tuple[str, ...]] = {
    "bank": ("car_min", "cet1_min", "tier1_min", "leverage_min"),
    "sdi": (),
}

#: The CRD capital-tier floors, buffers and recognition caps for banks (ICAAP P0,
#: regulatory audit B2 / M21): (code, value, confirmation_status, citation).
#: Locators follow the minimum-ratios table of the Capital Requirements Directive
#: 2018 (¶71–91) as transcribed in ``docs/research/bog_returns_and_templates.md``
#: §5.2, with the operative paragraph from ``backend/docs/bog_parameter_sources.md``.
#: Migration ``202609190054`` pins these same rows inline (a later edit here must
#: not change what that migration seeds); a test holds the two equal.
#:
#: ``cet1_min`` and ``tier1_min`` ship ``pending`` (D-024): whether their minima
#: should also carry the capital conservation buffer is open (M20). The values
#: stand as seeded, apply in every calculation, and are labelled "pending
#: confirmation" wherever they are shown; staff confirm or change them in the
#: operator console (Admin → Regulatory Parameters) without a code change.
CRD_CAPITAL_FLOOR_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "cet1_min",
        "6.5",
        "pending",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 1: "
        f"minimum CET1 6.5% of RWA (¶73(a)); {CCB_TREATMENT_PENDING_NOTE}",
    ),
    (
        "tier1_min",
        "8",
        "pending",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 5: "
        f"minimum Tier 1 capital ratio 8.0% of RWA (¶73(b)); {CCB_TREATMENT_PENDING_NOTE}",
    ),
    (
        "leverage_min",
        "6",
        "confirmed",
        "BoG Capital Requirements Directive 2018 ¶88–90: leverage ratio on a Tier 1 "
        "definition of capital, 'should be a minimum of 6% for all banks' (¶90)",
    ),
    (
        "ccb1_pct",
        "3",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 2: "
        "capital conservation buffer (CCB1) 3.0%, CET1 only (¶81)",
    ),
    (
        "ccyb_pct",
        "0",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 9: "
        "countercyclical buffer (CCB2) 0 (¶85)",
    ),
    (
        "dsib_buffer_pct",
        "0",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 10: "
        "D-SIB buffer 0 (¶86–87)",
    ),
    (
        "at1_cap_pct_rwa",
        "1.5",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 4: "
        "maximum AT1 1.5% of RWA (¶73(b))",
    ),
    (
        "tier2_cap_pct_rwa",
        "2",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 6: "
        "maximum Tier 2 2.0% of RWA (¶73(c))",
    ),
)

# --- ICAAP Pillar 2 engine (P2, 2026-09-19) ------------------------------
# Founder directive D-024: no regulatory number in code. Every threshold, band,
# shock and severity the Pillar 2 engine applies is a console row, seeded here
# and pinned identically by ``202609190056``. Eight further ICAAP codes (the
# filing deadline, the amber window, the materiality thresholds and the two
# horizons) belong to the workspace and are seeded by ``202609190055`` through
# ``app/services/icaap/parameters.py`` — each ICAAP code is seeded exactly once,
# and ``tests/services/test_regulatory_parameters_icaap_p2.py`` pins that.
#
# Most of these are REPRESENTATIVE calibrations (D-039): the Bank of Ghana has
# published no benchmark for concentration bands, FX shocks, operational
# severities or sovereign haircuts. Their citations therefore begin
# "REPRESENTATIVE:" and they ship ``pending`` so every output that uses one says
# so, and so staff can replace them in the console without a code change.
_CCR_METRIC_SET: dict[str, Any] = {
    "schema": "icaap-ccr-metric-set-v1",
    "single_name": ["hhi", "crn"],
    "sector": ["hhi"],
}
_CCR_NAME_BANDS_HHI: dict[str, Any] = {
    "schema": "icaap-band-table-v1",
    "metric": "hhi",
    "dimension": "single_name",
    "scale": "unit_interval",
    "mode": "step",
    "basis": "pct_pillar1_credit_capital",
    "bands": [
        {"lower": "0", "upper": "0.01", "addon": "0"},
        {"lower": "0.01", "upper": "0.02", "addon": "2"},
        {"lower": "0.02", "upper": "0.05", "addon": "5"},
        {"lower": "0.05", "upper": "0.10", "addon": "10"},
        {"lower": "0.10", "upper": None, "addon": "15"},
    ],
}
_CCR_NAME_BANDS_GINI: dict[str, Any] = {
    "schema": "icaap-band-table-v1",
    "metric": "gini",
    "dimension": "single_name",
    "scale": "unit_interval",
    "mode": "step",
    "basis": "pct_pillar1_credit_capital",
    "bands": [
        {"lower": "0", "upper": "0.85", "addon": "0"},
        {"lower": "0.85", "upper": "0.95", "addon": "2"},
        {"lower": "0.95", "upper": None, "addon": "5"},
    ],
}
_CCR_NAME_BANDS_CRN: dict[str, Any] = {
    "schema": "icaap-band-table-v1",
    "metric": "crn",
    "dimension": "single_name",
    "scale": "unit_interval",
    "mode": "step",
    "basis": "pct_pillar1_credit_capital",
    "bands": [
        {"lower": "0", "upper": "0.20", "addon": "0"},
        {"lower": "0.20", "upper": "0.35", "addon": "3"},
        {"lower": "0.35", "upper": "0.50", "addon": "6"},
        {"lower": "0.50", "upper": None, "addon": "10"},
    ],
}
_CCR_SECTOR_BANDS_HHI: dict[str, Any] = {
    "schema": "icaap-band-table-v1",
    "metric": "hhi",
    "dimension": "sector",
    "scale": "unit_interval",
    "mode": "step",
    "basis": "pct_pillar1_credit_capital",
    "bands": [
        {"lower": "0", "upper": "0.15", "addon": "0"},
        {"lower": "0.15", "upper": "0.25", "addon": "3"},
        {"lower": "0.25", "upper": "0.40", "addon": "7"},
        {"lower": "0.40", "upper": None, "addon": "12"},
    ],
    "taxonomy": "canonical_sector_attribute",
}
_IRRBB_INTERIM_SCENARIOS: dict[str, Any] = {
    "schema": "icaap-code-list-v1",
    "codes": [
        "parallel_up_200",
        "parallel_down_200",
        "short_up_250",
        "short_down_250",
        "steepener",
        "flattener",
        "parallel_up_450",
        "parallel_down_450",
    ],
    "required": ["parallel_up_450", "parallel_down_450"],
}
_FX_P2_SHOCKS: dict[str, Any] = {
    "schema": "icaap-fx-shocks-v1",
    "horizon": "one_year",
    "depreciation": {"default": "30"},
    "appreciation": {"default": "10"},
}
_OP_P2_SEVERITIES: dict[str, Any] = {
    "schema": "icaap-severity-map-v1",
    "basis": "pct_annual_gross_income",
    "scenarios": {
        "cloud_outage": "4",
        "cyber_data_corruption": "12",
        "payments_outage": "8",
        "telecom_failure": "3",
        "staff_unavailability": "5",
        "flood_epidemic": "7",
        "civil_strife": "9",
    },
}
_SOV_P2_HAIRCUTS: dict[str, Any] = {
    "schema": "icaap-haircut-grid-v1",
    "tenor_buckets": [
        {"key": "up_to_1y", "max_years": "1"},
        {"key": "1y_to_5y", "max_years": "5"},
        {"key": "over_5y", "max_years": None},
    ],
    "currency_kinds": ["reporting", "foreign"],
    "haircut_pct": {
        "reporting": {"up_to_1y": "10", "1y_to_5y": "25", "over_5y": "35"},
        "foreign": {"up_to_1y": "15", "1y_to_5y": "35", "over_5y": "45"},
    },
}
_SOV_P2_CATEGORIES: dict[str, Any] = {
    "schema": "icaap-code-list-v1",
    "codes": ["gov_securities"],
    "required": [],
}


def _p2(  # noqa: PLR0913 - a seed row is its named columns
    param_code: str,
    value: str | None,
    unit: str,
    source_citation: str,
    confirmation_status: str = "pending",
    value_json: Mapping[str, Any] | None = None,
) -> ParamSpec:
    """One ICAAP Pillar 2 seed row — banks only, Ghana, institution class."""
    return ParamSpec(
        "institution_class",
        "bank",
        param_code,
        value,
        unit,
        source_citation,
        confirmation_status,
        value_json,
    )


#: The 20 governed codes the Pillar 2 engine reads. Pinned identically in
#: ``alembic/versions/202609190056_icaap_pillar2_parameters.py``.
ICAAP_P2_SEED_PARAMETERS: tuple[ParamSpec, ...] = (
    _p2(
        "icaap_independent_review_max_months",
        "12",
        "months",
        "BoG ICAAP Guideline (Exposure Draft Feb 2026) ¶42: independent review and audit "
        "of the ICAAP at least annually; pending final text",
    ),
    _p2(
        "icaap_review_max_months",
        "12",
        "months",
        "BoG ICAAP Guideline (Exposure Draft Feb 2026) ¶73: the ICAAP is reviewed and "
        "updated at least annually; pending final text",
    ),
    _p2(
        "icaap_diversification_benefit_allowed",
        "0",
        "boolean",
        "AequorOS platform policy (regulatory audit M19): Pillar 2 risks are summed as in "
        "Stress Testing Guideline Appendix II Table 5; no inter-risk diversification "
        "unless confirmed",
    ),
    _p2(
        "icaap_pillar2_source_tolerance_pct",
        "1",
        "percent",
        "REPRESENTATIVE: AequorOS internal-control tolerance for Pillar 2 source "
        "consistency (regulatory audit M1); relative difference; no published value",
    ),
    _p2(
        "icaap_car_min_includes_ccb1",
        "1",
        "boolean",
        "BoG Capital Requirements Directive 2018 ¶71 (10%) + ¶75 CCB1 (3%): the governed "
        "car_min for banks already includes the capital conservation buffer",
        "confirmed",
    ),
    _p2(
        "ccr_min_dimension_coverage_pct",
        "80",
        "percent",
        "REPRESENTATIVE: minimum share of the credit book stating a concentration "
        "dimension before the top benchmark band applies (regulatory audit M5); no "
        "published value",
    ),
    _p2(
        "ccr_metric_set",
        None,
        "metric_set",
        "BoG Credit Concentration Guidelines (Sept 2025) definitions: model-free measures "
        "are HHI, concentration ratios and Gini; which ones drive the add-on is platform "
        "methodology",
        value_json=_CCR_METRIC_SET,
    ),
    _p2(
        "ccr_name_cr_n",
        "20",
        "count",
        "REPRESENTATIVE: number of largest obligor groups in the name concentration ratio "
        "(CRn); no published value",
    ),
    _p2(
        "ccr_name_bands_hhi",
        None,
        "band_table",
        "REPRESENTATIVE: benchmark bands for name HHI (0-1 scale); add-on as % of Pillar 1 "
        "credit capital; BoG has published no benchmark",
        value_json=_CCR_NAME_BANDS_HHI,
    ),
    _p2(
        "ccr_name_bands_gini",
        None,
        "band_table",
        "REPRESENTATIVE: benchmark bands for name Gini (raw, 0-1); add-on as % of Pillar 1 "
        "credit capital; used only if ccr_metric_set lists gini; no published benchmark",
        value_json=_CCR_NAME_BANDS_GINI,
    ),
    _p2(
        "ccr_name_bands_crn",
        None,
        "band_table",
        "REPRESENTATIVE: benchmark bands for the name concentration ratio CRn (share of "
        "book); add-on as % of Pillar 1 credit capital; no published benchmark",
        value_json=_CCR_NAME_BANDS_CRN,
    ),
    _p2(
        "ccr_sector_bands_hhi",
        None,
        "band_table",
        "REPRESENTATIVE: benchmark bands for sector HHI (0-1) over the canonical sector "
        "attribute; add-on as % of Pillar 1 credit capital; no published benchmark",
        value_json=_CCR_SECTOR_BANDS_HHI,
    ),
    _p2(
        "ccr_name_hhi_coeff",
        "0.5",
        "ratio",
        "REPRESENTATIVE: name-HHI coefficient of the HHI-proportional heuristic (legacy "
        "stress default); not a granularity adjustment (D-016)",
    ),
    _p2(
        "ccr_sector_hhi_coeff",
        "0.5",
        "ratio",
        "REPRESENTATIVE: sector-HHI coefficient of the HHI-proportional heuristic (legacy "
        "stress default); not a granularity adjustment (D-016)",
    ),
    _p2(
        "irrbb_outlier_threshold_pct_tier1",
        "15",
        "percent",
        "BoG IRRBB Guideline (Exposure Draft Feb 2026) preamble: IRRBB exposure above 15% "
        "of Tier 1 capital marks an outlier bank; pending final text",
    ),
    _p2(
        "icaap_irrbb_interim_scenarios",
        None,
        "code_list",
        "BoG IRRBB Guideline (Exposure Draft Feb 2026) App II: parallel shocks mandatory "
        "for the annual ICAAP; interim set over the legacy engine scenario codes (D-013)",
        value_json=_IRRBB_INTERIM_SCENARIOS,
    ),
    _p2(
        "fx_p2_shock_pct",
        None,
        "shock_table",
        "REPRESENTATIVE: one-year reporting-currency depreciation and appreciation shocks "
        "for the ICAAP FX add-on (regulatory audit M8); no published value",
        value_json=_FX_P2_SHOCKS,
    ),
    _p2(
        "op_p2_scenario_severity_pct_gross_income",
        None,
        "severity_map",
        "REPRESENTATIVE: severities as % of annual gross income; the seven scenarios follow "
        "BoG Stress Testing Guideline (ED Feb 2026) Appendix I ¶12 (audit M7)",
        value_json=_OP_P2_SEVERITIES,
    ),
    _p2(
        "sov_p2_haircut_pct",
        None,
        "haircut_grid",
        "REPRESENTATIVE: sovereign haircuts by tenor and currency for the ICAAP sovereign "
        "add-on (audit M2); Stress Guideline App III names sovereign restructuring; no "
        "published haircut",
        value_json=_SOV_P2_HAIRCUTS,
    ),
    _p2(
        "sov_p2_exposure_categories",
        None,
        "code_list",
        "AequorOS platform methodology: canonical fact categories treated as sovereign "
        "exposure when no manual sovereign grid is entered (derived mode)",
        value_json=_SOV_P2_CATEGORIES,
    ),
)

#: The codes above, for the consumers, the migration test and the duplicate-seed
#: guard. ``app/services/icaap/parameters.ICAAP_PARAM_CODES`` holds P1's eight;
#: the two sets are disjoint by construction and a test proves it.
P2_PARAMETER_CODES: frozenset[str] = frozenset(spec.param_code for spec in ICAAP_P2_SEED_PARAMETERS)

#: The first as-of date an ICAAP report may be filed for. A DATE, so it is a
#: structural body rather than a number — but governed by the same rule (D-024,
#: D-032): the BoG ICAAP Guideline's exposure draft is effective 1 January 2027
#: and does not say which year end it first bites on, so the platform must not
#: settle that in code. Staff correct it in the console when BoG confirms, and
#: until then every output that leans on it says "pending confirmation".
_ICAAP_REPORT_FIRST_AS_OF: dict[str, Any] = {
    "schema": "icaap-effective-date-v1",
    "date": "2026-12-31",
}

#: The governed codes the ICAAP FILING plane reads (P3). Deliberately small:
#: the deadline and disclosure months are already seeded by ``202609190055``
#: (P1's workspace catalogue, ``app/services/icaap/parameters.py``), and the
#: stress horizon with them — this adds only what the filing plane introduced.
#: Pinned identically in
#: ``alembic/versions/202609190059_icaap_filing_parameters.py``.
ICAAP_FILING_SEED_PARAMETERS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "institution_class",
        "bank",
        "icaap_report_first_as_of_date",
        None,
        "date",
        "BoG Guideline on ICAAP (Exposure Draft, February 2026) ¶9: effective "
        "1 January 2027. The first annual as-of date is INFERRED as the 31 December "
        "2026 year end, pending confirmation with BoG; an earlier as-of is refused, "
        "never back-dated",
        "pending",
        _ICAAP_REPORT_FIRST_AS_OF,
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "icaap_stress_severe_scenarios_min",
        "1",
        "count",
        "BoG Stress Testing Guideline (Exposure Draft Feb 2026) ¶35, ¶75: at least "
        "one severe but plausible scenario is run and reported; pending final text",
        "pending",
    ),
)

#: The codes above, for the consumers, the migration test and the duplicate-seed
#: guard. Disjoint from P1's workspace codes and from ``P2_PARAMETER_CODES``.
FILING_PARAMETER_CODES: frozenset[str] = frozenset(
    spec.param_code for spec in ICAAP_FILING_SEED_PARAMETERS
)

# --- P5: IRRBB Standardised Framework + full granularity adjustment ----------
#
# Every number the Standardised Framework reads is a row here (D-024). The
# engine (``app/domain/irr/standardised*.py``) holds NO calibration at all — it
# refuses with ``missing_parameter`` when a row is absent, and the golden
# vectors move when a value here moves, which is the executable form of "fetch
# it from the console".
#
# The BoG IRRBB Guideline is an exposure draft, so every SF row ships
# ``pending``: a printed-but-unconfirmed supervisory number is not a confirmed
# one, and the outputs say so on every surface that quotes it.
#
# NOT here, deliberately:
#   * ``irrbb_outlier_threshold_pct_tier1`` — already seeded by
#     ``202609190056`` (Pillar 2). Re-seeding it would supersede the live row,
#     because a duplicate seed back-dates rather than fails (D-053).
#   * ``irrbb_sf_option_vol_uplift_pct`` — reserved for the automatic-option
#     valuation (P5.1). Seeding a calibration for a method that does not exist
#     would let a reader think options are priced; they are refused (DV-010).

_SF_CITATION = "BoG IRRBB Guideline (Exposure Draft, February 2026)"
_SF_REPRESENTATIVE_CITATION = (
    "AequorOS platform methodology (REPRESENTATIVE): default cash-flow profiles are "
    "applied ONLY where an ingested position carries no amortisation, payment "
    "frequency or repricing horizon; every application is tallied and disclosed"
)

#: The nineteen time buckets of Appendix I Table 1, upper-INCLUSIVE. ``upper``
#: is a tenor ("1D"/"3M"/"5Y"); the final bucket is open-ended, hence ``None``.
_SF_BUCKETS: tuple[tuple[str, str, str | None, str], ...] = (
    ("b01", "Overnight", "1D", "0.0028"),
    ("b02", "Overnight to 1 month", "1M", "0.0417"),
    ("b03", "1 to 3 months", "3M", "0.1667"),
    ("b04", "3 to 6 months", "6M", "0.375"),
    ("b05", "6 to 9 months", "9M", "0.625"),
    ("b06", "9 months to 1 year", "12M", "0.875"),
    ("b07", "1 to 1.5 years", "18M", "1.25"),
    ("b08", "1.5 to 2 years", "24M", "1.75"),
    ("b09", "2 to 3 years", "3Y", "2.5"),
    ("b10", "3 to 4 years", "4Y", "3.5"),
    ("b11", "4 to 5 years", "5Y", "4.5"),
    ("b12", "5 to 6 years", "6Y", "5.5"),
    ("b13", "6 to 7 years", "7Y", "6.5"),
    ("b14", "7 to 8 years", "8Y", "7.5"),
    ("b15", "8 to 9 years", "9Y", "8.5"),
    ("b16", "9 to 10 years", "10Y", "9.5"),
    ("b17", "10 to 15 years", "15Y", "12.5"),
    ("b18", "15 to 20 years", "20Y", "17.5"),
    ("b19", "Over 20 years", None, "25"),
)


#: THE ONE PLACE IN THIS FILE THAT NAMES A CURRENCY, and the only construct the
#: jurisdiction-neutrality guard exempts (by name, not by file).
#:
#: The IRRBB framework prescribes a DIFFERENT shock size per currency and prints
#: them as a table whose ROW KEYS are ISO codes, plus the regulator's own
#: "Other" column for currencies it does not calibrate individually. A currency
#: code used as a data key is not a claim that any bank reports in that
#: currency — the same reading AGENTS.md gives a ``bog_``-prefixed identifier,
#: which means "central-bank reserves" in every jurisdiction. Dropping the keys
#: would destroy the regulator's own table; resolving them from the bank would
#: be a category error, because the table is about the CURRENCY OF THE
#: POSITION, not the currency of the reporter.
#:
#: Everything else in this module stays currency-neutral and is still scanned.
_SF_SHOCKS_BY_CURRENCY: Mapping[str, Mapping[str, str]] = {
    # currency of the position -> shock in basis points
    "parallel": {
        "GHS": "450",
        "USD": "200",
        "EUR": "225",
        "GBP": "275",
        "CNY": "225",
        "OTHER": "325",
    },
    "short": {
        "GHS": "500",
        "USD": "300",
        "EUR": "350",
        "GBP": "425",
        "CNY": "300",
        "OTHER": "500",
    },
    "long": {
        "GHS": "300",
        "USD": "225",
        "EUR": "200",
        "GBP": "250",
        "CNY": "150",
        "OTHER": "300",
    },
}


def _shock_body(kind: str) -> dict[str, Any]:
    """One shock table as the console stores it."""
    return {"schema": "irrbb-sf-currency-bp-v1", **_SF_SHOCKS_BY_CURRENCY[kind]}


def _sf(  # noqa: PLR0913 - a seed row is its named columns
    param_code: str,
    value: str | None,
    unit: str,
    source_citation: str,
    value_json: Mapping[str, Any] | None = None,
) -> ParamSpec:
    """One IRRBB Standardised Framework seed row — Ghana, banks, pending."""
    return ParamSpec(
        "institution_class",
        "bank",
        param_code,
        value,
        unit,
        source_citation,
        "pending",
        value_json,
    )


#: The sixteen governed codes the Standardised Framework engine reads, on top of
#: the Pillar 2 outlier threshold it shares. Pinned identically in the P5
#: migration, which does not import this catalogue.
IRRBB_SF_SEED_PARAMETERS: tuple[ParamSpec, ...] = (
    _sf(
        "irrbb_sf_time_buckets",
        None,
        "tenor_table",
        f"{_SF_CITATION} Appendix I ¶8-9, Table 1: nineteen upper-inclusive buckets "
        "with their midpoints",
        {
            "schema": "irrbb-sf-buckets-v1",
            "buckets": [
                {"key": key, "label": label, "upper": upper, "midpoint_years": midpoint}
                for key, label, upper, midpoint in _SF_BUCKETS
            ],
        },
    ),
    _sf(
        "irrbb_sf_parallel_shock_bp",
        None,
        "bps",
        f"{_SF_CITATION} Appendix II ¶1-3, Table 5: prescribed parallel shocks per "
        "currency, with the regulator's own 'Other' column",
        _shock_body("parallel"),
    ),
    _sf(
        "irrbb_sf_short_shock_bp",
        None,
        "bps",
        f"{_SF_CITATION} Appendix III, Table 6: prescribed short-rate shocks per currency",
        _shock_body("short"),
    ),
    _sf(
        "irrbb_sf_long_shock_bp",
        None,
        "bps",
        # "one" until 2026-09-20, when the regulatory audit (R-5) compared the
        # printed column against the Basel standardised-framework long-rate
        # shocks and found THREE of the four individually calibrated currencies
        # differ, not one. The table is governed and unchanged; only the
        # sentence about it is ours, so only the sentence moved. The count is
        # pinned by test_regulatory_parameters_p5, so a later edit to a printed
        # value cannot leave this claim stale.
        f"{_SF_CITATION} App III Table 6: long-rate shocks, used by the two rotations "
        "only. Reproduced AS PRINTED; three printed values differ from the Basel "
        "standard, which is open for the supervisor",
        _shock_body("long"),
    ),
    _sf(
        "irrbb_sf_short_decay_x",
        "4",
        "years",
        f"{_SF_CITATION} Appendix III ¶2(i), footnote 23: the short-rate scalar decays "
        "as exp(-t/x)",
    ),
    _sf(
        "irrbb_sf_rotation_coefficients",
        None,
        "multiplier",
        f"{_SF_CITATION} Appendix III ¶2(iii): the steepener and flattener weights on "
        "the short and long shock components",
        {
            "schema": "irrbb-sf-rotation-v1",
            "steepener": {"short": "-0.65", "long": "0.9"},
            "flattener": {"short": "0.8", "long": "-0.6"},
        },
    ),
    _sf(
        "irrbb_sf_cpr_multipliers",
        None,
        "multiplier",
        f"{_SF_CITATION} Appendix I ¶27-28, Table 3: the scenario multipliers applied "
        "to the bank's own base prepayment rate",
        {
            "schema": "irrbb-sf-scenario-scalars-v1",
            "parallel_up": "0.8",
            "parallel_down": "1.2",
            "steepener": "0.8",
            "flattener": "1.2",
            "short_up": "0.8",
            "short_down": "1.2",
        },
    ),
    _sf(
        "irrbb_sf_tdrr_scalars",
        None,
        "multiplier",
        f"{_SF_CITATION} Appendix I ¶32-34, Table 4: the scenario scalars applied to "
        "the bank's own term-deposit redemption rate",
        {
            "schema": "irrbb-sf-scenario-scalars-v1",
            "parallel_up": "1.2",
            "parallel_down": "0.8",
            "steepener": "0.8",
            "flattener": "1.2",
            "short_up": "1.2",
            "short_down": "0.8",
        },
    ),
    _sf(
        "irrbb_sf_nmd_caps",
        None,
        "percent_years",
        f"{_SF_CITATION} Appendix I ¶16-21, Table 2: the core-share and average "
        "repricing-maturity caps per deposit category",
        {
            "schema": "irrbb-sf-nmd-caps-v1",
            "retail_transactional": {"core_cap_pct": "90", "avg_maturity_cap_years": "5"},
            "retail_non_transactional": {"core_cap_pct": "70", "avg_maturity_cap_years": "4.5"},
            "wholesale": {"core_cap_pct": "50", "avg_maturity_cap_years": "4"},
        },
    ),
    _sf(
        "irrbb_sf_nmd_history_years",
        "10",
        "years",
        f"{_SF_CITATION} Appendix I ¶18: the observation period a core-deposit estimate "
        "is expected to rest on. A shorter history is disclosed, never a refusal",
    ),
    _sf(
        "irrbb_sf_major_currency_threshold_pct",
        "5",
        "percent",
        f"{_SF_CITATION} Appendix I ¶37 and ¶56 footnote 16: a currency is material "
        "when it exceeds this share of banking-book assets or liabilities",
    ),
    _sf(
        "irrbb_sf_outlier_scenario_set",
        None,
        "code_list",
        f"{_SF_CITATION} ¶38 prints the outlier test over i in {{1..6}}; App II ¶1 "
        "mandates the two parallel shocks. Seeded as all six, the ¶38 reading, with "
        "the two-scenario measure reported alongside",
        {
            "schema": "icaap-code-list-v1",
            "codes": [
                "parallel_up",
                "parallel_down",
                "steepener",
                "flattener",
                "short_up",
                "short_down",
            ],
            "required": [],
        },
    ),
    _sf(
        "irrbb_sf_mandatory_scenarios",
        None,
        "code_list",
        f"{_SF_CITATION} Appendix II ¶1: the parallel shocks are reported in every case",
        {
            "schema": "icaap-code-list-v1",
            "codes": ["parallel_up", "parallel_down"],
            "required": ["parallel_up", "parallel_down"],
        },
    ),
    _sf(
        "irrbb_sf_nii_horizon_months",
        "12",
        "months",
        f"{_SF_CITATION} Appendix IV, Table 8 definitions (ii): the earnings horizon",
    ),
    _sf(
        "irrbb_sf_cpr_time_scaling",
        None,
        "method",
        f"{_SF_CITATION} App I ¶29 does not settle whether the prepayment rate is "
        "annual and scaled to each bucket's width or applied per bucket as printed. "
        "The scaled reading is seeded and is switchable here",
        {"schema": "irrbb-sf-cpr-scaling-v1", "mode": "annual_rate_scaled_to_bucket_width"},
    ),
    _sf(
        "irrbb_sf_default_cash_flow_profile",
        None,
        "profile",
        _SF_REPRESENTATIVE_CITATION,
        {
            "schema": "irrbb-sf-cash-flow-profile-v1",
            "LOAN": {
                "amortisation": "annuity",
                "frequency_months": "1",
                "horizonless_bucket": "b13",
            },
            "SECURITY_HOLDING": {
                "amortisation": "bullet",
                "frequency_months": "6",
                "horizonless_bucket": "b13",
            },
            "INTERBANK_PLACEMENT": {
                "amortisation": "bullet",
                "frequency_months": "0",
                "horizonless_bucket": "b01",
            },
            "INTERBANK_BORROWING": {
                "amortisation": "bullet",
                "frequency_months": "0",
                "horizonless_bucket": "b01",
            },
            "DEPOSIT_TERM": {
                "amortisation": "bullet",
                "frequency_months": "0",
                "horizonless_bucket": "b01",
            },
            "OTHER_LIABILITY": {
                "amortisation": "bullet",
                "frequency_months": "0",
                "horizonless_bucket": "b01",
            },
            "CAPITAL_INSTRUMENT": {
                "amortisation": "bullet",
                "frequency_months": "6",
                "horizonless_bucket": "b13",
            },
            "INTEREST_RATE_SWAP": {"fixed_leg_frequency_months": "6"},
        },
    ),
    _sf(
        "irrbb_sf_mandatory_from_as_of",
        None,
        "date",
        f"{_SF_CITATION} ¶9 (effective 1 January 2027) and ¶60 (annual ICAAP). Which "
        "year-end it first bites on is INFERRED as the 31 December 2026 position, "
        "aligned with the first ICAAP report as-of",
        {"schema": "icaap-effective-date-v1", "date": "2026-12-31"},
    ),
)

#: The GA calibration (P5-DESIGN §4). Gordy-Lutkebohmert is not regulator
#: specific, so these are platform rows with Basel IRB provenance where one
#: exists. ``ga_min_effective_names`` decides whether the method engages AT ALL
#: (D-059 F9), which is why its citation says representative in so many words.
_GA_REPRESENTATIVE = "REPRESENTATIVE: AequorOS platform methodology"

GRANULARITY_SEED_PARAMETERS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "institution_class",
        "bank",
        "ga_confidence_q",
        "0.999",
        "ratio",
        "Gordy-Lutkebohmert (2013); the Basel IRB 99.9% solvency confidence level",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "ga_delta",
        "4.83",
        "multiplier",
        "Gordy-Lutkebohmert (2013) gamma-factor multiplier; derived as 4.833601 for "
        "an LGD variance coefficient of 0.25 at the 99.9% level and seeded rounded",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "ga_lgd_variance_gamma",
        "0.25",
        "ratio",
        "Gordy-Lutkebohmert (2013): VLGD = gamma x ELGD x (1 - ELGD)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "ga_default_elgd_pct",
        "45",
        "percent",
        "Basel IRB foundation senior unsecured loss given default; the LAST fallback, "
        "used only where an exposure and its segment state none",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "ga_min_effective_names",
        "50",
        "count",
        f"{_GA_REPRESENTATIVE}: below this many EFFECTIVE names (1/HHI) the "
        "adjustment is declined rather than approximated. No published basis, and it "
        "decides whether the method engages at all, so every result says so",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "ga_asset_correlation",
        None,
        "correlation",
        "Basel IRB CRE31.5 (corporate) and CRE31.14 (other retail): the asset "
        "correlation interpolates between r_min and r_max on exp(-k x PD)",
        "pending",
        {
            "schema": "ga-asset-correlation-v1",
            "corporate": {"r_min": "0.12", "r_max": "0.24", "k": "50"},
            "retail_other": {"r_min": "0.03", "r_max": "0.16", "k": "35"},
        },
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "ga_maturity_adjustment",
        None,
        "method",
        "Basel IRB CRE31.7 maturity adjustment. Seeded OFF: with no maturity the "
        "capital function is the one-year form, which is the conservative reading "
        "until the exposure book carries reliable effective maturities",
        "pending",
        {
            "schema": "ga-maturity-adjustment-v1",
            "apply": False,
            "b_intercept": "0.11852",
            "b_slope": "0.05478",
            "m_centre": "2.5",
            "m_scale": "1.5",
        },
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "ga_proxy_pd_by_rw_code",
        None,
        "percent",
        f"{_GA_REPRESENTATIVE}: indicative long-run default rates by risk weight, "
        "used ONLY where an exposure states no PD. RW0 is excluded rather than "
        "proxied: a zero-weighted sovereign is the sovereign component's",
        "pending",
        {
            "schema": "ga-proxy-pd-v1",
            "RW20": "0.03",
            "RW35": "0.5",
            "RW50": "0.1",
            "RW75": "1",
            "RW100": "2",
            "RW150": "10",
        },
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "ga_counterparty_segment_map",
        None,
        "mapping",
        f"{_GA_REPRESENTATIVE}: which correlation segment each canonical counterparty "
        "type takes. Sovereign-family types map to 'excluded' because they are "
        "measured by the sovereign component, not by name granularity",
        "pending",
        {
            "schema": "ga-segment-map-v1",
            "RETAIL_INDIVIDUAL": "retail_other",
            "SME": "corporate",
            "SME_MANAGED_AS_RETAIL": "retail_other",
            "CORPORATE": "corporate",
            "BANK_OECD": "corporate",
            "BANK_NON_OECD": "corporate",
            "NBFI": "corporate",
            "OTHER": "corporate",
            "UNSTATED": "corporate",
            "CENTRAL_BANK": "excluded",
            "SOVEREIGN": "excluded",
            "GOVERNMENT_ENTITY": "excluded",
            "MULTILATERAL_DEV_BANK": "excluded",
        },
    ),
)

#: Everything P5 seeds, in one tuple, so the migration, the hermetic fixture and
#: the duplicate-seed guard all name the same set.
P5_SEED_PARAMETERS: tuple[ParamSpec, ...] = (
    *IRRBB_SF_SEED_PARAMETERS,
    *GRANULARITY_SEED_PARAMETERS,
)

#: The codes above. Disjoint from P1's workspace codes, ``P2_PARAMETER_CODES``
#: and ``FILING_PARAMETER_CODES`` — a test proves it, because a duplicate seed
#: does not fail, it back-dates the live row (D-053).
P5_PARAMETER_CODES: frozenset[str] = frozenset(spec.param_code for spec in P5_SEED_PARAMETERS)

#: The authoritative seed catalogue (docs/sdi.md §2.2, §4). Confirmed [C] values
#: ship 'confirmed'; documented defaults awaiting BoG/internal confirmation [U]
#: ship 'pending' and are editable in the operator console.
SEED_PARAMETERS: tuple[ParamSpec, ...] = (
    # --- capital adequacy --------------------------------------------------
    ParamSpec(
        "institution_class",
        "bank",
        "car_min",
        "13",
        "percent",
        "BoG Capital Requirements Directive 2018 ¶71 (10%) + ¶75 CCB1 (3%)",
        "confirmed",
    ),
    ParamSpec("institution_class", "sdi", "car_min", "10", "percent", "Act 930 s.29", "confirmed"),
    # --- CRD capital-tier floors, buffers and recognition caps (banks) -----
    # Governed since 2026-09-19 (ICAAP P0, regulatory audit B2 / M21). Before
    # this only ``car_min`` had a control-plane row, so the tighten-only clamp
    # passed a board's ``cet1_min``/``tier1_min``/``leverage_min`` straight
    # through — and the default board register carried a 3% leverage floor
    # against the CRD's 6%, which every capital run, the ICAAP stress minima
    # check and Appendix II's residual-capital line then measured against.
    # ``cet1_min``/``tier1_min`` are governed at their current values (no
    # behaviour change) and ship ``pending``: whether their status floors should
    # also include the CCB1 buffer is an open question (M20), deliberately NOT
    # decided in code (D-024 — the console decides).
    # Banks only: an SDI runs the Act 930 s.29 regime, which structurally zeroes
    # the Basel sub-tier and leverage floors.
    *(
        ParamSpec(
            "institution_class",
            "bank",
            code,
            value,
            "percent",
            citation,
            status,
        )
        for code, value, status, citation in CRD_CAPITAL_FLOOR_SEEDS
    ),
    # The SDI AT1 / Tier 2 recognition caps (D-042): pre-P0 platform behaviour,
    # pending — see ``SDI_RECOGNITION_CAP_SEEDS``.
    *(
        ParamSpec("institution_class", "sdi", code, value, "percent", citation, status)
        for code, value, status, citation in SDI_RECOGNITION_CAP_SEEDS
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "statutory_reserve_fund_pct",
        "50",
        "percent",
        "Act 930 s.34; NBFI r.7",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "statutory_reserve_fund_pct",
        "50",
        "percent",
        "NBFI r.7; Act 930 s.34",
        "confirmed",
    ),
    # --- minimum paid-up capital (licence-specific) ------------------------
    ParamSpec(
        "institution_type",
        "universal_bank",
        "paid_up_min",
        "400",
        "ghs_millions",
        "BoG minimum capital (banks)",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "financial_holding_company",
        "paid_up_min",
        "400",
        "ghs_millions",
        "BoG minimum capital (banks)",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "savings_and_loans",
        "paid_up_min",
        "15",
        "ghs_millions",
        "SDI Subsector ToR",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "finance_house",
        "paid_up_min",
        "15",
        "ghs_millions",
        "SDI Subsector ToR",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "microfinance_bank",
        "paid_up_min",
        "2",
        "ghs_millions",
        "MFI Framework 2026",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "rural_community_bank",
        "paid_up_min",
        "1",
        "ghs_millions",
        "SDI Subsector ToR",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "other_rfi",
        "paid_up_min",
        "15",
        "ghs_millions",
        "SDI Subsector ToR (default)",
        "pending",
    ),
    # --- exposures ---------------------------------------------------------
    ParamSpec(
        "institution_class",
        "bank",
        "single_obligor_limit_pct",
        "25",
        "percent",
        "Act 930 s.62(1)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "single_obligor_limit_pct",
        "25",
        "percent",
        "Act 930 s.62(1)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "large_exposure_limit_pct",
        "20",
        "percent",
        "Large Exposures Directive Sept 2025",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "large_exposure_limit_pct",
        "15",
        "percent",
        "Large Exposures Directive Sept 2025",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "large_exposure_id_threshold_pct",
        "10",
        "percent",
        "BoG LE return (BSD) 10% identification",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "large_exposure_id_threshold_pct",
        "10",
        "percent",
        "BoG LE return (BSD) 10% identification",
        "confirmed",
    ),
    # related-party & aggregate caps: mechanism built, value pending confirmation
    ParamSpec(
        "institution_class",
        "sdi",
        "related_party_limit_pct",
        "25",
        "percent",
        "Act 930 related-party (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "related_party_limit_pct",
        "25",
        "percent",
        "Act 930 related-party (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "aggregate_large_exposure_cap",
        "8",
        "multiplier",
        "LED aggregate cap (×NOF; value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "aggregate_large_exposure_cap",
        "8",
        "multiplier",
        "LED aggregate cap (×NOF; value pending BoG)",
        "pending",
    ),
    # --- SDI liquidity reserves (NBFI r.11) --------------------------------
    ParamSpec(
        "institution_class",
        "sdi",
        "primary_liquidity_reserve_pct",
        "10",
        "percent",
        "NBFI Business Rules 2000 r.11",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "secondary_liquidity_reserve_pct",
        "15",
        "percent",
        "NBFI Business Rules 2000 r.11",
        "confirmed",
    ),
    # --- provisioning rates (grid choice [U]; RATES [C]) -------------------
    ParamSpec(
        "institution_class",
        "sdi",
        "prov_standard",
        "0",
        "percent",
        "NBFI Rules 2000 r.19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "prov_substandard",
        "20",
        "percent",
        "NBFI Rules 2000 r.19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "prov_doubtful",
        "50",
        "percent",
        "NBFI Rules 2000 r.19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "prov_loss",
        "100",
        "percent",
        "NBFI Rules 2000 r.19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_standard",
        "1",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_olem",
        "10",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_substandard",
        "25",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_doubtful",
        "50",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_loss",
        "100",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    # --- NPL prudential limits (Notice BG/GOV/SEC/2025/23, Aug 2025) -------
    # The NPL-ratio ceiling (10%, compliance by end-Dec 2026) and the level at
    # which dividend/bonus/loan-growth restrictions apply immediately (15%)
    # bind banks AND SDIs alike, so both classes carry the same rows. The
    # restructure cure counts implement ¶12: a restructured facility stays
    # non-performing until 6 consecutive full repayments (4 for semi-annual
    # schedules; a bullet cures only at full settlement — structural, unseeded).
    *(
        ParamSpec(
            "institution_class",
            klass,
            code,
            value,
            unit,
            "BoG Notice BG/GOV/SEC/2025/23 (Regulatory Measures to Reduce NPLs)",
            "confirmed",
        )
        for klass in ("bank", "sdi")
        for code, value, unit in (
            ("npl_limit_pct", "10", "percent"),
            ("npl_dividend_restriction_pct", "15", "percent"),
            ("restructure_cure_payments", "6", "count"),
            ("restructure_cure_payments_semi_annual", "4", "count"),
        )
    ),
    # --- loan-classification DPD boundaries (days) -------------------------
    ParamSpec(
        "institution_class",
        "sdi",
        "npl_dpd_threshold",
        "90",
        "days",
        "NBFI Rules 2000 rr.17-19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "dpd_substandard_min",
        "90",
        "days",
        "NBFI Rules 2000 rr.17-19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "dpd_doubtful_min",
        "180",
        "days",
        "NBFI Rules 2000 rr.17-19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "dpd_loss_min",
        "360",
        "days",
        "NBFI Rules 2000 rr.17-19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "npl_dpd_threshold",
        "90",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "dpd_olem_min",
        "30",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "dpd_substandard_min",
        "90",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "dpd_doubtful_min",
        "180",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "dpd_loss_min",
        "360",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    # --- simplified SDI risk weights (value pending BoG) -------------------
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_sovereign",
        "0",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_cash",
        "0",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_interbank",
        "20",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_mortgage",
        "50",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_other_loans",
        "100",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_other_assets",
        "100",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    # --- Basel LCR HQLA haircuts + Level-2 caps (bank class only) ----------
    # The stock of HQLA was an unweighted face-value sum before 2026-08-21
    # (enterprise audit P0-8): no Level-2A haircut, no Level-2B haircut, no 40%
    # Level-2 cap, no 15% Level-2B sub-cap. These are the governed values the
    # pure engine now resolves through ``LiquidityParams``; nothing about a
    # haircut or a cap is written in the engine.
    #
    # LCR is a Basel measure and is bank-only under Act 930 / the SDI regime
    # (docs/sdi.md §4.6), so these are seeded for ``institution_class='bank'``
    # only — an SDI never runs ``compute_lcr``.
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_l1_haircut_pct",
        "0",
        "percent",
        "BCBS 238 (Basel III LCR) ¶50 — Level 1 assets carry no haircut",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_l2a_haircut_pct",
        "15",
        "percent",
        "BCBS 238 ¶52 — 15% haircut on every Level 2A asset",
        "confirmed",
    ),
    # Basel sets the Level 2B haircut BY SUB-CLASS: 25% for qualifying RMBS
    # (¶54(a)) and 50% for qualifying corporate debt and common equity
    # (¶54(b),(c)). The canonical fact model carries only an HQLA *level*, not an
    # L2B sub-class, so the platform applies the most conservative rate in the
    # range. That is a documented modelling choice, not a BoG-confirmed number —
    # it ships 'pending' so it is visible in the operator console and every
    # resolution is logged. Splitting L2B into sub-classes (and confirming the
    # per-sub-class rate) is the follow-on work.
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_l2b_haircut_pct",
        "50",
        "percent",
        "BCBS 238 ¶54(b),(c) — conservative bound of the 25-50% L2B range",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_level2_cap_pct",
        "40",
        "percent",
        "BCBS 238 ¶47 — Level 2 assets may not exceed 40% of the stock of HQLA",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_level2b_cap_pct",
        "15",
        "percent",
        "BCBS 238 ¶47 — Level 2B assets may not exceed 15% of the stock of HQLA",
        "confirmed",
    ),
    # --- data-integrity controls (enterprise audit 2026-08-20 P0-10) -------
    # The balance-sheet identity tolerance: |assets − (liabilities + equity)| as
    # a percent of total assets, above which the book may not produce a FILED
    # number (app/services/reconciliation.py). It is a supervisory-judgement
    # number, not a BoG-published one, so it ships 'pending' and is editable in
    # the operator console under four eyes. A tenant board override may only
    # TIGHTEN it (PARAMETER_DIRECTION: ceiling).
    ParamSpec(
        "institution_class",
        "bank",
        "balance_identity_tolerance_pct",
        "0.10",
        "percent",
        "AequorOS data-integrity control (value pending internal confirmation)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "balance_identity_tolerance_pct",
        "0.10",
        "percent",
        "AequorOS data-integrity control (value pending internal confirmation)",
        "pending",
    ),
    *_lmtd_specs(),
    *ICAAP_P2_SEED_PARAMETERS,
    *ICAAP_FILING_SEED_PARAMETERS,
    *P5_SEED_PARAMETERS,
)

#: The HQLA parameter codes the LCR engine consumes, in the order the loaders
#: resolve them. Exported so the loaders, the seed and the tests name the same
#: set (``app/domain/liquidity/engine.py`` never names a rate).
HQLA_HAIRCUT_CODES: dict[str, str] = {
    "L1": "hqla_l1_haircut_pct",
    "L2A": "hqla_l2a_haircut_pct",
    "L2B": "hqla_l2b_haircut_pct",
}
HQLA_LEVEL2_CAP_CODE = "hqla_level2_cap_pct"
HQLA_LEVEL2B_CAP_CODE = "hqla_level2b_cap_pct"


@dataclass(frozen=True)
class ResolvedParameter:
    """A resolved regulatory number with its full audit provenance."""

    param_code: str
    value: Decimal | None
    value_json: dict | None
    unit: str
    source_citation: str
    confirmation_status: str
    scope_type: str
    scope_key: str
    jurisdiction_code: str
    effective_from: date
    parameter_id: str
    #: Which link of the chain supplied the value ('institution_type' |
    #: 'institution_class'). Defaulted so historic keyword construction still works.
    layer: str = ""
    #: Set when this value was used to clamp a weaker tenant board override.
    clamped_from: Decimal | None = None

    @property
    def is_pending(self) -> bool:
        return self.confirmation_status == "pending"

    @property
    def decimal(self) -> Decimal:
        if self.value is None:
            msg = f"Regulatory parameter {self.param_code!r} has no scalar value."
            raise ValueError(msg)
        return self.value

    @property
    def normalized_value(self) -> Decimal | None:
        """The scalar value with the trailing zeros a ``Numeric(18,6)`` round-trip
        adds stripped (Decimal("80.000000") -> Decimal("80")), so a value sourced
        from the control plane is byte-identical to an in-code constant when it
        lands in generated return content / a content digest. Integral values
        quantize to scale 0; fractional values normalise (12.500000 -> 12.5)
        without scientific notation for the percentage ranges in use."""
        if self.value is None:
            return None
        v = self.value
        return v.quantize(Decimal(1)) if v == v.to_integral_value() else v.normalize()

    def provenance(self) -> dict[str, object]:
        """The audit record for this resolution: which parameter, which version,
        what source, was it confirmed. Stable wire keys — the WS-A provenance
        struct integration point (mirrors
        ``app.domain.policy.PolicyResolution.provenance``)."""
        return {
            "param_code": self.param_code,
            "value": None if self.value is None else str(self.normalized_value),
            "unit": self.unit,
            "layer": self.layer or self.scope_type,
            "scope_type": self.scope_type,
            "scope_key": self.scope_key,
            "jurisdiction_code": self.jurisdiction_code,
            "effective_from": self.effective_from.isoformat(),
            "source_citation": self.source_citation,
            "confirmation_status": self.confirmation_status,
            "parameter_id": self.parameter_id,
            "clamped": self.clamped_from is not None,
            "clamped_from": None if self.clamped_from is None else str(self.clamped_from),
        }


class RegulatoryParameterError(LookupError):
    """A required regulatory parameter is not seeded for the tenant's scope.

    Carries WS-A's ``POLICY_UNRESOLVED`` outcome detail on ``.detail`` so a caller
    that persists fail-closed states against a run can record it, while
    ``str(exc)`` stays the plain message it has always been.
    """

    def __init__(self, message: str, detail: OutcomeDetail | None = None) -> None:
        super().__init__(message)
        self.detail: OutcomeDetail | None = detail


# The tighten-only rules (``PARAMETER_DIRECTION``, ``Direction``, ``tighten``,
# ``clamp_overrides``) moved to ``app/domain/policy/resolver.py`` on 2026-08-21 and
# are re-exported above. They were pure functions living in a database module, and
# keeping them here is what allowed two call sites to disagree about whether
# ``car_min`` was clamped at all. The DB-bound generalisation is
# :func:`clamp_overrides` further down.


def _active_row(  # noqa: PLR0913 - the resolution key is 5 explicit keyword parts
    db: Session,
    *,
    scope_type: str,
    scope_key: str,
    param_code: str,
    jurisdiction_code: str,
    as_of: date,
    record: bool,
) -> RegulatoryParameter | None:
    """The newest APPROVED generation active on ``as_of`` for one scope.

    Active window mirrors ``params.get_active_params`` exactly (one date rule in
    the codebase): ``effective_from <= as_of`` and ``effective_to`` null or > as_of.

    ``record=False`` reads without entering the row in the session's consumption
    ledger — for a read that feeds no run (seeding a tenant register, a REPORT
    plane assembling its own provenance), so a run sealed later in the same
    session is not credited with a row it did not resolve.

    ``record`` is REQUIRED and has no default, exactly as
    :meth:`PrefetchedParameterResolver.load` requires it and for the same reason
    (D-078): the ledger is ambient, so a caller that never thought about the
    question silently joins the CALCULATION plane. There is no safe default —
    every caller states its plane.
    """
    stmt = (
        select(RegulatoryParameter)
        .where(
            RegulatoryParameter.scope_type == scope_type,
            RegulatoryParameter.scope_key == scope_key,
            RegulatoryParameter.param_code == param_code,
            RegulatoryParameter.jurisdiction_code == jurisdiction_code,
            RegulatoryParameter.status == "approved",
            RegulatoryParameter.effective_from <= as_of,
            or_(
                RegulatoryParameter.effective_to.is_(None),
                RegulatoryParameter.effective_to > as_of,
            ),
        )
        .order_by(RegulatoryParameter.effective_from.desc(), RegulatoryParameter.id)
        .limit(1)
    )
    row = db.scalar(stmt)
    if row is not None and record:
        _record_consumption(db, row)
    return row


# --- governed-parameter row provenance (audit 2026-08-22 D-18) -------------
#
# A sealed ``RegulatoryRun`` recorded the parameter VALUES it consumed
# (``inputs["parameters"]``, covered by the value-based ``input_hash``) but never
# WHICH ROW supplied them, so "prove this filed ratio used the approved
# parameter" had no answer on the governance axis. ``_active_row`` above is the
# single place a governed row is read, so it is where consumption is recorded.
#
# The ledger is keyed by ``Session`` and DRAINED when a run is sealed
# (``consume_parameter_provenance``), which is what binds a row to the run that
# used it. Within the CALCULATION plane it is deliberately over-inclusive rather
# than silent: it holds every governed row that plane resolved since the previous
# run was sealed, so it can name a row the engine did not arithmetically consume
# and can never MISS one it did.
#
# **The plane boundary (2026-09-20).** Over-inclusiveness is only tolerable while
# every contributor belongs to the run's own family. The DISPATCH plane — the
# registry's eligibility scan and the reporting calendar — is the opposite: it
# resolves EVERY registered return's ``effective_from_parameter`` and
# ``deadline_parameter``, across EVERY family, to answer "which returns exist for
# this institution and when are they due". Recording those made a run's
# provenance a function of the registry's contents rather than of its own
# calculation, and an ICAAP commencement date therefore moved an already-filed
# LIQUIDITY package's ``content_digest`` — the value every signer signs. Dispatch
# reads now go through a resolver built with ``record=False``
# (:meth:`PrefetchedParameterResolver.load`), so no registry entry — present or
# future, ICAAP or Nigeria or Kenya — can reach this ledger at all.
_CONSUMED: WeakKeyDictionary[Session, dict[str, dict[str, Any]]] = WeakKeyDictionary()


def parameter_row_provenance(row: RegulatoryParameter) -> dict[str, Any]:
    """The identity + authority of one control-plane row, JSON-ready.

    ``row_version`` is ``updated_at``: the control plane has no version column,
    and since ``202608230038`` an approved generation cannot be edited in place,
    so the pair (id, updated_at) pins exactly one immutable state of the row.
    """
    entry: dict[str, Any] = {
        "parameter_id": str(row.id),
        "param_code": row.param_code,
        "scope_type": row.scope_type,
        "scope_key": row.scope_key,
        "jurisdiction_code": row.jurisdiction_code,
        "unit": row.unit,
        "value": None if row.value_numeric is None else str(row.value_numeric),
        "source_citation": row.source_citation,
        "confirmation_status": row.confirmation_status,
        "status": row.status,
        "effective_from": row.effective_from.isoformat(),
        "effective_to": None if row.effective_to is None else row.effective_to.isoformat(),
        "proposed_by": row.proposed_by,
        "approved_by": row.approved_by,
        "approved_at": None if row.approved_at is None else row.approved_at.isoformat(),
        "row_version": None if row.updated_at is None else row.updated_at.isoformat(),
    }
    if row.value_json is not None:
        entry["value_json"] = row.value_json
    return entry


def _record_consumption(db: Session, row: RegulatoryParameter) -> None:
    _CONSUMED.setdefault(db, {})[str(row.id)] = parameter_row_provenance(row)


def consume_parameter_provenance(db: Session) -> list[dict[str, Any]]:
    """Take the rows resolved since the last run was sealed, and clear the ledger.

    Returns a deterministically ordered list. An EMPTY list is a positive
    statement — "this run resolved no governed parameter" — and is stored as
    such; ``None`` on a run means the run predates the column and is never
    written by this function.
    """
    recorded = _CONSUMED.pop(db, {})
    return sorted(
        recorded.values(),
        key=lambda entry: (
            entry["param_code"],
            entry["scope_type"],
            entry["scope_key"],
            entry["jurisdiction_code"],
            entry["effective_from"],
            entry["parameter_id"],
        ),
    )


def _to_resolved(row: RegulatoryParameter) -> ResolvedParameter:
    return ResolvedParameter(
        param_code=row.param_code,
        value=row.value_numeric,
        value_json=row.value_json,
        unit=row.unit,
        source_citation=row.source_citation,
        confirmation_status=row.confirmation_status,
        scope_type=row.scope_type,
        scope_key=row.scope_key,
        jurisdiction_code=row.jurisdiction_code,
        effective_from=row.effective_from,
        parameter_id=str(row.id),
        layer=row.scope_type,
    )


def resolve_class_value(
    db: Session,
    institution_class: str,
    param_code: str,
    *,
    jurisdiction: str,
    as_of: date | None = None,
) -> Decimal | None:
    """The class-keyed scalar value, independent of a specific bank. Used to
    surface the ENFORCED value on a display payload so it equals what the engine
    resolves (one source of truth) — e.g. the institution-type detail's exposure
    limits. Returns None when the class row is not seeded.

    ``jurisdiction`` is REQUIRED and has no default. It used to default to "GH",
    which meant a display payload for a Nigerian institution silently showed
    Ghana's enforced limits (enterprise audit 2026-08-20 §6). Pass the bank's own
    ``jurisdiction_code`` — ``jurisdictions.jurisdiction_code(bank)``.
    """
    row = _active_row(
        db,
        scope_type="institution_class",
        scope_key=institution_class,
        param_code=param_code,
        jurisdiction_code=jurisdiction,
        as_of=as_of or date.today(),
        # Unchanged from the implicit default this call carried before ``record``
        # became required. It is arguably a display read and therefore a DISPATCH
        # one, but flipping it would SHRINK the provenance of a run sealed later
        # in the same session — a change to stored evidence, not a gate. Stated
        # here so the next reader sees a decision rather than a default.
        record=True,
    )
    return _to_resolved(row).normalized_value if row is not None else None


def policy_scope(db: Session, bank: Bank, *, as_of: date | None = None) -> PolicyScope:
    """Build the full policy chain key for ``bank`` (FAIL CLOSED at every link).

    ``Jurisdiction -> Regulator -> Institution Type -> Regime -> Return Family
    -> Effective Date``. This is the ONE place the chain is assembled from a
    ``Bank``; engines and services call it (or the resolvers built on it) instead
    of re-deriving ``institution_class``/``jurisdiction_code`` themselves.

    Raises ``PolicyUnresolvedError`` when the jurisdiction is unset or unknown and
    ``InstitutionTypeUnresolved`` when the licence class does not resolve — no
    link is ever substituted.
    """
    jurisdiction_row = jurisdictions.require_jurisdiction(db, bank)
    type_row = institution_types.get_type(db, bank)
    return PolicyScope(
        jurisdiction_code=jurisdiction_row.code,
        currency=jurisdictions.base_currency(bank),
        regulator_short=jurisdiction_row.regulator_short,
        regulator_name=jurisdiction_row.central_bank_name,
        institution_type=type_row.type_code,
        institution_class=type_row.institution_class,
        capital_regime=type_row.capital_regime,
        return_family=type_row.return_family,
        liquidity_binding=bool(type_row.liquidity_binding),
        as_of=as_of or date.today(),
    )


@dataclass(frozen=True)
class PrefetchedParameterResolver:
    """Request-local governed-parameter generations for one institution.

    The full policy scope (including jurisdiction and institution type) is
    resolved once, then every approved generation overlapping the requested
    date window is loaded in one query. Resolution still applies the exact
    licence-before-class precedence, active-window rule and observability used
    by :func:`try_resolve`.

    ``records_consumption`` declares which PLANE the resolver serves, and
    :meth:`load` requires it explicitly because there is no safe default. A
    CALCULATION-plane resolver (``record=True``) feeds an engine whose run is
    sealed with :func:`consume_parameter_provenance`, so its reads are that
    run's governed-row provenance. A DISPATCH-plane resolver (``record=False``)
    answers registry and calendar questions that span every family and seal no
    run; its reads are not any run's provenance and must never enter the ledger.
    See the ``_CONSUMED`` note above for the regression that made the boundary
    explicit.
    """

    db: Session
    bank: Bank
    scope: PolicyScope
    rows: tuple[RegulatoryParameter, ...]
    records_consumption: bool = True

    @classmethod
    def load(
        cls, db: Session, bank: Bank, *, as_of_dates: Iterable[date], record: bool
    ) -> PrefetchedParameterResolver:
        dates = tuple(as_of_dates)
        anchor = min(dates) if dates else date.today()
        scope = policy_scope(db, bank, as_of=anchor)
        if not dates:
            return cls(db=db, bank=bank, scope=scope, rows=(), records_consumption=record)
        first, last = min(dates), max(dates)
        scope_conditions = [
            and_(
                RegulatoryParameter.scope_type == scope_type,
                RegulatoryParameter.scope_key == scope_key,
            )
            for scope_type, scope_key in resolution_order(scope)
        ]
        rows = tuple(
            db.scalars(
                select(RegulatoryParameter)
                .where(
                    or_(*scope_conditions),
                    RegulatoryParameter.jurisdiction_code == scope.jurisdiction_code,
                    RegulatoryParameter.status == "approved",
                    RegulatoryParameter.effective_from <= last,
                    or_(
                        RegulatoryParameter.effective_to.is_(None),
                        RegulatoryParameter.effective_to > first,
                    ),
                )
                .order_by(
                    RegulatoryParameter.param_code,
                    RegulatoryParameter.scope_type,
                    RegulatoryParameter.scope_key,
                    RegulatoryParameter.effective_from,
                    RegulatoryParameter.id,
                )
            )
        )
        return cls(db=db, bank=bank, scope=scope, rows=rows, records_consumption=record)

    def _scope_on(self, as_of: date) -> PolicyScope:
        return replace(self.scope, as_of=as_of)

    def try_resolve(self, param_code: str, *, as_of: date) -> ResolvedParameter | None:
        scope = self._scope_on(as_of)
        for scope_type, scope_key in resolution_order(scope):
            candidates = [
                row
                for row in self.rows
                if row.scope_type == scope_type
                and row.scope_key == scope_key
                and row.param_code == param_code
                and row.effective_from <= as_of
                and (row.effective_to is None or row.effective_to > as_of)
            ]
            if not candidates:
                continue
            newest_date = max(row.effective_from for row in candidates)
            row = min(
                (row for row in candidates if row.effective_from == newest_date),
                key=lambda item: str(item.id),
            )
            if self.records_consumption:
                _record_consumption(self.db, row)
            return _observe_resolution(self.bank, _to_resolved(row))
        return None

    def resolve(self, param_code: str, *, as_of: date) -> ResolvedParameter:
        resolved = self.try_resolve(param_code, as_of=as_of)
        if resolved is not None:
            return resolved
        scope = self._scope_on(as_of)
        logger.error(
            "regulatory_parameter.unseeded code=%s bank=%s org=%s scope=%s",
            param_code,
            self.bank.id,
            self.bank.organization_id,
            scope.describe(),
        )
        msg = (
            f"Regulatory parameter {param_code!r} is not seeded for bank {self.bank.id} "
            f"({scope.describe()}). It must exist in the regulatory-parameter control "
            "plane — configure it in the operator console."
        )
        raise RegulatoryParameterError(
            msg,
            policy_unresolved(param_code, scope, reason=msg, items=(f"param:{param_code}",)),
        )

    def resolve_hqla_parameters(self, *, as_of: date) -> HqlaParameters:
        haircuts: dict[str, Decimal] = {}
        for level, code in HQLA_HAIRCUT_CODES.items():
            resolved = self.try_resolve(code, as_of=as_of)
            if resolved is not None and resolved.value is not None:
                haircuts[level] = resolved.decimal
        cap2 = self.try_resolve(HQLA_LEVEL2_CAP_CODE, as_of=as_of)
        cap2b = self.try_resolve(HQLA_LEVEL2B_CAP_CODE, as_of=as_of)
        return HqlaParameters(
            haircut_pct=haircuts,
            level2_cap_pct=None if cap2 is None else cap2.value,
            level2b_cap_pct=None if cap2b is None else cap2b.value,
        )

    def clamp_overrides(self, tenant_values: Mapping[str, Decimal], *, as_of: date) -> ClampReport:
        governed = {
            code: value for code, value in tenant_values.items() if code in governed_codes()
        }
        absent = _absent_register_codes(self.scope.institution_class, tenant_values)
        if not governed and not absent:
            return ClampReport(values=dict(tenant_values), clamped=())
        controls = {
            code: (
                param.normalized_value
                if (param := self.try_resolve(code, as_of=as_of)) is not None
                else None
            )
            for code in (*governed, *absent)
        }
        report = _clamp_values(governed, {code: controls[code] for code in governed})
        if report.clamped:
            logger.warning(
                "regulatory_parameter.tenant_override_clamped bank=%s org=%s codes=%s details=%s",
                self.bank.id,
                self.bank.organization_id,
                ",".join(report.codes_clamped()),
                [record.to_dict() for record in report.clamped],
            )
        merged = dict(tenant_values)
        merged.update(report.values)
        merged.update(_filled(absent, controls))
        return ClampReport(values=merged, clamped=report.clamped)


def _absent_register_codes(
    institution_class: str, tenant_values: Mapping[str, Decimal]
) -> tuple[str, ...]:
    """The governed capital minima this register carries no row for (D-042)."""
    return tuple(
        code
        for code in CONTROL_PLANE_REGISTER_CODES.get(institution_class, ())
        if code not in tenant_values
    )


def _filled(absent: Iterable[str], controls: Mapping[str, Decimal | None]) -> dict[str, Decimal]:
    """The governed value for each absent register code that the control plane has.

    A code with no approved, effective row stays absent, so the calculation that
    needs it refuses with ``missing_parameter`` — no value is invented."""
    return {code: value for code in absent if (value := controls.get(code)) is not None}


def _observe_resolution(bank: Bank, resolved: ResolvedParameter) -> ResolvedParameter:
    """Record the observability event for a resolution and return it unchanged.

    A *pending* (unconfirmed) value driving a live regulatory calculation is the
    signal an operator must be able to alert on — a documented default is standing
    in for a not-yet-confirmed BoG number. Confirmed resolutions are not logged
    (they are the steady state and would drown the signal)."""
    if resolved.is_pending:
        logger.warning(
            "regulatory_parameter.pending_value_used code=%s scope=%s/%s value=%s unit=%s "
            "bank=%s org=%s citation=%r",
            resolved.param_code,
            resolved.scope_type,
            resolved.scope_key,
            resolved.value,
            resolved.unit,
            bank.id,
            bank.organization_id,
            resolved.source_citation,
        )
    return resolved


def try_resolve(
    db: Session,
    bank: Bank,
    param_code: str,
    *,
    as_of: date | None = None,
    record: bool = True,
) -> ResolvedParameter | None:
    """Resolve ``param_code`` for ``bank`` or return ``None`` if unseeded.

    Precedence is ``app.domain.policy.resolution_order``: the licence-specific
    (institution_type) row, then the coarse (institution_class) row. Use this for
    dormant/optional parameters (e.g. an aggregate-exposure cap that is inactive
    until a value is confirmed); use :func:`resolve` where the number is mandatory.

    The jurisdiction is resolved FAIL-CLOSED from the bank (no ``or "GH"``): an
    institution with no jurisdiction cannot have a parameter set selected for it.

    ``record`` defaults to ``True`` because the engines are the overwhelming
    caller and this is their door. A plane that seals no run — the ICAAP report
    plane, which writes its own governed-row record into the snapshot — passes
    ``record=False`` and through ONE module of its own
    (``app/services/icaap/parameters.py``), so the decision is taken once rather
    than at each of its call sites.
    """
    scope = policy_scope(db, bank, as_of=as_of)
    for scope_type, scope_key in resolution_order(scope):
        row = _active_row(
            db,
            scope_type=scope_type,
            scope_key=scope_key,
            param_code=param_code,
            jurisdiction_code=scope.jurisdiction_code,
            as_of=scope.as_of,
            record=record,
        )
        if row is not None:
            return _observe_resolution(bank, _to_resolved(row))
    return None


def seed_values(
    db: Session, bank: Bank, param_codes: Iterable[str], *, as_of: date
) -> dict[str, ResolvedParameter | None]:
    """The governed row for each code on ``as_of``, for SEEDING a tenant register.

    Same chain and precedence as :func:`try_resolve` (licence-specific row, then
    class row; approved generations only), but the reads feed no calculation, so
    they are neither logged as pending-value consumption nor entered in the run
    provenance ledger. ``None`` for a code with no approved, effective row — the
    caller refuses; a value is never invented (founder directive D-024).
    """
    scope = policy_scope(db, bank, as_of=as_of)
    resolved: dict[str, ResolvedParameter | None] = {}
    for code in param_codes:
        resolved[code] = None
        for scope_type, scope_key in resolution_order(scope):
            row = _active_row(
                db,
                scope_type=scope_type,
                scope_key=scope_key,
                param_code=code,
                jurisdiction_code=scope.jurisdiction_code,
                as_of=scope.as_of,
                record=False,
            )
            if row is not None:
                resolved[code] = _to_resolved(row)
                break
    return resolved


def resolve(
    db: Session,
    bank: Bank,
    param_code: str,
    *,
    as_of: date | None = None,
    record: bool = True,
) -> ResolvedParameter:
    """Resolve a MANDATORY regulatory parameter for ``bank`` (fail-loud).

    Raises :class:`RegulatoryParameterError` when no approved row exists for the
    bank's institution_type or institution_class — a regulatory number is never
    substituted out of thin air (mirror of ``institution_types.get_type`` and
    ``jurisdictions.base_currency`` discipline).

    ``record`` carries the plane, exactly as on :func:`try_resolve`.
    """
    resolved = try_resolve(db, bank, param_code, as_of=as_of, record=record)
    if resolved is None:
        scope = policy_scope(db, bank, as_of=as_of)
        logger.error(
            "regulatory_parameter.unseeded code=%s bank=%s org=%s scope=%s",
            param_code,
            bank.id,
            bank.organization_id,
            scope.describe(),
        )
        msg = (
            f"Regulatory parameter {param_code!r} is not seeded for bank {bank.id} "
            f"({scope.describe()}). It must exist in the regulatory-parameter control "
            "plane — configure it in the operator console."
        )
        raise RegulatoryParameterError(
            msg, policy_unresolved(param_code, scope, reason=msg, items=(f"param:{param_code}",))
        )
    return resolved


def resolve_decimal(
    db: Session, bank: Bank, param_code: str, *, as_of: date | None = None
) -> Decimal:
    """Convenience: the scalar value of a mandatory parameter."""
    return resolve(db, bank, param_code, as_of=as_of).decimal


def resolve_many(
    db: Session, bank: Bank, param_codes: list[str], *, as_of: date | None = None
) -> dict[str, ResolvedParameter]:
    """Resolve several mandatory parameters at once (all must exist)."""
    return {code: resolve(db, bank, code, as_of=as_of) for code in param_codes}


class HqlaParameters(NamedTuple):
    """The governed HQLA haircuts + Level-2 caps for one institution.

    Deliberately PARTIAL rather than fail-loud: a code with no approved,
    effective row is simply absent/``None`` here, and the pure engine then fails
    closed only if it actually binds — i.e. only if the bank really holds an
    asset at that level, or really holds a Level-2 asset whose cap is unresolved.
    A Level-1-only book is therefore never blocked on an unseeded Level-2B rate,
    and no missing rate is ever substituted with a zero.
    """

    haircut_pct: dict[str, Decimal]
    level2_cap_pct: Decimal | None
    level2b_cap_pct: Decimal | None

    @property
    def unresolved_codes(self) -> tuple[str, ...]:
        """The HQLA codes that did not resolve — for an operator-facing message."""
        missing = [
            code for level, code in HQLA_HAIRCUT_CODES.items() if level not in self.haircut_pct
        ]
        if self.level2_cap_pct is None:
            missing.append(HQLA_LEVEL2_CAP_CODE)
        if self.level2b_cap_pct is None:
            missing.append(HQLA_LEVEL2B_CAP_CODE)
        return tuple(missing)


def resolve_hqla_parameters(
    db: Session, bank: Bank, *, as_of: date | None = None
) -> HqlaParameters:
    """The Basel HQLA haircuts + Level-2 caps from the control plane.

    THE single seam through which a haircut or a cap reaches ``compute_lcr``
    (enterprise audit P0-8). The pure liquidity engine names no rate; it consumes
    what this returns via ``LiquidityParams`` and refuses to weight an asset whose
    rate is absent.
    """
    haircuts: dict[str, Decimal] = {}
    for level, code in HQLA_HAIRCUT_CODES.items():
        resolved = try_resolve(db, bank, code, as_of=as_of)
        if resolved is not None and resolved.value is not None:
            haircuts[level] = resolved.decimal
    cap2 = try_resolve(db, bank, HQLA_LEVEL2_CAP_CODE, as_of=as_of)
    cap2b = try_resolve(db, bank, HQLA_LEVEL2B_CAP_CODE, as_of=as_of)
    return HqlaParameters(
        haircut_pct=haircuts,
        level2_cap_pct=None if cap2 is None else cap2.value,
        level2b_cap_pct=None if cap2b is None else cap2b.value,
    )


def control_values(
    db: Session,
    bank: Bank,
    param_codes: Iterable[str],
    *,
    as_of: date | None = None,
) -> dict[str, Decimal | None]:
    """The governed value for each requested code, or ``None`` where unseeded.

    Never invents a value: a code with no approved, effective row for the bank's
    scope maps to ``None``, and :func:`clamp_overrides` then leaves the tenant
    value untouched rather than clamping against a fabricated floor.
    """
    resolved: dict[str, Decimal | None] = {}
    for code in param_codes:
        param = try_resolve(db, bank, code, as_of=as_of)
        resolved[code] = param.normalized_value if param is not None else None
    return resolved


def clamp_overrides(
    db: Session,
    bank: Bank,
    tenant_values: Mapping[str, Decimal],
    *,
    as_of: date | None = None,
) -> ClampReport:
    """Apply tighten-only enforcement to an ENTIRE tenant register in one call.

    THE generalised enforcement (QA audit 2026-08-20 P1-5). Every governed code
    present in ``tenant_values`` is clamped against its control-plane value; codes
    with no declared direction, and codes with no seeded governed value, pass
    through unchanged. Returns the effective values plus a record of each override
    that was weaker than the regulatory value.

    Loaders call this once with their whole threshold dict instead of clamping a
    single code by hand — the pattern that left 16 of the 25 governed codes
    unenforced and made ``regulatory_forecasting`` read ``car_min`` raw while
    ``regulatory_capital`` clamped it.

    A governed capital minimum the register carries NO row for
    (:data:`CONTROL_PLANE_REGISTER_CODES`) is supplied from the control plane
    (founder directive D-042), so a console change — up or down — reaches every
    tenant without a board row; one the control plane cannot supply stays absent
    and the calculation refuses (``missing_parameter``).
    """
    governed = {code: value for code, value in tenant_values.items() if code in governed_codes()}
    absent = _absent_register_codes(institution_types.institution_class(db, bank), tenant_values)
    if not governed and not absent:
        return ClampReport(values=dict(tenant_values), clamped=())
    controls = control_values(db, bank, (*governed, *absent), as_of=as_of)
    report = _clamp_values(governed, {code: controls[code] for code in governed})
    if report.clamped:
        logger.warning(
            "regulatory_parameter.tenant_override_clamped bank=%s org=%s codes=%s details=%s",
            bank.id,
            bank.organization_id,
            ",".join(report.codes_clamped()),
            [record.to_dict() for record in report.clamped],
        )
    merged = dict(tenant_values)
    merged.update(report.values)
    merged.update(_filled(absent, controls))
    return ClampReport(values=merged, clamped=report.clamped)


def _seed_row(spec: ParamSpec, actor: str) -> dict[str, object]:
    if (spec.value is None) == (spec.value_json is None):
        # A seed with both, or with neither, would leave the resolver a choice it
        # has no rule for. Caught here rather than at INSERT, where the failure
        # would be a NOT NULL violation naming no code.
        msg = (
            f"Seed {spec.param_code!r} ({spec.scope_type}={spec.scope_key!r}) must carry "
            "exactly one of value or value_json."
        )
        raise ValueError(msg)
    return {
        "scope_type": spec.scope_type,
        "scope_key": spec.scope_key,
        "param_code": spec.param_code,
        "jurisdiction_code": spec.jurisdiction_code,
        "value_numeric": None if spec.value is None else Decimal(spec.value),
        "value_json": None if spec.value_json is None else dict(spec.value_json),
        "unit": spec.unit,
        "source_citation": spec.source_citation,
        "confirmation_status": spec.confirmation_status,
        "effective_from": SEED_EFFECTIVE_FROM,
        "effective_to": None,
        "status": "approved",
        "proposed_by": actor,
        "approved_by": actor,
    }


def seed_rows(actor: str = SEED_ACTOR) -> list[dict[str, object]]:
    """The seed catalogue as insertable row dicts — shared by the migration and
    the hermetic-test seed so the two can never drift."""
    return [_seed_row(spec, actor) for spec in SEED_PARAMETERS]


def p2_seed_rows(actor: str = SEED_ACTOR) -> list[dict[str, object]]:
    """Just the ICAAP Pillar 2 rows, for a database that already has the rest.

    ``seed_rows`` is inserted under a "the table is empty" guard, which is right
    for a database built before these codes existed but wrong for one built
    after: the hermetic fixture would then skip them silently and every Pillar 2
    route would answer ``missing_parameter``. This is the row-by-row arm, the
    same shape ``app/services/icaap/parameters.seed_rows`` uses for P1's eight.
    """
    return [_seed_row(spec, actor) for spec in ICAAP_P2_SEED_PARAMETERS]


def filing_seed_rows(actor: str = SEED_ACTOR) -> list[dict[str, object]]:
    """Just the ICAAP filing rows, for a database that already has the rest.

    Same reason as :func:`p2_seed_rows`: ``seed_rows`` only fires on an empty
    table, so a hermetic database built after the rest of the catalogue existed
    would silently miss these and every ICAAP eligibility check would answer
    ``missing_parameter``.
    """
    return [_seed_row(spec, actor) for spec in ICAAP_FILING_SEED_PARAMETERS]


def p5_seed_rows(actor: str = SEED_ACTOR) -> list[dict[str, object]]:
    """Just the P5 rows (IRRBB Standardised Framework + granularity adjustment).

    Same reason as :func:`p2_seed_rows`: ``seed_rows`` only fires on an empty
    table, so a hermetic database built before these codes existed would miss
    them and every Standardised Framework run would answer
    ``missing_parameter``.
    """
    return [_seed_row(spec, actor) for spec in P5_SEED_PARAMETERS]
