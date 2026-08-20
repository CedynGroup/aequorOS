from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalCounterpartyRating,
    CanonicalMarketIndex,
    DeskMethodology,
    ImpliedRatingRun,
    IngestionBatch,
    LineageRecord,
    LiveMetric,
    Organization,
    RegulatoryRun,
    User,
)
from app.services import implied_rating

ORG_ID = "OR-RAT00001"
BANK_ID = "BK-RAT00001"
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
PERIOD_END = date(2026, 3, 31)
CTX = TenantContext(organization_id=ORG_ID, actor_user_id=USER_ID)


def _canonical_metadata(db_session):
    batch = IngestionBatch(
        organization_id=ORG_ID,
        bank_id=BANK_ID,
        source_system="MANUAL_UPLOAD",
        adapter_version="test/1",
        extraction_mode="full",
        status="accepted",
        as_of_date=PERIOD_END,
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_ID,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="rating-canonical-fixture",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    return {
        "organization_id": ORG_ID,
        "bank_id": BANK_ID,
        "as_of_date": PERIOD_END,
        "ingested_at": datetime(2026, 4, 1, tzinfo=UTC),
        "source_system": "MANUAL_UPLOAD",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def _fact(period_id, group: str, category: str, amount: str) -> BankFinancialFact:
    return BankFinancialFact(
        organization_id=ORG_ID,
        bank_id=BANK_ID,
        reporting_period_id=period_id,
        fact_group=group,
        category=category,
        amount=Decimal(amount),
        currency="GHS",
        attributes={"source": "canonical_etl"},
    )


def _successful_run(period_id, module: str, metrics: dict[str, str]) -> RegulatoryRun:
    return RegulatoryRun(
        organization_id=ORG_ID,
        bank_id=BANK_ID,
        reporting_period_id=period_id,
        module=module,
        scenario_code="baseline",
        status="succeeded",
        engine_version="test/1",
        input_schema_version="test/1",
        output_schema_version="test/1",
        input_hash=f"{module}-input-hash",
        inputs={"source": "canonical_etl"},
        metrics=metrics,
        started_at=datetime(2026, 4, 1, tzinfo=UTC),
        completed_at=datetime(2026, 4, 1, tzinfo=UTC),
        created_by=USER_ID,
    )


def test_rating_run_snapshots_canonical_facts_calculations_and_market_data(db_session) -> None:
    db_session.add_all(
        [
            Organization(id=ORG_ID, name="Rating integration tenant"),
            User(
                id=USER_ID,
                organization_id=ORG_ID,
                email="rating.analyst@example.test",
                display_name="Rating Analyst",
            ),
            Bank(
                id=BANK_ID,
                organization_id=ORG_ID,
                name="Canonical Bank Ltd",
                short_name="Canonical Bank",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            ),
        ]
    )
    db_session.flush()
    period = BankReportingPeriod(
        organization_id=ORG_ID,
        bank_id=BANK_ID,
        period_start=date(2026, 1, 1),
        period_end=PERIOD_END,
        label="2026-Q1",
        status="closed",
    )
    db_session.add(period)
    db_session.flush()
    db_session.add_all(
        [
            _fact(period.id, "balance_sheet", "cash_vault", "50000000"),
            _fact(period.id, "balance_sheet", "bog_required_reserves", "100000000"),
            _fact(period.id, "balance_sheet", "bog_excess_reserves", "50000000"),
            _fact(period.id, "balance_sheet", "securities_bog_bills", "150000000"),
            _fact(period.id, "balance_sheet", "securities_gog_bonds", "250000000"),
            _fact(period.id, "balance_sheet", "loans_gross", "900000000"),
            _fact(period.id, "balance_sheet", "other_assets", "50000000"),
            _fact(period.id, "balance_sheet", "retail_deposits_stable", "500000000"),
            _fact(period.id, "balance_sheet", "retail_deposits_less_stable", "300000000"),
            _fact(period.id, "balance_sheet", "wholesale_operational", "150000000"),
            _fact(period.id, "loan_exposure", "past_due_90", "60000000"),
            _fact(period.id, "capital_component", "general_provisions", "40000000"),
            _fact(period.id, "operational_income", "gross_income_2024", "160000000"),
            _fact(period.id, "operational_income", "gross_income_2025", "180000000"),
            _fact(period.id, "operational_income", "net_income_2025", "42000000"),
            _fact(period.id, "operational_income", "net_interest_income_2025", "125000000"),
            _fact(period.id, "operational_income", "operating_expenses_2025", "85000000"),
            _fact(period.id, "operational_income", "provisions_2025", "12000000"),
            _fact(period.id, "cashflow", "inflows_90d", "360000000"),
            _fact(period.id, "cashflow", "outflows_90d", "300000000"),
            _fact(period.id, "cashflow", "net_cashflow_90d", "60000000"),
            _successful_run(
                period.id,
                "capital",
                {
                    "cet1_ratio_pct": "13.2",
                    "car_pct": "16.4",
                    "leverage_ratio_pct": "7.5",
                    "total_capital_ghs": "260000000",
                    "total_rwa_ghs": "1585000000",
                },
            ),
            _successful_run(period.id, "liquidity", {"lcr_pct": "132", "nsfr_pct": "118"}),
            _successful_run(period.id, "irr", {"worst_eve_change_pct_tier1": "8.5"}),
            _successful_run(period.id, "fx", {"nop_pct_tier1": "7.2"}),
        ]
    )
    metadata = _canonical_metadata(db_session)
    db_session.add_all(
        [
            CanonicalCounterpartyRating(
                **metadata,
                source_reference="market-desk/Ghana/sovereign-rating",
                issuer="GHANA_SOVEREIGN",
                agency="fitch",
                rating="B-",
                watch_status="stable",
                rating_date=PERIOD_END,
            ),
            CanonicalMarketIndex(
                **metadata,
                source_reference="market-desk/Ghana/operating-environment",
                index_code="GHANA_OPERATING_ENVIRONMENT_SCORE",
                value=Decimal("0.55"),
                scenario="base",
                horizon_months=None,
            ),
        ]
    )
    implied_rating.ensure_default_methodology(db_session)
    db_session.commit()

    rating_run = implied_rating.run(db_session, CTX, BANK_ID, period.id)

    assert rating_run.status == "succeeded"
    assert rating_run.results["pit"]["sovereign_ceiling"] == "b-"
    assert rating_run.results["pit"]["rating_grade"] == "ccc+"
    assert rating_run.results["pit"]["pd_band"]["basis"] == "PIT"
    assert rating_run.results["ttc"]["pd_band"]["basis"] == "TTC"
    assert rating_run.results["pit"]["key_drivers_up"]
    assert rating_run.results["pit"]["key_drivers_down"]
    assert rating_run.input_snapshot["operating_environment"]["source"] == "market_index"
    assert rating_run.results["ddep_stress"]["sovereign_loss_ghs"] == "120000000.000000"
    # §2.2 weaker-of degrades to latest with a single quarterly period.
    conservative = rating_run.input_snapshot["conservative_income_basis"]
    assert conservative["applied"] is False
    assert conservative["annual_periods_used"] == 0
    assert db_session.query(DeskMethodology).count() == 1
    assert db_session.query(ImpliedRatingRun).count() == 1

    db_session.add_all(
        [
            LiveMetric(
                organization_id=ORG_ID,
                bank_id=BANK_ID,
                reporting_period_id=period.id,
                module="capital",
                metrics={
                    "cet1_ratio_pct": "13.2",
                    "car_pct": "16.4",
                    "leverage_ratio_pct": "7.5",
                    "total_capital_ghs": "260000000",
                    "total_rwa_ghs": "1585000000",
                },
                status="green",
                computed_from_input_hash="capital-live-hash",
                computed_at=datetime(2026, 4, 1, tzinfo=UTC),
            ),
            LiveMetric(
                organization_id=ORG_ID,
                bank_id=BANK_ID,
                reporting_period_id=period.id,
                module="liquidity",
                metrics={"lcr_pct": "132", "nsfr_pct": "118"},
                status="green",
                computed_from_input_hash="liquidity-live-hash",
                computed_at=datetime(2026, 4, 1, tzinfo=UTC),
            ),
            LiveMetric(
                organization_id=ORG_ID,
                bank_id=BANK_ID,
                reporting_period_id=period.id,
                module="irr",
                metrics={"worst_eve_change_pct_tier1": "8.5"},
                status="green",
                computed_from_input_hash="irr-live-hash",
                computed_at=datetime(2026, 4, 1, tzinfo=UTC),
            ),
            LiveMetric(
                organization_id=ORG_ID,
                bank_id=BANK_ID,
                reporting_period_id=period.id,
                module="fx",
                metrics={"nop_pct_tier1": "7.2"},
                status="green",
                computed_from_input_hash="fx-live-hash",
                computed_at=datetime(2026, 4, 1, tzinfo=UTC),
            ),
        ]
    )
    db_session.commit()

    live = implied_rating.compute_live(db_session, CTX, db_session.get(Bank, BANK_ID), period)

    assert live.metrics["pit_rating_grade"] == "ccc+"
    assert live.metrics["pit_pd_upper_pct"]
    # Driver metrics the live card depends on are present on the success path.
    assert live.metrics["pit_pd_central_pct"]
    assert live.metrics["ttc_pd_central_pct"]
    assert live.metrics["pit_systematic_factor"]
    assert live.metrics["key_driver_up"]
    assert live.metrics["key_driver_down"]
    assert live.input_hash is not None
    assert db_session.query(ImpliedRatingRun).count() == 1
