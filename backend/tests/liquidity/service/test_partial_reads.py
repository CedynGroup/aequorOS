from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.domain.authority.results import FigureResult
from app.domain.liquidity.engine import LcrResult, LiquidityFact, LiquidityParams, NsfrResult
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CurrentFinancialFact,
    ParamCapitalThreshold,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate, RegulatoryScenarioCode
from app.schemas.scenario_workbench import AnalysisRunCreate, ScenarioRefIn
from app.services import analysis_workbench, authorization, regulatory_liquidity, window_analytics
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)


@pytest.mark.parametrize(
    "refused, threshold",
    [
        (("lcr_pct",), None),
        (("nsfr_pct",), None),
        (("lcr_pct", "nsfr_pct"), None),
        (("lcr_pct",), "lcr_inflow_cap_pct"),
        (("lcr_pct",), "lcr_min"),
        (("nsfr_pct",), "nsfr_min"),
        (("lcr_pct", "nsfr_pct"), "lcr_amber_floor"),
        (("lcr_pct",), "unclassified"),
    ],
)
@pytest.mark.parametrize("scenario", ["baseline", "combined"])
def test_partial_results_survive_every_reader_and_block_official_runs(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    refused: tuple[str, ...],
    threshold: str | None,
    scenario: RegulatoryScenarioCode,
) -> None:
    materialize_canonical_test_book(db_session)
    ctx = TenantContext(
        organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
    )
    authorization.create_role_binding(
        db_session,
        organization_id=DEMO_ORG_ID,
        principal_user_id=DEMO_USER_ID,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            SAMPLE_BANK_ID,
            ModuleScope.LIQUIDITY,
            SensitivityScope.ALL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "liquidity-result-test"),
        reason="Verify partial liquidity reads under scoped authority",
    )
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == date(2026, 3, 31),
        )
    )
    assert bank is not None and period is not None
    baseline = regulatory_liquidity.get_liquidity_dashboard(db_session, ctx, bank.id, period.id)
    expected = cast(dict[str, object], baseline.metrics.model_dump(mode="json"))
    facts = list(
        db_session.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
            )
        )
    )
    db_session.add_all(
        [
            CurrentFinancialFact(
                organization_id=fact.organization_id,
                bank_id=fact.bank_id,
                source_as_of_date=period.period_end,
                source_generation=1,
                fact_group=fact.fact_group,
                category=fact.category,
                amount=fact.amount,
                currency=fact.currency,
                hqla_level=fact.hqla_level,
                risk_weight_code=fact.risk_weight_code,
                ccf_pct=fact.ccf_pct,
                rate_pct=fact.rate_pct,
                income_year=fact.income_year,
                attributes=fact.attributes,
            )
            for fact in facts
        ]
    )
    db_session.flush()
    original_compute = regulatory_liquidity.compute_liquidity_results

    def missing_rates(
        facts: Sequence[LiquidityFact], params: LiquidityParams[Decimal | None], tenant_id: str
    ) -> tuple[FigureResult[LcrResult], FigureResult[NsfrResult]]:
        return original_compute(
            facts,
            replace(
                params,
                outflow_rates={} if "lcr_pct" in refused else params.outflow_rates,
                rsf_weights={} if "nsfr_pct" in refused else params.rsf_weights,
            ),
            tenant_id,
        )

    if threshold is None:
        monkeypatch.setattr(regulatory_liquidity, "compute_liquidity_results", missing_rates)
    elif threshold == "unclassified":
        for fact in facts:
            if fact.fact_group == "securities" and fact.hqla_level is not None:
                fact.hqla_level = "unknown"
        for fact in db_session.scalars(
            select(CurrentFinancialFact).where(
                CurrentFinancialFact.bank_id == bank.id,
                CurrentFinancialFact.fact_group == "securities",
                CurrentFinancialFact.hqla_level.is_not(None),
            )
        ):
            fact.hqla_level = "unknown"
        db_session.flush()
    else:
        for row in db_session.scalars(
            select(ParamCapitalThreshold).where(
                ParamCapitalThreshold.threshold_code == threshold,
            )
        ):
            db_session.delete(row)
        db_session.flush()
    dashboard = regulatory_liquidity.get_liquidity_dashboard(db_session, ctx, bank.id, period.id)
    payload = dashboard.model_dump(mode="json")
    metrics = cast(dict[str, object], payload["metrics"])
    live = regulatory_liquidity.compute_live(db_session, ctx, bank, period)
    analysis = analysis_workbench.run_analysis(
        db_session,
        ctx,
        bank.id,
        "liquidity",
        AnalysisRunCreate(
            reporting_period_id=period.id, scenarios=[ScenarioRefIn(kind="system", code="baseline")]
        ),
    ).results[0]
    window = window_analytics.compute_window(
        db_session, ctx, bank.id, start_date=period.period_end, end_date=period.period_end
    )
    assert (
        set(dashboard.metrics.refusals)
        == set(cast(dict[str, object], live.metrics["refusals"]))
        == set(analysis.refusals)
    )
    assert set(dashboard.metrics.refusals) == set(refused)
    assert live.status == "red"
    assert analysis.status == "succeeded"
    for metric, sections, totals in (
        (
            "lcr_pct",
            (dashboard.hqla_composition, dashboard.outflows),
            ("hqla_total_ghs", "net_outflows_30d_ghs"),
        ),
        ("nsfr_pct", (dashboard.asf, dashboard.rsf), ("asf_total_ghs", "rsf_total_ghs")),
    ):
        if metric in refused:
            refusal = dashboard.metrics.refusals[metric]
            assert refusal.reason_code == (
                "unclassified_hqla" if threshold == "unclassified" else "missing_parameter"
            )
            assert refusal.rule_citation in {"BCBS 238", "BCBS 295"}
            assert refusal.reason
            if threshold in (None, "unclassified"):
                assert refusal.row_ref
            assert metrics[metric] is None
            assert all(metrics[total] is None for total in totals)
            assert metric not in live.metrics and metric not in analysis.metrics
            assert all(not section for section in sections)
            affected_trend = [
                point
                for point in dashboard.trend
                if threshold != "unclassified" or point.reporting_period_id == period.id
            ]
            assert affected_trend
            assert all(metric in point.refusals for point in affected_trend)
            assert not any(stat.ratio == metric for stat in window.ratios)
        else:
            assert (
                metrics[metric]
                == live.metrics[metric]
                == analysis.metrics[metric]
                == expected[metric]
            )
            assert all(section for section in sections)
            assert all(metric not in point.refusals for point in dashboard.trend)
            assert any(stat.ratio == metric for stat in window.ratios)
    run = regulatory_liquidity.create_liquidity_run(
        db_session,
        ctx,
        bank.id,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period.id, scenario_code=scenario
        ),
    )
    assert run.status == "failed" and run.error is not None
    assert run.error.code == (
        "unclassified_hqla" if threshold == "unclassified" else "missing_parameter"
    )
    details = cast(dict[str, object], run.error.details)
    if threshold in (None, "unclassified"):
        assert details["row_ref"]
        loaded = sorted(
            [
                fact
                for fact in facts
                if fact.fact_group
                in ("balance_sheet", "loan_exposure", "securities", "off_balance", "lcr_inflow")
            ],
            key=lambda fact: (fact.fact_group, fact.category),
        )
        figure = "lcr_pct" if "lcr_pct" in refused else "nsfr_pct"
        affected = [
            {
                "fact_group": loaded[index - 1].fact_group,
                "category": loaded[index - 1].category,
                "amount": str(loaded[index - 1].amount),
                "hqla_level": loaded[index - 1].hqla_level,
                "side": cast(object, loaded[index - 1].attributes.get("side")),
                "cash_derived": loaded[index - 1].attributes.get("source") == "cash",
            }
            for index in dashboard.metrics.refusals[figure].row_ref
        ]
        snapshot = cast(list[dict[str, object]], run.inputs["facts"])
        indices = cast(list[int], details["row_ref"])
        assert sorted([snapshot[index - 1] for index in indices], key=str) == sorted(
            affected, key=str
        )
    assert not run.metric_results and not run.line_items
