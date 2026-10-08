"""Shared test-only capital registers and derived engine-fact fixtures."""

from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.capital.engine import CapitalFact
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
)
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.factories.canonical import seed_canonical_fixture

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
REPORTING_DATE = date(2026, 3, 31)


def push_register(db: Session, *rows: tuple[str, str, str]) -> None:
    """Store a capital_structure generation directly, as a pre-fix push would have."""
    batch = IngestionBatch(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=REPORTING_DATE,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=DEMO_ORG_ID,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="capital-structure-tiers",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    for index, (component, amount, tier) in enumerate(rows, start=1):
        db.add(
            CanonicalReferenceRow(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                dataset_kind="capital_structure",
                as_of_date=REPORTING_DATE,
                row_index=index,
                source_reference=f"CAPITAL/{index}",
                payload={"capital_component": component, "amount_ghs": amount, "tier": tier},
            )
        )
    db.flush()


def seed_book(db: Session) -> None:
    materialize_canonical_test_book(db)
    seed_canonical_fixture(
        db, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID, as_of=REPORTING_DATE
    )


def capital_engine_facts(
    db: Session, bank: Bank, period: BankReportingPeriod
) -> tuple[CapitalFact, ...]:
    """Read the derived fixture through ORM fields, without private service helpers."""
    rows = db.scalars(
        select(BankFinancialFact)
        .where(
            BankFinancialFact.organization_id == bank.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period.id,
        )
        .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
    )
    return tuple(
        CapitalFact(
            fact_group=row.fact_group,
            category=row.category,
            amount=Decimal(str(row.amount)),
            risk_weight_code=row.risk_weight_code,
            ccf_pct=Decimal(str(row.ccf_pct)) if row.ccf_pct is not None else None,
            income_year=row.income_year,
            capital_tier=row.capital_tier,
            is_deduction=row.is_deduction,
            side=cast(str | None, row.attributes.get("side")),
            ecl_coverage_complete=(
                row.attributes.get("ecl_coverage_complete") is True
                if "ecl_coverage_complete" in row.attributes
                else None
            ),
        )
        for row in rows
    )
