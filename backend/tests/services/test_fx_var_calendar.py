"""Synthetic #582 journeys: calendar evidence from derivation through saved FX runs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast

import pytest
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ModuleDataUnavailable
from app.domain.fx.engine import FxComputationError, FxPosition, compute_var
from app.models import BankFinancialFact, RegulatoryRun
from app.services import regulatory_fx
from app.services.fact_derivation import (
    _Canonical,  # pyright: ignore[reportPrivateUsage]
    _derive_fx_returns,  # pyright: ignore[reportPrivateUsage]
)
from tests.services.test_nop_currency_coverage import _book  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("source", ["reference", "market"])
def test_reported_spot_reproduction_preserves_interval_evidence(source: str) -> None:
    canonical = _Canonical(
        as_of=date(2026, 1, 3), base_currency="GHS", positions=[], gl_accounts=[], refs={}
    )
    observations = {
        "USD": [(1, "100"), (2, "90"), (3, "99")],
        "EUR": [(2, "100"), (3, "110")],
    }
    for currency, values in observations.items():
        if source == "market":
            canonical.market_fx_history[currency] = [
                (date(2026, 1, day), Decimal(rate)) for day, rate in values
            ]
        else:
            canonical.refs.setdefault("fx_rates_historical", []).extend(
                {"currency": currency, "date": date(2026, 1, day).isoformat(), "spot_rate": rate}
                for day, rate in values
            )
    specs = _derive_fx_returns(canonical, set(observations), [])
    facts = [BankFinancialFact(fact_group=s.fact_group, attributes=s.attributes) for s in specs]
    histories = regulatory_fx._returns_from_facts(facts)  # pyright: ignore[reportPrivateUsage]
    assert histories["USD"][0].start_date == date(2026, 1, 1)
    assert histories["USD"][0].value == Decimal("-0.1")
    assert histories["EUR"][0].end_date == date(2026, 1, 3)
    positions = [
        FxPosition(
            currency,
            Decimal(net),
            Decimal("1"),
            Decimal(net),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )
        for currency, net in [("USD", "100"), ("EUR", "-100")]
    ]
    result = compute_var(positions, histories, Decimal("99"))
    assert result.portfolio_var == Decimal("0.0000")
    assert result.observations == 1


def test_undated_legacy_facts_require_rederivation() -> None:
    fact = BankFinancialFact(
        fact_group="fx_return_history", attributes={"currency": "USD", "returns": [-0.1]}
    )
    with pytest.raises(FxComputationError, match="re-derive"):
        regulatory_fx._returns_from_facts([fact])  # pyright: ignore[reportPrivateUsage]


def test_short_sample_is_disclosed_in_dashboard_and_immutable_evidence(db_session: Session) -> None:
    bank, period, ctx = _book(db_session)
    facts = list(
        db_session.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
                BankFinancialFact.fact_group == "fx_return_history",
            )
        )
    )
    assert facts
    for fact in facts:
        attributes = cast(dict[str, object], fact.attributes)
        intervals = TypeAdapter(list[dict[str, str]]).validate_python(attributes["intervals"])
        fact.attributes = {**attributes, "intervals": intervals[-1:]}
    db_session.flush()
    run = regulatory_fx._create_and_execute(db_session, ctx, bank, period, "baseline")  # pyright: ignore[reportPrivateUsage]
    assert run.status == "succeeded", run
    evidence = _Evidence.model_validate(run.metrics)
    assert evidence.var_observations == 1
    assert evidence.var_sample_limited is True
    assert len(evidence.var_intervals) == 1
    assert evidence.stressed_var_intervals == evidence.var_intervals
    snapshot = _Snapshot.model_validate(run.inputs)
    assert snapshot.var_sample_policy.version == "exact-interval-intersection-v1"
    dashboard = regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, period.id)
    assert dashboard.stored
    sample = next(v for v in dashboard.validations if v.rule_code == "var_sample_evidence")
    assert not sample.passed and sample.severity == "warning"
    assert "1/250" in sample.message
    assert "not adequate pilot risk evidence" in sample.message
    assert evidence.var_intervals[0][0].isoformat() in sample.message
    stress = next(v for v in dashboard.validations if v.rule_code == "stressed_var_disclosed")
    assert "1 shared intervals" in stress.message
    assert "indicative management" in stress.message


def test_old_saved_results_cannot_supply_dashboard_var(db_session: Session) -> None:
    bank, period, ctx = _book(db_session)
    run = db_session.scalars(
        select(RegulatoryRun).where(RegulatoryRun.bank_id == bank.id, RegulatoryRun.module == "fx")
    ).one()
    run.metrics = {
        key: value
        for key, value in cast(dict[str, object], run.metrics).items()
        if key != "var_sample_policy"
    }
    db_session.flush()
    with pytest.raises(ModuleDataUnavailable, match="re-derive"):
        regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, period.id)


class _Evidence(BaseModel):
    var_observations: int
    var_sample_limited: bool
    var_intervals: list[tuple[date, date]]
    stressed_var_intervals: list[tuple[date, date]]


class _Policy(BaseModel):
    version: str


class _Snapshot(BaseModel):
    var_sample_policy: _Policy
