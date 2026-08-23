"""Materialise the LIVE fact plane for the canonical test book.

Every Treasury/ALM module page (capital, liquidity, IRRBB, FX, FTP, the implied
rating) reads ``current_financial_facts`` — the live plane — not the period-keyed
``bank_financial_facts`` the canonical test book writes. In the product that
table is written by the worker's ``pipeline_refresh`` job
(``pipeline.recompute_live`` → ``fact_derivation.derive_current_facts``) from
accepted canonical position snapshots.

The e2e stack runs **no worker** (``RUN_INPROCESS_WORKER=0``, and
``POST /banks/{id}/refresh`` only enqueues a job), so nothing ever writes it
there. Until 2026-08-22 that meant the entire live half of the dashboard opened
on the "no computed data yet" envelope — ``/basel``, ``/liquidity``, ``/ftp/*``,
``/irr/*``, ``/fx/*`` all rendered an error card — and because the e2e harness
had been unbootable since the institution-type registry landed, nobody saw it.

The mirror invents nothing. ``current_financial_facts`` and
``bank_financial_facts`` carry the same columns (amount, currency, risk weight,
HQLA level, CCF, rate, income year, capital tier, deduction flag, attributes);
the live plane is therefore the LATEST period's fact set stamped with that
period's end date as the source business date — exactly what a refresh would
have derived from the positions those facts were built from. Then
``pipeline.recompute_modules`` — the product's own cheap-tier recompute — turns
them into ``live_metrics``/``live_findings``, so the Command Center pulse and
every module cockpit read real engine output.

Fixture-only: nothing in ``app/`` imports this, and the live plane in a
deployment is only ever written by derivation from canonical positions.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankFinancialFact, BankReportingPeriod, CurrentFinancialFact
from app.services.pipeline import recompute_modules

#: Copied verbatim from the period fact onto its live twin.
_MIRRORED_COLUMNS = (
    "fact_group",
    "category",
    "amount",
    "currency",
    "risk_weight_code",
    "hqla_level",
    "ccf_pct",
    "rate_pct",
    "income_year",
    "capital_tier",
    "is_deduction",
    "attributes",
)


def materialize_live_plane(
    session: Session, *, organization_id: str, bank_id: str
) -> tuple[int, list[str], dict[str, str]]:
    """Mirror the latest period's facts into the live plane and recompute modules.

    Returns ``(facts_created, modules_ok, modules_failed)``.
    """
    period = session.scalars(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == organization_id,
            BankReportingPeriod.bank_id == bank_id,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    ).one()

    facts = list(
        session.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.organization_id == organization_id,
                BankFinancialFact.bank_id == bank_id,
                BankFinancialFact.reporting_period_id == period.id,
            )
        )
    )
    session.execute(
        delete(CurrentFinancialFact).where(
            CurrentFinancialFact.organization_id == organization_id,
            CurrentFinancialFact.bank_id == bank_id,
        )
    )
    # One live row per (fact_group, category) — the table's uniqueness contract,
    # which the period plane already satisfies. If it ever stops satisfying it,
    # say so: silently summing amounts would keep one row's risk weight / HQLA
    # level for a merged total, which is a fabricated fact.
    mirrored: dict[tuple[str, str], CurrentFinancialFact] = {}
    for fact in facts:
        key = (fact.fact_group, fact.category)
        if key in mirrored:
            msg = (
                f"Period {period.label} carries more than one fact for {key!r}; the live "
                "plane holds one row per (fact_group, category) and this fixture will not "
                "invent a merged one."
            )
            raise ValueError(msg)
        mirrored[key] = CurrentFinancialFact(
            organization_id=organization_id,
            bank_id=bank_id,
            source_as_of_date=period.period_end,
            source_generation=1,
            **{name: getattr(fact, name) for name in _MIRRORED_COLUMNS},
        )
    session.add_all(mirrored.values())
    session.flush()

    ctx = TenantContext(organization_id=organization_id)
    bank = session.scalars(
        select(Bank).where(Bank.organization_id == organization_id, Bank.id == bank_id)
    ).one()
    modules_ok, modules_failed = recompute_modules(session, ctx, bank, period)
    return len(mirrored), modules_ok, modules_failed
