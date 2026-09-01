"""Credit module service (credit PR-2): tier equality, hash discipline, failure-as-data."""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalLoanEvent,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
    RegulatoryRun,
)
from app.schemas.regulatory_credit import CreditScenarioBatchCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import fact_derivation, regulatory_credit
from app.services.regulatory_reporting import generation as reporting_generation
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


def _seed_prior_month_book(db_session: Session) -> None:
    """A minimal prior-month LOAN book (May month-end) mirroring three of the
    fixture's June loans plus one that will 'depart'."""
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=date_type(2026, 5, 31),
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="prior-month-book",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": date_type(2026, 5, 31),
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }
    # Loans that also exist in the June fixture (same source refs), so they
    # MATCH across the two dates, plus one May-only loan that departs.
    refs = {
        "LOAN/1": ("1000000", 0, 1),
        # Performing in May (60 DPD, stage 2); the June fixture carries this
        # facility at stage 3 → a performing→npl flow.
        "LOAN/6": ("3200000", 60, 2),
        "MAY-ONLY": ("500000", 0, 1),
    }
    for ref, (balance, dpd, stage) in refs.items():
        position = db_session.scalar(
            select(CanonicalPosition).where(
                CanonicalPosition.source_reference == ref,
                CanonicalPosition.superseded_by.is_(None),
            )
        )
        if position is None:
            position = CanonicalPosition(
                **common, source_reference=ref, position_type="LOAN", currency="GHS"
            )
            db_session.add(position)
            db_session.flush()
        db_session.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=ref,
                position_id=position.id,
                balance=Decimal(balance),
                ifrs9_stage=stage,
                attributes={"balance_ghs": balance, "days_past_due": dpd},
            )
        )
    db_session.commit()


def test_migration_view_flows_between_two_month_ends(db_session: Session) -> None:
    _prepare(db_session)
    fact_derivation.derive_current_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    _seed_prior_month_book(db_session)
    read = regulatory_credit.get_credit_migration(db_session, CTX, SAMPLE_BANK_ID)
    assert read.available is True
    assert read.opening_as_of == "2026-05-31"
    # The May-only loan departed; June-only fixture loans entered.
    assert read.exit_loan_count == 1
    assert read.entry_loan_count >= 1
    # LOAN/GHS/NPL was performing in May (60 DPD) and is stage-3/NPL in June:
    # a performing→npl flow must exist.
    flows = {(cell.from_state, cell.to_state) for cell in read.matrix}
    assert ("performing", "npl") in flows
    # Roll rates only cover matched loans with DPD on both dates.
    assert all(cell.rate_pct >= 0 for cell in read.roll_rates)


def test_migration_without_a_prior_month_is_soft_unavailable(db_session: Session) -> None:
    _prepare(db_session)
    fact_derivation.derive_current_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    read = regulatory_credit.get_credit_migration(db_session, CTX, SAMPLE_BANK_ID)
    assert read.available is False
    assert "previous month" in (read.reason or "")
    assert read.matrix == []


def test_npl_monthly_return_generates_from_the_sealed_run(db_session: Session) -> None:
    """The NPL-MONTHLY package (credit PR-6): levels from the sealed baseline
    credit run; event-driven sections omitted WITH the omission stated when no
    events are ingested; migration omitted on a single month-end."""
    period = _prepare(db_session)
    batch = regulatory_credit.run_all_credit_scenarios(
        db_session, CTX, SAMPLE_BANK_ID, CreditScenarioBatchCreate(reporting_period_id=period.id)
    )
    assert batch.runs[0].status == "succeeded"

    package = reporting_generation.generate_package(
        db_session,
        CTX,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="NPL-MONTHLY", reporting_date=FIXTURE_AS_OF),
    )
    snapshot = package.snapshot
    sections = {section["code"]: section for section in snapshot["sections"]}
    levels = {row["code"]: row for row in sections["npl_levels"]["rows"]}
    assert Decimal(levels["total_gross_loans_ghs"]["value"]) > 0
    assert Decimal(levels["npl_ratio_pct"]["value"]) > 0
    # PR-1 fixture states provisions, so coverage rows are present.
    assert "npl_coverage_pct" in levels

    metadata = snapshot["metadata"]
    omissions = " ".join(metadata["omissions"])
    assert "migration" in omissions.lower()
    assert "write-off" in omissions.lower()
    assert metadata["baseline_credit_input_hash"] == batch.runs[0].input_hash
    assert "credit_migration" not in sections
    assert "write_offs" not in sections


def test_npl_monthly_refuses_without_a_sealed_credit_run(db_session: Session) -> None:
    _prepare(db_session)
    with pytest.raises(HTTPException) as raised:
        reporting_generation.generate_package(
            db_session,
            CTX,
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(return_code="NPL-MONTHLY", reporting_date=FIXTURE_AS_OF),
        )
    assert raised.value.status_code == 409


def test_vintages_build_cohorts_with_origination_coverage(db_session: Session) -> None:
    """Three seeded month-ends → cohort curves; the fixture loans carrying an
    origination date form cohorts, and coverage discloses the rest."""
    from datetime import date as dt

    period = _prepare(db_session)
    fact_derivation.derive_current_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    _seed_prior_month_book(db_session)
    # A third month-end (April) so the availability floor is met.
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=dt(2026, 4, 30),
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="april-book",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    position = db_session.scalar(
        select(CanonicalPosition).where(
            CanonicalPosition.source_reference == "LOAN/1",
            CanonicalPosition.superseded_by.is_(None),
        )
    )
    assert position is not None
    if position.origination_date is None:
        position.origination_date = dt(2026, 1, 15)
    db_session.add(
        CanonicalPositionSnapshot(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            as_of_date=dt(2026, 4, 30),
            source_system="EXCEL_CSV",
            ingestion_batch_id=batch.id,
            lineage_id=lineage.id,
            validation_status="accepted",
            source_reference="LOAN/1",
            position_id=position.id,
            balance=Decimal("990000"),
            ifrs9_stage=1,
            attributes={"balance_ghs": "990000", "days_past_due": 0},
        )
    )
    db_session.commit()

    read = regulatory_credit.get_credit_vintages(db_session, CTX, SAMPLE_BANK_ID)
    assert read.available is True
    assert read.months_observed == 3
    jan = next((c for c in read.cohorts if c.cohort == "2026-01"), None)
    assert jan is not None
    # LOAN/1 observed at April (MOB 3), May (4) and June (5).
    ages = {p.months_on_book for p in jan.points}
    assert {3, 4, 5} <= ages
    assert read.origination_coverage_pct is not None


def test_vintages_below_three_months_is_soft_unavailable(db_session: Session) -> None:
    _prepare(db_session)
    fact_derivation.derive_current_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    read = regulatory_credit.get_credit_vintages(db_session, CTX, SAMPLE_BANK_ID)
    assert read.available is False
    assert "three month-end" in (read.reason or "")
