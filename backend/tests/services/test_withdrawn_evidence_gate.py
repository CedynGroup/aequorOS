"""A sealed run whose canonical inputs were withdrawn cannot pass as current (D-12).

`canonical_withdrawal.request_withdrawal` refuses an empty reason with the words
*"it removes data from every filed number derived for this date."* Nothing
guarded that statement: a ``RegulatoryRun`` sealed before the withdrawal kept its
snapshot, its ``input_hash`` and its metrics, and read back exactly like a run
whose evidence still stood.

It stopped being hypothetical on 2026-08-22, when a governed two-officer
withdrawal retired 150,314 ``DB_DIRECT`` position snapshots at 2026-06-30 on the
primary database. Measured read-only against that database: **117** sealed
succeeded runs across **eight** modules are orphaned (132 including the 15 failed
runs on the same date), and **28** further succeeded runs at the same date are
NOT — they sealed on 2026-07-16, before the duplicated book was ingested on
2026-07-18, so they never saw a withdrawn row.

That 117-versus-145 split is the ``first_ingested_at`` floor doing its only job,
and it is measured in SQL by ``withdrawal_impact._first_ingested_at``. The pure
half of the clause is pinned in ``tests/domain/authority/test_run_evidence.py``;
``test_a_run_sealed_before_the_duplicate_book_was_ingested_stays_current`` below
pins the measured half, because a floor that silently resolves to ``None`` would
fail closed on all 145 and look like caution rather than a bug.

The mechanism these tests pin is DERIVED, never stored — see
``app/domain/authority/evidence.py`` for why — and it appears in three places:

* every run read carries an ``evidence`` block;
* the single package-mint site refuses to bind an orphaned run;
* every post-mint filing act re-asks the question through
  ``filing_reconciliation.assert_package_reconciled``.

And, throughout: the sealed rows are never touched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.evidence import EvidenceStatus
from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalPositionSnapshot,
    CanonicalWithdrawal,
    IngestionBatch,
    LineageRecord,
    RegulatoryPackage,
    RegulatoryRun,
)
from app.services import canonical_withdrawal, regulatory_liquidity, withdrawal_impact
from app.services.withdrawal_impact import WithdrawnEvidenceError
from tests.api.helpers import ORG_1, USER_1, USER_2
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

SECOND_SOURCE = "API_PUSH"

#: Ordered so the sealed run sits strictly between the duplicate book's
#: ingestion and its withdrawal — the production shape.
INGESTED_AT = datetime(2026, 7, 15, 22, 0, tzinfo=UTC)
SEALED_AT = datetime(2026, 8, 12, 6, 6, 9, tzinfo=UTC)


def _ctx(user: UUID | None = USER_1) -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=user)


def _bank(db_session: Session) -> Bank:
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return bank


def _seed_book(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    db_session.flush()


def _seed_duplicate_second_source(db_session: Session, *, rows: int = 3) -> None:
    """A second system's loan book on the same date, ingested BEFORE the run seals."""
    from app.models import CanonicalPosition  # noqa: PLC0415 - local to the fixture

    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system=SECOND_SOURCE,
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
        operation_ref="d12-duplicate-book",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": FIXTURE_AS_OF,
        "source_system": SECOND_SOURCE,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }
    for index in range(rows):
        reference = f"D12-LOAN/{index}"
        position = CanonicalPosition(
            **common, source_reference=reference, position_type="LOAN", currency="GHS"
        )
        db_session.add(position)
        db_session.flush()
        db_session.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=reference,
                position_id=position.id,
                balance=Decimal("100"),
                attributes={"balance_ghs": "100"},
                ingested_at=INGESTED_AT,
            )
        )
    db_session.flush()


def _period(db_session: Session, period_end: date = FIXTURE_AS_OF) -> BankReportingPeriod:
    """The reporting period a run seals against.

    The canonical fixture's period spine stops before the canonical book's
    as-of, so the period for that date is created here rather than assumed.
    """
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ORG_1,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == period_end,
        )
    )
    if period is None:
        period = BankReportingPeriod(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            period_start=period_end.replace(day=1),
            period_end=period_end,
            label=f"{period_end.year:04d}-{period_end.month:02d}",
            status="open",
        )
        db_session.add(period)
        db_session.flush()
    return period


def _seal_run(
    db_session: Session,
    *,
    sealed_at: datetime = SEALED_AT,
    module: str = "liquidity",
) -> RegulatoryRun:
    """One immutable, succeeded run over the fixture's as-of date."""
    run = RegulatoryRun(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        reporting_period_id=_period(db_session).id,
        module=module,
        scenario_code="baseline",
        status="succeeded",
        engine_version="regulatory-liquidity-v1.0.0",
        input_schema_version="bank-facts-v2",
        output_schema_version="liquidity-metrics-v1",
        input_hash="d" * 64,
        inputs={"schema_version": "bank-facts-v2", "as_of_date": FIXTURE_AS_OF.isoformat()},
        metrics={"lcr_pct": "142.0"},
        started_at=sealed_at,
        completed_at=sealed_at,
        created_at=sealed_at,
        created_by=USER_1,
    )
    db_session.add(run)
    db_session.flush()
    return run


def _withdraw(db_session: Session) -> CanonicalWithdrawal:
    """The governed two-officer act, exactly as the API performs it."""
    row = canonical_withdrawal.request_withdrawal(
        db_session,
        _ctx(USER_1),
        _bank(db_session),
        entity="position",
        source_system=SECOND_SOURCE,
        as_of_date=FIXTURE_AS_OF,
        reason="Duplicate source book: this date was ingested twice.",
        requested_by="analyst@bank.test",
    )
    return canonical_withdrawal.approve_withdrawal(
        db_session, _ctx(USER_2), _bank(db_session), row.id, approved_by="cro@bank.test"
    )


def _naive(stamp: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on round-trip; compare the instants, not the shapes."""
    return stamp.replace(tzinfo=None) if stamp is not None else None


def _snapshot(run: RegulatoryRun | None) -> dict[str, object]:
    """Everything about a sealed run that must be identical afterwards."""
    assert run is not None
    return {
        "status": run.status,
        "input_hash": run.input_hash,
        "inputs": dict(run.inputs),
        "metrics": dict(run.metrics),
        "completed_at": _naive(run.completed_at),
        "updated_at": _naive(run.updated_at),
    }


# ---------------------------------------------------------------------------
# The derived status
# ---------------------------------------------------------------------------


def test_a_sealed_run_is_current_until_a_withdrawal_lands(db_session: Session) -> None:
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    assert withdrawal_impact.run_evidence(db_session, run).status is EvidenceStatus.CURRENT


def test_a_governed_withdrawal_orphans_the_run_it_preceded(db_session: Session) -> None:
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    before = _snapshot(run)

    withdrawal = _withdraw(db_session)
    assert withdrawal.status == "applied"
    assert withdrawal.rows_withdrawn == 3

    evidence = withdrawal_impact.run_evidence(db_session, run)
    assert evidence.status is EvidenceStatus.INPUTS_WITHDRAWN
    assert evidence.blocks_filing is True
    assert evidence.rows_withdrawn == 3
    assert [impact.withdrawal_id for impact in evidence.impacts] == [str(withdrawal.id)]

    # The finding's own constraint: the evidence is not mutated to say so.
    db_session.expire(run)
    assert _snapshot(db_session.get(RegulatoryRun, run.id)) == before


def test_a_run_sealed_before_the_duplicate_book_was_ingested_stays_current(
    db_session: Session,
) -> None:
    """The ``first_ingested_at`` floor, measured against a real database.

    This is the production 28: runs sealed for the same business date, before
    the duplicated book existed. They computed on a book that never contained a
    withdrawn row, so the withdrawal cannot orphan them — and refusing them
    would refuse filings that are genuinely clean.

    The pure rule already pins this clause. What this pins is the SQL behind it:
    ``withdrawal_impact._first_ingested_at`` reads ``min(ingested_at)`` over
    ``withdrawn_by_batch_id``, and if that measurement came back ``None`` the
    fail-closed branch would refuse every historical run for the date while
    looking exactly like prudence.
    """
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    earlier = _seal_run(db_session, sealed_at=INGESTED_AT - timedelta(days=14))
    later = _seal_run(db_session)
    _withdraw(db_session)

    assert withdrawal_impact.run_evidence(db_session, earlier).status is EvidenceStatus.CURRENT
    # ...and the run that DID see the duplicated book is still refused, so the
    # floor is discriminating rather than simply passing everything.
    assert withdrawal_impact.run_evidence(db_session, later).status is (
        EvidenceStatus.INPUTS_WITHDRAWN
    )

    # The floor is a real measurement, not an absent value falling through.
    register = withdrawal_impact.load_withdrawal_register(db_session, ORG_1, SAMPLE_BANK_ID)
    assert [record.first_ingested_at for record in register] == [INGESTED_AT]


def test_the_session_memo_is_dropped_by_both_governed_acts(db_session: Session) -> None:
    """No caller has to know the register is memoised for the answer to be right.

    ``load_withdrawal_register`` memoises on the ``Session`` because one request
    can assess many runs. The two acts that CHANGE the register therefore drop
    the memo themselves — otherwise a session that had already read a run would
    keep answering from the pre-approval register, and the filing gate would
    pass a run the approval had just orphaned.
    """
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    # Warm the memo BEFORE the act, which is the only way it can go stale.
    assert withdrawal_impact.run_evidence(db_session, run).status is EvidenceStatus.CURRENT

    withdrawal = _withdraw(db_session)
    assert withdrawal_impact.run_evidence(db_session, run).status is (
        EvidenceStatus.INPUTS_WITHDRAWN
    )
    with pytest.raises(WithdrawnEvidenceError):
        withdrawal_impact.assert_source_runs_current(
            db_session, [run], purpose="package_generation"
        )

    canonical_withdrawal.reverse_withdrawal(
        db_session,
        _ctx(USER_2),
        _bank(db_session),
        withdrawal.id,
        reversed_by="cro@bank.test",
        reason="The IT sign-off named the wrong system.",
    )
    assert withdrawal_impact.run_evidence(db_session, run).status is EvidenceStatus.CURRENT


def test_a_run_sealed_after_the_withdrawal_is_current(db_session: Session) -> None:
    """Re-running is the remedy, and the remedy is recognised."""
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    _withdraw(db_session)
    later = _seal_run(db_session, sealed_at=datetime.now(UTC))
    assert withdrawal_impact.run_evidence(db_session, later).status is EvidenceStatus.CURRENT


def test_reversing_the_withdrawal_restores_the_run_with_no_write_to_it(
    db_session: Session,
) -> None:
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    before = _snapshot(run)
    withdrawal = _withdraw(db_session)
    db_session.info.pop("withdrawal_impact.register", None)
    assert withdrawal_impact.run_evidence(db_session, run).blocks_filing is True

    canonical_withdrawal.reverse_withdrawal(
        db_session,
        _ctx(USER_2),
        _bank(db_session),
        withdrawal.id,
        reversed_by="cro@bank.test",
        reason="The IT sign-off named the wrong system.",
    )
    db_session.info.pop("withdrawal_impact.register", None)
    assert withdrawal_impact.run_evidence(db_session, run).status is EvidenceStatus.CURRENT
    db_session.expire(run)
    assert _snapshot(db_session.get(RegulatoryRun, run.id)) == before


# ---------------------------------------------------------------------------
# The read surface
# ---------------------------------------------------------------------------


def test_every_run_read_carries_the_evidence_block(db_session: Session) -> None:
    """A run detail read cannot present an orphaned run as current."""
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)

    clean = regulatory_liquidity.get_regulatory_run(db_session, _ctx(), SAMPLE_BANK_ID, run.id)
    assert clean.evidence.status == "current"
    assert clean.evidence.blocks_filing is False
    assert clean.evidence.reason is None

    _withdraw(db_session)
    db_session.info.pop("withdrawal_impact.register", None)
    orphaned = regulatory_liquidity.get_regulatory_run(db_session, _ctx(), SAMPLE_BANK_ID, run.id)
    assert orphaned.evidence.status == "inputs_withdrawn"
    assert orphaned.evidence.blocks_filing is True
    assert orphaned.evidence.reason is not None
    assert "must not be filed" in orphaned.evidence.reason
    assert orphaned.evidence.withdrawals[0].source_system == SECOND_SOURCE
    # The figures themselves are unchanged on the very same read.
    assert orphaned.input_hash == clean.input_hash
    assert orphaned.metrics == clean.metrics


def test_the_run_history_list_carries_it_too(db_session: Session) -> None:
    """A list page is where an orphaned run would most easily pass as current."""
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    _withdraw(db_session)
    db_session.info.pop("withdrawal_impact.register", None)

    listing = regulatory_liquidity.list_regulatory_runs(db_session, _ctx(), SAMPLE_BANK_ID)
    summary = next(item for item in listing.runs if item.id == run.id)
    assert summary.evidence.status == "inputs_withdrawn"
    assert summary.evidence.rows_withdrawn == 3


# ---------------------------------------------------------------------------
# The filing gate
# ---------------------------------------------------------------------------


def test_the_filing_gate_refuses_an_orphaned_source_run(db_session: Session) -> None:
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    _withdraw(db_session)
    db_session.info.pop("withdrawal_impact.register", None)

    with pytest.raises(WithdrawnEvidenceError) as refusal:
        withdrawal_impact.assert_source_runs_current(
            db_session, [run], purpose="package_generation"
        )
    # Doubly typed: a precise 409 for an API caller, NotComputable for an engine.
    assert refusal.value.status_code == 409
    assert isinstance(refusal.value, NotComputable)
    detail = refusal.value.details[0]
    assert detail.state is OutcomeState.DATA_QUALITY_BLOCK
    assert detail.blocks_filing is True
    assert "withdrawn under two-officer approval" in detail.reason


def test_the_filing_gate_passes_a_current_run_and_an_empty_binding(
    db_session: Session,
) -> None:
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    withdrawal_impact.assert_source_runs_current(db_session, [run], purpose="package_generation")
    # A master-data return binds no engine run; there is nothing to orphan.
    withdrawal_impact.assert_source_runs_current(db_session, [], purpose="package_generation")


def test_the_package_gate_reads_the_binding_off_the_minted_package(
    db_session: Session,
) -> None:
    """The between-mint-and-file window: a package minted before the withdrawal."""
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    package = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        return_family="liquidity",
        return_code="LCR-NSFR",
        reporting_date=FIXTURE_AS_OF,
        frequency="monthly",
        basis="solo",
        status="generated",
        version=1,
        snapshot={"sections": []},
        source_runs=[{"module": "liquidity", "run_id": str(run.id)}],
        generated_by=USER_1,
        generated_at=SEALED_AT,
        snapshot_sha256="e" * 64,
    )
    db_session.add(package)
    db_session.flush()

    withdrawal_impact.assert_package_source_runs_current(
        db_session, package, purpose="package_approval"
    )
    _withdraw(db_session)
    db_session.info.pop("withdrawal_impact.register", None)
    with pytest.raises(WithdrawnEvidenceError):
        withdrawal_impact.assert_package_source_runs_current(
            db_session, package, purpose="package_approval"
        )


def test_a_package_binding_an_unresolvable_run_is_not_refused(db_session: Session) -> None:
    """A lookup problem is not a data-integrity refusal."""
    _seed_book(db_session)
    package = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        return_family="liquidity",
        return_code="LCR-NSFR",
        reporting_date=FIXTURE_AS_OF,
        frequency="monthly",
        basis="solo",
        status="generated",
        version=1,
        snapshot={"sections": []},
        source_runs=[{"module": "liquidity", "run_id": str(uuid4())}, {"module": "capital"}],
        generated_by=USER_1,
        generated_at=SEALED_AT,
        snapshot_sha256="f" * 64,
    )
    db_session.add(package)
    db_session.flush()
    withdrawal_impact.assert_package_source_runs_current(
        db_session, package, purpose="package_approval"
    )


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_a_withdrawal_at_another_date_leaves_the_run_alone(db_session: Session) -> None:
    """Position derivation binds the exact as-of, so an adjacent date is clean."""
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    run = _seal_run(db_session)
    _withdraw(db_session)
    db_session.info.pop("withdrawal_impact.register", None)

    other_period = db_session.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == ORG_1,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end < FIXTURE_AS_OF,
        )
        .order_by(BankReportingPeriod.period_end.desc())
    )
    assert other_period is not None
    run.inputs = {**run.inputs, "as_of_date": other_period.period_end.isoformat()}
    db_session.flush()
    assert withdrawal_impact.run_evidence(db_session, run).status is EvidenceStatus.CURRENT
