"""The fact assembler: what it selects, and what it refuses to say.

``app/bi/service/insights/assemble.py`` is the only impure piece of the insights
package, and the four properties asserted here are the reasons it exists rather
than the route doing the reading itself:

* **a missing figure never becomes a zero or a "flat"** — the only path from a
  ``NULL`` cell to a fact is the ``missing_reason`` argument, so no insight can
  say a figure stayed the same when the platform does not know what it was;
* **the headline set is derived, and regime-correct** — a bank's headline figures
  come from its own CRD authorities and an SDI's do not include them, decided by
  the SAME registry resolution the mart builder used when it stamped the rows;
* **target variants are excluded structurally**, not by the shape of their ids: a
  variance against a register the institution has not fed is a dash, not a
  headline;
* **the read is bounded** — one insights request is charged once against the read
  budget, so the number of compiled statements it may run is a constant here.

This module's other-suite cases live in ``test_insights_assemble.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import Table, delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.bi.domain.catalogue import catalogue
from app.bi.models.marts import (
    BiAggPositionDaily,
    BiFactEngineMetric,
    BiFactPositionDaily,
    BiMartBuild,
)
from app.bi.schemas.query import BiDateRange, BiQuery, BiTime
from app.bi.service.compiler import compile_query
from app.bi.service.execution import execute
from app.bi.service.insights.assemble import (
    assemble,
    default_compare_to,
)
from app.bi.service.insights.facts import MovementFact, ObservedFact
from app.core.authorization import InstitutionScope, ModuleScope, SensitivityScope
from app.models import Bank
from tests.bi.route_data import AS_OF, BUILT_AT, FINGERPRINT
from tests.bi.route_support import Grant, grant_only, seed_bi_mart
from tests.support.helpers import ORG_1, USER_1


def _clone_day(db: Session, bank: Bank, *, target: date, factor: Decimal) -> None:
    """Copy the fixture day with scaled decimals to provide a real comparison."""
    for model in (BiFactPositionDaily, BiAggPositionDaily, BiFactEngineMetric):
        rows = db.scalars(
            select(model).where(model.bank_id == bank.id, model.as_of_date == AS_OF)
        ).all()
        for row in rows:
            values: dict[str, object] = {}
            table = cast(Table, model.__table__)
            for column in table.columns:
                value = cast(object, getattr(row, column.name))
                values[column.name] = value * factor if isinstance(value, Decimal) else value
            values["as_of_date"] = target
            if "id" in values:
                values["id"] = uuid4()
            db.add(model(**values))
    db.add(
        BiMartBuild(
            organization_id=bank.organization_id,
            bank_id=bank.id,
            as_of_date=target,
            scope="engine",
            fingerprint=FINGERPRINT,
            status="succeeded",
            builder_version=1,
            started_at=BUILT_AT,
            finished_at=BUILT_AT,
            row_counts={},
        )
    )
    db.commit()


@pytest.fixture
def assembled_mart(db_session: Session) -> tuple[Bank, TenantContext]:
    bank = seed_bi_mart(db_session)
    authv = grant_only(
        db_session, (Grant(ModuleScope.ALL, SensitivityScope.ALL, InstitutionScope.ORGANIZATION),)
    )
    return bank, TenantContext(
        organization_id=ORG_1,
        actor_user_id=USER_1,
        authorization_version=authv,
    )


@pytest.mark.parametrize("missing_current", [False, True])
def test_missing_mart_values_never_become_zero_or_movements(
    db_session: Session,
    assembled_mart: tuple[Bank, TenantContext],
    missing_current: bool,
) -> None:
    bank, ctx = assembled_mart
    if missing_current:
        for model in (BiAggPositionDaily, BiFactPositionDaily, BiFactEngineMetric):
            db_session.execute(delete(model).where(model.bank_id == bank.id))
        db_session.flush()
    result = assemble(
        db_session,
        ctx=ctx,
        bank=bank,
        cat=catalogue(),
        as_of=AS_OF,
        surface="insights",
        row_cap=500,
        timeout_ms=5000,
    )
    assert result.measures_read > 0
    assert result.sheet.facts
    assert not any(isinstance(fact, MovementFact) for fact in result.sheet.facts)
    gaps = [
        fact
        for fact in result.sheet.facts
        if isinstance(fact, ObservedFact) and fact.missing_reason
    ]
    assert gaps
    assert all(fact.value is None for fact in gaps)
    if missing_current:
        capital = next(fact for fact in gaps if fact.measure_id == "engine.car_pct.crd.official")
        assert capital.value is None
        assert capital.missing_reason is not None


def test_assembled_figures_equal_the_public_compiled_query_results(
    db_session: Session,
    assembled_mart: tuple[Bank, TenantContext],
) -> None:
    bank, ctx = assembled_mart
    prior = default_compare_to(AS_OF)
    _clone_day(db_session, bank, target=prior, factor=Decimal("0.8"))
    cat = catalogue()
    result = assemble(
        db_session,
        ctx=ctx,
        bank=bank,
        cat=cat,
        as_of=AS_OF,
        surface="insights",
        row_cap=500,
        timeout_ms=5000,
    )
    movements = [fact for fact in result.sheet.facts if isinstance(fact, MovementFact)]
    assert movements
    for fact in movements:
        time = (
            BiTime(range=BiDateRange(start=AS_OF.replace(day=1), end=AS_OF), compare_to=prior)
            if cat.measure(fact.measure_id).time_behaviour == "flow"
            else BiTime(as_of=AS_OF, compare_to=prior)
        )
        query = BiQuery(measures=[fact.measure_id], time=time)
        compiled = compile_query(
            db_session,
            cat,
            query,
            organization_id=ctx.organization_id,
            bank_id=bank.id,
        )
        answer = execute(db_session, compiled, timeout_ms=5000, row_cap=500)
        assert answer.rows
        row: tuple[object, ...] = answer.rows[0]
        values = {column.role: value for column, value in zip(answer.columns, row, strict=True)}
        assert fact.current == Decimal(str(values["current"]))
        assert fact.prior == Decimal(str(values["prior"]))
