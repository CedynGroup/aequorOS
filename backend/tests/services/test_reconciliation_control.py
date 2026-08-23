"""The fail-closed reconciliation control (enterprise audit 2026-08-20, P0-10).

What these tests pin, in the order the audit raised it:

* a material ``assets ≠ liabilities + equity`` gap BLOCKS the official fact
  plane — no reporting period, no facts, no run, and therefore no package,
  certification or filing;
* the tolerance is GOVERNED — it resolves from the regulatory-parameter control
  plane and is stamped with its provenance, not hardcoded;
* a sub-tolerance plug is still applied but is never silent: it lands in the
  fact's provenance and in the structured outcome;
* the escape valve is governed — reason, requester, named approver, timestamp,
  effective window, breach ceiling, revocation, audit event — and a gap beyond
  the exception's ceiling still blocks;
* the live plane keeps materialising (an operator must see the broken book) but
  reports ``blocks_filing``.
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.domain.ingestion.constants import POSITION_TYPES, SOURCE_SYSTEMS
from app.models import (
    AuditEvent,
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
    LiveMetric,
    RegulatoryPackage,
    RegulatoryParameter,
    RegulatoryRun,
)
from app.schemas.regulatory_capital import CapitalScenarioBatchCreate
from app.schemas.regulatory_liquidity import LiquidityScenarioBatchCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import (
    job_queue,
    live_view,
    pipeline,
    reconciliation,
    regulatory_capital,
    regulatory_liquidity,
)
from app.services.fact_derivation import (
    DerivationError,
    ReconciliationBlockedError,
    derive_current_facts,
    derive_facts,
    diagnose_source_overlap,
)
from app.services.regulatory_reporting import generation
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.factories.reconciliation import (
    FIXTURE_APPROVER,
    allow_fixture_balance_gap,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

#: The compact canonical fixture's known, deliberate defect: 15.28m GHS of
#: funding is absent against a 146.85m balance sheet. ``gap = funding - assets``,
#: so the sign says WHICH side is short — here, the funding side.
#:
#: These moved from 17.28m / 148.85m / 11.609002% on 2026-08-21 for ONE reason:
#: the fixture's ``1399 Loan Loss Provisions (contra)`` of -2m is now carried in
#: ``other_assets`` instead of being discarded with the loan GL block. The loan
#: sub-ledger is GROSS, so nothing was ever standing in for the contra and total
#: assets were overstated by it (148.85m → 146.85m). No other fixture figure
#: moved — deposits, loans, securities, cash and capital are unchanged, which is
#: why the whole shift lands in ``other_assets`` (12m → 10m) and shrinks the gap
#: by exactly the same 2m.
FIXTURE_GAP = Decimal("-15280000.000000")
FIXTURE_PLUG = Decimal("15280000.000000")
FIXTURE_GAP_FRACTION = Decimal("0.10405175")


def _ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _bank(db_session: Session) -> Bank:
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return bank


def _seed_book(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)


def _seed_duplicate_second_source(db_session: Session) -> None:
    """Push a SECOND system's loan book on top of the fixture's Excel/CSV book.

    Deliberately tiny in amount (300 GHS against a 146.85m balance sheet) and
    substantial in record count (3 rows against the fixture's 7 loans): it
    reproduces a duplicated book WITHOUT moving the fixture's identity gap past
    its governed exception ceiling, so this test exercises the diagnosis rather
    than the block. Supersession is scoped per source system, so the fixture's
    own loan rows survive untouched — which is the whole point.
    """
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
        operation_ref="second-source-fixture",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": FIXTURE_AS_OF,
        "source_system": "API_PUSH",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }
    for index in range(3):
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


def _revoke_fixture_exception(db_session: Session) -> None:
    """Withdraw the fixture's standing exception to expose the raw book."""
    grant = reconciliation.active_exception(db_session, ORG_1, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    assert grant is not None, "the fixture must ship with its governed exception"
    reconciliation.revoke_exception(
        db_session,
        _ctx(),
        _bank(db_session),
        grant.exception_id,
        revoked_by="test_supervisor",
        reason="Expose the raw fixture book for the fail-closed assertions.",
    )
    db_session.flush()


# ---------------------------------------------------------------------------
# The block
# ---------------------------------------------------------------------------


def test_material_gap_blocks_the_official_fact_plane(db_session: Session) -> None:
    """Audit P0-10: the identity is now blocked, not plugged."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)

    with pytest.raises(DerivationError) as excinfo:
        derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    assert excinfo.value.code == reconciliation.BALANCE_IDENTITY_BLOCK_CODE
    message = excinfo.value.message
    assert "Balance-sheet identity failed" in message
    assert str(FIXTURE_PLUG) in message
    # The refusal names the governed tolerance it was measured against.
    assert "governed tolerance" in message


def test_the_refusal_speaks_ws_a_fail_closed_vocabulary(db_session: Session) -> None:
    """The block is a WS-A ``NotComputable``, not a bespoke exception."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)

    with pytest.raises(ReconciliationBlockedError) as excinfo:
        derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    error = excinfo.value
    # Every existing ``except DerivationError`` handler still catches it...
    assert isinstance(error, DerivationError)
    # ...and so does any fail-closed boundary.
    assert isinstance(error, NotComputable)
    assert error.state is OutcomeState.RECONCILIATION_FAILED
    assert error.blocks_filing is True
    detail = error.details[0]
    assert detail.metric_id == reconciliation.BALANCE_IDENTITY_METRIC_ID
    assert detail.context["bank_id"] == SAMPLE_BANK_ID
    assert detail.context["code"] == reconciliation.BALANCE_IDENTITY_BLOCK_CODE
    assert Decimal(detail.context["gap"]) == FIXTURE_GAP


def test_blocked_derivation_leaves_no_official_facts_behind(db_session: Session) -> None:
    """A refused book must not look like a successful official derivation."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)
    before_periods = _period_count(db_session)

    with pytest.raises(DerivationError):
        derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    assert _period_count(db_session) == before_periods
    facts = db_session.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.organization_id == ORG_1,
            BankFinancialFact.bank_id == SAMPLE_BANK_ID,
        )
    ).all()
    assert [fact for fact in facts if fact.attributes.get("source") == "data_engine"] == []


def _period_count(db_session: Session) -> int:
    return len(
        db_session.scalars(
            select(BankReportingPeriod).where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
        ).all()
    )


def test_every_official_check_writes_an_audit_event(db_session: Session) -> None:
    """Pass or block, the verdict is answerable from ``audit_events`` alone."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)
    with pytest.raises(DerivationError):
        derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.flush()

    events = _events(db_session, reconciliation.RECONCILIATION_CHECK_EVENT)
    assert len(events) == 1
    details = events[0].details
    assert details["status"] == "blocked"
    assert details["outcome"] == "reconciliation_failed"
    assert Decimal(details["gap"]) == FIXTURE_GAP
    assert details["tolerance"]["param_code"] == reconciliation.TOLERANCE_PARAM_CODE


def _events(db_session: Session, event_type: str) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == ORG_1,
                AuditEvent.event_type == event_type,
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# The governed tolerance
# ---------------------------------------------------------------------------


def test_tolerance_resolves_from_the_control_plane_with_provenance(
    db_session: Session,
) -> None:
    """The tolerance is a governed, effective-dated parameter, not a constant."""
    _seed_book(db_session)
    tolerance = reconciliation.resolve_tolerance(db_session, _bank(db_session), FIXTURE_AS_OF)

    assert tolerance.source == "control_plane"
    assert tolerance.param_code == reconciliation.TOLERANCE_PARAM_CODE
    assert tolerance.scope_type == "institution_class"
    assert tolerance.parameter_id
    assert tolerance.effective_from is not None
    provenance = tolerance.provenance()
    assert provenance["source"] == "control_plane"
    assert provenance["parameter_id"] == tolerance.parameter_id


def test_widening_the_governed_tolerance_admits_the_book(db_session: Session) -> None:
    """Changing the governed number changes the verdict — nothing else does."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)
    _set_tolerance_pct(db_session, Decimal("15"))

    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    assert result.reconciliation is not None
    assert result.reconciliation.within_tolerance is True
    assert result.reconciliation.blocks_filing is False
    assert result.reconciliation.tolerance.percent == Decimal("15")


def test_module_default_applies_and_is_labelled_when_unseeded(db_session: Session) -> None:
    """An unseeded control plane never silently becomes 'no tolerance'."""
    _seed_book(db_session)
    for row in _tolerance_rows(db_session):
        db_session.delete(row)
    db_session.flush()

    tolerance = reconciliation.resolve_tolerance(db_session, _bank(db_session), FIXTURE_AS_OF)
    assert tolerance.source == "module_default"
    assert tolerance.percent == reconciliation.MODULE_DEFAULT_TOLERANCE_PCT
    assert tolerance.provenance()["module_default_version"] == (
        reconciliation.MODULE_DEFAULT_VERSION
    )


def _tolerance_rows(db_session: Session) -> list[RegulatoryParameter]:
    return list(
        db_session.scalars(
            select(RegulatoryParameter).where(
                RegulatoryParameter.param_code == reconciliation.TOLERANCE_PARAM_CODE
            )
        ).all()
    )


def _set_tolerance_pct(db_session: Session, percent: Decimal) -> None:
    rows = _tolerance_rows(db_session)
    assert rows, "the tolerance parameter must be seeded"
    for row in rows:
        row.value_numeric = percent
    db_session.flush()


# ---------------------------------------------------------------------------
# The plug is never silent
# ---------------------------------------------------------------------------


def test_sub_tolerance_plug_is_recorded_in_fact_provenance(db_session: Session) -> None:
    """Audit P0-10: below 0.5% the pre-audit plug was ENTIRELY silent."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)
    _set_tolerance_pct(db_session, Decimal("15"))

    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    plugged = db_session.scalar(
        select(BankFinancialFact).where(
            BankFinancialFact.reporting_period_id == result.reporting_period_id,
            BankFinancialFact.fact_group == "balance_sheet",
            BankFinancialFact.category == "term_borrowings_gt_1y",
        )
    )
    assert plugged is not None
    record = plugged.attributes["reconciliation"]
    assert record["status"] == "within_tolerance"
    assert Decimal(record["plug"]["amount"]) == FIXTURE_PLUG
    assert record["plug"]["target"] == "term_borrowings_gt_1y"
    assert record["tolerance"]["source"] == "control_plane"
    # And the plug is reported at any size, not only above a threshold.
    assert any("was plugged" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# The governed escape valve
# ---------------------------------------------------------------------------


def test_approved_exception_admits_the_book_and_is_recorded(db_session: Session) -> None:
    _seed_book(db_session)  # ships with the fixture's governed exception

    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    assert result.reconciliation is not None
    outcome = result.reconciliation
    assert outcome.status == "exception_applied"
    assert outcome.blocks_filing is False
    assert outcome.exception is not None
    assert outcome.exception.approved_by == FIXTURE_APPROVER
    assert outcome.exception.reason
    assert outcome.gap_fraction == FIXTURE_GAP_FRACTION

    applied = _events(db_session, reconciliation.RECONCILIATION_APPLIED_EVENT)
    assert len(applied) == 1
    assert applied[0].details["exception"]["exception_id"] == outcome.exception.exception_id
    granted = _events(db_session, reconciliation.RECONCILIATION_GRANTED_EVENT)
    assert granted and granted[0].details["approved_by"] == FIXTURE_APPROVER


def test_exception_ceiling_still_blocks_a_wider_gap(db_session: Session) -> None:
    """An exception acknowledges a BOUNDED defect; it does not disable the control."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)
    allow_fixture_balance_gap(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        actor_user_id=USER_1,
        max_gap_fraction=Decimal("0.05"),  # below the fixture's 11.61% gap
    )

    with pytest.raises(DerivationError) as excinfo:
        derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    assert "exceeds the ceiling of the active reconciliation exception" in excinfo.value.message


def test_expired_exception_no_longer_admits_the_book(db_session: Session) -> None:
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)
    allow_fixture_balance_gap(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        actor_user_id=USER_1,
        effective_from=FIXTURE_AS_OF - timedelta(days=60),
        effective_to=FIXTURE_AS_OF - timedelta(days=1),
    )

    with pytest.raises(DerivationError):
        derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)


def test_revoked_exception_no_longer_admits_the_book(db_session: Session) -> None:
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)

    with pytest.raises(DerivationError):
        derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    revoked = _events(db_session, reconciliation.RECONCILIATION_REVOKED_EVENT)
    assert len(revoked) == 1
    assert revoked[0].details["revoked_by"] == "test_supervisor"


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"reason": "   "}, "non-empty reason"),
        ({"max_gap_fraction": Decimal("0")}, "positive max_gap_fraction"),
        ({"approved_by": ""}, "named approver"),
        (
            {"effective_to": date(2000, 1, 1), "effective_from": date(2026, 1, 1)},
            "effective_to precedes",
        ),
    ],
)
def test_exception_governance_rules_are_enforced(
    db_session: Session, kwargs: dict[str, object], fragment: str
) -> None:
    _seed_book(db_session)
    base: dict[str, object] = {
        "reason": "documented defect",
        "approved_by": "supervisor",
        "max_gap_fraction": Decimal("0.2"),
        "effective_from": date(2026, 1, 1),
    }
    base.update(kwargs)
    with pytest.raises(reconciliation.ReconciliationExceptionError) as excinfo:
        reconciliation.grant_exception(db_session, _ctx(), _bank(db_session), **base)  # type: ignore[arg-type]
    assert fragment in str(excinfo.value)


def test_exception_cannot_be_self_approved(db_session: Session) -> None:
    """Four eyes: the officer who asks for the exception may not grant it."""
    _seed_book(db_session)
    with pytest.raises(reconciliation.ReconciliationExceptionError) as excinfo:
        reconciliation.grant_exception(
            db_session,
            _ctx(),
            _bank(db_session),
            reason="known defect",
            approved_by="the same officer",
            approved_by_user_id=USER_1,
            max_gap_fraction=Decimal("0.2"),
            effective_from=date(2026, 1, 1),
        )
    assert "second approver is required" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The filing gate and the live plane
# ---------------------------------------------------------------------------


def test_filing_gate_refuses_a_material_gap(db_session: Session) -> None:
    """The seam the reporting workflow calls before minting/certifying/filing."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)
    with pytest.raises(reconciliation.FilingBlockedError) as excinfo:
        reconciliation.assert_filing_reconciled(
            db_session,
            _ctx(),
            _bank(db_session),
            FIXTURE_AS_OF,
            Decimal("100000000"),
            Decimal("80000000"),
        )
    error = excinfo.value
    assert error.provenance["status"] == "blocked"
    assert error.outcome is OutcomeState.RECONCILIATION_FAILED
    assert reconciliation.blocks_filing(error.outcome)
    # Doubly typed: a precise 409 for an API caller, NotComputable for a
    # fail-closed boundary (the sdi_capital pattern).
    assert isinstance(error, NotComputable)
    assert error.status_code == 409


def test_filing_gate_passes_a_balanced_book(db_session: Session) -> None:
    _seed_book(db_session)
    outcome = reconciliation.assert_filing_reconciled(
        db_session,
        _ctx(),
        _bank(db_session),
        FIXTURE_AS_OF,
        Decimal("100000000"),
        Decimal("100000000"),
    )
    assert outcome.blocks_filing is False
    assert outcome.status == "within_tolerance"


def test_live_plane_materialises_but_reports_the_block(db_session: Session) -> None:
    """The dashboard must show the broken book; the FILING is what is barred."""
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)

    result = derive_current_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    assert result.facts_created > 0
    assert result.reconciliation is not None
    assert result.reconciliation.blocks_filing is True
    assert result.reconciliation.status == "blocked"
    assert any("Balance-sheet identity failed" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# Source-book overlap: the DIAGNOSIS behind an identity failure
# ---------------------------------------------------------------------------
#
# The identity control catches the symptom — "your book is out by x%". It
# cannot say WHY, and the commonest why on a real tenant is that two source
# systems each pushed a complete book at the same as-of. Supersession is scoped
# per (bank, source_system, source_ref) BY DESIGN — a bank legitimately splits
# its book across a core and a treasury system, and a cross-source supersession
# would delete the other system's data — so the platform must keep both books
# and tell the operator, not silently pick one.
#
# These tests pin the definition of "overlapping" and, just as importantly, the
# silence: a single-source book and a properly partitioned two-source book must
# produce nothing at all.


def _tolerance(percent: str = "0.10") -> reconciliation.ResolvedTolerance:
    value = Decimal(percent)
    return reconciliation.ResolvedTolerance(
        fraction=value / Decimal("100"),
        percent=value,
        source="module_default",
        param_code=reconciliation.TOLERANCE_PARAM_CODE,
        module_default_version=reconciliation.MODULE_DEFAULT_VERSION,
    )


def _book(source: str, position_type: str, rows: int, total: str) -> reconciliation.SourceBook:
    return reconciliation.SourceBook(
        source_system=source, position_type=position_type, rows=rows, total=Decimal(total)
    )


def test_tally_counts_and_totals_per_source_and_type() -> None:
    """Step 1 of the diagnosis: the counts-and-totals table, per source."""
    books = reconciliation.tally_source_books(
        [
            ("API_PUSH", "LOAN", Decimal("100")),
            ("API_PUSH", "LOAN", Decimal("50")),
            ("DB_DIRECT", "LOAN", Decimal("70")),
            ("API_PUSH", "DEPOSIT", Decimal("20")),
        ]
    )
    assert {(b.source_system, b.position_type, b.rows, b.total) for b in books} == {
        ("API_PUSH", "DEPOSIT", 1, Decimal("20")),
        ("API_PUSH", "LOAN", 2, Decimal("150")),
        ("DB_DIRECT", "LOAN", 1, Decimal("70")),
    }


def test_single_source_book_reports_no_overlap() -> None:
    """One source system can never contest itself — and must not be accused."""
    result = reconciliation.detect_source_overlap(
        [_book("API_PUSH", "LOAN", 400, "1000"), _book("API_PUSH", "DEPOSIT", 600, "1200")],
        tolerance=_tolerance(),
    )
    assert result.determined is True
    assert result.overlapping is False
    assert result.contested == ()
    assert result.message("GHS") is None
    assert result.detail("BK-X", "OR-X", FIXTURE_AS_OF, "GHS") is None


def test_complementary_sources_partition_the_book_and_stay_silent() -> None:
    """The legitimate split — core owns loans, treasury owns securities."""
    result = reconciliation.detect_source_overlap(
        [
            _book("T24", "LOAN", 400, "1000000"),
            _book("T24", "DEPOSIT", 900, "1500000"),
            _book("DB_DIRECT", "SECURITY_HOLDING", 30, "800000"),
        ],
        tolerance=_tolerance(),
    )
    assert result.overlapping is False
    assert result.source_systems == ("DB_DIRECT", "T24")
    assert result.message("GHS") is None


def test_two_sources_reporting_the_same_type_is_the_signal() -> None:
    """Both systems report LOAN at the same as-of: that is the overlap."""
    result = reconciliation.detect_source_overlap(
        [
            _book("API_PUSH", "LOAN", 400, "1000000"),
            _book("DB_DIRECT", "LOAN", 300, "700000"),
            _book("API_PUSH", "DEPOSIT", 900, "1500000"),
        ],
        tolerance=_tolerance(),
    )
    assert result.overlapping is True
    assert [contest.position_type for contest in result.contested] == ["LOAN"]
    contest = result.contested[0]
    # Largest book first: the incumbent, then the candidates for retirement.
    assert [book.source_system for book in contest.books] == ["API_PUSH", "DB_DIRECT"]
    assert reconciliation.REASON_MATERIAL_AMOUNT in contest.reasons
    # DEPOSIT has one source and is never named.
    assert "deposits" not in (result.message("GHS") or "")


def test_immaterial_second_book_inside_a_type_is_not_an_accusation() -> None:
    """A handful of rows worth a rounding amount is a slice, not a second book.

    Both materiality axes must miss: 3 rows of 500 against 400 rows of 1,000,000
    is 0.7% of the type's records and far under the governed amount floor.
    """
    result = reconciliation.detect_source_overlap(
        [
            _book("API_PUSH", "LOAN", 400, "1000000"),
            _book("MANUAL", "LOAN", 3, "500"),
        ],
        tolerance=_tolerance(),
    )
    assert result.overlapping is False
    assert result.message("GHS") is None


def test_amount_floor_is_the_governed_identity_tolerance() -> None:
    """The floor is not a second invented constant: it moves with the control plane.

    The second book is 0.30% of the whole position book. Under the 0.10% module
    default it is material; under a governed 0.50% tolerance it is not — and the
    record axis is kept out of the way (1 row against 400).
    """
    books = [_book("API_PUSH", "LOAN", 400, "997000"), _book("DB_DIRECT", "LOAN", 1, "3000")]
    assert reconciliation.detect_source_overlap(books, tolerance=_tolerance("0.10")).overlapping
    loose = reconciliation.detect_source_overlap(books, tolerance=_tolerance("0.50"))
    assert loose.overlapping is False
    assert loose.amount_floor == Decimal("5000.00")


def test_record_share_axis_catches_a_book_with_no_base_currency_amount() -> None:
    """The amount axis alone would dismiss a duplicated off-balance-sheet book.

    Guarantees ingested in a foreign currency with no stated base-currency
    amount total exactly zero. Dropping the type for want of a measurable
    amount would be the silent substitution this module exists to stop, so the
    record-count axis reports it and the prose says the size is unknown.
    """
    result = reconciliation.detect_source_overlap(
        [
            _book("API_PUSH", "LC_GUARANTEE", 35, "354347195.09"),
            _book("DB_DIRECT", "LC_GUARANTEE", 40, "0"),
            _book("API_PUSH", "LOAN", 400, "1000000000"),
        ],
        tolerance=_tolerance(),
    )
    assert [contest.position_type for contest in result.contested] == ["LC_GUARANTEE"]
    contest = result.contested[0]
    assert contest.reasons == (reconciliation.REASON_MATERIAL_RECORD_SHARE,)
    assert [book.source_system for book in contest.unmeasured] == ["DB_DIRECT"]
    message = result.message("GHS")
    assert message is not None
    assert "how much it adds cannot be measured" in message


def test_a_material_middle_book_is_not_hidden_by_a_tiny_third_feed() -> None:
    """Every challenger is tested, not just the smallest.

    Ranking the sides and testing only the last one lets a genuine second book
    (400 rows, 0.5% of the position book) escape behind a one-row correction
    feed that is immaterial on both axes.
    """
    result = reconciliation.detect_source_overlap(
        [
            _book("T24", "LOAN", 1000, "99000"),
            _book("DB_DIRECT", "LOAN", 400, "900"),
            _book("MANUAL", "LOAN", 1, "50"),
        ],
        tolerance=_tolerance(),
    )
    # The tiny feed clears NEITHER axis on its own (50 against a 99.95 floor,
    # 1 row of 1,401), so testing only the smallest side would report nothing.
    assert (
        reconciliation.detect_source_overlap(
            [_book("T24", "LOAN", 1000, "99000"), _book("MANUAL", "LOAN", 1, "50")],
            tolerance=_tolerance(),
        ).overlapping
        is False
    )
    assert [contest.position_type for contest in result.contested] == ["LOAN"]
    contest = result.contested[0]
    assert contest.incumbent.source_system == "T24"
    assert [book.source_system for book in contest.challengers] == ["DB_DIRECT", "MANUAL"]
    assert reconciliation.REASON_MATERIAL_AMOUNT in contest.reasons


def test_no_position_data_is_not_determined_never_clean() -> None:
    """ "Not assessed" is never dressed up as "no overlap"."""
    result = reconciliation.detect_source_overlap([], tolerance=_tolerance())
    assert result.determined is False
    assert result.overlapping is False
    assert result.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert result.provenance()["status"] == "not_determined"
    message = result.message("GHS")
    assert message is not None
    assert "could not be checked" in message


def test_the_diagnosis_never_blocks_a_filing() -> None:
    """It explains the gate; it is not a second gate.

    ``BLOCKING_STATES`` is every declared state, so the only thing keeping a
    fail-closed boundary from refusing on this outcome is ``advisory=True``.
    """
    result = reconciliation.detect_source_overlap(
        [_book("API_PUSH", "LOAN", 400, "1000000"), _book("DB_DIRECT", "LOAN", 300, "700000")],
        tolerance=_tolerance(),
    )
    detail = result.detail("BK-X", "OR-X", FIXTURE_AS_OF, "GHS")
    assert detail is not None
    assert detail.advisory is True
    assert detail.blocks_filing is False
    assert detail.metric_id == reconciliation.SOURCE_OVERLAP_METRIC_ID


def test_message_names_the_systems_types_and_both_sides_totals() -> None:
    """What the operator needs to decide WHICH book to retire — in plain words."""
    result = reconciliation.detect_source_overlap(
        [
            _book("API_PUSH", "DEPOSIT", 107704, "2124737718.12"),
            _book("DB_DIRECT", "DEPOSIT", 139169, "1755325611.21"),
        ],
        tolerance=_tolerance(),
    )
    message = result.message("GHS")
    assert message is not None
    # Operator-facing names, never the wire enum values.
    assert "API push" in message
    assert "direct database connection" in message
    assert "API_PUSH" not in message
    assert "DB_DIRECT" not in message
    assert "DEPOSIT" not in message
    assert "deposits" in message
    # Both sides' counts and totals, so the operator can see which book is which.
    assert "2124737718.12 GHS over 107704 positions" in message
    assert "1755325611.21 GHS over 139169 positions" in message
    assert "withdraw the other system's data for this date" in message


def test_labels_never_leak_a_wire_enum_or_a_retired_vendor_brand() -> None:
    assert reconciliation.source_system_label("DB_DIRECT") == "direct database connection"
    assert reconciliation.source_system_label("REFINITIV") == "LSEG (formerly Refinitiv)"
    assert reconciliation.position_type_label("LC_GUARANTEE") == "guarantees and letters of credit"
    # Every declared value has a hand-written label; the fallback is a safety
    # net for a value added later, not a licence to skip one.
    for code in SOURCE_SYSTEMS:
        assert code in reconciliation.SOURCE_SYSTEM_LABELS
    for code in POSITION_TYPES:
        assert code in reconciliation.POSITION_TYPE_LABELS


def test_overlap_is_absent_from_the_identity_provenance_stamped_on_facts() -> None:
    """``input_hash`` discipline: the diagnosis must not reach a fact's attributes.

    ``BalanceIdentityOutcome.provenance()`` is stamped onto the plugged fact,
    and the FTP and FX input snapshots hash fact attributes verbatim. A
    diagnosis landing there would move ``input_hash`` for books whose numbers
    had not changed.
    """
    policy = reconciliation.ReconciliationPolicy(tolerance=_tolerance(), exception=None)
    identity, _plug, _target = policy.evaluate_balance_identity(
        Decimal("100000000"), Decimal("100000000")
    )
    assert "source_overlap" not in identity.provenance()


def test_derivation_reports_the_overlap_and_the_audit_event_carries_it(
    db_session: Session,
) -> None:
    """End to end on the fixture book, through the seams that already exist."""
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)

    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    assert result.source_overlap is not None
    assert result.source_overlap.overlapping is True
    assert [c.position_type for c in result.source_overlap.contested] == ["LOAN"]
    # Surfaced through the balance-sheet group's warnings — the seam the
    # activation response and the live view already read.
    overlap_warnings = [w for w in result.warnings if "counted twice" in w]
    assert len(overlap_warnings) == 1
    assert "Excel/CSV upload" in overlap_warnings[0]
    assert "API push" in overlap_warnings[0]

    # ... and through the reconciliation control's existing audit event, on the
    # SAME record as the identity verdict it explains.
    event = db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == reconciliation.RECONCILIATION_CHECK_EVENT)
        .order_by(AuditEvent.created_at.desc())
    ).first()
    assert event is not None
    overlap = event.details["source_overlap"]
    assert overlap["status"] == "overlapping"
    assert overlap["control"] == reconciliation.CONTROL_SOURCE_BOOK_OVERLAP
    assert sorted(overlap["source_systems"]) == ["API_PUSH", "EXCEL_CSV"]
    assert overlap["contested"][0]["position_type"] == "LOAN"


def test_derivation_stays_silent_on_the_single_source_fixture(db_session: Session) -> None:
    """The negative case: the untouched fixture has ONE source and must not fire.

    A detector that speaks on a healthy book is worse than no detector, so this
    is pinned as hard as the positive case.
    """
    _seed_book(db_session)

    result = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    assert result.source_overlap is not None
    assert result.source_overlap.determined is True
    assert result.source_overlap.overlapping is False
    assert not [w for w in result.warnings if "counted twice" in w]
    event = db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == reconciliation.RECONCILIATION_CHECK_EVENT)
        .order_by(AuditEvent.created_at.desc())
    ).first()
    assert event is not None
    assert "source_overlap" not in event.details


def test_diagnose_source_overlap_is_read_only_and_answers_for_a_bank_and_as_of(
    db_session: Session,
) -> None:
    _seed_book(db_session)
    _seed_duplicate_second_source(db_session)
    db_session.flush()

    def counts() -> tuple[int | None, int | None, int | None]:
        return (
            db_session.scalar(select(func.count()).select_from(BankFinancialFact)),
            db_session.scalar(select(func.count()).select_from(BankReportingPeriod)),
            db_session.scalar(select(func.count()).select_from(AuditEvent)),
        )

    before = counts()

    result = diagnose_source_overlap(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)

    assert result.overlapping is True
    # Nothing derived, no period opened, no audit event: a diagnosis is a read.
    assert counts() == before

    # An as-of with no book is NOT determined — never reported as clean.
    empty = diagnose_source_overlap(
        db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF + timedelta(days=1)
    )
    assert empty.determined is False
    assert empty.state is OutcomeState.MISSING_REQUIRED_INPUT


# ---------------------------------------------------------------------------
# The wiring: a built control with no caller controls nothing
# ---------------------------------------------------------------------------
#
# The 2026-08-22 independent forensic re-audit (D-1..D-3) found this control
# COMPLETE and UNWIRED: ``assert_filing_reconciled`` had zero production
# callers, the official-run path short-circuited the only check that existed,
# and the live plane computed the verdict and discarded it. The defect was never
# in the logic — every test above passed throughout — so the tests below pin the
# CALL SITES, not the arithmetic. They are the ones that fail if the control is
# ever unwired again.


#: (module path, enclosing function, dotted call that must appear inside it).
#: Deleting or relocating any of these out of its function fails
#: ``test_every_filing_path_reaches_the_reconciliation_gate``.
_FILING_GATE_CALL_SITES: tuple[tuple[str, str, str], ...] = (
    # The gate itself, reached from the one module that knows how to feed it.
    (
        "app/services/filing_reconciliation.py",
        "_gate",
        "reconciliation.assert_filing_reconciled",
    ),
    # Package mint — the ARCH-8 eligibility pattern, same single site.
    (
        "app/services/regulatory_reporting/generation.py",
        "_generate_package",
        "filing_reconciliation.assert_filing_reconciled",
    ),
    # Approval, certification, transmission.
    (
        "app/services/regulatory_reporting/workflow.py",
        "decide_approval",
        "filing_reconciliation.assert_package_reconciled",
    ),
    (
        "app/services/regulatory_reporting/workflow.py",
        "submit_package",
        "filing_reconciliation.assert_package_reconciled",
    ),
    (
        "app/services/regulatory_reporting/workflow.py",
        "submit_package_via_channel",
        "filing_reconciliation.assert_package_reconciled",
    ),
    (
        "app/services/attestation/signing.py",
        "certify",
        "filing_reconciliation.assert_package_reconciled",
    ),
    # The per-module official-run mints the audit found wide open (D-3b).
    (
        "app/services/regulatory_capital.py",
        "create_capital_run",
        "filing_reconciliation.assert_filing_reconciled",
    ),
    (
        "app/services/regulatory_capital.py",
        "run_all_capital_scenarios",
        "filing_reconciliation.assert_filing_reconciled",
    ),
    (
        "app/services/regulatory_liquidity.py",
        "create_liquidity_run",
        "filing_reconciliation.assert_filing_reconciled",
    ),
    (
        "app/services/regulatory_liquidity.py",
        "run_all_liquidity_scenarios",
        "filing_reconciliation.assert_filing_reconciled",
    ),
    # The other three official-run mints. The audit named capital and liquidity;
    # the reasoning — an immutable run is filing evidence — is the same here.
    (
        "app/services/regulatory_irr.py",
        "run_all_irr_scenarios",
        "filing_reconciliation.assert_filing_reconciled",
    ),
    (
        "app/services/regulatory_fx.py",
        "run_all_fx_scenarios",
        "filing_reconciliation.assert_filing_reconciled",
    ),
    (
        "app/services/regulatory_ftp.py",
        "run_all_ftp_scenarios",
        "filing_reconciliation.assert_filing_reconciled",
    ),
    # The scheduled official run's fact short-circuit (D-3a).
    (
        "app/services/pipeline.py",
        "run_official",
        "filing_reconciliation.assert_filing_reconciled",
    ),
)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _dotted_call_names(node: ast.AST) -> set[str]:
    """Every ``a.b(...)`` / ``b(...)`` callee name reachable inside ``node``."""
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            names.add(f"{func.value.id}.{func.attr}")
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


#: Sentinel ``function`` value meaning "anywhere in the module", for a call site
#: that lives at module scope (a router registration is a wiring act too — an
#: endpoint that no router includes is exactly as unreachable as an uncalled
#: function).
MODULE_SCOPE = "<module>"


def _calls_within(module_path: str, function: str) -> set[str]:
    """Every callee name reachable inside ``function`` of ``module_path``.

    ``function`` may be :data:`MODULE_SCOPE` to scan the whole module. Raises
    through an assertion when the function has vanished, so a rename fails here
    rather than passing vacuously.
    """
    tree = ast.parse((_BACKEND_ROOT / module_path).read_text(encoding="utf-8"))
    if function == MODULE_SCOPE:
        return _dotted_call_names(tree)
    target = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
        ),
        None,
    )
    assert target is not None, f"{module_path} no longer defines {function}()"
    return _dotted_call_names(target)


@pytest.mark.parametrize(("module_path", "function", "call"), _FILING_GATE_CALL_SITES)
def test_every_filing_path_reaches_the_reconciliation_gate(
    module_path: str, function: str, call: str
) -> None:
    """A built control with no caller is not a control (audit 2026-08-22 D-2).

    ``assert_filing_reconciled`` shipped complete, tested, documented as
    integrated — and unreachable from any production code path. Nothing in the
    behavioural suite noticed, because every behavioural test called the gate
    directly. This reads the source of each filing surface and asserts the call
    is actually there, so deleting it fails here and nowhere else has to.
    """
    assert call in _calls_within(module_path, function), (
        f"{module_path}::{function}() no longer calls {call}. The reconciliation "
        "control is only a control while its callers exist — if this move was "
        "deliberate, move the assertion, do not delete it."
    )


# ---------------------------------------------------------------------------
# The same guard, for the controls the RE-AUDIT found unwired (D-19, D-20)
# ---------------------------------------------------------------------------
#
# D-2 was not a one-off. The 2026-08-22 re-audit found four more controls in the
# identical state — complete, tested, described in a docstring or an evidence
# document as integrated, and reachable from no production path:
#
#   D-19  the SDI risk-weighted-asset SCOPE gate. ``sdi_capital`` said a code
#         default "marks the ratio provisional and blocks filing"; the only
#         consumer was an advisory read model, and the official mint's own check
#         (``credit_in_scope``) is satisfied by the code default BY CONSTRUCTION.
#   D-20a the CF-1 divergence disclosure. ``declared_methodologies`` reached
#         ``snapshot["provenance"]`` and stopped — no API field, no artifact line.
#   D-20b the SDI zero-obligation explanation. ``coverage_note()`` was called
#         only from tests; the payload had no field to carry it.
#   D-20c the reconciliation escape valve. ``grant_exception`` had no route, so
#         a blocked tenant could not be unblocked through the product.
#   (+)   ``CalculationProvenance.require_complete()`` — WS-A's "may this run be
#         filed from?" primitive, zero production callers, while the package
#         recorded ``filable`` and bound the run regardless.
#
# Same table, same mechanism, same reason: the behavioural tests all called the
# control directly, so none of them could see that nothing else did.
_CONTROL_CALL_SITES: tuple[tuple[str, str, str], ...] = (
    # -- D-19: the SDI RWA scope gate, on both official capital mints ---------
    (
        "app/services/regulatory_capital.py",
        "create_capital_run",
        "sdi_capital.assert_official_rwa_scope_governed",
    ),
    (
        "app/services/regulatory_capital.py",
        "run_all_capital_scenarios",
        "sdi_capital.assert_official_rwa_scope_governed",
    ),
    # ...and the two halves of what that gate must actually assert. Without
    # these rows the gate could be hollowed out while keeping its name.
    (
        "app/services/sdi_capital.py",
        "assert_official_rwa_scope_governed",
        "assert_scope_filable",
    ),
    (
        "app/services/sdi_capital.py",
        "assert_official_rwa_scope_governed",
        "assert_bucket_map_filable",
    ),
    # -- D-20a: the CF-1 divergence disclosure gets a reader ------------------
    (
        "app/services/regulatory_reporting/common.py",
        "read_package",
        "declared_methodologies",
    ),
    (
        "app/services/regulatory_reporting/templates.py",
        "_authority_lines",
        "_declared_methodology_lines",
    ),
    # -- D-20b: the SDI zero-obligation explanation gets a field --------------
    (
        "app/services/regulatory_reporting/calendar.py",
        "list_obligations",
        "eligibility.coverage_note",
    ),
    # -- D-20c: the reconciliation escape valve gets routes -------------------
    (
        "app/features/manage_reconciliation.py",
        "grant_reconciliation_exception",
        "reconciliation.grant_exception",
    ),
    (
        "app/features/manage_reconciliation.py",
        "revoke_reconciliation_exception",
        "reconciliation.revoke_exception",
    ),
    # A route nothing includes is as unreachable as an uncalled function, so the
    # registration is pinned too.
    (
        "app/api/router.py",
        MODULE_SCOPE,
        "v1_router.include_router",
    ),
    # -- (+) WS-A's filability primitive is enforced, not merely recorded -----
    (
        "app/services/regulatory_reporting/generation.py",
        "_source_run_entry",
        "require_complete",
    ),
)


@pytest.mark.parametrize(("module_path", "function", "call"), _CONTROL_CALL_SITES)
def test_every_built_control_reaches_its_production_call_site(
    module_path: str, function: str, call: str
) -> None:
    """The D-19 / D-20 generalisation of the gate above.

    Deleting any of these calls fails here and, for several of them, nowhere
    else — which is precisely the property the re-audit showed was missing. If a
    move is deliberate, move the call and move the row; do not delete either.
    """
    assert call in _calls_within(module_path, function), (
        f"{module_path}::{function} no longer calls {call}. A control that is "
        "built, tested and documented as integrated but reached from no "
        "production path is worse than an admitted gap, because it is invisible "
        "to the next reviewer."
    )


#: The other half of an anti-unwiring guard: calls that must NOT come back.
#:
#: D-19 was not only a missing gate, it was an unlabelled SUBSTITUTION —
#: ``regulatory_capital`` reached for ``sdi_capital.default_rwa_scope()`` whenever
#: the resolved scope was absent, so the path that mints filing evidence quietly
#: fell back to the platform's own placeholder. Adding the gate does not stop that
#: line from being reintroduced, and the positive table above cannot see it. Both
#: SDI capital paths that consume :class:`SdiRwaScope` are pinned here; the
#: documented default survives for the tests and for a caller with no session, but
#: no path that produces a regulatory number may reach it.
_FORBIDDEN_CALL_SITES: tuple[tuple[str, str, str], ...] = (
    (
        "app/services/regulatory_capital.py",
        "_sdi_engine_params",
        "sdi_capital.default_rwa_scope",
    ),
    (
        "app/services/regulatory_capital.py",
        "_load_active_params",
        "sdi_capital.default_rwa_scope",
    ),
    (
        "app/services/enterprise_stress.py",
        "_sdi_capital_params",
        "sdi_capital.default_rwa_scope",
    ),
    # The live s.29 view resolves the SAME object; a default here would put the
    # live CAR and the filed CAR on different scopes, which is the divergence the
    # single-authority refactor removed.
    (
        "app/services/sdi_capital.py",
        "compute_sdi_capital_summary",
        "default_rwa_scope",
    ),
)


@pytest.mark.parametrize(("module_path", "function", "call"), _FORBIDDEN_CALL_SITES)
def test_no_regulatory_path_falls_back_to_the_placeholder_rwa_scope(
    module_path: str, function: str, call: str
) -> None:
    """A gate in front of a substitution is not a control (audit 2026-08-22 D-19).

    ``assert_official_rwa_scope_governed`` refuses the mint, but the params build
    behind it used to substitute the code default rather than fail, so the two
    disagreed about what an unresolved scope means. The positive table pins the
    gate; this pins the absence of the escape hatch it was added to close.
    """
    assert call not in _calls_within(module_path, function), (
        f"{module_path}::{function} calls {call} again. Which risk classes an SDI's "
        "capital adequacy ratio charges for is a regulatory determination "
        "(Act 930 s.29(4)-(5) delegates the methodology and the categories of risk "
        "assets to a Bank of Ghana directive, and none has been issued for this "
        "class). The documented credit-only default is a placeholder, so a "
        "regulatory path must refuse rather than reach for it."
    )


def test_the_reconciliation_escape_valve_router_is_registered() -> None:
    """``include_router`` appearing in router.py is not enough — it must be THIS
    router. The AST row above pins the mechanism; this pins the identity."""
    source = (_BACKEND_ROOT / "app/api/router.py").read_text(encoding="utf-8")
    assert "from app.features.manage_reconciliation import router" in source
    assert "v1_router.include_router(reconciliation_router)" in source


def _derive_official_then_break_the_book(db_session: Session) -> BankReportingPeriod:
    """Official facts derived from a book that reconciles, then broken beneath them.

    This is the audit's D-3(a) scenario, staged exactly: the facts were minted
    honestly (the fixture's governed exception carried its known gap), and the
    exception is then withdrawn so the SAME canonical book no longer clears the
    control. Everything downstream is now filing on a book the platform's own
    control says does not balance.
    """
    _seed_book(db_session)
    derivation = derive_facts(db_session, _ctx(), SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    _revoke_fixture_exception(db_session)
    db_session.commit()
    period = db_session.get(BankReportingPeriod, derivation.reporting_period_id)
    assert period is not None
    return period


def test_official_capital_runs_refuse_a_book_that_no_longer_reconciles(
    db_session: Session,
) -> None:
    """D-3(b): the per-module endpoint minted CAR/CET1/RWA with no gate at all."""
    period = _derive_official_then_break_the_book(db_session)
    runs_before = db_session.scalar(select(func.count()).select_from(RegulatoryRun))

    with pytest.raises(reconciliation.FilingBlockedError) as excinfo:
        regulatory_capital.run_all_capital_scenarios(
            db_session,
            _ctx(),
            SAMPLE_BANK_ID,
            CapitalScenarioBatchCreate(reporting_period_id=period.id),
        )

    assert excinfo.value.status_code == 409
    assert isinstance(excinfo.value, NotComputable)
    assert excinfo.value.outcome is OutcomeState.RECONCILIATION_FAILED
    # Refused before anything was minted.
    assert db_session.scalar(select(func.count()).select_from(RegulatoryRun)) == runs_before


def test_official_liquidity_runs_refuse_a_book_that_no_longer_reconciles(
    db_session: Session,
) -> None:
    period = _derive_official_then_break_the_book(db_session)

    with pytest.raises(reconciliation.FilingBlockedError):
        regulatory_liquidity.run_all_liquidity_scenarios(
            db_session,
            _ctx(),
            SAMPLE_BANK_ID,
            LiquidityScenarioBatchCreate(reporting_period_id=period.id),
        )


def test_package_generation_refuses_a_book_that_no_longer_reconciles(
    db_session: Session,
) -> None:
    """D-2: the package-mint site never asked the control."""
    _derive_official_then_break_the_book(db_session)
    packages_before = db_session.scalar(select(func.count()).select_from(RegulatoryPackage))

    with pytest.raises(reconciliation.FilingBlockedError) as excinfo:
        generation.generate_package(
            db_session,
            _ctx(),
            SAMPLE_BANK_ID,
            RegulatoryPackageCreate(return_code="LCR-NSFR", reporting_date=FIXTURE_AS_OF),
        )

    assert "Balance-sheet identity failed" in str(excinfo.value.detail)
    assert db_session.scalar(select(func.count()).select_from(RegulatoryPackage)) == packages_before


def test_scheduled_official_run_no_longer_short_circuits_the_control(
    db_session: Session,
) -> None:
    """D-3(a): existing facts made ``run_official`` skip the only check there was."""
    _derive_official_then_break_the_book(db_session)
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "official_run",
        bank_id=SAMPLE_BANK_ID,
        payload={"as_of_date": FIXTURE_AS_OF.isoformat(), "actor_user_id": str(USER_1)},
    )
    db_session.commit()

    with pytest.raises(reconciliation.FilingBlockedError):
        pipeline.run_official(db_session, job)

    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(RegulatoryRun)) == 0


def test_live_plane_serves_the_broken_book_marked_blocked(db_session: Session) -> None:
    """D-1: the live plane computed the verdict and discarded it.

    The choice made here is deliberate and is NOT "refuse": an operator who
    cannot see the ratios has no way to judge how bad the break is or whether
    their correction worked. So the figures are served — and every channel a
    consumer can read them through says the book is unreconciled.
    """
    _seed_book(db_session)
    _revoke_fixture_exception(db_session)
    db_session.commit()
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id=SAMPLE_BANK_ID,
        payload={"as_of_date": FIXTURE_AS_OF.isoformat()},
    )
    db_session.commit()

    pipeline.run_refresh(db_session, job)

    rows = {
        row.module: row
        for row in db_session.scalars(
            select(LiveMetric).where(LiveMetric.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert rows, "the live plane must still materialise a broken book"
    for module, row in rows.items():
        assert row.pipeline_state == "blocked", module
        assert row.pipeline_error and "Balance-sheet identity failed" in row.pipeline_error
        assert row.metrics.get(pipeline.RECONCILIATION_METRIC_KEY) == (
            pipeline.RECONCILIATION_METRIC_BLOCKED
        )
    # The operator still gets the numbers they have to act on.
    assert Decimal(rows["capital"].metrics["car_pct"]) > 0

    summary = live_view.get_live_summary(db_session, _ctx(), SAMPLE_BANK_ID)
    assert summary.reconciliation is not None
    assert summary.reconciliation.blocks_filing is True
    assert summary.reconciliation.status == "blocked"
    assert summary.reconciliation.gap_fraction == str(FIXTURE_GAP_FRACTION)
    # The governed tolerance rides the payload with its provenance, so a reader
    # can see WHICH threshold this book was measured against and who set it.
    assert Decimal(summary.reconciliation.tolerance_pct) == (
        reconciliation.MODULE_DEFAULT_TOLERANCE_PCT
    )
    assert summary.reconciliation.tolerance_source == "control_plane"
    assert summary.reconciliation.message
    assert all(module.pipeline_state == "blocked" for module in summary.modules)


def test_live_plane_on_a_reconciled_book_is_byte_identical(db_session: Session) -> None:
    """The blocked marking must not leak onto a book that reconciles."""
    _seed_book(db_session)
    db_session.commit()
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id=SAMPLE_BANK_ID,
        payload={"as_of_date": FIXTURE_AS_OF.isoformat()},
    )
    db_session.commit()

    pipeline.run_refresh(db_session, job)

    rows = list(db_session.scalars(select(LiveMetric).where(LiveMetric.bank_id == SAMPLE_BANK_ID)))
    assert rows
    for row in rows:
        assert row.pipeline_state == "ready"
        assert row.pipeline_error is None
        assert pipeline.RECONCILIATION_METRIC_KEY not in row.metrics
    summary = live_view.get_live_summary(db_session, _ctx(), SAMPLE_BANK_ID)
    # The fixture's gap is carried by a governed exception, so the control has
    # something to say — and says it without blocking.
    assert summary.reconciliation is not None
    assert summary.reconciliation.status == "exception_applied"
    assert summary.reconciliation.blocks_filing is False
