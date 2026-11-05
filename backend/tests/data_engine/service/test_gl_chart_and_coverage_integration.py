"""GL chart resolution and GL/sub-ledger coverage in the fact derivation.

These pin the three defects found on the primary database on 2026-08-21, each of
which put a wrong number on a filed balance sheet:

1. **A retired account code was resurrected forever.** The loader kept the
   newest row per account code across all history, so a code dropped from the
   chart kept contributing its last balance — 24m of phantom cash and 180m of
   phantom deposits on Sample Bank.
2. **A blank chart refresh silently republished last month's ledger.** The
   ``balance IS NOT NULL`` filter dropped an entire current generation that
   carried no balances, and the per-code fallback then served the prior
   period's figures with nothing said.
3. **The identity compared two different books.** GL securities were counted
   again from the position sub-ledger; the interbank asset leg was counted
   nowhere while its liability leg was counted; GL liabilities and GL equity
   were never read at all.

This module's other-suite cases live in ``test_gl_chart_and_coverage.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.live.service.fact_derivation import derive_facts
from app.models import BankFinancialFact, CanonicalGlAccount, IngestionBatch, LineageRecord
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1, USER_1


def _ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _seed(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)


def _add_gl_generation(
    db_session: Session, *, as_of: date, rows: list[tuple[str, str, str, str]]
) -> None:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        bank_id=batch.bank_id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="gl-chart-test",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    for code, name, account_class, balance in rows:
        db_session.add(
            CanonicalGlAccount(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                as_of_date=as_of,
                source_system="EXCEL_CSV",
                source_reference=f"GL/{code}/{as_of.isoformat()}",
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                validation_status="accepted",
                account_code=code,
                name=name,
                account_class=account_class,
                currency="GHS",
                balance=Decimal(balance),
            )
        )
    db_session.flush()


def test_an_older_retired_code_never_reaches_the_derived_balance_sheet(
    db_session: Session,
) -> None:
    _seed(db_session)
    # A code that existed in April and was dropped from the June chart. Under
    # the pre-fix rule its 24m walked straight into cash_vault.
    _add_gl_generation(
        db_session,
        as_of=date(2026, 4, 30),
        rows=[("1000", "Cash and balances", "ASSET", "24000000")],
    )

    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()

    facts = {
        fact.category: fact
        for fact in db_session.scalars(
            select(BankFinancialFact).where(
                BankFinancialFact.reporting_period_id == result.reporting_period_id,
                BankFinancialFact.fact_group == "balance_sheet",
            )
        )
    }
    assert facts["cash_vault"].amount == Decimal("5000000")
    assert any(
        "absent from the chart of accounts in force" in warning and "1000" in warning
        for warning in result.warnings
    )
