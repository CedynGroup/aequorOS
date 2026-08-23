"""The resolution layer: system-of-record register + canonical withdrawal.

Two findings drove this, both verified against the codebase and against the
primary database before a line was written:

* the platform prescribed a remedy it could not perform — the duplicated-book
  diagnosis says "withdraw the other system's data for this date" and
  ``CanonicalPosition.superseded_by`` was never assigned anywhere (0 of 571,984
  rows on the primary), because supersession can only retire a row by naming its
  REPLACEMENT and a duplicated book has none;
* a *balanced* duplicate passes every control — when two systems duplicate the
  loans AND the matching deposits, assets and liabilities inflate together, the
  balance-sheet identity holds, and a wrong CAR/LCR is computed on doubled
  inputs. The primary database holds exactly this: BK-0PMD7Z5M at 2026-06-30
  carries LOAN, SECURITY_HOLDING, DEPOSIT, INTERBANK_BORROWING and
  INTERBANK_PLACEMENT books from BOTH API_PUSH and DB_DIRECT.

What these tests pin:

* a single-source bank is asked for NOTHING — the register is consulted only for
  contested types, so absence never blocks;
* a contested type without a declaration is reported as undeclared, and WITH one
  becomes a named rule violation against the non-authoritative system;
* the register is four-eyed, effective-dated, and citation-bearing;
* a core-banking migration resolves to different systems on either side of the
  cutover, from the same register;
* a WITHDRAWN position snapshot is invisible to ``_load_canonical`` — the fact
  plane every filed number is derived from;
* a withdrawal is IMPOSSIBLE without a reason and an approver, at the service
  layer and again at the database;
* nothing withdraws automatically;
* withdrawal is append-only and reversible, and both acts are in lineage and
  audit.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    AuditEvent,
    Bank,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalWithdrawal,
    IngestionBatch,
    LineageRecord,
    SystemOfRecordDeclaration,
)
from app.services import canonical_withdrawal, system_of_record
from app.services.canonical_withdrawal import WithdrawalError
from app.services.fact_derivation import _load_canonical, diagnose_source_overlap
from app.services.system_of_record import SystemOfRecordError
from tests.api.helpers import ORG_1, USER_1, USER_2
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

SECOND_SOURCE = "API_PUSH"
FIXTURE_SOURCE = "EXCEL_CSV"


def _ctx(user=USER_1) -> TenantContext:  # noqa: ANN001 - UUID | None
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
    """A SECOND system's loan book on top of the fixture's Excel/CSV book.

    Tiny in amount and substantial in record count, so it reproduces a duplicated
    book without moving the fixture's identity gap. Supersession is scoped per
    source system, so the fixture's own loan rows survive untouched — which is
    exactly the condition the register exists to adjudicate.
    """
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
        operation_ref="second-source-fixture",
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
        reference = f"DUP-LOAN/{index}"
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
            )
        )
    db_session.flush()


def _reingest_second_source(db_session: Session) -> None:
    """The withdrawn feed comes back for the same date, exactly as ingestion does.

    Position IDENTITIES are dateless and are never withdrawn, so re-ingestion
    reuses them and writes fresh SNAPSHOTS for the same ``(position, as_of)`` —
    which the current-generation unique index now permits precisely because it
    excludes the withdrawn rows.
    """
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
        operation_ref="second-source-reingest",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    positions = db_session.scalars(
        select(CanonicalPosition).where(
            CanonicalPosition.organization_id == ORG_1,
            CanonicalPosition.source_system == SECOND_SOURCE,
        )
    ).all()
    assert positions
    for position in positions:
        db_session.add(
            CanonicalPositionSnapshot(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                as_of_date=FIXTURE_AS_OF,
                source_system=SECOND_SOURCE,
                source_reference=position.source_reference,
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                validation_status="accepted",
                position_id=position.id,
                balance=Decimal("100"),
                attributes={"balance_ghs": "100"},
            )
        )
    db_session.flush()


def _declare(
    db_session: Session,
    *,
    source_system: str = FIXTURE_SOURCE,
    position_type: str = "LOAN",
    effective_from: date = date(2026, 1, 1),
) -> SystemOfRecordDeclaration:
    """Propose and approve a declaration under genuine four eyes."""
    row = system_of_record.propose(
        db_session,
        _ctx(USER_1),
        _bank(db_session),
        position_type=position_type,
        source_system=source_system,
        effective_from=effective_from,
        source_citation="IT sign-off ITSO-2026-014",
        rationale="Core banking is the book of record for the lending portfolio.",
        proposed_by="analyst@bank.test",
    )
    return system_of_record.approve(
        db_session, _ctx(USER_2), row.id, approved_by="cro@bank.test"
    )


# ---------------------------------------------------------------------------
# Absence must not fail closed
# ---------------------------------------------------------------------------


def test_single_source_bank_is_never_asked_for_a_declaration(db_session: Session) -> None:
    """The property that makes the register safe to ship.

    A bank whose entire book arrives from one system has no ambiguity to
    resolve. The register is consulted ONLY for contested types, so it produces
    no findings, no warnings and no obligation. A register that blocked every
    single-source bank would be worse than none.
    """
    _seed_book(db_session)

    overlap = diagnose_source_overlap(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    assessment = system_of_record.assess(
        db_session, _ctx(), _bank(db_session), FIXTURE_AS_OF, overlap
    )

    assert overlap.determined is True
    assert overlap.overlapping is False
    assert assessment.clean is True
    assert assessment.findings == ()
    assert assessment.message() is None
    assert assessment.detail() is None
    # And nothing was recorded merely by asking.
    assert db_session.scalar(select(func.count()).select_from(SystemOfRecordDeclaration)) == 0


def test_contested_type_without_a_declaration_is_reported_as_undeclared(
    db_session: Session,
) -> None:
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)

    overlap = diagnose_source_overlap(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    assessment = system_of_record.assess(
        db_session, _ctx(), _bank(db_session), FIXTURE_AS_OF, overlap
    )

    assert overlap.overlapping is True
    assert [f.position_type for f in assessment.undeclared] == ["LOAN"]
    assert assessment.violations == ()
    assert "no system of record has been declared" in (assessment.message() or "")


def test_declaration_turns_a_heuristic_into_a_named_rule_violation(db_session: Session) -> None:
    """The point of the whole register.

    Before: "these two books look duplicated". After: "the LOAN book arrived
    from API_PUSH, which is not the declared book of record".
    """
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    declaration = _declare(db_session, source_system=FIXTURE_SOURCE)

    overlap = diagnose_source_overlap(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    assessment = system_of_record.assess(
        db_session, _ctx(), _bank(db_session), FIXTURE_AS_OF, overlap
    )

    assert [f.position_type for f in assessment.violations] == ["LOAN"]
    finding = assessment.violations[0]
    assert finding.declared_source_system == FIXTURE_SOURCE
    assert finding.declaration_id == declaration.id
    assert [book.source_system for book in finding.offending] == [SECOND_SOURCE]
    assert finding.offending_rows == 3
    message = assessment.message() or ""
    assert SECOND_SOURCE in message
    assert "declared book of record" in message

    # The outcome is ADVISORY: the register explains and attributes; the
    # balance-sheet identity control remains the only thing that stops a filing.
    detail = assessment.detail()
    assert detail is not None
    assert detail.advisory is True


# ---------------------------------------------------------------------------
# Governance of the register
# ---------------------------------------------------------------------------


def test_a_draft_declaration_does_not_resolve_and_cannot_be_self_approved(
    db_session: Session,
) -> None:
    _seed_book(db_session)
    row = system_of_record.propose(
        db_session,
        _ctx(USER_1),
        _bank(db_session),
        position_type="LOAN",
        source_system=FIXTURE_SOURCE,
        effective_from=date(2026, 1, 1),
        source_citation="IT sign-off ITSO-2026-014",
        rationale="Core banking owns the lending portfolio.",
        proposed_by="analyst@bank.test",
    )

    assert row.status == "draft"
    assert system_of_record.resolve(db_session, ORG_1, SAMPLE_BANK_ID, FIXTURE_AS_OF) == {}

    with pytest.raises(SystemOfRecordError) as same_name:
        system_of_record.approve(
            db_session, _ctx(USER_2), row.id, approved_by="analyst@bank.test"
        )
    assert "second approver" in same_name.value.detail

    with pytest.raises(SystemOfRecordError) as same_user:
        system_of_record.approve(db_session, _ctx(USER_1), row.id, approved_by="cro@bank.test")
    assert "second approver" in same_user.value.detail

    approved = system_of_record.approve(
        db_session, _ctx(USER_2), row.id, approved_by="cro@bank.test"
    )
    assert approved.status == "approved"
    assert system_of_record.resolve(db_session, ORG_1, SAMPLE_BANK_ID, FIXTURE_AS_OF)[
        "LOAN"
    ].id == row.id


def test_a_declaration_requires_a_citation_and_a_rationale(db_session: Session) -> None:
    _seed_book(db_session)
    for citation, rationale in (("   ", "why"), ("cite", "  ")):
        with pytest.raises(SystemOfRecordError):
            system_of_record.propose(
                db_session,
                _ctx(),
                _bank(db_session),
                position_type="LOAN",
                source_system=FIXTURE_SOURCE,
                effective_from=date(2026, 1, 1),
                source_citation=citation,
                rationale=rationale,
                proposed_by="analyst@bank.test",
            )


def test_the_database_refuses_an_approved_declaration_with_no_approver(
    db_session: Session,
) -> None:
    """Four-eyes is enforced below the service layer too."""
    _seed_book(db_session)
    db_session.add(
        SystemOfRecordDeclaration(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            position_type="LOAN",
            source_system=FIXTURE_SOURCE,
            effective_from=date(2026, 1, 1),
            source_citation="cite",
            rationale="why",
            confirmation_status="pending",
            status="approved",
            proposed_by="analyst@bank.test",
            proposed_at=date.today(),  # noqa: DTZ011
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_core_migration_resolves_differently_on_either_side_of_the_cutover(
    db_session: Session,
) -> None:
    """The reason this must not auto-resolve.

    During a core-banking migration both systems are live and both books are
    real. The register dates the answer instead of guessing it: the legacy core
    owns the book until cutover, the new core from it, and approval closes the
    prior window rather than deleting it.
    """
    _seed_book(db_session)
    cutover = date(2026, 6, 1)
    legacy = _declare(
        db_session, source_system=FIXTURE_SOURCE, effective_from=date(2026, 1, 1)
    )
    new_core = _declare(db_session, source_system=SECOND_SOURCE, effective_from=cutover)

    day_before = cutover - timedelta(days=1)
    before = system_of_record.resolve(db_session, ORG_1, SAMPLE_BANK_ID, day_before)
    after = system_of_record.resolve(db_session, ORG_1, SAMPLE_BANK_ID, cutover)

    assert before["LOAN"].source_system == FIXTURE_SOURCE
    assert after["LOAN"].source_system == SECOND_SOURCE
    db_session.refresh(legacy)
    assert legacy.effective_to == cutover, "the prior window is closed, not deleted"
    assert new_core.effective_to is None


def test_revoking_a_declaration_removes_it_from_resolution_without_deleting_it(
    db_session: Session,
) -> None:
    _seed_book(db_session)
    declaration = _declare(db_session)

    system_of_record.revoke(
        db_session,
        _ctx(USER_2),
        declaration.id,
        revoked_by="cro@bank.test",
        reason="The IT sign-off named the wrong system.",
    )

    assert system_of_record.resolve(db_session, ORG_1, SAMPLE_BANK_ID, FIXTURE_AS_OF) == {}
    assert db_session.get(SystemOfRecordDeclaration, declaration.id) is not None
    with pytest.raises(SystemOfRecordError):
        system_of_record.revoke(
            db_session, _ctx(USER_2), declaration.id, revoked_by="x", reason="again"
        )


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------


def _request_and_approve(db_session: Session, **overrides) -> CanonicalWithdrawal:
    payload = {
        "entity": "position",
        "source_system": SECOND_SOURCE,
        "as_of_date": FIXTURE_AS_OF,
        "reason": "API_PUSH duplicates the declared LOAN book of record.",
        "requested_by": "analyst@bank.test",
        "position_type": "LOAN",
    }
    payload.update(overrides)
    row = canonical_withdrawal.request_withdrawal(
        db_session, _ctx(USER_1), _bank(db_session), **payload
    )
    return canonical_withdrawal.approve_withdrawal(
        db_session, _ctx(USER_2), _bank(db_session), row.id, approved_by="cro@bank.test"
    )


def test_a_withdrawn_position_is_invisible_to_load_canonical(db_session: Session) -> None:
    """The load-bearing test: withdrawal actually removes the book from the facts.

    ``_load_canonical`` is the population every derived fact, every regulatory
    run and every filed number is built from. A withdrawal that did not reach it
    would be governance theatre.
    """
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    _declare(db_session, source_system=FIXTURE_SOURCE)

    before = _load_canonical(db_session, _ctx(), _bank(db_session), FIXTURE_AS_OF)
    duplicated = [row for row in before.positions if row.source_system == SECOND_SOURCE]
    assert len(duplicated) == 3, "the duplicate book is present before withdrawal"

    withdrawal = _request_and_approve(db_session)
    assert withdrawal.status == "applied"
    assert withdrawal.rows_withdrawn == 3

    after = _load_canonical(db_session, _ctx(), _bank(db_session), FIXTURE_AS_OF)
    assert [row for row in after.positions if row.source_system == SECOND_SOURCE] == []
    # The declared book of record is untouched — withdrawal retires ONE system's
    # book, never the bank's whole position set.
    assert [row.source_reference for row in after.positions if row.source_system == FIXTURE_SOURCE]

    # Nothing was deleted: the rows are still there, carrying their evidence.
    withdrawn_rows = db_session.scalars(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.organization_id == ORG_1,
            CanonicalPositionSnapshot.withdrawn_at.is_not(None),
        )
    ).all()
    assert len(withdrawn_rows) == 3
    batch_id = withdrawal.withdrawal_batch_id
    assert all(row.withdrawn_by_batch_id == batch_id for row in withdrawn_rows)
    assert all(row.withdrawal_reason == withdrawal.reason for row in withdrawn_rows)
    assert all(row.superseded_by is None for row in withdrawn_rows), (
        "withdrawal is not supersession — there is no replacement row"
    )

    # And the diagnosis that prescribed the remedy now reports a clean book.
    overlap = diagnose_source_overlap(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    assert overlap.determined is True
    assert overlap.overlapping is False


def test_a_withdrawal_cannot_happen_without_a_reason_and_an_approver(
    db_session: Session,
) -> None:
    """The governance floor, proved at three layers."""
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    bank = _bank(db_session)

    # 1. No reason — refused at the request step, before anything exists.
    with pytest.raises(WithdrawalError) as blank_reason:
        canonical_withdrawal.request_withdrawal(
            db_session,
            _ctx(USER_1),
            bank,
            entity="position",
            source_system=SECOND_SOURCE,
            as_of_date=FIXTURE_AS_OF,
            reason="   ",
            requested_by="analyst@bank.test",
            position_type="LOAN",
        )
    assert "non-empty reason" in blank_reason.value.detail

    row = canonical_withdrawal.request_withdrawal(
        db_session,
        _ctx(USER_1),
        bank,
        entity="position",
        source_system=SECOND_SOURCE,
        as_of_date=FIXTURE_AS_OF,
        reason="Duplicate of the declared book of record.",
        requested_by="analyst@bank.test",
        position_type="LOAN",
    )
    # A pending request stamps NOTHING.
    assert row.status == "pending"
    assert row.rows_withdrawn == 0
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(CanonicalPositionSnapshot)
            .where(CanonicalPositionSnapshot.withdrawn_at.is_not(None))
        )
        == 0
    )

    # 2. No approver, and no SELF-approval — by name or by user id.
    with pytest.raises(WithdrawalError):
        canonical_withdrawal.approve_withdrawal(
            db_session, _ctx(USER_2), bank, row.id, approved_by="  "
        )
    with pytest.raises(WithdrawalError) as same_name:
        canonical_withdrawal.approve_withdrawal(
            db_session, _ctx(USER_2), bank, row.id, approved_by="analyst@bank.test"
        )
    assert "second approver" in same_name.value.detail
    with pytest.raises(WithdrawalError) as same_user:
        canonical_withdrawal.approve_withdrawal(
            db_session, _ctx(USER_1), bank, row.id, approved_by="cro@bank.test"
        )
    assert "second approver" in same_user.value.detail
    pending = db_session.get(CanonicalWithdrawal, row.id)
    assert pending is not None
    assert pending.status == "pending"

    # 3. The database refuses an applied withdrawal with no approver, whatever a
    #    future code path forgets to check.
    db_session.rollback()
    db_session.add(
        CanonicalWithdrawal(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_system=SECOND_SOURCE,
            as_of_date=FIXTURE_AS_OF,
            entity="position",
            reason="no approver",
            status="applied",
            requested_by="analyst@bank.test",
            requested_at=date.today(),  # noqa: DTZ011
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_database_refuses_a_withdrawal_with_a_blank_reason(db_session: Session) -> None:
    _seed_book(db_session)
    db_session.add(
        CanonicalWithdrawal(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_system=SECOND_SOURCE,
            as_of_date=FIXTURE_AS_OF,
            entity="position",
            reason="   ",
            status="pending",
            requested_by="analyst@bank.test",
            requested_at=date.today(),  # noqa: DTZ011
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_withdrawal_whose_scope_matches_nothing_is_refused(db_session: Session) -> None:
    """"Withdrawn" must never quietly mean "matched nothing"."""
    _seed_book(db_session)
    with pytest.raises(WithdrawalError) as exc:
        canonical_withdrawal.request_withdrawal(
            db_session,
            _ctx(),
            _bank(db_session),
            entity="position",
            source_system=SECOND_SOURCE,
            as_of_date=FIXTURE_AS_OF,
            reason="Retire a book that is not there.",
            requested_by="analyst@bank.test",
        )
    assert "nothing to withdraw" in exc.value.detail


def test_withdrawal_emits_the_supersession_lineage_node_and_audit_trail(
    db_session: Session,
) -> None:
    """``SUPERSESSION`` has been declared since the Data Engine shipped, unused."""
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    withdrawal = _request_and_approve(db_session)

    batch = db_session.get(IngestionBatch, withdrawal.withdrawal_batch_id)
    assert batch is not None
    assert batch.adapter_version == canonical_withdrawal.WITHDRAWAL_ADAPTER_VERSION
    assert batch.validation_report["kind"] == "withdrawal"

    node = db_session.scalars(
        select(LineageRecord).where(LineageRecord.ingestion_batch_id == batch.id)
    ).one()
    assert node.operation_type == "SUPERSESSION"
    assert node.operation_ref == f"withdrawal/{withdrawal.id}"
    assert node.details["reason"] == withdrawal.reason
    assert node.details["withdrawn_source_batch_ids"]

    events = {
        event.event_type
        for event in db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_type == "canonical_withdrawal")
        )
    }
    assert events == {
        canonical_withdrawal.REQUESTED_EVENT,
        canonical_withdrawal.APPROVED_EVENT,
    }


def test_withdrawal_is_reversible_and_append_only(db_session: Session) -> None:
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    withdrawal = _request_and_approve(db_session)

    reversed_row = canonical_withdrawal.reverse_withdrawal(
        db_session,
        _ctx(USER_2),
        _bank(db_session),
        withdrawal.id,
        reversed_by="cro@bank.test",
        reason="The IT sign-off named the wrong system; API_PUSH is the book of record.",
    )

    assert reversed_row.status == "reversed"
    assert reversed_row.rows_restored == 3
    # The evidence survives: the record, both batches, both lineage nodes.
    assert reversed_row.rows_withdrawn == 3
    assert reversed_row.withdrawal_batch_id is not None
    assert reversed_row.reversal_batch_id is not None
    nodes = db_session.scalars(
        select(LineageRecord).where(LineageRecord.operation_type == "SUPERSESSION")
    ).all()
    assert {node.operation_ref for node in nodes} == {
        f"withdrawal/{withdrawal.id}",
        f"withdrawal-reversal/{withdrawal.id}",
    }

    restored = _load_canonical(db_session, _ctx(), _bank(db_session), FIXTURE_AS_OF)
    assert len([row for row in restored.positions if row.source_system == SECOND_SOURCE]) == 3

    with pytest.raises(WithdrawalError):
        canonical_withdrawal.reverse_withdrawal(
            db_session,
            _ctx(USER_2),
            _bank(db_session),
            withdrawal.id,
            reversed_by="cro@bank.test",
            reason="again",
        )


def test_reversal_is_refused_when_it_would_resurrect_a_duplicate(db_session: Session) -> None:
    """A withdrawn book that has since been re-ingested cannot be restored.

    Restoring it would put two live records on one natural key — the exact
    double-count the withdrawal removed.
    """
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    withdrawal = _request_and_approve(db_session)
    _reingest_second_source(db_session)

    with pytest.raises(WithdrawalError) as exc:
        canonical_withdrawal.reverse_withdrawal(
            db_session,
            _ctx(USER_2),
            _bank(db_session),
            withdrawal.id,
            reversed_by="cro@bank.test",
            reason="restore",
        )
    assert "same natural key" in exc.value.detail


def test_nothing_withdraws_automatically(db_session: Session) -> None:
    """No detector, resolver or assessment may retire data.

    The overlap detector is advisory and the register only names the violation;
    both run over a duplicated book here and neither stamps a thing.
    """
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    _declare(db_session, source_system=FIXTURE_SOURCE)

    overlap = diagnose_source_overlap(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    assessment = system_of_record.assess(
        db_session, _ctx(), _bank(db_session), FIXTURE_AS_OF, overlap
    )
    assert assessment.violations

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(CanonicalPositionSnapshot)
            .where(CanonicalPositionSnapshot.withdrawn_at.is_not(None))
        )
        == 0
    )
    assert db_session.scalar(select(func.count()).select_from(CanonicalWithdrawal)) == 0


def test_withdrawal_does_not_touch_another_source_systems_book(db_session: Session) -> None:
    """Withdrawal is not cross-source supersession.

    Ingestion scopes supersession per source system deliberately — a bank
    legitimately splits its book across systems — and this feature must not
    widen it.
    """
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    fixture_loans_before = db_session.scalar(
        select(func.count())
        .select_from(CanonicalPositionSnapshot)
        .where(
            CanonicalPositionSnapshot.organization_id == ORG_1,
            CanonicalPositionSnapshot.source_system == FIXTURE_SOURCE,
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
        )
    )

    _request_and_approve(db_session)

    assert (
        db_session.scalar(
            select(func.count())
            .select_from(CanonicalPositionSnapshot)
            .where(
                CanonicalPositionSnapshot.organization_id == ORG_1,
                CanonicalPositionSnapshot.source_system == FIXTURE_SOURCE,
                CanonicalPositionSnapshot.withdrawn_at.is_(None),
            )
        )
        == fixture_loans_before
    )
