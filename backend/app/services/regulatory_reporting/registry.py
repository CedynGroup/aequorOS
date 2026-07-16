"""Return-family registry (docs/regulatory_reporting.md §4).

Each :class:`ReturnDefinition` names one official return, the generator that
assembles its snapshot from existing computed state, the template it renders
into, and an honest fidelity grade:

- ``CONFIRMED`` — official appendix structure verified from the directive.
- ``PARTIAL`` — directive-described, official appendix not public.
- ``REPRESENTATIVE`` — professional reconstruction, awaiting the official form.

Deadline rules are parameterized callables (reporting_date -> due_date).
Citations, deadlines, and fidelity grades follow the BoG research dossiers
(docs/research/bog_returns_and_templates.md, read 2026-07-16, and
docs/research/bog_orass_submission_channels.md §4–5). Where the public record
runs out (marked UNKNOWN in the research) the entry says so explicitly and is
graded no higher than the record supports — nothing invented is passed off as
official.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

type FidelityGrade = Literal["CONFIRMED", "PARTIAL", "REPRESENTATIVE"]
type ReturnFamily = Literal["liquidity", "capital", "irrbb", "fx", "icaap_stress"]
type ReturnFrequency = Literal["monthly", "quarterly", "semiannual", "annual"]
type ChannelCode = Literal["orass_sandbox", "email", "manual"]

REGULATOR_BOG = "BOG"


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _add_months(anchor: date, months: int) -> tuple[int, int]:
    total = anchor.year * 12 + (anchor.month - 1) + months
    return total // 12, total % 12 + 1


def monthly_day(day: int) -> Callable[[date], date]:
    """Due on the given day of the calendar month after the reporting date."""

    def rule(reporting_date: date) -> date:
        year, month = _add_months(reporting_date, 1)
        return date(year, month, min(day, monthrange(year, month)[1]))

    return rule


def quarterly_days_after(days: int) -> Callable[[date], date]:
    """Due a fixed number of days after the quarter-end reporting date."""

    def rule(reporting_date: date) -> date:
        return reporting_date + timedelta(days=days)

    return rule


def annual_month_day(month: int, day: int) -> Callable[[date], date]:
    """Due on a fixed month/day in the calendar year after the reporting date."""

    def rule(reporting_date: date) -> date:
        year = reporting_date.year + 1
        return date(year, month, min(day, monthrange(year, month)[1]))

    return rule


@dataclass(frozen=True)
class ReturnDefinition:
    """One registry entry: an official return and how AequorOS produces it."""

    code: str
    family: ReturnFamily
    title: str
    directive_citation: str
    frequency: ReturnFrequency
    deadline_rule: Callable[[date], date]
    generator: str
    template_id: str
    fidelity: FidelityGrade
    default_channel: ChannelCode = "email"
    regulator: str = field(default=REGULATOR_BOG)


REGISTRY: dict[str, ReturnDefinition] = {
    definition.code: definition
    for definition in (
        ReturnDefinition(
            code="BSD3",
            family="liquidity",
            title="Liquidity Returns (LCR & NSFR)",
            directive_citation=(
                "Liquidity Monitoring Tools Directive (LMTD), 2026 (exposure draft, "
                "Feb 2026; effective 1 Jan 2027) read with the Liquidity Risk "
                "Management Directive, 2026. The LCR Directive, 2026 (banks only) is "
                "referenced by name in LMTD ¶4 but is not public; NSFR has no BoG "
                "directive — both are Basel-default pending BoG calibration."
            ),
            frequency="monthly",
            # CONFIRMED: LMTD Part II ¶7 — monthly reports "not later than 9
            # days after the last day of each month"; the LCR deadline is
            # assumed to match the liquidity pack until the LCR Directive is
            # published (research gap G1).
            deadline_rule=monthly_day(9),
            generator="liquidity",
            template_id="bog-bsd3-liquidity-v1",
            fidelity="PARTIAL",
            default_channel="orass_sandbox",
        ),
        ReturnDefinition(
            code="LMT",
            family="liquidity",
            title="Liquidity Monitoring Tools Return (LMTD Appendix Templates)",
            directive_citation=(
                "Liquidity Monitoring Tools Directive (LMTD), 2026 (exposure draft, "
                "Feb 2026; effective 1 Jan 2027) — Appendix Reporting Templates, "
                "Tables 1–11 published (CONFIRMED); monthly per Part II ¶7."
            ),
            frequency="monthly",
            # CONFIRMED: LMTD Part II ¶7 — within 9 days after month end.
            deadline_rule=monthly_day(9),
            # Reuses the liquidity generator's snapshot: only the LCR-by-
            # significant-currency subset (LMTD Table 11 taxonomy, aggregate
            # currency) is honestly fillable today, hence PARTIAL rather than
            # CONFIRMED despite the published appendix.
            # TODO(RR-6): extend the liquidity snapshot with contractual
            # maturity buckets and funding-concentration data so LMTD Tables
            # 1–10 can be exported verbatim; never fabricate bucket values.
            generator="liquidity",
            template_id="bog-lmt-liquidity-v1",
            fidelity="PARTIAL",
            default_channel="orass_sandbox",
        ),
        ReturnDefinition(
            code="BSD2",
            family="capital",
            title="Capital Adequacy Return (CAR & RWA)",
            directive_citation=(
                "Capital Requirements Directive (CRD), 2018 (final, in force since "
                "1 Jan 2019): CAR 10% + 3% conservation buffer = 13%, CET1 ≥ 6.5%, "
                "leverage ≥ 6%. The CRD contains no return form and the ORASS CAR "
                "return layout is not public (research §5.4, gap G3) — layout "
                "reconstructed from CRD Parts 1–4 and Stress Testing Guideline "
                "Appendix II Tables 2 & 5."
            ),
            frequency="monthly",
            # TODO(RR-3 follow-up): the CAR return deadline is UNKNOWN in the
            # public record (monthly cadence REPORTED only, research §2 row 7);
            # day 14 remains a placeholder until ORASS onboarding confirms it.
            deadline_rule=monthly_day(14),
            generator="capital",
            template_id="bog-bsd2-capital-v1",
            fidelity="REPRESENTATIVE",
            default_channel="orass_sandbox",
        ),
        ReturnDefinition(
            code="IRRBB-PILOT",
            family="irrbb",
            title="IRRBB Pilot Return (Repricing Gap, ΔEVE & ΔNII by Shock)",
            directive_citation=(
                "Guideline on Management and Measurement of IRRBB (exposure draft, "
                "Feb 2026; effective 1 Jan 2027; one-year pilot with quarterly "
                "reports from publication, ¶10). Appendix IV Table 8 ΔEVE/ΔNII grid "
                "is published; engine shocks are Basel ±200 bp pending alignment to "
                "the prescribed GHS ±450 bp standardised framework."
            ),
            frequency="quarterly",
            # CONFIRMED: quarterly reports "not later than nine (9) days after
            # the ensuing quarter" (IRRBB Guideline ¶11, ¶55).
            deadline_rule=quarterly_days_after(9),
            generator="irrbb",
            template_id="bog-irrbb-pilot-v1",
            fidelity="REPRESENTATIVE",
            default_channel="email",
        ),
        ReturnDefinition(
            code="FX-NOP",
            family="fx",
            title="Net Open Position Return (Monthly Summary)",
            directive_citation=(
                "Revised Directive on FX Net Open Position Limits, Notice "
                "BG/FMD/2026/07 (final, 10 Feb 2026): single-currency 0% to −10% of "
                "NOF, aggregate ≤ 20% NOF. The confirmed cadence is DAILY Bank "
                "Returns (DBK) by 10:00 a.m. the next business day via ORASS; "
                "AequorOS registers this monthly summary while the DBK 102/300/400/"
                "700 layouts remain unpublished (research §9, gap G5)."
            ),
            frequency="monthly",
            # The monthly summary is an AequorOS registration (the official
            # obligation is daily); day 10 is a placeholder aligned to the
            # 9-day monthly-return convention plus one day, pending ORASS
            # onboarding.
            deadline_rule=monthly_day(10),
            generator="fx",
            template_id="bog-fx-nop-v1",
            fidelity="REPRESENTATIVE",
            default_channel="email",
        ),
        ReturnDefinition(
            code="ICAAP-STRESS",
            family="icaap_stress",
            title="ICAAP Data Companion & Stress Summary",
            directive_citation=(
                "ICAAP Guideline (Feb 2026) ¶72 — annual submission no later than "
                "three months after year-end with Board resolutions; Stress Testing "
                "Guideline (Feb 2026) ¶67 — stress results within the ICAAP 'by end "
                "of March of the ensuing year', Appendix II Tables 1–6 published. "
                "Both effective 1 Jan 2027."
            ),
            frequency="annual",
            # CONFIRMED: end of March of the ensuing year (Stress Testing
            # Guideline ¶67; ICAAP Guideline ¶72/¶82).
            deadline_rule=annual_month_day(3, 31),
            generator="icaap_stress",
            template_id="bog-icaap-stress-v1",
            fidelity="REPRESENTATIVE",
            default_channel="manual",
        ),
    )
}


def get_definition(return_code: str) -> ReturnDefinition | None:
    return REGISTRY.get(return_code)
