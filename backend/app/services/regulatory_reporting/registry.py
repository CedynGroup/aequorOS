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
    # Official Bank of Ghana BSD prudential returns (BSD1 … BSD17) — see
    # docs/bog_returns/00_full_return_registry.md and bog_forms/.
    "bsd",
    # Specialised deposit-taking institution reports compiled from published
    # BoG directive appendices. These are never aliases for BSD forms.
    "sdi",
    # The credit / NPL family (Notice BG/GOV/SEC/2025/23; credit PR-6).
    "credit",
]
type ReturnFrequency = Literal["weekly", "monthly", "quarterly", "semiannual", "annual", "daily"]
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
    # The institution classes (``institution_class`` axis: 'bank' | 'sdi',
    # docs/sdi.md §1) this return applies to. The reporting calendar filters
    # obligations by the tenant's resolved class (SDI § de-hardcoding row at
    # docs/sdi.md §6.2 / calendar.py) so a savings-&-loans tenant sees only its
    # own returns. Every return registered so far is a bank/BoG return, hence
    # the default — the SDI/ORASS return pack (docs/sdi.md §Phase F, blocked on
    # BoG) sets ('sdi',) or ('bank', 'sdi') explicitly when its layouts land.
    institution_classes: tuple[str, ...] = ("bank",)
    # --- eligibility dimensions (forensic audit ARCH-8) ------------------
    # Evaluated by the SINGLE eligibility authority, ``eligibility.py``, which
    # both the reporting calendar and the package-mint site consume. A dimension
    # added here changes both surfaces at once, by construction — the audit
    # found the question answered twice, on different criteria.
    #
    # Jurisdictions the return applies in. Every return registered so far is a
    # Bank of Ghana return (the BoG research dossiers in this module's docstring
    # are the only source registered), so ``("GH",)`` states a fact rather than
    # standing in for an unmade decision. An empty tuple = unrestricted.
    jurisdictions: tuple[str, ...] = ("GH",)
    # The date this return comes into force, where the registry establishes one.
    # ``None`` means NO effective date is established here, and the eligibility
    # decision says so explicitly instead of treating silence as "in force
    # forever". Several citations above DO name a directive commencement date
    # ("effective 1 Jan 2027"); those are deliberately not encoded as generation
    # gates, because blocking generation on them would stop a bank preparing and
    # dry-running a return before its first live filing.
    effective_from: date | None = None
    # Declarative prerequisites a package needs before it can be generated
    # ("run:liquidity:baseline", "template:pending"). Reported on the
    # eligibility decision as metadata and ENFORCED where the answer can be
    # obtained honestly — the generators' 409s. Re-enforcing them here would
    # recreate the two-implementations defect this authority exists to end.
    prerequisites: tuple[str, ...] = ()
    # The ingested datasets the return draws on, for the same reporting purpose.
    required_data: tuple[str, ...] = ()
    # ``((metric_id, methodology_id), …)`` — WHICH declared methodology this
    # return means for a metric that legitimately has more than one (audit CF-1:
    # LCR-NSFR's aggregate-capped LCR vs LMT Table 11's per-currency 75% cap;
    # BOTH cap inflows — see the authority registry's divergence entry). Resolved
    # against the WS-A metric authority registry and written into the package's
    # provenance block, so a filed return states which number it reports. Only
    # methodologies the repository establishes are declared; nothing is guessed.
    # A tuple of pairs rather than a dict so the frozen definition stays
    # hashable and genuinely immutable.
    declared_methodologies: tuple[tuple[str, str], ...] = ()
    # A working workbook is an internal review artifact with recalculable
    # formulas. It is separately labelled and never filed or signed.
    supports_working_copy: bool = False


def _bog_definitions() -> list[ReturnDefinition]:
    """Every official BoG BSD form, built from the bog_forms catalogue.

    Deferred import: bog_forms.registry_entries constructs ReturnDefinition
    instances with THIS class (passed in) and never imports registry.py, so
    there is no cycle.
    """
    from app.services.regulatory_reporting.bog_forms.registry_entries import (  # noqa: PLC0415
        build_definitions,
    )

    return build_definitions(ReturnDefinition)


REGISTRY: dict[str, ReturnDefinition] = {
    definition.code: definition
    for definition in (
        # NOTE (2026-08-15, right-reporting reconciliation): this entry was
        # registered as "BSD3" before the official BoG templates were available.
        # Official BSD3 is the Large Exposures return (BSD3A/BSD3B, registered
        # below from bog_forms). The LCR/NSFR reconstruction keeps generating
        # under its honest code; stored return_code values were migrated
        # (alembic 202608150013). See docs/bog_returns/00_full_return_registry.md §3.
        ReturnDefinition(
            code="LCR-NSFR",
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
            prerequisites=("run:liquidity:baseline",),
            # Audit CF-1: ``lcr_pct`` legitimately exists twice. BOTH cap
            # inflows; the divergence is in HOW. THIS return applies ONE
            # AGGREGATE cap across the whole book, at the governed,
            # effective-dated ``lcr_inflow_cap_pct`` threshold (required by
            # ``regulatory_liquidity._REQUIRED_THRESHOLDS``, applied
            # unconditionally at ``domain/liquidity/engine.py``). The LMT return
            # below caps SEPARATELY PER CURRENCY at a hard-coded 75%
            # (``le_generation._LCR_INFLOW_CAP``). Both are correct under their
            # own authority — never assert them equal.
            # This comment used to say THIS return reports the "uncapped" LCR.
            # That was FALSE and it contradicted the authority registry's own
            # divergence entry, which warns that saying so invites an engineer
            # to add a cap that is already there — or to remove one believing it
            # was never intended. Do not restore it.
            # The id is the AUTHORITY registry's own
            # (``app.domain.authority.registry``); a name that resolves nowhere
            # makes the declaration a no-op. Audit 2026-08-22 D-10: this read
            # ``basel_bog_bsd3``, which is registered for no metric, so the
            # flagship LCR return shipped ``registry_status: not_registered``
            # and disclosed no divergence at all. Pinned by
            # ``test_every_declared_methodology_resolves_in_the_authority_registry``.
            declared_methodologies=(("lcr_pct", "basel_bog_liquidity_run"),),
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
            prerequisites=("run:liquidity:baseline",),
            # Audit CF-1, the other half: the Table 11 LCR applies the LMTD
            # 75% inflow cap and is therefore lower than, and NOT reconcilable
            # by equality with, the LCR-NSFR figure above.
            declared_methodologies=(("lcr_pct", "lmtd_table11_capped"),),
        ),
        ReturnDefinition(
            code="SDI-LMT-MONTHLY",
            family="sdi",
            title="SDI Liquidity Monitoring Tools Return (LMTD Tables 1-10)",
            directive_citation=(
                "Liquidity Monitoring Tools Directive (LMTD), 2026 — EXPOSURE DRAFT "
                "posted 19 February 2026, effective 1 January 2027 — Part II ¶7 and "
                "Appendix Tables 1-10: applies to Savings and Loans and Finance "
                "Houses. ¶9 would make the SDI Table 1 ratios binding compliance "
                "ratios ON COMMENCEMENT; the directive is not in force, so they bind "
                "nothing today. Table 11 is excluded because it is banks-only."
            ),
            frequency="monthly",
            deadline_rule=monthly_day(9),
            generator="sdi_lmt",
            template_id="bog-sdi-lmt-monthly-v1",
            # The public appendix structures are confirmed. BoG has not
            # published an institution-specific SDI ORASS form catalogue, so
            # this is an evidence-backed packet rather than a claimed portal ID.
            fidelity="PARTIAL",
            default_channel="orass_sandbox",
            institution_classes=("sdi",),
            jurisdictions=("GH",),
            required_data=("canonical_positions",),
            supports_working_copy=True,
            # The Table 1 ratios this return files are owned by the LMTD
            # methodology, which is registered for BOTH institution classes.
            declared_methodologies=(
                ("narrow_to_volatile", "lmtd_table1_ratio"),
                ("broad_to_volatile", "lmtd_table1_ratio"),
                ("narrow_to_short_term", "lmtd_table1_ratio"),
                ("broad_to_short_term", "lmtd_table1_ratio"),
                ("narrow_to_total_deposits", "lmtd_table1_ratio"),
                ("broad_to_total_deposits", "lmtd_table1_ratio"),
            ),
        ),
        # NOTE (2026-08-15, right-reporting reconciliation): registered as
        # "BSD2" before the official templates were available. Official BSD2 is
        # the Statement of Assets and Liabilities; the Capital Adequacy Return
        # is BSD5A (both registered below from bog_forms, BSD5A fed by this same
        # capital engine). Stored return_code values migrated (202608150013).
        ReturnDefinition(
            code="CAR-RWA",
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
            prerequisites=("run:capital:baseline",),
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
            code="SDI-IRRBB-QUARTERLY",
            family="sdi",
            title="SDI IRRBB Quarterly Pilot Return (Appendix IV)",
            directive_citation=(
                "Guideline on Management and Measurement of IRRBB, 2026 — EXPOSURE "
                "DRAFT, February 2026, NOT IN FORCE — ¶¶3, 10-11 and Appendix IV: "
                "Savings and Loans and Finance "
                "Houses pilot quarterly reporting in the prescribed templates. This packet "
                "uses the actual computed GHS shocks and omits the bank Tier 1 outlier "
                "verdict because the SDI capital regime is Act 930 s.29."
            ),
            frequency="quarterly",
            deadline_rule=quarterly_days_after(9),
            generator="sdi_irrbb",
            template_id="bog-sdi-irrbb-quarterly-v1",
            fidelity="PARTIAL",
            default_channel="manual",
            institution_classes=("sdi",),
            jurisdictions=("GH",),
            prerequisites=("run:irr:baseline",),
            required_data=("bank_financial_facts", "capital_structure"),
            supports_working_copy=True,
            # DELIBERATELY EMPTY, and this comment is the reason the completeness
            # gate requires. The only registered IRRBB methodology is
            # ``basel_irrbb_run``, which is institution_class=bank / regime=crd.
            # Declaring it here would hand a CRD authority to an s.29 institution
            # - precisely the regime inheritance the D-9 gate was built to stop
            # (see statutory_reserve_fund_ghs, narrowed 2026-08-22). No s.29 IRRBB
            # authority exists because BoG has published none: the Guideline is an
            # exposure draft. Until one does, this return files its shocks with no
            # claimed regulatory basis, which the citation states on its face.
            declared_methodologies=(),
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
            code="SDI-LE-MONTHLY",
            family="sdi",
            title="SDI Large Exposures Return (Templates 1, 1a, 2, 3 and 4)",
            directive_citation=(
                "Large Exposures Directive, September 2025 — FINAL but NOT YET IN "
                "FORCE, effective 1 January 2027 (docs/bog_parameter_sources.md: "
                "\"All VERIFIED; none in force yet\") — ¶¶11-12 and ¶¶57-58, Appendix "
                "Templates 1, 1a, 2, 3 and 4: applies to Savings and Loans and Finance "
                "Houses; monthly reporting; 15% of Net Own Funds limit on commencement."
            ),
            frequency="monthly",
            # The Directive establishes monthly reporting but does not publish
            # a day count. The calendar stays provisional until the institution's
            # ORASS obligation confirms it.
            deadline_rule=monthly_day(9),
            generator="sdi_large_exposures",
            template_id="bog-sdi-le-monthly-v1",
            fidelity="PARTIAL",
            default_channel="orass_sandbox",
            institution_classes=("sdi",),
            jurisdictions=("GH",),
            required_data=("canonical_positions", "capital_structure"),
            supports_working_copy=True,
            # Exposures are expressed as a percentage of Net Own Funds, and NOF
            # for an SDI comes from the governed Act 930 s.29 calculation - the
            # s29 authority, never the bank CRD one.
            declared_methodologies=(("net_own_funds_ghs", "act930_s29_nof_rwa"),),
        ),
        ReturnDefinition(
            code="NPL-MONTHLY",
            family="credit",
            title="Monthly Non-Performing Loans Report (Notice 2025/23 Appendix II)",
            directive_citation=(
                "BoG Notice BG/GOV/SEC/2025/23 (Regulatory Measures to Reduce "
                "Non-Performing Loans in Banks, SDIs and NBFIs, Aug 2025) — Appendix "
                "II Monthly Regulatory Reporting of NPL: NPL level and flows, credit "
                "migration over the month, write-offs (wilful / non-wilful) with "
                "recoveries, cash recovery from NPLs, and restructuring activity. "
                "The prudential NPL limit (10%) binds from end-December 2026; the "
                "notice is IN FORCE and applies to banks and SDIs alike."
            ),
            frequency="monthly",
            # The notice mandates monthly reporting but publishes no day count.
            # Provisional 9-day rule (the LE precedent) until the institution's
            # ORASS obligation confirms it.
            deadline_rule=monthly_day(9),
            generator="npl_monthly",
            template_id="aeq-npl-monthly-v1",
            fidelity="PARTIAL",
            institution_classes=("bank", "sdi"),
            jurisdictions=("GH",),
            prerequisites=("run:credit:baseline",),
            required_data=("canonical_positions", "canonical_loan_events"),
            supports_working_copy=True,
            # One figure, two legal grids: the bank 5-grade under CRD and the
            # NBFI 4-grade under Act 930 s.29 own npl_ratio_pct for their own
            # class. declared_methodologies is a per-metric MAP and cannot
            # express a class-conditional declaration (two pairs for one
            # metric_id collapse to the last), so nothing is declared here —
            # the sealed credit run's provenance and the WS-A registry carry
            # the class-scoped ownership instead of a mis-declared single pick.
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
        ReturnDefinition(
            code="ICAAP-STRESS-APPENDIX2",
            family="icaap_stress",
            title="ICAAP Stress Test — Appendix II Tables 1–6",
            directive_citation=(
                "Stress Testing Guideline (Feb 2026) ¶67 — RFIs submit annual stress-test "
                "results to BoG as part of the ICAAP in the Appendix II formats by end of "
                "March of the ensuing year; ¶68 / Part IV — pre/post-stress regulatory "
                "capital projected ≥3 years; Appendix II Tables 1–6 (Summary Results, "
                "Regulatory Capital, P&L, Statement of Financial Position, Evolution of "
                "RWA & Capital Requirements, Key Risk Drivers). Results reported with and "
                "without management actions (¶67(f)), at the currency / business-line / "
                "sector / borrower-group granularity of ¶67(g). Board-attested per ¶20. "
                "Effective 1 Jan 2027."
            ),
            frequency="annual",
            # CONFIRMED: end of March of the ensuing year (Stress Testing
            # Guideline ¶67), submitted within the ICAAP.
            deadline_rule=annual_month_day(3, 31),
            generator="icaap_stress_appendix2",
            template_id="bog-icaap-stress-appendix2-v1",
            # The Appendix II structure is published in the directive (CONFIRMED);
            # the snapshot IS those tables, sourced from a Board-attested enterprise-
            # stress run (docs/stress.md §1.8, §3.4, §3.8).
            fidelity="CONFIRMED",
            default_channel="manual",
        ),
        ReturnDefinition(
            code="SDI-STRESS-ANNUAL",
            family="sdi",
            title="SDI Annual Stress Test Return (Proportionate Appendix II)",
            directive_citation=(
                "Stress Testing Guideline, 2026 — EXPOSURE DRAFT, February 2026, NOT "
                "IN FORCE — ¶3 and ¶67: applies proportionately to Savings and Loans "
                "and Finance Houses. "
                "This packet renders the SDI-applicable Appendix II evidence from a "
                "Board-attested enterprise-stress run; the Basel CET1/AT1/Tier2 "
                "Table 2 is excluded because the SDI uses the Act 930 s.29 capital regime."
            ),
            frequency="annual",
            deadline_rule=annual_month_day(3, 31),
            generator="sdi_stress_annual",
            template_id="bog-sdi-stress-annual-v1",
            # Appendix II is public, but BoG has not published a separate SDI
            # ORASS layout or submission identity. Do not claim otherwise.
            fidelity="PARTIAL",
            default_channel="manual",
            institution_classes=("sdi",),
            jurisdictions=("GH",),
            prerequisites=("run:enterprise_stress:board_attested",),
            required_data=("canonical_positions", "capital_structure", "macro_scenario"),
            supports_working_copy=True,
            # The packet renders a Board-attested enterprise-stress run; that
            # engine's authority is class-neutral (advisory_internal).
            declared_methodologies=(
                ("stressed_car_end_pct", "enterprise_stress_orchestrator"),
                ("car_erosion_pp", "enterprise_stress_orchestrator"),
            ),
        ),
        # --- Template-gated returns (product.md §Phase 2 items 12/14) ------
        # The former "BSD-MONTHLY" placeholder (Balance Sheet + P&L pack, gated
        # on the official form) is RETIRED as of 2026-08-15: the official
        # workbooks landed and BSD2 (Statement of Assets & Liabilities) + BSD7A
        # (Current Year Results) are registered below from bog_forms. LAS stays
        # gated: its official layout is still unpublished.
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
        # --- Official Bank of Ghana BSD prudential returns (BSD1 … BSD17) -----
        # Every template under docs/reporting/, registered from the bog_forms
        # catalogue so this list can never drift from it: frequency + time
        # limit from the Guide's List of Prudential Returns, basis per General
        # Notes §1 (subsidiaries consolidated only on BSD7B/BSD9 + the GROUP
        # variants BSD3B/BSD5B), generator "bog_form" (template-faithful export
        # from the committed official layouts). Registry doc:
        # docs/bog_returns/00_full_return_registry.md.
        *_bog_definitions(),
    )
}


def get_definition(return_code: str) -> ReturnDefinition | None:
    return REGISTRY.get(return_code)
