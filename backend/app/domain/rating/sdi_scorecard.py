"""``AEQ-GH-SDI-FS`` — the candidate SDI financial-strength scorecard.

Implements §3 of ``docs/internal/AequorOS_SDI_Financial_Strength_Methodology.md``:
the component and ratio STRUCTURE for a Ghanaian savings-&-loans institution or
finance house, expressed in the same engine vocabulary the bank scorecard uses
(``domain/rating/engine.py``) so both run through one scoring implementation.

What this module is, and is not
-------------------------------
It is the **structure**: which components exist, which ratios feed each, which
direction is better, and the bounds each ratio is scored between.

It is **NOT a calibration**. The dossier is explicit — "No numerical anchors are
approved by this document. Candidate anchors are model parameters, not
regulatory facts, and must be proposed with evidence before approval" (§3) — so
every floor, cap and weight here is a CANDIDATE that exists to be challenged in
the §5 calibration pack, not a determination. Nothing in this module may release
a score on its own: the release gate lives in ``services/sdi_rating.py`` and
requires an APPROVED ``DeskMethodology`` version, which only Track-2
maker-checker through the operator Desk can create.

Why these components
--------------------
They are §2's evidence table, one component per row, and each maps to an
AequorOS authority that already computes it for an SDI:

===================== ==================================================
component             authority
===================== ==================================================
capital_resilience    ``sdi_capital`` (Act 930 s.29 CAR, NOF)
asset_quality         ``loan_classification`` (NBFI four-grade, NPL)
liquidity_resilience  ``sdi_views.get_sdi_liquidity_position`` (LMTD T1)
concentration         ``sdi_views.get_sdi_large_exposures``
earnings_capacity     ``CurrentFinancialFact`` (ROA, NIM, cost/income)
irrbb_sensitivity     ``regulatory_irr`` — OMITTED when no SDI IRR run
===================== ==================================================

Excluded by construction, never imputed (§2): FX NOP, CET1, Tier-1 leverage,
Basel LCR and NSFR. Their absence is not neutral evidence.

The bank scorecard's sovereign ceiling, support uplift and PD mapping are all
out of scope for v1 (§4 states 4 and 5 stay closed), so this methodology is
consumed for COMPONENT SCORES only.
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from app.domain.rating.engine import ComponentDefinition, RatioDefinition

#: The methodology code, matching ``implied_rating.SDI_METHODOLOGY_CODE`` and
#: the dossier. One string, so the register, the refusal and the scorecard
#: cannot drift apart.
METHODOLOGY_CODE = "AEQ-GH-SDI-FS"

#: Bumped whenever the STRUCTURE or the ANCHORS below change. It is not an
#: approval — an approved ``DeskMethodology`` row carries its own version, and
#: this is the candidate structure that row's parameters were built from.
CANDIDATE_STRUCTURE_VERSION = "sdi-fs-candidate/0.2-anchored"


#: How a floor/cap was arrived at. This is the field that makes the calibration
#: auditable: a reader can see at a glance which anchors rest on a published
#: instrument or statistic and which are still conventions awaiting the peer
#: panel the dossier's §5 requires.
#:
#: ``regulatory``
#:     The anchor IS a governed limit — the Act 930 s.29 CAR floor, the SDI
#:     large-exposure limit. Not a model parameter at all; it moves when the
#:     instrument moves.
#: ``sector``
#:     Anchored on a published Ghanaian sector statistic, cited in
#:     :data:`RATIO_ANCHORS`.
#: ``convention``
#:     An international practice anchor with NO Ghanaian published reference
#:     found. The weakest basis, and the one the calibration pack must close
#:     first.
ANCHOR_BASES: tuple[str, ...] = ("regulatory", "sector", "convention")


class RatioAnchor(NamedTuple):
    """Why a ratio's floor and cap are where they are."""

    basis: str
    citation: str


#: The calibration record, one entry per ratio in :data:`CANDIDATE_RATIOS`.
#:
#: A NOTE ON WHAT THIS CALIBRATION CAN AND CANNOT BE (2026-08-23). A scorecard
#: exists to separate strong institutions from weak ones, which is a
#: CROSS-SECTIONAL judgement. AequorOS holds one SDI book — ten years of it, but
#: one institution — so anchors set from that book's own percentiles would
#: measure its volatility, not sector dispersion, and would score it ~average at
#: every date by construction. The anchors below therefore come from PUBLISHED
#: references, and the institution panel is used only to test whether each
#: transform discriminates across observed values (``scripts/calibrate_sdi_scorecard.py``).
#:
#: One sector fact shapes every asset-quality and capital anchor: the SDI
#: sub-sector is not centred on "sound". Reporting in 2025-26 has the IMF
#: pressing BoG on SDI reform with close to half the sub-sector insolvent, and
#: the NBFI average NPL ratio at 20.1% (October 2018). An anchor set as if a
#: typical SDI were healthy would place the whole scale in the wrong region.
RATIO_ANCHORS: dict[str, RatioAnchor] = {
    "car_headroom_pp": RatioAnchor(
        "regulatory",
        "Floor 0pp = at the Act 930 s.29 CAR floor itself (a governed value, "
        "`car_min`, resolved per institution class — not hardcoded here). Cap 8pp "
        "sits an SDI at ~18% CAR, comparable with the banking sector's 17.5% at "
        "December 2025 (BoG, reported May 2026) against its own 13% minimum.",
    ),
    "paid_up_coverage_x": RatioAnchor(
        "regulatory",
        "Multiples of the licence-class minimum paid-up capital. Floor 1.0x = at "
        "the statutory minimum. NOT YET SOURCED — the paid-up floor check is "
        "unbuilt (docs/sdi.md), so this ratio never contributes today.",
    ),
    "reserve_fund_pct": RatioAnchor(
        "regulatory",
        "Statutory reserve fund as % of paid-up capital under the Act 930 s.34 "
        "progression. NOT YET SOURCED — unbuilt, so it never contributes today.",
    ),
    "npl_pct": RatioAnchor(
        "sector",
        "Floor 25% is above the 20.1% NBFI-sector average NPL ratio (BoG, October "
        "2018) and above the 21.8% banking-sector ratio at December 2024, so a book "
        "scores zero only when it is worse than an already-weak sector. Cap 5% "
        "matches the banking sector's NPL-excluding-loss-category ratio at December "
        "2025, the cleanest published Ghanaian book-quality figure.",
    ),
    "provision_coverage_pct": RatioAnchor(
        "regulatory",
        "Provisions held against provisions REQUIRED by the NBFI four-grade grid "
        "(governed rates). Cap 100% = fully provisioned to the regulatory "
        "requirement. NOT YET SOURCED — provisions held are not carried by the "
        "classification report, so this never contributes today.",
    ),
    "par30_pct": RatioAnchor(
        "convention",
        "Portfolio-at-risk beyond 30 days, the microfinance-sector convention "
        "(MicroRate / MFR institutional rating practice). No Ghanaian published "
        "PAR30 distribution was found, so the 25%/5% bounds are practice anchors "
        "awaiting the peer panel. NOT YET SOURCED in any case.",
    ),
    "lmtd_weakest_headroom_pp": RatioAnchor(
        "regulatory",
        "Headroom on the weakest BINDING LMTD Table 1 ratio, whose thresholds are "
        "governed values resolved per institution class. Floor 0pp = at the "
        "threshold. Cap 15pp is a convention pending a published SDI liquidity "
        "distribution.",
    ),
    "reserve_coverage_pct": RatioAnchor(
        "regulatory",
        "Primary and secondary reserve holdings against their governed required "
        "levels. Floor 80% = a fifth short of the requirement; cap 150% = half as "
        "much again as required.",
    ),
    "mismatch_90d_pct_assets": RatioAnchor(
        "convention",
        "Cumulative 90-day contractual mismatch as a share of assets. No Ghanaian "
        "published anchor found. NOT YET SOURCED — the ladder carries GHS, not a "
        "share of assets.",
    ),
    "largest_exposure_pct_nof": RatioAnchor(
        "regulatory",
        "Both bounds are governed limits, not model parameters. Floor 25% is the "
        "statutory single-obligor limit (`single_obligor_limit_pct`): at or above "
        "it the institution is in breach and scores zero. Cap 7.5% is half the 15% "
        "SDI large-exposure limit (`large_exposure_limit_pct`).",
    ),
    "top5_funding_pct": RatioAnchor(
        "convention",
        "Top-five depositor share. No Ghanaian published funding-concentration "
        "distribution was found; 50%/15% are practice anchors and this is the "
        "weakest-anchored ratio that currently CONTRIBUTES. Flagged for the "
        "calibration pack.",
    ),
    "roa_pct": RatioAnchor(
        "sector",
        "Cap 4% sits above the 2.6% banking-sector return on assets reported for "
        "2025, so a strong SDI can score at the top without the sector average "
        "being treated as excellent. Floor 0% = loss-making.",
    ),
    "net_interest_margin_pct": RatioAnchor(
        "convention",
        "SDIs run structurally wider margins than banks (small-ticket, high-rate "
        "lending), so the banking-sector NIM is not a valid anchor. 5%/18% are "
        "practice anchors awaiting the peer panel.",
    ),
    "cost_to_income_pct": RatioAnchor(
        "convention",
        "Cap 55% is near the 46.4% a large Ghanaian bank reported for 2024, "
        "adjusted upward because an SDI's branch-heavy, small-ticket model carries "
        "a structurally higher ratio. Floor 95% = nearly all income consumed by "
        "costs. Practice anchors; no SDI-sector distribution found.",
    ),
    "eve_sensitivity_pct_nof": RatioAnchor(
        "convention",
        "The s.29 analogue of the BCBS IRRBB outlier test (ΔEVE above 15% of Tier "
        "1), restated against Net Own Funds because an SDI has no Tier 1. Floor 20% "
        "is set beyond that threshold. NOT YET SOURCED — needs a complete SDI IRR "
        "run.",
    ),
    "repricing_gap_1y_pct": RatioAnchor(
        "convention",
        "Cumulative one-year repricing gap. Practice anchors. NOT YET SOURCED.",
    ),
}

#: The IRRBB component is DROPPED, not zeroed, when an SDI has no complete IRR
#: run — §3's "omitted rather than substituted when unavailable". Component
#: weights are renormalised over what remains, so a missing component never
#: silently scores as average.
OPTIONAL_COMPONENTS: frozenset[str] = frozenset({"irrbb_sensitivity"})


def _ratio(  # noqa: PLR0913 - one call per ratio; every field is part of the definition
    # NOTE ON BOUNDS. The engine requires ``floor < cap`` for every ratio and lets
    # ``direction`` do the inversion: for ``lower_is_better`` the score is
    # ``(cap - value) / (cap - floor)``, so the FLOOR is the best value and the
    # CAP the worst. Written the intuitive way round (floor = the bad number) the
    # engine refuses with "requires floor < cap" — which is how the seven
    # lower-is-better ratios here were caught before any score was produced.
    code: str,
    component: str,
    weight: str,
    direction: str,
    floor: str,
    cap: str,
) -> RatioDefinition:
    return RatioDefinition(
        code=code,
        component=component,
        weight=Decimal(weight),
        direction=direction,
        floor=Decimal(floor),
        cap=Decimal(cap),
    )


#: CANDIDATE component weights. Deliberately close to the CAMELS convention that
#: supervisors of smaller deposit-takers use (capital and asset quality carry the
#: most weight, earnings and rate sensitivity the least) rather than to the bank
#: scorecard's own weights, which are tuned to a Basel input set an SDI does not
#: have. This is a starting hypothesis for §5, not a finding.
CANDIDATE_COMPONENTS: tuple[ComponentDefinition, ...] = (
    ComponentDefinition(code="capital_resilience", weight=Decimal("0.25")),
    ComponentDefinition(code="asset_quality", weight=Decimal("0.25")),
    ComponentDefinition(code="liquidity_resilience", weight=Decimal("0.20")),
    ComponentDefinition(code="concentration", weight=Decimal("0.10")),
    ComponentDefinition(code="earnings_capacity", weight=Decimal("0.10")),
    ComponentDefinition(code="irrbb_sensitivity", weight=Decimal("0.10")),
)

#: CANDIDATE ratio definitions. ``floor``/``cap`` bound the monotonic transform:
#: at or beyond ``floor`` the ratio scores 0, at or beyond ``cap`` it scores 1.
#: Every bound below is a hypothesis to be challenged with the SDI panel in §5.
CANDIDATE_RATIOS: tuple[RatioDefinition, ...] = (
    # --- capital resilience ------------------------------------------------
    # Headroom over the Act 930 s.29 floor, in percentage points. 0pp = at the
    # floor, 10pp = comfortably above. The floor itself is governed data
    # (``car_min``), so only the HEADROOM is a model parameter.
    _ratio("car_headroom_pp", "capital_resilience", "0.60", "higher_is_better", "0", "8"),
    # Paid-up capital as a multiple of the licence-class minimum.
    _ratio("paid_up_coverage_x", "capital_resilience", "0.25", "higher_is_better", "1", "2.5"),
    # Statutory reserve fund as % of paid-up capital (Act 930 s.34 progression).
    _ratio("reserve_fund_pct", "capital_resilience", "0.15", "higher_is_better", "0", "100"),
    # --- asset quality -----------------------------------------------------
    # NBFI four-grade NPL share of gross loans.
    _ratio("npl_pct", "asset_quality", "0.45", "lower_is_better", "5", "25"),
    # Provisions held against required provisions.
    _ratio("provision_coverage_pct", "asset_quality", "0.35", "higher_is_better", "40", "100"),
    # Portfolio at risk beyond 30 days — the MFI-sector convention, which reads
    # a deteriorating book earlier than a 90-day NPL cut.
    _ratio("par30_pct", "asset_quality", "0.20", "lower_is_better", "5", "25"),
    # --- liquidity resilience ---------------------------------------------
    # Headroom on the WEAKEST binding LMTD Table 1 ratio, in percentage points.
    _ratio(
        "lmtd_weakest_headroom_pp", "liquidity_resilience", "0.45", "higher_is_better", "0", "15"
    ),
    # Primary + secondary reserve holdings against their required levels.
    _ratio("reserve_coverage_pct", "liquidity_resilience", "0.30", "higher_is_better", "80", "150"),
    # Cumulative 90-day contractual mismatch as % of total assets (negative gap
    # is the risk, so a less negative number is better).
    _ratio(
        "mismatch_90d_pct_assets", "liquidity_resilience", "0.25", "higher_is_better", "-25", "0"
    ),
    # --- concentration -----------------------------------------------------
    # Largest connected-group exposure as % of Net Own Funds, against the 15%
    # SDI large-exposure limit.
    _ratio("largest_exposure_pct_nof", "concentration", "0.60", "lower_is_better", "7.5", "25"),
    # Top-five depositor share of total deposits — funding-side concentration,
    # which for a deposit-taker is the faster-moving of the two.
    _ratio("top5_funding_pct", "concentration", "0.40", "lower_is_better", "15", "50"),
    # --- earnings capacity -------------------------------------------------
    _ratio("roa_pct", "earnings_capacity", "0.35", "higher_is_better", "0", "4"),
    _ratio("net_interest_margin_pct", "earnings_capacity", "0.30", "higher_is_better", "5", "18"),
    _ratio("cost_to_income_pct", "earnings_capacity", "0.35", "lower_is_better", "55", "95"),
    # --- interest-rate sensitivity ----------------------------------------
    # EVE sensitivity as % of Net Own Funds (the s.29 analogue of the bank
    # scorecard's ΔEVE/Tier 1), and the cumulative 1-year repricing gap.
    _ratio("eve_sensitivity_pct_nof", "irrbb_sensitivity", "0.60", "lower_is_better", "5", "20"),
    _ratio("repricing_gap_1y_pct", "irrbb_sensitivity", "0.40", "lower_is_better", "10", "30"),
)


def candidate_parameters() -> dict[str, object]:
    """The candidate structure as a ``DeskMethodology.parameters`` payload.

    This is what an operator proposes through the Desk register. It carries its
    own uncalibrated status in-band so a reader of the stored row cannot mistake
    a staged candidate for an approved model.
    """
    return {
        "parameter_status": (
            "CANDIDATE STRUCTURE ONLY — uncalibrated. Component weights and ratio "
            "floors/caps are hypotheses for the calibration pack required by "
            "AequorOS_SDI_Financial_Strength_Methodology.md §5, not determinations. "
            "No grade and no PD are produced under this methodology (§4 states 4 and "
            "5 remain closed); an approved version releases ADVISORY COMPONENT "
            "SCORES only."
        ),
        "structure_version": CANDIDATE_STRUCTURE_VERSION,
        "assessment_kind": "sdi_financial_strength",
        "optional_components": sorted(OPTIONAL_COMPONENTS),
        "excluded_inputs": [
            "fx_nop",
            "cet1",
            "tier1_leverage",
            "basel_lcr",
            "basel_nsfr",
        ],
        "components": [
            {"code": component.code, "weight": str(component.weight)}
            for component in CANDIDATE_COMPONENTS
        ],
        "anchor_summary": anchor_summary(),
        "ratios": [
            {
                "code": ratio.code,
                "component": ratio.component,
                "weight": str(ratio.weight),
                "direction": ratio.direction,
                "floor": str(ratio.floor),
                "cap": str(ratio.cap),
                # Every bound carries where it came from, so an approver reads a
                # citation rather than a number.
                "anchor_basis": anchor_for(ratio.code).basis,
                "anchor_citation": anchor_for(ratio.code).citation,
            }
            for ratio in CANDIDATE_RATIOS
        ],
    }


def anchor_for(code: str) -> RatioAnchor:
    """The calibration record for one ratio. Raises if a ratio has none.

    Every ratio MUST declare where its bounds came from — that is the whole
    point of :data:`RATIO_ANCHORS`, and an undeclared anchor is an uncited
    number in a model that decides how strong an institution looks.
    """
    try:
        return RATIO_ANCHORS[code]
    except KeyError as exc:  # pragma: no cover - the parity test prevents this
        msg = (
            f"Ratio {code!r} declares no anchor basis. Add a RATIO_ANCHORS entry "
            "naming the instrument, sector statistic or practice it rests on."
        )
        raise KeyError(msg) from exc


def anchor_summary() -> dict[str, int]:
    """How many ratios rest on each basis — the calibration's honesty headline."""
    counts = dict.fromkeys(ANCHOR_BASES, 0)
    for ratio in CANDIDATE_RATIOS:
        counts[anchor_for(ratio.code).basis] += 1
    return counts


# ---------------------------------------------------------------------------
# Score → internal grade (dossier §4 state 4)
# ---------------------------------------------------------------------------

#: CANDIDATE cutpoints: composite score -> internal grade, strongest first.
#:
#: The ladder STOPS at ``bb+`` and there are no investment-grade entries at all.
#: That is a deliberate scope statement, not a truncation: a Ghanaian SDI cannot
#: realistically be investment grade, the sovereign ceiling sits at Ghana's own
#: ``ccc`` in any case, and reporting through 2025-26 has the IMF pressing BoG on
#: a sub-sector with close to half of it insolvent. Carrying ``aaa``…``bbb-`` as
#: unreachable placeholders would imply the model can express a standing it
#: cannot evidence; leaving them out says so plainly.
#:
#: These are MODEL PARAMETERS, not regulatory facts, and they are the least
#: evidenced part of this methodology — the first thing the §5 calibration pack
#: must replace with a mapping benchmarked against real SDI financials and known
#: distress events.
CANDIDATE_GRADE_CUTPOINTS: dict[str, str] = {
    "bb+": "0.90",
    "bb": "0.84",
    "bb-": "0.78",
    "b+": "0.70",
    "b": "0.62",
    "b-": "0.54",
    "ccc+": "0.46",
    "ccc": "0.38",
    "ccc-": "0.30",
    "cc": "0.18",
    "c": "0",
}

#: The bands this scorecard can express, strongest first. A SUBSET of the bank
#: scorecard's 21-grade ladder, and a subset on purpose (see above). Ceiling
#: comparisons use the bank's full order so the two remain comparable.
GRADE_ORDER: tuple[str, ...] = tuple(CANDIDATE_GRADE_CUTPOINTS)


def grade_cutpoints() -> dict[str, Decimal]:
    return {grade: Decimal(value) for grade, value in CANDIDATE_GRADE_CUTPOINTS.items()}
