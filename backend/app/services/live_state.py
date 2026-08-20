"""Current live-state resolution shared by Treasury/ALM module readers.

The fact period is an internal materialisation provenance link while the
Data Engine migrates from period-keyed facts. Live callers resolve this link
from their bank/module state; they never select a regulatory reporting period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, CurrentFinancialFact


@dataclass(frozen=True)
class CurrentFactSet:
    """A module-scoped slice of the current live fact materialisation."""

    facts: list[CurrentFinancialFact]
    source_as_of_date: date


def load_current_facts(
    db: Session, ctx: TenantContext, bank: Bank, fact_groups: tuple[str, ...]
) -> CurrentFactSet:
    """Load current facts for one bank and assert their shared business date."""
    facts = list(
        db.scalars(
            select(CurrentFinancialFact)
            .where(
                CurrentFinancialFact.organization_id == ctx.organization_id,
                CurrentFinancialFact.bank_id == bank.id,
                CurrentFinancialFact.fact_group.in_(fact_groups),
            )
            .order_by(CurrentFinancialFact.fact_group, CurrentFinancialFact.category)
        )
    )
    if not facts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "current_facts_missing",
                "message": "Current financial facts are not available yet.",
            },
        )
    source_dates = {fact.source_as_of_date for fact in facts}
    if len(source_dates) != 1:
        raise RuntimeError("Current financial facts must have one source as-of date.")
    return CurrentFactSet(facts=facts, source_as_of_date=source_dates.pop())


def current_snapshot(snapshot: dict[str, object], source_as_of_date: date) -> dict[str, object]:
    """Return a value-compatible current snapshot for hash comparison.

    Filing drift compares economics, not storage topology. Current facts are a
    separate table, but their hash must equal an official snapshot built from
    the same canonical book and parameters. Live-only provenance belongs on
    ``LiveMetric``, never inside the value-based calculation input hash.
    """
    _ = source_as_of_date
    return snapshot


def current_fact_period_or_409(
    db: Session, ctx: TenantContext, bank: Bank, module: str
) -> BankReportingPeriod:
    _ = module
    source_as_of_date = db.scalar(
        select(CurrentFinancialFact.source_as_of_date)
        .where(
            CurrentFinancialFact.organization_id == ctx.organization_id,
            CurrentFinancialFact.bank_id == bank.id,
        )
        .limit(1)
    )
    if source_as_of_date is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "current_facts_missing",
                "message": (
                    "Current financial facts are not available yet. Wait for the ingestion "
                    "pipeline refresh to complete."
                ),
            },
        )
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end == source_as_of_date,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "live_analysis_context_missing",
                "message": "The current facts have no compatible analysis context.",
            },
        )
    return period