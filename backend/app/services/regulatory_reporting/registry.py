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
type ReturnFamily = Literal[
    "liquidity",
    "capital",
    "irrbb",
    "fx",
    "icaap_stress",
    "corporate",
    "large_exposures",
    "dbk",
    "stress",
]
type ReturnFrequency = Literal["monthly", "quarterly", "semiannual", "annual", "daily"]
type ChannelCode = Literal["orass_sandbox", "email", "manual"]
type FilingFormat = Literal["xlsx", "csv", "pdf"]

REGULATOR_BOG = "BOG"

# Africa/Accra is UTC±00:00 with no DST, so a naive next-business-day rule is
# exact for BoG deadlines; the timezone label is carried for display only.
ACCRA_TZ = "Africa/Accra"


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


def daily_next_business_day(hour: int, minute: int, tz: str = ACCRA_TZ) -> Callable[[date], date]:
    """Due the next business day after the reporting date (T+1).

    Weekends roll forward (Fri reporting date -> Mon due date). The cut-off
    time-of-day (``hour``:``minute`` in ``tz``) is carried on the
    :class:`ReturnDefinition` as ``due_time`` — the due *date* stays a plain
    date for the model while the calendar surfaces the time separately.
    """

    _ = (hour, minute, tz)  # captured on the definition's due_time, not the date

    def rule(reporting_date: date) -> date:
        due = reporting_date + timedelta(days=1)
        while due.weekday() >= 5:  # noqa: PLR2004 — Sat=5, Sun=6
            due += timedelta(days=1)
        return due

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
    # Cut-off time-of-day on the due date, "HH:MM" (daily DBK filings close at
    # 10:00 the next business day); None for calendar-day-only deadlines.
    due_time: str | None = None
    # Event-driven returns (plan W5: the LRT corporate packs) have no periodic
    # reporting cycle: a pack exists because a corporate event happened. The
    # calendar skips them entirely — their nominal ``frequency``/
    # ``deadline_rule`` exist only to satisfy the package row shape and must
    # never mint month-end obligations.
    event_driven: bool = False
    # The rendered template format the regulator expects in the filing. Once a
    # return is certified the SIGNED PDF is what gets filed, and this format is
    # attached ALONGSIDE it rather than dropped: whether ORASS accepts a PDF as
    # the filing itself is unconfirmed (docs/attestation_esignature.md §8 C1),
    # and silently replacing a required template on a statutory filing is not a
    # bet worth taking on an unconfirmed fact. ``None`` where a return needs no
    # template beside the signed document. The default restates what the
    # submission path has always auto-exported — it is the status quo as data,
    # not a new claim about any return.
    filing_format: FilingFormat | None = "xlsx"


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
            # Dedicated "lmt" generator (plan W6.3, retires TODO(RR-6)): the
            # LCR-by-significant-currency subset (LMTD Table 11 taxonomy,
            # aggregate currency) from the liquidity run, plus canonical-data
            # monitoring tools — a contractual maturity-mismatch ladder
            # (condensed Table 2 bucket set), top-10 depositor funding
            # concentration (Table 5 asks Top 20/100), and HQLA-classified
            # available assets (Table 9 subset). PARTIAL rather than
            # CONFIRMED: the published grids carry more columns/rows than the
            # canonical data honestly fills — nothing missing is fabricated.
            generator="lmt",
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
            code="DBK-DAILY",
            family="dbk",
            title="Daily Bank Return (FX Net Open Position & Contingents)",
            directive_citation=(
                "Revised Directive on FX Net Open Position Limits, Notice "
                "BG/FMD/2026/07 (final, 10 Feb 2026): DAILY Bank Returns (DBK) via "
                "ORASS by 10:00 a.m. the next business day; single-currency 0% to "
                "−10% of NOF, aggregate ≤ 20% NOF. The DBK 102/300/400/700 forms "
                "are named but their full layouts are unpublished (research §9, "
                "gap G5) — this daily family reconstructs the NOP and contingents "
                "figures from the FX engine pending the official DBK templates."
            ),
            frequency="daily",
            # CONFIRMED cadence: next business day by 10:00 a.m. Africa/Accra.
            deadline_rule=daily_next_business_day(10, 0),
            due_time="10:00",
            generator="dbk",
            template_id="bog-dbk-daily-v1",
            fidelity="REPRESENTATIVE",
            default_channel="orass_sandbox",
        ),
        ReturnDefinition(
            code="LE-MONTHLY",
            family="large_exposures",
            title="Large Exposures Return (Templates 1/1a/2/3/4)",
            directive_citation=(
                "Large Exposures Directive (exposure draft Dec 2024), Part VI "
                "Templates 1/1a/2/3/4; the final directive (September 2025, "
                "effective 1 Jan 2027) confirms the five appendix templates and "
                "monthly reporting (¶57–58). Template STRUCTURE is CONFIRMED "
                "from the published appendix; the exposure derivation basis "
                "(canonical positions, Tier-1 Net-Own-Funds proxy, "
                "group_reference connected-counterparty grouping) is AequorOS's."
            ),
            frequency="monthly",
            # The directive requires monthly reporting but does not state the
            # day-count (draft Part VI; research bog_orass_submission_channels
            # §4.5 "exact day-count not stated in draft"). Day 9 follows the
            # observed BoG monthly-return convention (LMTD Part II ¶7) and is
            # overridable once ORASS onboarding confirms the LE deadline.
            deadline_rule=monthly_day(9),
            generator="large_exposures",
            template_id="bog-le-monthly-v1",
            fidelity="CONFIRMED",
            default_channel="orass_sandbox",
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
        # --- Template-gated returns (product.md §Phase 2 items 12/14) ------
        # Both are REAL periodic obligations banks must plan for, so they
        # live in the registry and the compliance calendar TODAY; but their
        # BoG form layouts are unpublished, and the standing order is to get
        # the form first, never infer it (the BSD-2 inference is not repeated).
        # Generation refuses with error_code "template_pending" until the
        # official layout lands; deadlines are stated conventions and remain
        # overridable through the existing deadline-override machinery.
        ReturnDefinition(
            code="BSD-MONTHLY",
            family="capital",
            title="Monthly BSD Prudential Pack (Balance Sheet + P&L)",
            directive_citation=(
                "BoG monthly prudential returns (BSD) — the bread-and-butter "
                "monthly filing carrying balance sheet, P&L, deposits and "
                "withdrawals in BoG's own form. The official layout is NOT "
                "published to us; generation is gated until the form is obtained "
                "(validation register: get the form first, never infer it)."
            ),
            frequency="monthly",
            # Day 9 follows the observed BoG monthly-return convention
            # (LMTD Part II ¶7 precedent, as for LE-MONTHLY); overridable.
            deadline_rule=monthly_day(9),
            generator="template_pending",
            template_id="bog-bsd-monthly-pending",
            fidelity="REPRESENTATIVE",
            default_channel="manual",
        ),
        ReturnDefinition(
            code="LAS-QUARTERLY",
            family="liquidity",
            title="Quarterly Liquidity Adequacy Statement (LAS)",
            directive_citation=(
                "LRMD 2026 ¶12 — the Board files a quarterly Liquidity Adequacy "
                "Statement to the regulator, ILAAP-supported and embedded in the "
                "annual ICAAP report; quarterly from 2027. No template is "
                "published; generation is gated until the form is obtained. The "
                "Board-level signing chain is an open practitioner question "
                "(lrmd_gap_analysis.md §9 Q12). The quarterly ILAAP snapshot "
                "(capital-plan workspace) is the prepared substance this filing "
                "will draw on."
            ),
            frequency="quarterly",
            # Cadence is directive-given; the day-count is NOT (first LAS
            # presumed after Q1 2027). 30 days is a stated presumption,
            # overridable via deadline overrides.
            deadline_rule=quarterly_days_after(30),
            generator="template_pending",
            template_id="bog-las-quarterly-pending",
            fidelity="REPRESENTATIVE",
            default_channel="manual",
        ),
        ReturnDefinition(
            code="STRESS-PACK",
            family="stress",
            title="Stress Test Output Report pack",
            directive_citation=(
                "Stress Testing Guideline (Feb 2026) ¶¶24–27 — stress-test results "
                "must be reported to Board and senior management with remedial "
                "actions; the standardized output-report structure (traffic lights, "
                "pro-forma capital, ratio evolution, attribution, recommended "
                "actions, reverse-stress frontier) is AequorOS's own artifact "
                "(product.md §Phase 2 item 6), not a published BoG template."
            ),
            # Event-driven: this is a Board/ALCO artifact generated on demand
            # after the stress engines run — no BoG filing deadline exists for
            # it (the regulator sees stress results inside ICAAP-STRESS). The
            # nominal frequency/deadline only satisfy the package row shape.
            frequency="quarterly",
            deadline_rule=quarterly_days_after(30),
            generator="stress_pack",
            template_id="aeq-stress-pack-v1",
            fidelity="REPRESENTATIVE",
            default_channel="manual",
            event_driven=True,
        ),
        # --- LRT corporate return packs (plan W5) --------------------------
        # Event-driven (event_driven=True): the calendar never expands them
        # into periodic obligations. frequency="annual" +
        # annual_month_day(12, 31) are nominal placeholders only — they exist
        # because packages carry a frequency column, not because these
        # returns have a reporting cycle. Structures come from the ORASS LRT
        # Portal User Guide v1.0 (Sept 2020, draft): form-set structure
        # documented; field-level layouts transcribed from the guide's
        # screenshots. Generators pre-fill from the W4 institution-profile
        # register only (no engine runs).
        ReturnDefinition(
            code="LRT-PROFILE",
            family="corporate",
            title="Corporate Profile Update pack",
            directive_citation=(
                "ORASS LRT Portal User Guide v1.0 (Sept 2020, draft) — Reporting "
                "Institution Profile form set: General Details, Business "
                "Activities, Stock Exchange membership, Ownership; event-driven "
                "corporate submission, no periodic deadline."
            ),
            frequency="annual",
            deadline_rule=annual_month_day(12, 31),
            generator="lrt_profile",
            template_id="bog-lrt-profile-v1",
            fidelity="CONFIRMED",
            default_channel="orass_sandbox",
            event_driven=True,
        ),
        ReturnDefinition(
            code="LRT-OUTLET",
            family="corporate",
            title="Outlet Opening / Relocation / Closure pack",
            directive_citation=(
                "ORASS LRT Portal User Guide v1.0 (Sept 2020, draft) — Contact "
                "Information form set: ACI (add) / UCI (update) with OO Opening "
                "of Outlets, CRO Closure and Relocation of Outlets, RD Required "
                "Documents; event-driven corporate submission."
            ),
            frequency="annual",
            deadline_rule=annual_month_day(12, 31),
            generator="lrt_outlet",
            template_id="bog-lrt-outlet-v1",
            fidelity="CONFIRMED",
            default_channel="orass_sandbox",
            event_driven=True,
        ),
        ReturnDefinition(
            code="LRT-PARTY",
            family="corporate",
            title="Related Party / Service Provider pack",
            directive_citation=(
                "ORASS LRT Portal User Guide v1.0 (Sept 2020, draft) — Related "
                "Parties/Service Providers form set: ARP (add) with ARD role "
                "details, ARCI contact information, CDD comprehensive due "
                "diligence, EDD enhanced due diligence (individuals), PNF "
                "personality notes (individuals), ASI shareholder information, "
                "ADI director information, AAI auditor information, RD required "
                "documents; event-driven corporate submission."
            ),
            frequency="annual",
            deadline_rule=annual_month_day(12, 31),
            generator="lrt_party",
            template_id="bog-lrt-party-v1",
            fidelity="CONFIRMED",
            default_channel="orass_sandbox",
            event_driven=True,
        ),
        ReturnDefinition(
            code="LRT-CAPITAL",
            family="corporate",
            title="Capital Injection pack",
            directive_citation=(
                "ORASS LRT Portal User Guide v1.0 (Sept 2020, draft) — Capital "
                "Injection form set: URP update related party, USI update "
                "shareholder information, RES Resolution, SOP Submission of "
                "Payments; event-driven corporate submission."
            ),
            frequency="annual",
            deadline_rule=annual_month_day(12, 31),
            generator="lrt_capital",
            template_id="bog-lrt-capital-v1",
            fidelity="CONFIRMED",
            default_channel="orass_sandbox",
            event_driven=True,
        ),
        ReturnDefinition(
            code="LRT-PRODUCT",
            family="corporate",
            title="Product / Service Approval pack",
            directive_citation=(
                "ORASS LRT Portal User Guide v1.0 (Sept 2020, draft) — Products/"
                "Services form set: AP (add) with DN Declaration, MOU, RES "
                "Resolution, SOP Submission of Payments; event-driven corporate "
                "submission."
            ),
            frequency="annual",
            deadline_rule=annual_month_day(12, 31),
            generator="lrt_product",
            template_id="bog-lrt-product-v1",
            fidelity="CONFIRMED",
            default_channel="orass_sandbox",
            event_driven=True,
        ),
    )
}


def get_definition(return_code: str) -> ReturnDefinition | None:
    return REGISTRY.get(return_code)
