"""Pre-P0 template text, frozen (ICAAP P0 fix round, decision D-021).

A package renders with the template text it was GENERATED under. ICAAP P0
(2026-09-19) rewrote the wording of six templates — neutral capital-requirement
headers, the Appendix II Pillar 2 grid and narrative, corrected IRRBB notes, the
capital-return citation, humanised reverse-stress columns — and a package that
predates that change must re-export exactly as it did, signed or not: the bytes
an institution filed, or will file from a dry run, are not rewritten under it.

This module holds those templates as they stood at ``ca294f04``, copied
verbatim (Appendix II and the SDI packet in full; the others as the one section
P0 changed). ``templates.template_for_snapshot`` selects them for a snapshot
that carries no ``metadata.template_revision`` stamp — every package generated
before P0 — and, for the two Appendix II templates, for any snapshot without the
``metadata.parameter_provenance`` the neutral headers rely on to state the
minima. ``tests/services/test_pre_p0_template_rerender.py`` pins the re-export of
real pre-P0 packages byte-for-byte against the baseline renderer.

NEVER EDIT THE TEXT BELOW. It is the historical artifact wording; a change here
changes documents that already exist. New wording belongs in ``templates.py``
under a new revision.
"""

from __future__ import annotations

from dataclasses import replace

from app.services.regulatory_reporting import templates as current
from app.services.regulatory_reporting.templates import (
    BOARD_ATTESTATION_LINES,
    ColumnSpec,
    ReturnTemplate,
    SectionLayout,
)

# Shared building blocks, unchanged by P0. Referenced (not copied) so the
# frozen text below reads exactly as it did at ca294f04; the byte-for-byte
# re-render test is the guard that they stay unchanged.
_A2_APPENDIX2_CITATION = current._A2_APPENDIX2_CITATION  # noqa: SLF001
_A2_CODE = current._A2_CODE  # noqa: SLF001
_A2_PERIOD = current._A2_PERIOD  # noqa: SLF001
_A2_POSITION_COLUMNS = current._A2_POSITION_COLUMNS  # noqa: SLF001
_a2_num = current._a2_num  # noqa: SLF001
_CODE = current._CODE  # noqa: SLF001
_ITEM = current._ITEM  # noqa: SLF001
_CRD_CITATION = current._CRD_CITATION  # noqa: SLF001
_IRRBB_CITATION = current._IRRBB_CITATION  # noqa: SLF001
_T_MINUS_1_NOTE = current._T_MINUS_1_NOTE  # noqa: SLF001
_STRESS_PACK_CITATION = current._STRESS_PACK_CITATION  # noqa: SLF001

# --- ICAAP-STRESS-APPENDIX2 and SDI-STRESS-ANNUAL, in full (ca294f04) ----------

LEGACY_APPENDIX2_TEMPLATE = ReturnTemplate(
    template_id="bog-icaap-stress-appendix2-v1",
    return_code="ICAAP-STRESS-APPENDIX2",
    title="ICAAP Stress Test — Appendix II Tables 1–6",
    fidelity="CONFIRMED",
    source_citation=_A2_APPENDIX2_CITATION,
    currency_unit="Amounts in GHS '000 (Appendix II reporting unit); ratios in %",
    attestation_lines=BOARD_ATTESTATION_LINES,
    sections=(
        SectionLayout(
            section_code="t1_summary_positions",
            layout_id="appendix2_table1_positions",
            sheet_title="Table 1 — Summary Results (Capital Positions)",
            columns=_A2_POSITION_COLUMNS,
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 1 Current + Pre-Adverse (Base Case) + "
                "Post-Adverse (Stress Case), 3-year horizon"
            ),
        ),
        SectionLayout(
            section_code="t1_impact_of_adverse",
            layout_id="appendix2_table1_impact",
            sheet_title="Table 1 — Impact of Adverse (Loss by CRD Class)",
            columns=(
                ColumnSpec("code", "Key", "text"),
                ColumnSpec("description", "CRD Exposure Class", "text"),
                _a2_num("value", "Adverse Loss"),
                ColumnSpec("year", "Projection Year", "text"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 1 'Impact of Adverse' losses by CRD "
                "exposure class (¶67(g) vulnerability granularity)"
            ),
        ),
        SectionLayout(
            section_code="t1_capital_required",
            layout_id="appendix2_table1_required",
            sheet_title="Table 1 — Capital Required to Meet Minima",
            columns=(
                ColumnSpec("code", "Key", "text"),
                _A2_PERIOD,
                _a2_num("value", "To Meet 13% CAR"),
                _a2_num("paid_up_shortfall", "To Meet Paid-up Min"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 1 capital required to meet the 13% CAR "
                "and paid-up minima (¶77)"
            ),
        ),
        SectionLayout(
            section_code="t1_management_actions",
            layout_id="appendix2_table1_management_actions",
            sheet_title="Table 1 — Management Actions (with-actions)",
            columns=(
                ColumnSpec("code", "Key", "text"),
                _A2_PERIOD,
                _a2_num("value", "Total Management Actions"),
                _a2_num("capital_raised_total", "Capital Raised"),
                _a2_num("revision_of_dividend_policy", "Dividend Revision"),
                _a2_num("change_in_business_strategy", "Strategy Change"),
                _a2_num("sale_of_assets", "Sale of Assets"),
                _a2_num("risk_reduction", "Risk Reduction"),
                _a2_num("other", "Other"),
                _a2_num("rwa_relief_total", "RWA Relief"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 1 'Management actions' block; results "
                "with and without management actions (¶67(f), ¶78–81)"
            ),
            optional=True,
        ),
        SectionLayout(
            section_code="t1_post_capitalisation",
            layout_id="appendix2_table1_post_capitalisation",
            sheet_title="Table 1 — Post-capitalisation (Stress + Actions)",
            columns=_A2_POSITION_COLUMNS,
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 1 'Post-capitalisation' block (¶67(f))"
            ),
            optional=True,
        ),
        SectionLayout(
            section_code="t1_residual",
            layout_id="appendix2_table1_residual",
            sheet_title="Table 1 — Residual Capital After Actions",
            columns=(
                ColumnSpec("code", "Key", "text"),
                _A2_PERIOD,
                _a2_num("value", "Residual Capital Required"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 1 residual capital required after "
                "management actions (¶77)"
            ),
            optional=True,
        ),
        SectionLayout(
            section_code="t2_capital_projection",
            layout_id="appendix2_table2",
            sheet_title="Table 2 — Regulatory Capital Projection",
            columns=(
                _A2_CODE,
                _A2_PERIOD,
                _a2_num("value", "Total Reg. Capital"),
                _a2_num("gross_cet1", "Gross CET1"),
                _a2_num("total_deductions", "CET1 Deductions"),
                _a2_num("cet1_after_deductions", "CET1 After Deductions"),
                _a2_num("at1_eligible", "AT1 Eligible"),
                _a2_num("tier2_eligible", "Tier 2 Eligible"),
                _a2_num("credit_risk_reserve", "Credit Risk Reserve"),
                _a2_num("total_rwa", "Total RWA"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 2 CET1/AT1/Tier2 build with the 1.5%/2% "
                "of RWA caps and deductions (Current + Base + Stress, 3-year)"
            ),
        ),
        SectionLayout(
            section_code="t3_profit_and_loss",
            layout_id="appendix2_table3",
            sheet_title="Table 3 — Movement in Profit & Loss",
            columns=(
                _A2_CODE,
                _A2_PERIOD,
                _a2_num("value", "Profit After Tax"),
                _a2_num("net_interest_income", "Net Interest Income"),
                _a2_num("fees_and_commissions", "Fees & Commissions"),
                _a2_num("operating_expenses", "Operating Expenses"),
                _a2_num("impairment_losses", "Impairment (incl. stress)"),
                _a2_num("profit_before_tax", "Profit Before Tax"),
                _a2_num("tax", "Tax"),
                _a2_num("distributions", "Distributions"),
                _a2_num("adjusted_retained_earnings_for_car", "Adj. Retained for CAR"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 3 NII → adjusted retained earnings for "
                "CAR, impairment includes the stress impact (Base + Stress, 3-year)"
            ),
        ),
        SectionLayout(
            section_code="t4_financial_position",
            layout_id="appendix2_table4",
            sheet_title="Table 4 — Statement of Financial Position",
            columns=(
                _A2_CODE,
                _A2_PERIOD,
                _a2_num("value", "Total Assets"),
                _a2_num("loans", "Loans"),
                _a2_num("cash_and_balances", "Cash & Balances"),
                _a2_num("short_term_investments", "Short-term Investments"),
                _a2_num("other_assets", "Other Assets"),
                _a2_num("total_liabilities", "Total Liabilities"),
                _a2_num("total_deposits", "Deposits (D/S/T/Other)"),
                _a2_num("borrowings", "Borrowings"),
                _a2_num("capital", "Capital"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 4 asset / capital / liability line "
                "taxonomy (Current + Base + Stress, 3-year)"
            ),
        ),
        SectionLayout(
            section_code="t5_rwa",
            layout_id="appendix2_table5",
            sheet_title="Table 5 — Evolution of RWA & Capital Requirements",
            columns=(
                _A2_CODE,
                _A2_PERIOD,
                _a2_num("value", "Total Pillar-1 RWA"),
                _a2_num("credit_rwa", "Credit RWA"),
                _a2_num("operational_rwa", "Operational RWA"),
                _a2_num("market_rwa", "Market RWA"),
                _a2_num("pillar1_requirement", "Pillar-1 Requirement (13%)"),
                _a2_num("pillar2_total", "Pillar-2 Add-ons"),
                _a2_num("total_capital_requirement", "Total Capital Requirement"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 5 RWA by Pillar-1 type + Pillar-2 "
                "add-ons; stressed Total Pillar-1 RWA equals Table 1's stressed RWA"
            ),
        ),
        SectionLayout(
            section_code="t6_risk_drivers",
            layout_id="appendix2_table6",
            sheet_title="Table 6 — Key Risk Drivers & Forecasting Assumptions",
            columns=(
                ColumnSpec("description", "Risk Driver", "text"),
                ColumnSpec("year_index", "Projection Year", "text"),
                _a2_num("base_value", "Base"),
                _a2_num("stress_value", "Stress"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                f"{_A2_APPENDIX2_CITATION} — Table 6 GoG yield, GDP, rates, unemployment, "
                "FX, inflation, GSE index, fiscal deficit (Base + Stress, per year)"
            ),
        ),
        SectionLayout(
            section_code="governance",
            layout_id="appendix2_governance",
            sheet_title="Governance — Board Sign-off & Attestation",
            columns=(
                ColumnSpec("code", "Key", "text"),
                ColumnSpec("description", "Item", "text"),
                ColumnSpec("value", "Detail", "text"),
            ),
            fidelity="CONFIRMED",
            source_citation=(
                "Stress Testing Guideline (Feb 2026) ¶20 / ¶57–63 — the Board attests it "
                "has reviewed and challenged the framework and results; the source run is "
                "Board-attested before this submission is generated"
            ),
        ),
    ),
    notes=(
        "The snapshot IS the BoG Appendix II Tables 1–6, carried verbatim from a "
        "Board-attested enterprise-stress run (docs/stress.md §3.4, §3.8). No figure is "
        "recomputed here; unmodelled directive lines stay blank rather than fabricated.",
        "Results are shown with and without management actions (¶67(f)); the "
        "with-actions blocks appear only when the run modelled an approved "
        "management-actions plan.",
    ),
)


_LEGACY_SDI_STRESS_SECTION_CODES = frozenset(
    {
        "t1_summary_positions",
        "t1_impact_of_adverse",
        "t1_capital_required",
        "t1_management_actions",
        "t1_post_capitalisation",
        "t1_residual",
        "t3_profit_and_loss",
        "t4_financial_position",
        "t5_rwa",
        "t6_risk_drivers",
        "governance",
    }
)

_LEGACY_SDI_A2_POSITION_COLUMNS: tuple[ColumnSpec, ...] = (
    _A2_CODE,
    _A2_PERIOD,
    _a2_num("value", "Net Own Funds"),
    _a2_num("total_rwa", "Risk-Weighted Assets"),
    ColumnSpec("car_pct", "CAR %", "pct"),
    _a2_num("paid_up", "Paid-up Capital"),
)


def _legacy_sdi_stress_section(section: SectionLayout) -> SectionLayout:
    if section.section_code == "t1_summary_positions":
        return replace(section, columns=_LEGACY_SDI_A2_POSITION_COLUMNS)
    if section.section_code == "t1_impact_of_adverse":
        return replace(
            section,
            sheet_title="Table 1 — Impact of Adverse (Loss by Exposure Class)",
            columns=(
                ColumnSpec("code", "Key", "text"),
                ColumnSpec("description", "Exposure Class", "text"),
                _a2_num("value", "Adverse Loss"),
                ColumnSpec("year", "Projection Year", "text"),
            ),
        )
    if section.section_code == "t1_capital_required":
        return replace(
            section,
            sheet_title="Table 1 — Capital Required to Meet SDI Minima",
            columns=(
                ColumnSpec("code", "Key", "text"),
                _A2_PERIOD,
                _a2_num("value", "To Meet Governed CAR"),
                _a2_num("paid_up_shortfall", "To Meet Paid-up Minimum"),
            ),
        )
    if section.section_code == "t5_rwa":
        return replace(
            section,
            columns=(
                _A2_CODE,
                _A2_PERIOD,
                _a2_num("value", "Risk-Weighted Assets"),
                _a2_num("credit_rwa", "Credit RWA"),
                _a2_num("operational_rwa", "Operational RWA (if governed)"),
                _a2_num("market_rwa", "Market RWA (if governed)"),
                _a2_num("pillar1_requirement", "Capital Requirement at Governed CAR"),
                _a2_num("total_capital_requirement", "Total Capital Requirement"),
            ),
        )
    return section


LEGACY_SDI_STRESS_TEMPLATE = replace(
    LEGACY_APPENDIX2_TEMPLATE,
    template_id="bog-sdi-stress-annual-v1",
    return_code="SDI-STRESS-ANNUAL",
    title="SDI Annual Stress Test Return (Proportionate Appendix II)",
    fidelity="PARTIAL",
    sections=tuple(
        _legacy_sdi_stress_section(section)
        for section in LEGACY_APPENDIX2_TEMPLATE.sections
        if section.section_code in _LEGACY_SDI_STRESS_SECTION_CODES
    ),
    notes=(
        "This proportionate SDI stress packet is sourced only from a Board-attested "
        "enterprise-stress run. It reuses the published Appendix II structures where "
        "the SDI engine produces an honest value.",
        "Table 2 (Basel CET1/AT1/Tier2 regulatory-capital projection) is excluded: the "
        "SDI uses the Act 930 s.29 capital regime and its enterprise-stress run does not "
        "produce a Basel tier build.",
        "The packet does not state stressed LMTD Table 1 ratios or a survival horizon: the "
        "SDI stress methodology for those liquidity measures is not established in the run "
        "and no value is fabricated.",
        "The regulator's SDI-specific ORASS form identity and transport contract are not "
        "public. This is a proportionate evidence packet, not a claimed portal layout.",
    ),
)


# --- the single sections P0 changed in the other four templates (ca294f04) -----

_LEGACY_CAPITAL_RATIOS_CITATION = (
    "Minimum ratios per CRD 2018 ¶71–91 summary table (CONFIRMED): CET1 6.5%, "
    "CAR 10%, CAR + conservation buffer 13%, leverage ratio 6% (Tier 1)"
)

_LEGACY_EVE_NOTES: tuple[str, ...] = (
    "Engine shocks are the six Basel scenarios at ±200 bp; the Guideline "
    "prescribes GHS ±450 bp parallel (short 500 / long 300, Appendix II–III "
    "Tables 5–6, CONFIRMED). Prescribed-shock alignment is pending; results "
    "are labelled by their actual scenario codes, not renamed.",
    "BoG ±450 bp parallel ΔEVE rows (eve_up_450_ghs / eve_down_450_ghs) "
    "render ONLY when the IRR run metrics actually carry them. The BoG "
    "GHS calibration is stored as effective-dated param_stress_shock rows "
    "(module 'irr', parallel_up_450 / parallel_down_450), but the engine "
    "computes the fixed Basel scenario set today — engine-side ±450 "
    "computation is a documented gap, and no ±450 value is ever fabricated.",
    _T_MINUS_1_NOTE,
)

_LEGACY_EAR_NOTES: tuple[str, ...] = (
    "Earnings at risk is computed at the Basel ±200 bp parallel shocks; "
    "BoG ±450 bp EaR rows (ear_up_450_ghs / ear_down_450_ghs) render only "
    "when a run's metrics carry them (engine-side computation pending — "
    "documented gap, never fabricated).",
)

_LEGACY_REVERSE_STRESS_FRONTIER_COLUMNS: tuple[ColumnSpec, ...] = (
    _CODE,
    _ITEM,
    ColumnSpec("value", "Severity Multiplier (x)", "number"),
    ColumnSpec("breached", "Breached", "text"),
    ColumnSpec("floor_pct", "Floor (%)", "pct"),
    ColumnSpec("ratio_at_breach_pct", "Ratio at Breach (%)", "pct"),
    ColumnSpec("scenario_code", "Scenario Scaled", "text"),
)


def _with_sections(template: ReturnTemplate, **changed: SectionLayout) -> ReturnTemplate:
    """``template`` with the named sections swapped for their frozen text."""
    return replace(
        template,
        sections=tuple(changed.get(s.section_code, s) for s in template.sections),
    )


def _section(template: ReturnTemplate, code: str) -> SectionLayout:
    return next(s for s in template.sections if s.section_code == code)


def _legacy_irrbb() -> ReturnTemplate:
    base = current.TEMPLATES["bog-irrbb-pilot-v1"]
    return _with_sections(
        base,
        eve_scenarios=replace(_section(base, "eve_scenarios"), notes=_LEGACY_EVE_NOTES),
        earnings_at_risk=replace(_section(base, "earnings_at_risk"), notes=_LEGACY_EAR_NOTES),
    )


LEGACY_IRRBB_TEMPLATE = _legacy_irrbb()

LEGACY_SDI_IRRBB_TEMPLATE = replace(
    current.TEMPLATES["bog-sdi-irrbb-quarterly-v1"],
    sections=tuple(
        current._sdi_irrbb_section(section)  # noqa: SLF001
        for section in LEGACY_IRRBB_TEMPLATE.sections
    ),
)

LEGACY_CAPITAL_TEMPLATE = _with_sections(
    current.TEMPLATES["bog-bsd2-capital-v1"],
    capital_ratios=replace(
        _section(current.TEMPLATES["bog-bsd2-capital-v1"], "capital_ratios"),
        source_citation=_LEGACY_CAPITAL_RATIOS_CITATION,
    ),
    at1=replace(
        _section(current.TEMPLATES["bog-bsd2-capital-v1"], "at1"),
        notes=("CRD caps AT1 at 1.5% of RWA (CRD 2018 ¶71–91, CONFIRMED).",),
    ),
    tier2=replace(
        _section(current.TEMPLATES["bog-bsd2-capital-v1"], "tier2"),
        notes=("CRD caps Tier 2 at 2% of RWA (CRD 2018 ¶71–91, CONFIRMED).",),
    ),
)

LEGACY_STRESS_PACK_TEMPLATE = _with_sections(
    current.TEMPLATES["aeq-stress-pack-v1"],
    reverse_stress_frontier=replace(
        _section(current.TEMPLATES["aeq-stress-pack-v1"], "reverse_stress_frontier"),
        columns=_LEGACY_REVERSE_STRESS_FRONTIER_COLUMNS,
    ),
)

#: template_id -> the template text packages generated before P0 render with.
LEGACY_TEMPLATES: dict[str, ReturnTemplate] = {
    template.template_id: template
    for template in (
        LEGACY_APPENDIX2_TEMPLATE,
        LEGACY_SDI_STRESS_TEMPLATE,
        LEGACY_IRRBB_TEMPLATE,
        LEGACY_SDI_IRRBB_TEMPLATE,
        LEGACY_CAPITAL_TEMPLATE,
        LEGACY_STRESS_PACK_TEMPLATE,
    )
}

__all__ = ["LEGACY_TEMPLATES"]
