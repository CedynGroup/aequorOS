"""Credit module service (credit PR-2): tier equality, hash discipline, failure-as-data."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryRun
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
    run = db_session.scalar(
        select(RegulatoryRun).where(RegulatoryRun.id == batch.runs[0].id)
    )
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "no_loan_book"
