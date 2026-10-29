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
from app.fx import service as regulatory_fx
from app.fx.domain.engine import FxComputationError, FxPosition, compute_var
from app.live.service.fact_derivation import (
    _Canonical,  # pyright: ignore[reportPrivateUsage]
    _derive_fx_returns,  # pyright: ignore[reportPrivateUsage]
)
from app.models import BankFinancialFact, BankReportingPeriod, LiveMetric, RegulatoryRun
from tests.fixtures.live_plane import materialize_live_plane
from tests.services.test_nop_currency_coverage import (
    _add_unrepresented_currency,  # pyright: ignore[reportPrivateUsage]
    _book,  # pyright: ignore[reportPrivateUsage]
)


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


@pytest.mark.parametrize("observations", [1, 249, 250])
def test_sample_is_disclosed_in_dashboard_immutable_and_live_evidence(
    db_session: Session, observations: int
) -> None:
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
        fact.attributes = {**attributes, "intervals": intervals[-observations:]}
    db_session.flush()
    run = regulatory_fx._create_and_execute(db_session, ctx, bank, period, "baseline")  # pyright: ignore[reportPrivateUsage]
    assert run.status == "succeeded", run
    evidence = _Evidence.model_validate(run.metrics)
    assert evidence.var_observations == observations
    assert evidence.var_sample_limited is (observations < 250)
    assert len(evidence.var_intervals) == observations
    if observations == 1:
        assert evidence.stressed_var_intervals == evidence.var_intervals
    snapshot = _Snapshot.model_validate(run.inputs)
    assert snapshot.var_sample_policy.version == "exact-interval-intersection-v1"
    dashboard = regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, period.id)
    assert dashboard.stored
    sample = next(v for v in dashboard.validations if v.rule_code == "var_sample_evidence")
    assert sample.passed is (observations >= 250)
    assert sample.severity == ("warning" if observations < 250 else "info")
    assert f"{observations}/250" in sample.message
    assert ("not adequate pilot risk evidence" in sample.message) is (observations < 250)
    assert evidence.var_intervals[0][0].isoformat() in sample.message
    stress = next(v for v in dashboard.validations if v.rule_code == "stressed_var_disclosed")
    assert "shared intervals" in stress.message
    assert "indicative management" in stress.message
    _, modules_ok, _ = materialize_live_plane(
        db_session, organization_id=ctx.organization_id, bank_id=bank.id
    )
    assert "fx" in modules_ok
    live = db_session.scalars(
        select(LiveMetric).where(LiveMetric.bank_id == bank.id, LiveMetric.module == "fx")
    ).one()
    assert live.engine_version == regulatory_fx.ENGINE_VERSION
    assert live.metrics["var_99_1d_ghs"] == str(dashboard.metrics.var_99_1d_ghs)
    assert live.metrics["var_sample_evidence"] == sample.message


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


@pytest.mark.parametrize("stored", [True, False])
@pytest.mark.parametrize("observations", [1, 249, 250])
def test_trends_omit_only_insufficient_var_samples(
    db_session: Session, stored: bool, observations: int
) -> None:
    bank, period, ctx = _book(db_session)
    facts = db_session.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period.id,
            BankFinancialFact.fact_group == "fx_return_history",
        )
    ).all()
    assert facts
    for fact in facts:
        attributes = cast(dict[str, object], fact.attributes)
        intervals = TypeAdapter(list[dict[str, str]]).validate_python(attributes["intervals"])
        assert len(intervals) >= observations
        fact.attributes = {**attributes, "intervals": intervals[-observations:]}
    if stored:
        run = regulatory_fx._create_and_execute(db_session, ctx, bank, period, "baseline")  # pyright: ignore[reportPrivateUsage]
        assert run.status == "succeeded"
    else:
        for run in db_session.scalars(
            select(RegulatoryRun).where(
                RegulatoryRun.bank_id == bank.id,
                RegulatoryRun.reporting_period_id == period.id,
                RegulatoryRun.module == "fx",
            )
        ):
            run.status = "failed"
    db_session.flush()
    dashboard = regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, period.id)
    assert dashboard.stored is stored
    assert dashboard.metrics.var_observations == observations
    points = [point for point in dashboard.trend if point.reporting_period_id == period.id]
    assert len(points) == 1
    assert points[0].stored is stored
    assert points[0].nop_ghs == dashboard.metrics.nop_ghs
    assert points[0].nop_pct_tier1 == dashboard.metrics.nop_pct_tier1
    if observations == 250:
        assert points[0].var_99_1d_ghs == dashboard.metrics.var_99_1d_ghs
    else:
        assert points[0].var_99_1d_ghs is None
        assert points[0].model_dump(mode="json")["var_99_1d_ghs"] is None


@pytest.mark.parametrize("missing_field", ["var_sample_policy", "var_observations"])
def test_saved_trend_without_var_evidence_preserves_nop(
    db_session: Session, missing_field: str
) -> None:
    bank, period, ctx = _book(db_session)
    run = db_session.scalars(
        select(RegulatoryRun).where(RegulatoryRun.bank_id == bank.id, RegulatoryRun.module == "fx")
    ).one()
    metrics = cast(dict[str, object], run.metrics)
    nop = Decimal(str(metrics["nop_ghs"]))
    run.metrics = {key: value for key, value in metrics.items() if key != missing_field}
    db_session.flush()
    points = regulatory_fx._build_trend(db_session, ctx, bank, [period])  # pyright: ignore[reportPrivateUsage]
    assert len(points) == 1
    assert points[0].nop_ghs == nop
    assert points[0].var_99_1d_ghs is None


@pytest.mark.parametrize("stored", [True, False])
@pytest.mark.parametrize("sample", ["short", "disjoint", "missing", "invalid"])
def test_january_to_march_retains_nop_when_var_is_unavailable(
    db_session: Session, stored: bool, sample: str
) -> None:
    bank, latest, ctx = _book(db_session)
    periods = list(
        db_session.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.bank_id == bank.id,
                BankReportingPeriod.period_end >= date(2026, 1, 1),
            )
            .order_by(BankReportingPeriod.period_end)
        )
    )
    assert [period.period_end.month for period in periods] == [1, 2, 3]
    for run in db_session.scalars(
        select(RegulatoryRun).where(RegulatoryRun.bank_id == bank.id, RegulatoryRun.module == "fx")
    ):
        run.status = "failed"
    counts = (250, 250, 249) if sample == "short" else (250, 250, 250)
    for period, nop_pct, observations in zip(periods, (10, 20, 30), counts, strict=True):
        facts = list(
            db_session.scalars(
                select(BankFinancialFact).where(
                    BankFinancialFact.bank_id == bank.id,
                    BankFinancialFact.reporting_period_id == period.id,
                )
            )
        )
        tier1 = regulatory_fx._tier1_from_facts(facts)  # pyright: ignore[reportPrivateUsage]
        for fact in facts:
            attributes = cast(dict[str, object], fact.attributes)
            if fact.fact_group == "fx_return_history":
                intervals = TypeAdapter(list[dict[str, str]]).validate_python(
                    attributes["intervals"]
                )
                if period.period_end.month == 2:
                    if sample == "disjoint":
                        day = 1 if fact.category == "USD" else 2
                        intervals = [
                            {
                                "start_date": date(2026, 1, day).isoformat(),
                                "end_date": date(2026, 1, day + 1).isoformat(),
                                "value": "0.1",
                            }
                        ]
                    elif sample == "missing" and fact.category == "USD":
                        intervals = []
                    elif sample == "invalid" and fact.category == "USD":
                        intervals = [intervals[-1], intervals[-1]]
                fact.attributes = {**attributes, "intervals": intervals[-observations:]}
            elif fact.fact_group == "fx_position":
                fact.amount = (
                    tier1 * Decimal(nop_pct) / 100 if fact.category == "USD" else Decimal(0)
                )
                net_ccy = fact.amount / Decimal(str(attributes["spot_ghs"]))
                fact.attributes = {
                    **attributes,
                    "net_ccy": str(net_ccy),
                    "assets_ccy": str(net_ccy),
                    "liabilities_ccy": "0",
                    "net_derivatives_ccy": "0",
                    "net_ghs": str(fact.amount),
                }
        db_session.flush()
        if stored and (sample == "short" or period.period_end.month != 2):
            run = regulatory_fx._create_and_execute(db_session, ctx, bank, period, "baseline")  # pyright: ignore[reportPrivateUsage]
            assert run.status == "succeeded"
    dashboard = regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, latest.id)
    period_ids = {period.id for period in periods}
    points = [point for point in dashboard.trend if point.reporting_period_id in period_ids]
    assert [point.nop_pct_tier1 for point in points] == [Decimal(10), Decimal(20), Decimal(30)]
    assert [point.stored for point in points] == [stored, stored and sample == "short", stored]
    unavailable_index = 2 if sample == "short" else 1
    assert [point.var_99_1d_ghs is None for point in points] == [
        index == unavailable_index for index in range(3)
    ]
    assert dashboard.metrics.nop_pct_tier1 - points[-2].nop_pct_tier1 == Decimal(10)
    if sample != "short":
        message = {
            "disjoint": "No matching dated FX return intervals",
            "missing": "fx_return_history:USD",
            "invalid": "Invalid or duplicate FX return interval",
        }[sample]
        with pytest.raises(ModuleDataUnavailable, match=message):
            regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, periods[1].id)


@pytest.mark.parametrize("refusal", ["currency", "quote", "conversion"])
def test_var_unavailability_does_not_bypass_nop_refusals(db_session: Session, refusal: str) -> None:
    bank, latest, ctx = _book(db_session)
    february = db_session.scalars(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end == date(2026, 2, 28),
        )
    ).one()
    facts = db_session.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == february.id,
        )
    ).all()
    for fact in facts:
        attributes = cast(dict[str, object], fact.attributes)
        if fact.fact_group == "fx_return_history":
            day = 1 if fact.category == "USD" else 2
            fact.attributes = {
                **attributes,
                "intervals": [
                    {
                        "start_date": date(2026, 1, day).isoformat(),
                        "end_date": date(2026, 1, day + 1).isoformat(),
                        "value": "0.1",
                    }
                ],
            }
        elif fact.fact_group == "fx_position" and fact.category == "USD":
            if refusal == "quote":
                fact.attributes = {**attributes, "spot_ghs": None}
            elif refusal == "conversion":
                fact.amount = Decimal(0)
    if refusal == "currency":
        _add_unrepresented_currency(db_session, bank, february)
    db_session.flush()
    dashboard = regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, latest.id)
    assert latest.id in {point.reporting_period_id for point in dashboard.trend}
    assert february.id not in {point.reporting_period_id for point in dashboard.trend}
    with pytest.raises(ModuleDataUnavailable, match="CHF" if refusal == "currency" else "USD"):
        regulatory_fx.get_fx_dashboard(db_session, ctx, bank.id, february.id)


class _Evidence(BaseModel):
    var_observations: int
    var_sample_limited: bool
    var_intervals: list[tuple[date, date]]
    stressed_var_intervals: list[tuple[date, date]]


class _Policy(BaseModel):
    version: str


class _Snapshot(BaseModel):
    var_sample_policy: _Policy
