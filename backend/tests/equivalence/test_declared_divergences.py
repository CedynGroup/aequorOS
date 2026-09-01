"""Declared divergences, and the ledger of what equivalence is actually proved.

Two jobs.

**1. Keep legitimately-different methodologies different.** The audit is blunt
about the failure mode: forcing BSD3's LCR and LMT Table 11's LCR to agree
would "fix" a correct engine. So the rule this file enforces is the inverse of
an equivalence test — no pair the authority registry marks
``equality_assertion_forbidden`` may appear in this suite's equated set, and
every alternate methodology must carry a documented divergence.

**2. Say out loud which equivalence claims are NOT proved.** The registry's
``reporting_mappings`` are claims: "this engine figure reaches that return".
:data:`LEDGER` classifies every one of them, and the ledger must match the
registry exactly. A new mapping added without a classification fails here,
which makes "we'll test it later" a build failure rather than a memory.

The ``UNPROVEN`` rows are not a to-do list dressed as a test. They are the
honest inventory the audit asked for: equivalence asserted in the architecture
and nowhere in the test suite.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

import pytest

from app.domain.authority.registry import REGISTRY
from app.domain.liquidity.engine import LiquidityFact, compute_lcr
from app.services.regulatory_liquidity import _REQUIRED_THRESHOLDS
from app.services.regulatory_reporting.le_generation import _LCR_INFLOW_CAP
from tests.domain.test_forecasting_engine import bog_liquidity_params

#: ``(metric_id, methodology_id, reporting mapping)`` — one claim.
Claim = tuple[str, str, str]


class Coverage(StrEnum):
    PROVEN_HERE = "proven_here"
    """An assertion in tests/equivalence/ pins this claim."""

    PROVEN_ELSEWHERE = "proven_elsewhere"
    """A pre-existing test pins it; the value is quoted in the note."""

    DECLARED_DIVERGENCE = "declared_divergence"
    """Not an equivalence at all. The registry declares a separate methodology,
    and asserting equality here would be a defect."""

    UNPROVEN = "unproven"
    """The architecture claims this figure flows through; no test checks it."""


_HERE = "test_report_run_parity.py"
_HERE_CAPITAL = f"{_HERE}::test_package_headline_figures_equal_the_bound_run"
_HERE_RWA = f"{_HERE}::test_capital_package_rwa_sections_tie_to_the_runs_rwa_components"
_FORMS = "tests/services/bog_forms"
_BSD13 = f"{_FORMS}/test_bsd13.py::test_bsd13_reports_the_fx_engines_nop_and_bogs"
_BSD13 += "_schedule_arithmetic"
_BSD5 = f"{_FORMS}/test_bsd5.py::test_bsd5a_reconciles_to_the_capital_run_and_bog_arithmetic"
#: WS-D's sibling gate: every BoG form CELL bound to an engine-backed resolver,
#: compared against the source run's persisted line items — the form-cell half
#: of the equivalence problem, where this file covers the package half.
_WSD_GATE = "tests/services/test_reporting_equivalence.py"

_CAPITAL = "crd_basel_capital_run"
_LIQUIDITY = "basel_bog_liquidity_run"
_FX = "bog_fx_nop_run"
_FORECAST = "bank_forecast_projection_run"
_IRRBB = "basel_irrbb_run"
_STRESS = "enterprise_stress_orchestrator"
_REVERSE = "reverse_stress_frontier"
_TABLE1 = "lmtd_table1_ratio"
_ECL = "ifrs9_pd_lgd_ead"
_GRADES = "bog_five_grade_classification"
_GRADES4 = "nbfi_four_grade_classification"
_NPL_MONTHLY_TEST = (
    "tests/services/test_regulatory_credit.py::"
    "test_npl_monthly_return_generates_from_the_sealed_run"
)


def _rows(
    coverage: Coverage, note: str, mapping: str, methodology: str, metrics: tuple[str, ...]
) -> dict[Claim, tuple[Coverage, str]]:
    return {(metric, methodology, mapping): (coverage, note) for metric in metrics}


LEDGER: dict[Claim, tuple[Coverage, str]] = {
    # -- proved by this suite ------------------------------------------------
    **_rows(
        Coverage.PROVEN_HERE, _HERE_CAPITAL, "CAR-RWA", _CAPITAL,
        ("car_pct", "cet1_ratio_pct", "tier1_ratio_pct", "leverage_ratio_pct",
         "total_capital_ghs", "total_rwa_ghs"),
    ),
    **_rows(
        Coverage.PROVEN_HERE, _HERE_RWA, "CAR-RWA", _CAPITAL,
        ("credit_rwa_ghs", "market_rwa_ghs", "operational_rwa_ghs"),
    ),
    **_rows(
        Coverage.PROVEN_HERE, _HERE_CAPITAL, "LCR-NSFR", _LIQUIDITY,
        ("hqla_total_ghs", "net_outflows_30d_ghs", "lcr_pct", "asf_total_ghs",
         "rsf_total_ghs", "nsfr_pct"),
    ),
    **_rows(
        Coverage.PROVEN_HERE, _HERE_CAPITAL, "FX-NOP", _FX,
        ("nop_ghs", "nop_pct_tier1", "var_99_1d_ghs"),
    ),
    # -- proved by a pre-existing test ---------------------------------------
    **_rows(
        Coverage.PROVEN_ELSEWHERE,
        f"{_BSD13} (C50 / C53), and every BSD13 cell bound to an engine-backed "
        f"resolver by {_WSD_GATE}",
        "BSD13", _FX, ("nop_ghs", "nop_pct_tier1"),
    ),
    # -- declared divergences: never equate ----------------------------------
    ("car_pct", "bog_bsd5a_form_ratio", "BSD5A!E70"): (
        Coverage.DECLARED_DIVERGENCE,
        f"BoG's printed E25/E69 ratio, not Basel CAR. Inequality pinned at {_BSD5}.",
    ),
    ("car_pct", "bog_bsd5a_form_ratio", "BSD5B!D74"): (
        Coverage.DECLARED_DIVERGENCE,
        "Consolidated twin of BSD5A!E70; same BoG add-on rules.",
    ),
    ("lcr_pct", "lmtd_table11_capped", "LMT!lcr_by_currency.lcr_pct"): (
        Coverage.DECLARED_DIVERGENCE,
        "Per-currency contractual LCR under a hard-coded LMTD cap; see "
        "test_the_two_lcr_methodologies_diverge_in_mechanics_not_in_having_a_cap.",
    ),
    ("net_own_funds_ghs", "act930_s29_nof_rwa", "LE-MONTHLY"): (
        Coverage.DECLARED_DIVERGENCE,
        "s.29 NOF. LE-MONTHLY currently fills NOF with the CAPITAL run's Tier 1 as a "
        "documented proxy (le_generation.generate_large_exposures), so the s.29 figure "
        "and the filed figure are not the same construct.",
    ),
    # -- claimed, not proved anywhere ----------------------------------------
    **_rows(
        Coverage.UNPROVEN,
        "The capital package prints these as section rows; no test ties them to the "
        "run's RegulatoryLineItem rows.",
        "CAR-RWA", _CAPITAL, ("cet1_capital", "tier1_capital", "tier2_capital"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "No test asserts the capital package carries the ECL engine's totals; on a book "
        "with no ECL assumption register the run emits no ecl_* metric at all.",
        "CAR-RWA", _ECL, ("ecl_total_ghs", "ecl_general_ghs", "ecl_specific_ghs"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        f"BSD5A binds capital-run line items; {_BSD5} checks E25/E67/E68 but not these.",
        "BSD5A", _CAPITAL, ("car_pct", "cet1_capital", "tier2_capital", "total_capital_ghs"),
    ),
    ("tier1_capital", _CAPITAL, "BSD5A!E10"): (
        Coverage.UNPROVEN,
        "No assertion binds E10 to the capital run's Tier 1 line item.",
    ),
    ("total_rwa_ghs", _CAPITAL, "BSD5A!E69"): (
        Coverage.UNPROVEN,
        "SUSPECT CLAIM: E69 is BoG's adjusted asset base under the printed NEW RISK "
        "WEIGHTS, and E70 = E25/E69 is pinned NOT equal to the engine CAR. A zero "
        "tolerance against the engine's total RWA cannot hold at the same time.",
    ),
    ("car_pct", _CAPITAL, "BSD5B"): (
        Coverage.UNPROVEN,
        "Consolidated form; no test binds it to the capital run.",
    ),
    **_rows(
        Coverage.UNPROVEN,
        "Loan-classification outputs reaching BoG forms are untested against the "
        "classification engine.",
        "BSD5A", _GRADES, ("npl_ratio", "total_provision_required_ghs"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "BSD8 sums the position attribute ecl_provision_ghs directly; nothing reconciles "
        "it to the classification/ECL engines.",
        "BSD8", _GRADES, ("npl_ratio", "total_provision_required_ghs"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "ASPIRATIONAL MAPPING: the registry says BSD13 carries these, but no BSD13 line "
        f"binds them — {_WSD_GATE}'s classification test enumerates the measures the form "
        "actually binds and these are not among them. Either the form should bind them or "
        "the mapping should be dropped.",
        "BSD13", _FX, ("single_ccy_max_pct", "stressed_var_ghs", "var_99_1d_ghs"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "DBK-DAILY renames the FX run's tier1_ghs to nof_ghs and copies NOP figures; no "
        "test compares any of it to the FX run.",
        "DBK-DAILY", _FX, ("nop_ghs", "nop_pct_tier1", "single_ccy_max_pct",
                           "stressed_var_ghs", "var_99_1d_ghs"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "FX-NOP prints these in sections, not totals; untested against the run.",
        "FX-NOP", _FX, ("single_ccy_max_pct", "stressed_var_ghs"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "The IRRBB package copies run.metrics wholesale; only the conditional GHS-450 "
        "rows have a test, and that one checks presence, not equality to the IRR engine.",
        "IRRBB-PILOT", _IRRBB,
        ("cumulative_12m_gap_ghs", "duration_gap", "ear_down_200_ghs", "ear_up_200_ghs",
         "eve_base_ghs", "nii_base_ghs", "worst_eve_change_pct_tier1"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "Registered 2026-08-22 (D-9 round 2): four IRRBB figures were already being "
        "sealed into filing runs with no authority behind them. They inherit the "
        "coverage state of their siblings above, not a better one - asset_duration and "
        "liability_duration are the two terms of the duration_gap identity and no test "
        "compares them to the engine, and the GHS-450 EaR pair has only the presence "
        "test the note above describes.",
        "IRRBB-PILOT", _IRRBB,
        ("asset_duration", "ear_down_450_ghs", "ear_up_450_ghs", "liability_duration"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "The ICAAP pack copies the forecast run's summary; no test compares the two.",
        "ICAAP-STRESS", _FORECAST,
        ("avg_roe_pct", "cumulative_net_income", "min_car_pct", "min_lcr_pct",
         "min_nsfr_pct", "year5_car_pct", "year5_lcr_pct", "year5_nsfr_pct"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "The stress pack's forecast columns are untested against the forecast run.",
        "STRESS-PACK", _FORECAST,
        ("avg_roe_pct", "cumulative_net_income", "min_car_pct", "min_lcr_pct",
         "min_nsfr_pct", "year5_car_pct", "year5_lcr_pct", "year5_nsfr_pct"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "test_stress_pack.py proves the pack is internally consistent, not that its "
        "figures equal the enterprise-stress run's metrics.",
        "STRESS-PACK", _STRESS,
        ("car_erosion_pp", "lcr_erosion_pp", "stressed_car_end_pct", "stressed_lcr_pct"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "Same for the reverse-stress frontier figures.",
        "STRESS-PACK", _REVERSE, ("capital_breach_multiplier", "liquidity_breach_multiplier"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "Appendix II is board-attested and copied verbatim; no equality test against the "
        "enterprise-stress run.",
        "ICAAP-STRESS-APPENDIX2", _STRESS,
        ("car_erosion_pp", "lcr_erosion_pp", "stressed_car_end_pct", "stressed_lcr_pct"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "Same for the reverse-stress frontier figures in Appendix II.",
        "ICAAP-STRESS-APPENDIX2", _REVERSE,
        ("capital_breach_multiplier", "liquidity_breach_multiplier"),
    ),
    # -- credit PR-6/PR-9: the NPL-MONTHLY levels table ----------------------
    **_rows(
        Coverage.PROVEN_ELSEWHERE,
        f"{_NPL_MONTHLY_TEST} asserts the levels rows (total_gross_loans_ghs, "
        "npl_stock_ghs, npl_ratio_pct) EQUAL the sealed baseline credit run's "
        "metrics on the 5-grade fixture book.",
        "NPL-MONTHLY", _GRADES,
        ("gross_loans_ghs", "npl_exposure_ghs", "npl_ratio_pct"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "The return prints provision_specific_ghs - a COMPONENT of provisions held - "
        "and derives coverage/net-NPL in-form from it; the generation test asserts "
        "presence of the coverage row, not equality to the run's provision figures.",
        "NPL-MONTHLY", _GRADES, ("provision_held_ghs", "provision_coverage_pct"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "The NPL-MONTHLY generation test runs over the bank-class fixture book; no "
        "SDI-book generation asserts these equal a sealed 4-grade credit run yet.",
        "NPL-MONTHLY", _GRADES4,
        ("gross_loans_ghs", "npl_exposure_ghs", "npl_ratio_pct",
         "provision_held_ghs", "provision_coverage_pct"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "LMT Table 1 ratios are computed from canonical positions inside the generator; "
        "there is no engine to compare them to and no test pins the arithmetic.",
        "LMT", _TABLE1,
        ("broad_to_short_term", "broad_to_total_assets", "broad_to_total_deposits",
         "broad_to_volatile", "narrow_to_short_term", "narrow_to_total_assets",
         "narrow_to_total_deposits", "narrow_to_volatile"),
    ),
    **_rows(
        Coverage.UNPROVEN,
        "The LMT pack's FX funding-gap figures are untested against the liquidity run.",
        "LMT", _LIQUIDITY, ("fx_funding_gap_ghs", "stressed_fx_funding_gap_ghs"),
    ),
}

EQUATED_HERE = frozenset(
    claim for claim, (coverage, _) in LEDGER.items() if coverage is Coverage.PROVEN_HERE
)


def _registry_claims() -> set[Claim]:
    return {
        (entry.metric_id, entry.methodology_id, mapping)
        for entry in REGISTRY.all()
        for mapping in entry.reporting_mappings
    }


# ---------------------------------------------------------------------------
# The ledger must describe the registry, exactly
# ---------------------------------------------------------------------------


def test_every_reporting_claim_the_registry_makes_is_classified() -> None:
    """The ratchet.

    Declaring that an engine figure reaches a return is a claim about the
    system. This forces every such claim into one of four honest states, so a
    new mapping cannot be added with no coverage and no note.
    """
    registry = _registry_claims()
    ledger = set(LEDGER)
    unclassified = sorted(registry - ledger)
    assert not unclassified, (
        "these registry reporting claims have no coverage classification; add them to "
        f"LEDGER with a Coverage state and a note: {unclassified}"
    )
    stale = sorted(ledger - registry)
    assert not stale, f"LEDGER classifies claims the registry no longer makes: {stale}"


def test_declared_divergences_are_exactly_the_registry_entries_without_a_tolerance() -> None:
    """A divergence and an undefined tolerance must not be confused.

    ``expected_tolerance is None`` is the registry's way of saying "no
    equivalence check is defined for this figure". Every one of those is
    classified here as a declared divergence, and nothing else is.
    """
    no_tolerance = {
        (entry.metric_id, entry.methodology_id, mapping)
        for entry in REGISTRY.all()
        for mapping in entry.reporting_mappings
        if entry.expected_tolerance is None
    }
    declared = {
        claim
        for claim, (coverage, _) in LEDGER.items()
        if coverage is Coverage.DECLARED_DIVERGENCE
    }
    assert declared == no_tolerance


def test_no_claim_the_registry_forbids_equating_is_proved_equal_here() -> None:
    """The audit's rule, executable.

    If this suite ever asserted equality on a pair the registry marks
    ``equality_assertion_forbidden``, it would be pressuring a correct engine to
    match a different regulator's arithmetic.
    """
    forbidden = {
        (entry.metric_id, entry.methodology_id, mapping)
        for entry in REGISTRY.all()
        for mapping in entry.reporting_mappings
        if entry.divergence is not None and entry.divergence.equality_assertion_forbidden
    }
    overlap = sorted(EQUATED_HERE & forbidden)
    assert not overlap, f"this suite asserts equality on forbidden pairs: {overlap}"


def test_every_alternate_methodology_documents_its_divergence() -> None:
    """Consumer-side integrity: an alternate with no divergence record is
    indistinguishable from an accidental duplicate."""
    for metric_id, entries in REGISTRY.multi_authority_metrics().items():
        for entry in entries:
            if entry.is_primary:
                continue
            assert entry.divergence is not None, f"{metric_id}/{entry.methodology_id}"
            assert entry.divergence.reason.strip(), f"{metric_id}/{entry.methodology_id}"


def test_the_forecast_path_equality_this_suite_proves_is_not_registry_forbidden() -> None:
    """``tests/equivalence/test_forecast_capital_parity.py`` asserts the forecast
    path's year-0 CAR/LCR/NSFR EQUAL the capital and liquidity runs. That is
    only legitimate because the registry classifies the forecast-path entries as
    an unresolved defect to be closed by aligning fact scopes — explicitly not
    as a protected alternate methodology.

    WS-A INTEGRATION: those entries still read
    ``resolution_status=UNRESOLVED_AUDIT_FINDING`` with a reason naming the
    excluded ``ecl_exposure`` scope. The scope is aligned and the equality is
    proved, so the registry text is now stale; updating it is WS-A's edit. This
    test pins the one property the equality depends on, which holds either way.
    """
    for metric_id in ("car_pct", "lcr_pct", "nsfr_pct"):
        path = next(
            entry
            for entry in REGISTRY.for_metric(metric_id)
            if entry.methodology_id == "bank_forecast_projection_path"
        )
        assert path.divergence is not None
        assert path.divergence.equality_assertion_forbidden is False, (
            f"{metric_id}: the forecast path is now proved equal to its run; a registry "
            "entry forbidding that assertion would contradict the code"
        )


# ---------------------------------------------------------------------------
# The LCR divergence, pinned by mechanics rather than by prose
# ---------------------------------------------------------------------------


def test_the_two_lcr_methodologies_diverge_in_mechanics_not_in_having_a_cap() -> None:
    """Both LCRs cap inflows. They differ in HOW, and that is what must be kept.

    The registry's prose says the LCR-NSFR return's LCR applies **no** inflow
    cap. The code says otherwise: ``lcr_inflow_cap_pct`` is a REQUIRED liquidity
    threshold and ``compute_lcr`` applies it unconditionally. The real, durable
    divergences are:

    * governed parameter vs literal — BSD3 reads ``lcr_inflow_cap_pct`` from the
      effective-dated register; Table 11 hard-codes ``Decimal("0.75")``;
    * aggregate vs per-currency — BSD3 caps total inflows against total
      outflows once; Table 11 takes a ``min`` inside every currency column.

    This test pins both halves so nobody "fixes" either engine on the strength
    of the wrong reason.
    """
    assert "lcr_inflow_cap_pct" in _REQUIRED_THRESHOLDS
    table11_cap = _LCR_INFLOW_CAP
    assert table11_cap == Decimal("0.75")


def test_the_bsd3_engine_caps_inflows_in_aggregate() -> None:
    """The numeric half of the pin: with gross inflows above the cap, the
    engine's net outflow is exactly ``outflows x (1 - cap)`` — one aggregate
    comparison, not a per-currency one."""
    params = bog_liquidity_params()
    assert params.inflow_cap_pct == Decimal("75")

    facts = (
        LiquidityFact("securities", "gog_bonds", Decimal("500"), hqla_level="L1"),
        LiquidityFact(
            "balance_sheet", "retail_deposits_stable", Decimal("2000"), side="liability"
        ),
        # 100% inflow rate on a balance far above 75% of the weighted outflow.
        LiquidityFact("lcr_inflow", "interbank_maturing", Decimal("900")),
    )
    result = compute_lcr(facts, params)
    assert result.inflow_cap_applied is True
    assert result.capped_inflows_total == result.outflows_total * Decimal("0.75")
    assert result.net_outflows_total == result.outflows_total * Decimal("0.25")


@pytest.mark.parametrize("metric_id", ["car_pct", "lcr_pct"])
def test_a_metric_with_several_authorities_names_them_all(metric_id: str) -> None:
    """Sanity: the metrics this file reasons about really are multi-authority,
    so these guards are not silently inspecting an empty set."""
    assert len(REGISTRY.for_metric(metric_id)) > 1
