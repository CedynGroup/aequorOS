"""Request-scoped database batching for detailed regulatory dashboard trends.

This is deliberately a read-through helper, not a persisted trend read model.
It removes repeated round trips while leaving every module's calculation engine
and stored-run precedence unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bank, BankFinancialFact, RegulatoryRun


def latest_succeeded_baseline_runs(  # noqa: PLR0913 - full tenant/run identity is required
    db: Session,
    *,
    organization_id: str,
    bank: Bank,
    module: str,
    scenario_code: str,
    reporting_period_ids: Iterable[UUID],
) -> dict[UUID, RegulatoryRun]:
    """Newest succeeded baseline run per candidate period, in one query."""
    period_ids = tuple(dict.fromkeys(reporting_period_ids))
    if not period_ids:
        return {}
    rows = db.scalars(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id.in_(period_ids),
            RegulatoryRun.module == module,
            RegulatoryRun.scenario_code == scenario_code,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(
            RegulatoryRun.reporting_period_id,
            RegulatoryRun.created_at.desc(),
            RegulatoryRun.id.desc(),
        )
    )
    latest: dict[UUID, RegulatoryRun] = {}
    for row in rows:
        latest.setdefault(row.reporting_period_id, row)
    return latest


def facts_by_period(
    db: Session,
    *,
    organization_id: str,
    bank: Bank,
    reporting_period_ids: Iterable[UUID],
    fact_groups: Iterable[str],
) -> dict[UUID, list[BankFinancialFact]]:
    """Load scoped facts for every candidate period, in one query."""
    period_ids = tuple(dict.fromkeys(reporting_period_ids))
    groups = tuple(dict.fromkeys(fact_groups))
    if not period_ids or not groups:
        return {}
    rows = db.scalars(
        select(BankFinancialFact)
        .where(
            BankFinancialFact.organization_id == organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id.in_(period_ids),
            BankFinancialFact.fact_group.in_(groups),
        )
        .order_by(
            BankFinancialFact.reporting_period_id,
            BankFinancialFact.fact_group,
            BankFinancialFact.category,
        )
    )
    grouped: defaultdict[UUID, list[BankFinancialFact]] = defaultdict(list)
    for row in rows:
        grouped[row.reporting_period_id].append(row)
    return dict(grouped)
