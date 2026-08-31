"""Query-shape characterization for the five detailed regulatory dashboards."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, event, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
)
from app.services import (
    regulatory_capital,
    regulatory_ftp,
    regulatory_fx,
    regulatory_irr,
    regulatory_liquidity,
)
from tests.api.helpers import headers
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

_CTX = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
_MODULES = {
    "liquidity": regulatory_liquidity,
    "capital": regulatory_capital,
    "irr": regulatory_irr,
    "fx": regulatory_fx,
    "ftp": regulatory_ftp,
}
_FULL_HTTP_QUERY_COUNTS = {
    "liquidity": 14,
    "capital": 15,
    "irr": 16,
    "fx": 12,
    "ftp": 12,
}


def _legacy_trend(  # noqa: PLR0912, PLR0915 - faithful five-module legacy oracle
    module_name: str,
    db: Session,
    bank: Bank,
    periods: list[BankReportingPeriod],
) -> list[object]:
    """The pre-batching per-period loop, retained as an equivalence oracle."""
    service = _MODULES[module_name]
    points: list[object] = []
    for period in periods[-service._TREND_MAX_POINTS :]:
        run = service._latest_succeeded_baseline_run(db, _CTX, bank, period.id)
        if module_name == "liquidity":
            if run is not None:
                metrics = service._scalar_metrics(run)
                points.append(
                    service.LiquidityTrendPointRead(
                        reporting_period_id=period.id,
                        label=period.label,
                        period_end=period.period_end,
                        lcr_pct=metrics["lcr_pct"],
                        nsfr_pct=metrics["nsfr_pct"],
                        stored=True,
                    )
                )
                continue
            try:
                lcr, nsfr, _params = service._compute_inline(db, _CTX, bank, period)
            except (
                service.MissingParameterError,
                service.LiquidityComputationError,
                service.LiquidityRunError,
            ):
                continue
            points.append(
                service.LiquidityTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    lcr_pct=lcr.lcr_pct,
                    nsfr_pct=nsfr.nsfr_pct,
                    stored=False,
                )
            )
        elif module_name == "capital":
            if run is not None:
                metrics = service._decimal_metrics(run)
                points.append(
                    service.CapitalTrendPointRead(
                        reporting_period_id=period.id,
                        label=period.label,
                        period_end=period.period_end,
                        car_pct=metrics["car_pct"],
                        tier1_ratio_pct=metrics["tier1_ratio_pct"],
                        cet1_ratio_pct=metrics["cet1_ratio_pct"],
                        stored=True,
                    )
                )
                continue
            try:
                _rwa, ratios, _params = service._compute_inline(db, _CTX, bank, period)
            except (
                service.MissingParameterError,
                service.CapitalComputationError,
                service.CapitalRunError,
            ):
                continue
            points.append(
                service.CapitalTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    car_pct=ratios.car_pct,
                    tier1_ratio_pct=ratios.tier1_ratio_pct,
                    cet1_ratio_pct=ratios.cet1_ratio_pct,
                    stored=False,
                )
            )
        elif module_name == "irr":
            if run is not None:
                metrics = run.metrics
                points.append(
                    service.IrrTrendPointRead(
                        reporting_period_id=period.id,
                        label=period.label,
                        period_end=period.period_end,
                        worst_eve_change_pct_tier1=service._decimal(
                            metrics, "worst_eve_change_pct_tier1"
                        ),
                        duration_gap=service._decimal(metrics, "duration_gap"),
                        cumulative_12m_gap_ghs=service._decimal(metrics, "cumulative_12m_gap_ghs"),
                        stored=True,
                    )
                )
                continue
            try:
                analysis = service._compute_inline(db, _CTX, bank, period)
            except (
                service.MissingParameterError,
                service.IrrComputationError,
                service.IrrRunError,
                service.UnsupportedShockError,
            ):
                continue
            points.append(
                service.IrrTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    worst_eve_change_pct_tier1=analysis.eve.worst_delta_eve_pct_tier1,
                    duration_gap=analysis.duration.duration_gap,
                    cumulative_12m_gap_ghs=analysis.gap.cumulative_12m_gap,
                    stored=False,
                )
            )
        elif module_name == "fx":
            if run is not None:
                metrics = run.metrics
                points.append(
                    service.FxTrendPointRead(
                        reporting_period_id=period.id,
                        label=period.label,
                        period_end=period.period_end,
                        nop_ghs=service._decimal(metrics, "nop_ghs"),
                        nop_pct_tier1=service._decimal(metrics, "nop_pct_tier1"),
                        var_99_1d_ghs=service._decimal(metrics, "var_99_1d_ghs"),
                        stored=True,
                    )
                )
                continue
            try:
                analysis = service._compute_inline(db, _CTX, bank, period)
            except (
                service.MissingParameterError,
                service.FxComputationError,
                service.FxRunError,
                service.NotComputable,
            ):
                continue
            points.append(
                service.FxTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    nop_ghs=analysis.nop.overall_nop,
                    nop_pct_tier1=analysis.nop.nop_pct_tier1,
                    var_99_1d_ghs=analysis.var.portfolio_var,
                    stored=False,
                )
            )
        else:
            if run is not None:
                metrics = run.metrics
                points.append(
                    service.FtpTrendPointRead(
                        reporting_period_id=period.id,
                        label=period.label,
                        period_end=period.period_end,
                        portfolio_nim_pct=service._decimal(metrics, "portfolio_nim_pct"),
                        products_below_min_margin=int(metrics["products_below_min_margin"]),
                        nmd_core_pct=service._decimal(metrics, "nmd_core_pct"),
                        stored=True,
                    )
                )
                continue
            try:
                analysis = service._compute_inline(db, _CTX, bank, period)
            except (
                service.MissingParameterError,
                service.FtpComputationError,
                service.FtpRunError,
            ):
                continue
            points.append(
                service.FtpTrendPointRead(
                    reporting_period_id=period.id,
                    label=period.label,
                    period_end=period.period_end,
                    portfolio_nim_pct=analysis.products.portfolio_nim_pct,
                    products_below_min_margin=analysis.products.products_below_min_margin,
                    nmd_core_pct=analysis.nmd.core_pct,
                    stored=False,
                )
            )
    return points


@contextmanager
def _capture_sql(engine: Engine | Connection) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.lower().split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _measure[T](db: Session, operation: Callable[[], T]) -> tuple[T, list[str]]:
    # Detach prior identity-map state without expiring the already-loaded
    # period objects passed to the trend builder. Expiring those objects would
    # manufacture one refresh query per caller-owned period before the service
    # even reaches its own database work.
    db.expunge_all()
    with _capture_sql(db.get_bind()) as statements:
        result = operation()
    return result, statements


@pytest.mark.parametrize("module_name", _MODULES)
def test_trend_query_count_stays_flat_as_periods_accumulate(
    db_session: Session, module_name: str
) -> None:
    """Twelve inline points must use the same round-trip shape as one."""
    materialize_canonical_test_book(db_session)
    db_session.flush()
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    periods = list(
        db_session.scalars(
            select(BankReportingPeriod)
            .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
            .order_by(BankReportingPeriod.period_end)
        )
    )
    assert bank is not None
    assert len(periods) == 12
    service = _MODULES[module_name]

    _one, one_statements = _measure(
        db_session, lambda: service._build_trend(db_session, _CTX, bank, periods[:1])
    )
    _all, all_statements = _measure(
        db_session, lambda: service._build_trend(db_session, _CTX, bank, periods)
    )
    print(f"{module_name}: trend SQL one={len(one_statements)} all={len(all_statements)}")
    assert len(all_statements) <= len(one_statements) + 1


def test_sdi_irr_trend_batches_net_own_funds_and_matches_scalar_path(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    periods = list(
        db_session.scalars(
            select(BankReportingPeriod)
            .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
            .order_by(BankReportingPeriod.period_end)
        )
    )
    assert bank is not None
    bank.institution_type = "savings_and_loans"
    batch = IngestionBatch(
        organization_id=DEMO_ORG_ID,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=periods[0].period_end,
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=DEMO_ORG_ID,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="sdi-irr-query-shape",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    db_session.add_all(
        [
            CanonicalReferenceRow(
                organization_id=DEMO_ORG_ID,
                bank_id=bank.id,
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                dataset_kind="capital_structure",
                as_of_date=periods[0].period_end,
                row_index=index,
                source_reference=f"sdi-irr/{index}",
                payload={"amount_ghs": amount, "tier": tier},
            )
            for index, (amount, tier) in enumerate(
                (("80000000", "cet1"), ("5000000", "cet1_deduction"))
            )
        ]
    )
    db_session.flush()

    one, one_statements = _measure(
        db_session,
        lambda: regulatory_irr._build_trend(db_session, _CTX, bank, periods[:1]),
    )
    all_points, all_statements = _measure(
        db_session,
        lambda: regulatory_irr._build_trend(db_session, _CTX, bank, periods),
    )
    scalar_points = _legacy_trend("irr", db_session, bank, periods)

    assert one
    assert all_points == scalar_points
    assert len(all_statements) <= len(one_statements) + 1


@pytest.mark.parametrize(
    ("module_name", "parameter_prefetches", "policy_scopes"),
    [
        ("liquidity", 3, 1),
        ("capital", 4, 1),
        ("irr", 2, 1),
        ("fx", 2, 0),
        ("ftp", 2, 0),
    ],
)
def test_trend_uses_one_bulk_call_per_request_resource(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    parameter_prefetches: int,
    policy_scopes: int,
) -> None:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    periods = list(
        db_session.scalars(
            select(BankReportingPeriod)
            .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
            .order_by(BankReportingPeriod.period_end)
        )
    )
    assert bank is not None
    service = _MODULES[module_name]
    counts = {"runs": 0, "facts": 0, "parameters": 0, "policy_scope": 0}

    run_loader = service.regulatory_dashboard_batching.latest_succeeded_baseline_runs
    fact_loader = service.regulatory_dashboard_batching.facts_by_period
    parameter_loader = service.prefetch_active_params
    policy_scope = regulatory_liquidity.regulatory_parameters.policy_scope

    def count_runs(*args: Any, **kwargs: Any) -> Any:
        counts["runs"] += 1
        return run_loader(*args, **kwargs)

    def count_facts(*args: Any, **kwargs: Any) -> Any:
        counts["facts"] += 1
        return fact_loader(*args, **kwargs)

    def count_parameters(*args: Any, **kwargs: Any) -> Any:
        counts["parameters"] += 1
        return parameter_loader(*args, **kwargs)

    def count_policy_scope(*args: Any, **kwargs: Any) -> Any:
        counts["policy_scope"] += 1
        return policy_scope(*args, **kwargs)

    monkeypatch.setattr(
        service.regulatory_dashboard_batching,
        "latest_succeeded_baseline_runs",
        count_runs,
    )
    monkeypatch.setattr(service.regulatory_dashboard_batching, "facts_by_period", count_facts)
    monkeypatch.setattr(service, "prefetch_active_params", count_parameters)
    monkeypatch.setattr(
        regulatory_liquidity.regulatory_parameters, "policy_scope", count_policy_scope
    )

    service._build_trend(db_session, _CTX, bank, periods)

    assert counts == {
        "runs": 1,
        "facts": 1,
        "parameters": parameter_prefetches,
        "policy_scope": policy_scopes,
    }


@pytest.mark.parametrize("module_name", _MODULES)
def test_record_full_http_dashboard_query_count(db_client: TestClient, module_name: str) -> None:
    """Pin the audit-style count; scaling is enforced by the trend budget."""
    with get_sessionmaker()() as session:
        materialize_canonical_test_book(session)
        session.commit()
        engine = session.get_bind()
        period_id = session.scalar(
            select(BankReportingPeriod.id)
            .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
            .order_by(BankReportingPeriod.period_end.desc())
            .limit(1)
        )
    assert period_id is not None

    with _capture_sql(engine) as statements:
        response = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/{module_name}/dashboard",
            params={"reporting_period_id": str(period_id)},
            headers=headers(),
        )

    assert response.status_code == 200, response.text
    print(f"{module_name}: full HTTP dashboard SQL={len(statements)}")
    assert len(statements) == _FULL_HTTP_QUERY_COUNTS[module_name]


@pytest.mark.parametrize(
    ("module_name", "getter"),
    [
        ("liquidity", regulatory_liquidity.get_liquidity_dashboard),
        ("capital", regulatory_capital.get_capital_dashboard),
        ("irr", regulatory_irr.get_irr_dashboard),
        ("fx", regulatory_fx.get_fx_dashboard),
        ("ftp", regulatory_ftp.get_ftp_dashboard),
    ],
)
def test_batched_dashboard_is_byte_identical_to_per_period_path(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    getter: Callable[..., Any],
) -> None:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    period_id = db_session.scalar(
        select(BankReportingPeriod.id)
        .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )
    assert period_id is not None
    service = _MODULES[module_name]
    optimized_builder = service._build_trend
    optimized_inline = service._compute_inline_or_409
    optimized_active = getattr(service, "_active_params_from_batch", None)

    def legacy_builder(
        db: Session,
        _ctx: TenantContext,
        bank: Bank,
        periods: list[BankReportingPeriod],
        **_kwargs: object,
    ) -> list[object]:
        return _legacy_trend(module_name, db, bank, periods)

    def legacy_inline(
        db: Session,
        ctx: TenantContext,
        bank: Bank,
        period: BankReportingPeriod,
        **_kwargs: object,
    ) -> Any:
        return optimized_inline(db, ctx, bank, period)

    monkeypatch.setattr(service, "_build_trend", legacy_builder)
    monkeypatch.setattr(service, "_compute_inline_or_409", legacy_inline)
    if module_name == "capital":

        def legacy_active(
            db: Session,
            ctx: TenantContext,
            bank: Bank,
            as_of: Any,
            _batch: object,
        ) -> Any:
            return service._load_active_params(db, ctx, bank, as_of)

        monkeypatch.setattr(service, "_active_params_from_batch", legacy_active)
    legacy = getter(
        db_session, _CTX, SAMPLE_BANK_ID, reporting_period_id=period_id
    ).model_dump_json()
    monkeypatch.setattr(service, "_build_trend", optimized_builder)
    monkeypatch.setattr(service, "_compute_inline_or_409", optimized_inline)
    if optimized_active is not None:
        monkeypatch.setattr(service, "_active_params_from_batch", optimized_active)
    optimized = getter(
        db_session, _CTX, SAMPLE_BANK_ID, reporting_period_id=period_id
    ).model_dump_json()

    assert optimized == legacy
