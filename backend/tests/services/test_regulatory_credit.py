"""Credit module service (credit PR-2): tier equality, hash discipline, failure-as-data."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalLoanEvent,
    IngestionBatch,
    LineageRecord,
    RegulatoryRun,
)
from app.schemas.regulatory_credit import CreditScenarioBatchCreate
from app.services import fact_derivation, regulatory_credit
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _prepare(db_session: Session) -> BankReportingPeriod:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    fact_derivation.derive_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == FIXTURE_AS_OF,
        )
    )
    assert period is not None
    return period


def _bank(db_session: Session) -> Bank:
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


def test_live_hash_equals_the_sealed_baseline_hash_on_an_unchanged_book(
    db_session: Session,
) -> None:
    """Freshness compares economics, not storage: both tiers hash the same
    value-based snapshot, so an unchanged book is exactly 'fresh'."""
    period = _prepare(db_session)
    bank = _bank(db_session)

    live = regulatory_credit.compute_live(db_session, CTX, bank, period)
    batch = regulatory_credit.run_all_credit_scenarios(
        db_session, CTX, SAMPLE_BANK_ID, CreditScenarioBatchCreate(reporting_period_id=period.id)
    )
    assert batch.runs[0].status == "succeeded"
    assert live.input_hash == batch.runs[0].input_hash
    assert regulatory_credit.current_input_hash(db_session, CTX, bank, period) == live.input_hash


def test_the_hash_is_value_based_and_insensitive_to_reclassification_noise(
    db_session: Session,
) -> None:
    """Re-deriving facts (which churns fact UUIDs) must not move the credit
    hash: the snapshot reads canonical LOAN values, never row identity."""
    period = _prepare(db_session)
    bank = _bank(db_session)
    first = regulatory_credit.current_input_hash(db_session, CTX, bank, period)
    fact_derivation.derive_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    assert regulatory_credit.current_input_hash(db_session, CTX, bank, period) == first


def test_live_metrics_carry_the_governed_limit_and_grade_rollup(db_session: Session) -> None:
    period = _prepare(db_session)
    bank = _bank(db_session)
    live = regulatory_credit.compute_live(db_session, CTX, bank, period)
    assert live.status in {"green", "amber", "red"}
    assert Decimal(live.metrics["npl_limit_pct"]) == Decimal("10")
    grades = {bucket["grade"] for bucket in live.metrics["grades"]}
    assert {"standard", "olem", "substandard", "doubtful", "loss"} <= grades
    # Optional figures are explicit None only when genuinely unavailable —
    # this book states provisions (PR-1 fixture), so coverage is a number.
    assert live.metrics["provision_coverage_pct"] is not None


def test_a_failed_run_is_persisted_data_not_an_exception(db_session: Session) -> None:
    """With no loan book, the official run seals a FAILED row naming
    ``no_loan_book`` — refusals are evidence, never a 500."""
    materialize_canonical_test_book(db_session)
    db_session.flush()
    # No canonical fixture: the bank exists with reporting periods but no book.
    period = db_session.scalar(
        select(BankReportingPeriod)
        .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
        .order_by(BankReportingPeriod.period_end.desc())
    )
    assert period is not None
    db_session.commit()
    batch = regulatory_credit.run_all_credit_scenarios(
        db_session, CTX, SAMPLE_BANK_ID, CreditScenarioBatchCreate(reporting_period_id=period.id)
    )
    run = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == batch.runs[0].id))
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "no_loan_book"


def _seed_events(db_session: Session, events: list[dict]) -> None:
    """Insert loan events through the canonical model, batch-style."""
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="API_PUSH",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=FIXTURE_AS_OF,
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="credit-events-test",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    for event in events:
        db_session.add(
            CanonicalLoanEvent(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                as_of_date=FIXTURE_AS_OF,
                source_system="API_PUSH",
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                validation_status="accepted",
                source_reference=event["ref"],
                event_type=event["type"],
                event_subtype=event.get("subtype"),
                event_date=date_type.fromisoformat(event["date"]),
                position_source_reference=event.get("position", "LOAN/GHS/PERF"),
                amount=Decimal(event["amount"]),
                currency=event.get("currency", "GHS"),
                amount_ghs=None,
                attributes={},
            )
        )
    db_session.commit()


def test_trailing_flows_window_and_fx_absence_discipline(db_session: Session) -> None:
    """12-month totals: inside-window events sum; an event outside the window
    is excluded; an unconverted FX event contributes nothing (never invented);
    a type with NO events reports None, not zero."""
    period = _prepare(db_session)
    bank = _bank(db_session)
    _seed_events(
        db_session,
        [
            {
                "ref": "E1",
                "type": "WRITE_OFF",
                "subtype": "non_wilful",
                "date": "2026-06-01",
                "amount": "1000",
            },
            {
                "ref": "E2",
                "type": "WRITE_OFF",
                "subtype": "wilful",
                "date": "2026-01-10",
                "amount": "500",
            },
            # Outside the trailing year ending at the fixture as-of.
            {
                "ref": "E3",
                "type": "WRITE_OFF",
                "subtype": "wilful",
                "date": "2024-01-01",
                "amount": "99999",
            },
            # Foreign currency with no stated conversion: excluded, not invented.
            {
                "ref": "E4",
                "type": "WRITE_OFF",
                "subtype": "wilful",
                "date": "2026-06-02",
                "amount": "777",
                "currency": "USD",
            },
        ],
    )
    live = regulatory_credit.compute_live(db_session, CTX, bank, period)
    assert Decimal(live.metrics["write_off_12m_ghs"]) == Decimal("1500")
    assert live.metrics["recovery_12m_ghs"] is None


def test_activity_read_groups_events_by_type_and_month(db_session: Session) -> None:
    _prepare(db_session)
    fact_derivation.derive_current_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    _seed_events(
        db_session,
        [
            {
                "ref": "A1",
                "type": "RESTRUCTURE",
                "subtype": "moratorium",
                "date": "2026-05-30",
                "amount": "100",
            },
            {
                "ref": "A2",
                "type": "WRITE_OFF",
                "subtype": "non_wilful",
                "date": "2026-06-15",
                "amount": "250",
            },
            {
                "ref": "A3",
                "type": "RECOVERY",
                "subtype": "unsecured",
                "date": "2026-06-20",
                "amount": "40",
            },
            {"ref": "A4", "type": "DISBURSEMENT", "date": "2026-06-01", "amount": "5000"},
        ],
    )
    read = regulatory_credit.get_credit_activity(db_session, CTX, SAMPLE_BANK_ID)
    assert [event.source_reference for event in read.restructures] == ["A1"]
    assert [event.source_reference for event in read.write_offs] == ["A2"]
    assert [event.source_reference for event in read.recoveries] == ["A3"]
    assert read.disbursement_count == 1
    june = next(flow for flow in read.monthly_flows if flow.month == "2026-06")
    assert june.write_offs_ghs == Decimal("250")
    assert june.recoveries_ghs == Decimal("40")
