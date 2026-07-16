"""Return-family registry (docs/regulatory_reporting.md §4).

Each :class:`ReturnDefinition` names one official return, the generator that
assembles its snapshot from existing computed state, the template it renders
into, and an honest fidelity grade:

- ``CONFIRMED`` — official appendix structure verified from the directive.
- ``PARTIAL`` — directive-described, official appendix not public.
- ``REPRESENTATIVE`` — professional reconstruction, awaiting the official form.

Deadline rules are parameterized callables (reporting_date -> due_date).
Entries whose deadline or citation still awaits the BoG research companion
(docs/research/bog_returns_and_templates.md — the RR-3 wave) are marked with a
TODO and graded no higher than ``PARTIAL``.
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
                "BoG Liquidity Directive under the Capital Requirement framework "
                "(2026 directive; appendix reference TBC from research)"
            ),
            frequency="monthly",
            # TODO(RR-3): confirm from docs/research/ — the 2026 liquidity
            # directive puts the monthly LMT set at day 9 of the following
            # month; the LCR/NSFR deadline is assumed to match until confirmed.
            deadline_rule=monthly_day(9),
            generator="liquidity",
            template_id="bog-bsd3-liquidity-v1",
            fidelity="PARTIAL",
            default_channel="orass_sandbox",
        ),
        ReturnDefinition(
            code="BSD2",
            family="capital",
            title="Capital Adequacy Return (CAR & RWA)",
            directive_citation=(
                "BoG Capital Requirement Directive (Basel II/III CRD; "
                "appendix reference TBC from research)"
            ),
            frequency="monthly",
            # TODO(RR-3): confirm from docs/research/ — CRD prudential returns
            # are described as monthly/quarterly; day 14 is a placeholder.
            deadline_rule=monthly_day(14),
            generator="capital",
            template_id="bog-bsd2-capital-v1",
            fidelity="PARTIAL",
            default_channel="orass_sandbox",
        ),
        ReturnDefinition(
            code="IRRBB-PILOT",
            family="irrbb",
            title="IRRBB Pilot Return (Repricing Gap, ΔEVE & ΔNII by Shock)",
            directive_citation=(
                "BoG IRRBB pilot programme (no published appendix; "
                "professional reconstruction)"
            ),
            frequency="quarterly",
            # TODO(RR-3): confirm the pilot submission window from research.
            deadline_rule=quarterly_days_after(21),
            generator="irrbb",
            template_id="bog-irrbb-pilot-v1",
            fidelity="REPRESENTATIVE",
            default_channel="email",
        ),
        ReturnDefinition(
            code="FX-NOP",
            family="fx",
            title="Net Open Position Return",
            directive_citation=(
                "BoG Net Open Position notice under the Foreign Exchange Act "
                "(frequency/appendix TBC from research)"
            ),
            frequency="monthly",
            # TODO(RR-3): confirm frequency and deadline from research — NOP
            # reporting may be weekly; monthly day 10 is a placeholder.
            deadline_rule=monthly_day(10),
            generator="fx",
            template_id="bog-fx-nop-v1",
            fidelity="PARTIAL",
            default_channel="email",
        ),
        ReturnDefinition(
            code="ICAAP-STRESS",
            family="icaap_stress",
            title="ICAAP Data Companion & Stress Summary",
            directive_citation=(
                "BoG ICAAP guideline under the Capital Requirement Directive "
                "(submission window TBC from research)"
            ),
            frequency="annual",
            # TODO(RR-3): confirm the annual ICAAP submission date from
            # research; end of Q1 after the reporting year is a placeholder.
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
