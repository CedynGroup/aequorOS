"""``app.services.bi.mart_builder`` on the canonical fixture bank.

The builder is run synchronously (``tests/fixtures/bi_plane.py``, the same
call the e2e bootstrap makes) over ``tests/support/factories/canonical.py``'s book —
18 included current-generation snapshots plus one superseded and one
error-status row that must never appear — with the live plane materialised by
the product's own ``pipeline.recompute_live`` so the engine tier and the
engine tier has something to copy. Extra rows (an unconverted
foreign-currency loan, a withdrawn snapshot, a mid-month book, loan events,
an unmapped branch, a sealed run) are added per test through the canonical
models, never through the builder.

Every expected number is worked from the fixture's own amounts.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.bi.authority import designation_of, resolve_authority
from app.domain.bi.catalogue import catalogue
from app.models import (
    Bank,
    BankReportingPeriod,
    BiAggPositionDaily,
    BiDimBranch,
    BiDimCounterparty,
    BiDimDate,
    BiDimGlAccount,
    BiDimProduct,
    BiFactEngineMetric,
    BiFactLoanEvent,
    BiFactPositionDaily,
    BiFactPositionEom,
    BiMartBuild,
    CanonicalLoanEvent,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
    LiveMetric,
    Outlet,
    RegulatoryRun,
)
from app.models.bi import (
    MART_BUILD_SCOPES,
    UNASSIGNED_REGION,
    UNMAPPED_BRANCH_NAME,
)
from app.models.live import LIVE_MODULES
from app.schemas.bi import BiQuery
from app.services import pipeline
from app.services.bi import compiler, mart_builder, partitions, provenance
from app.services.bi.compiler import compile_query
from app.services.bi.mart_builder import BuildOutcome
from app.services.bi.versions import BUILDER_VERSION
from tests.fixtures.bi_plane import materialize_bi_plane
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.support.helpers import ORG_1, USER_1

AS_OF = FIXTURE_AS_OF  # 2026-06-30, the fixture's only date and the month's last
MID_MONTH = date(2026, 6, 15)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)

#: The fixture's included, current-generation snapshot count.
FIXTURE_ROWS = 18
#: Σ balance_ghs of the fixture's seven converted loans / six deposits.
FIXTURE_LOANS_RC = Decimal("84850000")
FIXTURE_DEPOSITS_RC = Decimal("80570000")


# --- seeding helpers (shared with the sibling BI suites) ----------------------------------


def seed_book(db: Session, *, live: bool = True) -> Bank:
    """The canonical fixture bank, committed, with the live plane when asked."""
    materialize_canonical_test_book(db)
    db.flush()
    seed_canonical_fixture(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    db.commit()
    bank = db.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    if live:
        outcome = pipeline.recompute_live(db, CTX, bank, AS_OF)
        assert not outcome.modules_failed, outcome.modules_failed
    return bank


def new_batch(db: Session, as_of: date, *, source_system: str = "EXCEL_CSV") -> dict[str, Any]:
    """An accepted batch + lineage; the ``common`` kwargs every canonical row takes."""
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system=source_system,
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="bi-test",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    return {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": as_of,
        "source_system": source_system,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def _product(db: Session, code: str) -> CanonicalProduct:
    product = db.scalar(
        select(CanonicalProduct).where(
            CanonicalProduct.bank_id == SAMPLE_BANK_ID, CanonicalProduct.product_code == code
        )
    )
    assert product is not None, code
    return product


def add_position(  # noqa: PLR0913 - keyword-only row builder
    db: Session,
    common: dict[str, Any],
    reference: str,
    position_type: str,
    currency: str,
    *,
    balance: str,
    balance_ghs: str | None = None,
    product: str | None = None,
    stage: int | None = None,
    branch: str | None = None,
    withdrawn: bool = False,
    position: CanonicalPosition | None = None,
    extra: dict[str, Any] | None = None,
) -> CanonicalPositionSnapshot:
    """One position (or a new snapshot of ``position``) at ``common['as_of_date']``."""
    if position is None:
        position = CanonicalPosition(
            **common, source_reference=reference, position_type=position_type, currency=currency
        )
        db.add(position)
        db.flush()
    attributes: dict[str, Any] = {}
    if balance_ghs is not None:
        attributes["balance_ghs"] = balance_ghs
    if branch is not None:
        attributes["branch_id"] = branch
    if extra:
        attributes.update(extra)
    snapshot = CanonicalPositionSnapshot(
        **common,
        source_reference=reference,
        position_id=position.id,
        product_id=_product(db, product).id if product else None,
        balance=Decimal(balance),
        ifrs9_stage=stage,
        attributes=attributes,
        withdrawn_at=datetime(2026, 7, 1, tzinfo=UTC) if withdrawn else None,
        withdrawal_reason="test withdrawal" if withdrawn else None,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def add_event(  # noqa: PLR0913 - keyword-only row builder
    db: Session,
    common: dict[str, Any],
    reference: str,
    position_reference: str,
    *,
    event_date: date,
    amount: str,
    currency: str = "GHS",
    amount_ghs: str | None = None,
    event_type: str = "REPAYMENT",
) -> CanonicalLoanEvent:
    event = CanonicalLoanEvent(
        **common,
        source_reference=reference,
        event_type=event_type,
        event_subtype=None,
        event_date=event_date,
        position_source_reference=position_reference,
        amount=Decimal(amount),
        currency=currency,
        amount_ghs=Decimal(amount_ghs) if amount_ghs is not None else None,
    )
    db.add(event)
    db.flush()
    return event


def position_of(db: Session, reference: str) -> CanonicalPosition:
    row = db.scalar(
        select(CanonicalPosition).where(
            CanonicalPosition.bank_id == SAMPLE_BANK_ID,
            CanonicalPosition.source_reference == reference,
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.withdrawn_at.is_(None),
        )
    )
    assert row is not None, reference
    return row


def resnapshot_loan_1(db: Session, as_of: date, *, balance: str) -> None:
    """A new accepted snapshot of the fixture's LOAN/1 at ``as_of``."""
    common = new_batch(db, as_of)
    add_position(
        db,
        common,
        "LOAN/1",
        "LOAN",
        "GHS",
        balance=balance,
        balance_ghs=balance,
        product="LN.CORP.5Y",
        stage=1,
        position=position_of(db, "LOAN/1"),
    )


def build(db: Session, as_of: date = AS_OF) -> BuildOutcome:
    outcome = materialize_bi_plane(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=as_of)
    assert outcome is not None
    return outcome


def daily_rows(db: Session, as_of: date = AS_OF) -> dict[str, BiFactPositionDaily]:
    return {
        row.source_reference: row
        for row in db.scalars(
            select(BiFactPositionDaily).where(
                BiFactPositionDaily.bank_id == SAMPLE_BANK_ID,
                BiFactPositionDaily.as_of_date == as_of,
            )
        )
    }



def _as_utc(value: datetime) -> datetime:
    """The instant, however the dialect chose to hand it back.

    A naive value is UTC by construction here (SQLite stores what the builder
    wrote); an aware one is converted rather than truncated.
    """
    return (
        value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    ).replace(tzinfo=None)


def build_records(db: Session, as_of: date = AS_OF) -> dict[str, BiMartBuild]:
    return {
        row.scope: row
        for row in db.scalars(
            select(BiMartBuild).where(
                BiMartBuild.bank_id == SAMPLE_BANK_ID, BiMartBuild.as_of_date == as_of
            )
        )
    }


def _cell_total(cells: Sequence[BiAggPositionDaily], column: str) -> Decimal:
    """Σ of one aggregate column over cells that CARRY it. An all-NULL cell has
    no value to add (audit A360 H1) — it is skipped, never read as 0."""
    return sum(
        (value for cell in cells if (value := getattr(cell, column)) is not None), Decimal(0)
    )


def _count(db: Session, model: type, **filters: Any) -> int:
    stmt = select(func.count()).select_from(model).where(model.bank_id == SAMPLE_BANK_ID)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model, column) == value)
    return int(db.scalar(stmt) or 0)


# --- positions ------------------------------------------------------------------------------


def test_builder_projects_every_included_current_generation_snapshot(db_session: Session) -> None:
    seed_book(db_session)
    outcome = build(db_session)

    assert outcome.status == "succeeded"
    assert outcome.row_counts["bi_fact_position_daily"] == FIXTURE_ROWS
    rows = daily_rows(db_session)
    assert len(rows) == FIXTURE_ROWS
    # the superseded and the error-status snapshots never appear
    assert "LOAN/OLD" not in rows
    assert "LOAN/BAD" not in rows
    assert all(row.builder_version == BUILDER_VERSION for row in rows.values())
    assert len({row.built_at for row in rows.values()}) == 1

    loan = rows["LOAN/1"]
    assert loan.balance_rc == Decimal("30000000")
    assert loan.classification_exposure_rc == Decimal("30000000")
    assert loan.fx_unconverted is False
    assert loan.grade is not None and loan.non_performing is False
    assert loan.branch_code == "BR-001"
    assert loan.product_code == "LN.CORP.5Y"
    assert loan.product_family == "corporate_loans"
    assert loan.exposure_category == "corporate_unrated"
    assert loan.counterparty_type == "CORPORATE"
    assert loan.provision_held_rc == Decimal("300000")
    impaired = rows["LOAN/6"]
    assert impaired.non_performing is True
    assert impaired.ifrs9_stage == 3
    assert impaired.exposure_category == "past_due_90"  # the stage-3 override lives here...
    assert impaired.product_family == "retail_loans"  # ...never on the product's family (D-037)
    # a converted foreign-currency loan carries its ingested conversion under both rules
    usd_loan = rows["LOAN/USD"]
    assert usd_loan.currency == "USD"
    assert usd_loan.balance_rc == Decimal("12850000")
    assert usd_loan.classification_exposure_rc == Decimal("12850000")
    assert usd_loan.fx_unconverted is False
    # an unconverted foreign-currency NON-loan: NULL balance, counted, no classification column
    guarantee = rows["LC/1"]
    assert guarantee.balance_rc is None
    assert guarantee.fx_unconverted is True
    assert guarantee.classification_exposure_rc is None
    assert guarantee.notional_rc == Decimal("2000000")
    deposit = rows["DEP/1"]
    assert deposit.product_family == "other_deposits"  # the fixture states no account type
    assert deposit.grade is None and deposit.classification_exposure_rc is None


def test_unconverted_loan_carries_both_fx_rules_and_withdrawn_rows_never_appear(
    db_session: Session,
) -> None:
    seed_book(db_session, live=False)
    common = new_batch(db_session, AS_OF)
    add_position(
        db_session,
        common,
        "LOAN/XFC",
        "LOAN",
        "USD",
        balance="500000",
        product="LN.CORP.5Y",
        stage=1,
    )
    add_position(
        db_session,
        common,
        "LOAN/GONE",
        "LOAN",
        "GHS",
        balance="777000000",
        balance_ghs="777000000",
        product="LN.CORP.5Y",
        stage=1,
        withdrawn=True,
    )
    db_session.commit()

    outcome = build(db_session)
    rows = daily_rows(db_session)
    assert outcome.row_counts["bi_fact_position_daily"] == FIXTURE_ROWS + 1
    assert "LOAN/GONE" not in rows
    unconverted = rows["LOAN/XFC"]
    assert unconverted.balance_rc is None  # derivation rule: excluded, counted
    assert unconverted.fx_unconverted is True
    assert unconverted.classification_exposure_rc == Decimal(
        0
    )  # classification rule: in the book at 0
    assert unconverted.grade is not None  # still classified
    # the aggregate follows: the row is counted, its exposure adds nothing
    cells = db_session.scalars(
        select(BiAggPositionDaily).where(
            BiAggPositionDaily.bank_id == SAMPLE_BANK_ID,
            BiAggPositionDaily.as_of_date == AS_OF,
            BiAggPositionDaily.currency == "USD",
            BiAggPositionDaily.position_type == "LOAN",
        )
    ).all()
    assert sum(cell.fx_unconverted_count for cell in cells) == 1
    assert sum(cell.row_count for cell in cells) == 2  # LOAN/USD (converted) + LOAN/XFC
    # Two USD loan cells: the converted one carries the balance, the unconverted
    # one carries NO reporting-currency balance — its sum is absent, not zero
    # (audit A360 H1) — while the classification rule puts it in the book at 0.
    assert _cell_total(cells, "balance_rc_sum") == Decimal("12850000")
    assert {cell.balance_rc_sum for cell in cells} == {Decimal("12850000"), None}
    assert _cell_total(cells, "classification_exposure_rc_sum") == Decimal("12850000")
    assert None not in {cell.classification_exposure_rc_sum for cell in cells}


def test_daily_aggregates_are_additive_sums_over_the_inserted_rows(db_session: Session) -> None:
    seed_book(db_session, live=False)
    build(db_session)
    rows = daily_rows(db_session).values()
    cells = db_session.scalars(
        select(BiAggPositionDaily).where(
            BiAggPositionDaily.bank_id == SAMPLE_BANK_ID, BiAggPositionDaily.as_of_date == AS_OF
        )
    ).all()
    assert sum(cell.row_count for cell in cells) == FIXTURE_ROWS
    assert _cell_total(cells, "balance_rc_sum") == sum(
        (row.balance_rc for row in rows if row.balance_rc is not None), Decimal(0)
    )
    assert _cell_total(cells, "non_performing_exposure_rc_sum") == Decimal("3000000")
    assert _cell_total(cells, "classification_exposure_rc_sum") == FIXTURE_LOANS_RC
    assert sum(cell.fx_unconverted_count for cell in cells) == 1
    assert _cell_total(cells, "provision_held_rc_sum") == Decimal("1560000")
    expected_rate_x = sum(
        (
            row.interest_rate * row.balance_rc
            for row in rows
            if row.balance_rc is not None and row.interest_rate is not None
        ),
        Decimal(0),
    )
    assert _cell_total(cells, "rate_x_balance_rc_sum") == expected_rate_x
    # the grain is unique
    grain = [
        (
            c.position_type,
            c.product_family,
            c.branch_code,
            c.currency,
            c.ifrs9_stage,
            c.dpd_band,
            c.grade,
            c.deposit_account_type,
        )
        for c in cells
    ]
    assert len(grain) == len(set(grain))


# --- fingerprint --------------------------------------------------------------------------


def test_second_run_skips_on_the_same_fingerprint_and_a_new_batch_changes_it(
    db_session: Session,
) -> None:
    seed_book(db_session, live=False)
    first = build(db_session)
    db_session.commit()
    second = build(db_session)
    assert second.status == "skipped"
    assert second.fingerprint == first.fingerprint
    records = build_records(db_session)
    assert set(records) == set(MART_BUILD_SCOPES)
    assert all(
        row.status == "succeeded" and row.fingerprint == first.fingerprint
        for row in records.values()
    )

    common = new_batch(db_session, AS_OF)
    add_position(
        db_session,
        common,
        "DEP/NEW",
        "DEPOSIT",
        "GHS",
        balance="1000",
        balance_ghs="1000",
        product="DEP.RET.CUR",
    )
    db_session.commit()
    third = build(db_session)
    assert third.status == "succeeded"
    assert third.fingerprint != first.fingerprint
    assert third.row_counts["bi_fact_position_daily"] == FIXTURE_ROWS + 1
    assert len(daily_rows(db_session)) == FIXTURE_ROWS + 1  # the slice was replaced, not appended


def test_fingerprint_moves_with_the_live_plane_and_the_builder_version(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_book(db_session, live=False)
    before = mart_builder.fingerprint_for(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
    )
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    pipeline.recompute_live(db_session, CTX, bank, AS_OF)
    with_live = mart_builder.fingerprint_for(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
    )
    assert with_live != before
    # the live plane at ANOTHER date does not enter this date's fingerprint
    other = mart_builder.fingerprint_for(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=MID_MONTH
    )
    db_session.execute(
        update(LiveMetric)
        .where(LiveMetric.bank_id == SAMPLE_BANK_ID)
        .values(computed_from_input_hash="0" * 64)
    )
    assert (
        mart_builder.fingerprint_for(
            db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=MID_MONTH
        )
        == other
    )
    assert (
        mart_builder.fingerprint_for(
            db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
        )
        != with_live
    )
    monkeypatch.setattr(mart_builder, "BUILDER_VERSION", BUILDER_VERSION + 1)
    assert (
        mart_builder.fingerprint_for(
            db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
        )
        != with_live
    )


def test_a_catalogue_version_bump_forces_a_rebuild_of_an_already_built_slice(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bump is the MECHANISM by which a new mart column gets filled.

    A catalogue change that adds a column leaves it NULL on every slice already
    built. Without ``CATALOGUE_VERSION`` in the fingerprint the builder would
    consider those slices current and never refill them, so every figure over the
    new column would read empty for all history while the mart looked healthy.
    Asserted from both ends: the fingerprint moves, and a slice whose stored
    fingerprint was minted under the old version is no longer ``skipped``.
    """
    seed_book(db_session, live=False)
    first = build(db_session)
    db_session.commit()
    assert first.status == "succeeded"
    assert build(db_session).status == "skipped", "the same catalogue must not rebuild"

    monkeypatch.setattr(mart_builder, "CATALOGUE_VERSION", "99.0.0")
    assert (
        mart_builder.fingerprint_for(
            db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF
        )
        != first.fingerprint
    )
    again = build(db_session)
    assert again.status == "succeeded", "a catalogue bump must refill the slice"
    assert again.fingerprint != first.fingerprint


# --- month-end (D-014) ------------------------------------------------------------------


def test_eom_rows_exist_only_for_the_last_date_with_data_in_the_month(db_session: Session) -> None:
    seed_book(db_session, live=False)
    resnapshot_loan_1(db_session, MID_MONTH, balance="31000000")
    db_session.commit()

    mid = build(db_session, MID_MONTH)
    assert mid.row_counts["bi_fact_position_daily"] == 1
    assert mid.row_counts["bi_fact_position_eom"] == 0
    assert _count(db_session, BiFactPositionEom) == 0

    end = build(db_session, AS_OF)
    assert end.row_counts["bi_fact_position_eom"] == FIXTURE_ROWS
    eom = db_session.scalars(
        select(BiFactPositionEom).where(BiFactPositionEom.bank_id == SAMPLE_BANK_ID)
    ).all()
    assert {row.as_of_date for row in eom} == {AS_OF}
    assert len(eom) == FIXTURE_ROWS

    calendar = {
        row.date: row
        for row in db_session.scalars(select(BiDimDate).where(BiDimDate.bank_id == SAMPLE_BANK_ID))
    }
    assert set(calendar) == {MID_MONTH + timedelta(days=n) for n in range(16)}
    assert calendar[MID_MONTH].has_data is True
    assert calendar[MID_MONTH].is_last_in_month is False
    assert calendar[date(2026, 6, 16)].has_data is False
    last = calendar[AS_OF]
    assert (
        last.has_data,
        last.is_last_in_month,
        last.is_last_in_quarter,
        last.is_last_in_year,
    ) == (
        True,
        True,
        True,
        True,
    )
    assert (last.calendar_month, last.calendar_quarter, last.calendar_year) == (
        date(2026, 6, 1),
        date(2026, 4, 1),
        2026,
    )
    assert (last.fiscal_year, last.fiscal_quarter) == (2026, 2)


def test_an_earlier_eom_copy_is_replaced_when_a_later_date_arrives(db_session: Session) -> None:
    """Build mid-month first (it IS the last date then), then the month-end arrives."""
    seed_book(db_session, live=False)
    # move the whole fixture book to mid-month so 15 June is the last date...
    db_session.execute(
        update(CanonicalPositionSnapshot)
        .where(CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID)
        .values(as_of_date=MID_MONTH)
    )
    db_session.commit()
    mid = build(db_session, MID_MONTH)
    assert mid.row_counts["bi_fact_position_eom"] == FIXTURE_ROWS
    # ...then a month-end book lands for one facility
    resnapshot_loan_1(db_session, AS_OF, balance="1")
    db_session.commit()
    end = build(db_session, AS_OF)
    assert end.row_counts["bi_fact_position_eom"] == 1
    assert {row.as_of_date for row in db_session.scalars(select(BiFactPositionEom))} == {AS_OF}
    # and a rebuild of the mid-month date removes its stale copy too
    build(db_session, MID_MONTH)
    assert _count(db_session, BiFactPositionEom, as_of_date=MID_MONTH) == 0


# --- loan events (D-018) --------------------------------------------------------------------


def test_loan_event_attribution_cases(db_session: Session) -> None:
    seed_book(db_session, live=False)
    common = new_batch(db_session, AS_OF)
    add_event(
        db_session, common, "EV/1", "LOAN/1", event_date=AS_OF, amount="1000", amount_ghs="1000"
    )
    add_event(
        db_session, common, "EV/2", "LOAN/NOBODY", event_date=AS_OF, amount="5", currency="USD"
    )
    # a same-reference facility in ANOTHER source system is never a match
    other = new_batch(db_session, AS_OF, source_system="API_PUSH")
    add_event(db_session, other, "EV/3", "LOAN/1", event_date=AS_OF, amount="7")
    # a facility whose only snapshot is AFTER the event date
    early = new_batch(db_session, MID_MONTH)
    add_event(db_session, early, "EV/4", "LOAN/2", event_date=MID_MONTH, amount="9")
    db_session.commit()

    build(db_session, AS_OF)
    build(db_session, MID_MONTH)
    events = {
        row.source_reference: row
        for row in db_session.scalars(
            select(BiFactLoanEvent).where(BiFactLoanEvent.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert set(events) == {"EV/1", "EV/2", "EV/3", "EV/4"}
    matched = events["EV/1"]
    assert matched.attribution_basis == "snapshot_on_or_before"
    assert matched.position_id == position_of(db_session, "LOAN/1").id
    assert matched.snapshot_id == daily_rows(db_session)["LOAN/1"].snapshot_id
    assert (matched.branch_code, matched.product_code, matched.product_family) == (
        "BR-001",
        "LN.CORP.5Y",
        "corporate_loans",
    )
    assert matched.amount_rc == Decimal("1000") and matched.fx_unconverted is False
    unmatched = events["EV/2"]
    assert unmatched.attribution_basis == "unmatched"
    assert unmatched.position_id is None and unmatched.branch_code is None
    assert unmatched.amount_rc is None and unmatched.fx_unconverted is True
    cross_system = events["EV/3"]
    assert cross_system.attribution_basis == "unmatched"
    assert cross_system.amount_rc == Decimal("7")  # base currency: its own amount
    no_snapshot = events["EV/4"]
    assert no_snapshot.attribution_basis == "no_snapshot"
    assert no_snapshot.position_id == position_of(db_session, "LOAN/2").id
    assert no_snapshot.snapshot_id is None and no_snapshot.product_code is None


# --- engine tier ------------------------------------------------------------------------------


def test_engine_rows_copy_every_live_module_with_tier_regime_and_designation(
    db_session: Session,
) -> None:
    seed_book(db_session)
    build(db_session)
    rows = db_session.scalars(
        select(BiFactEngineMetric).where(
            BiFactEngineMetric.bank_id == SAMPLE_BANK_ID, BiFactEngineMetric.as_of_date == AS_OF
        )
    ).all()
    lives = db_session.scalars(select(LiveMetric).where(LiveMetric.bank_id == SAMPLE_BANK_ID)).all()
    assert {live.module for live in lives} == set(LIVE_MODULES)
    # a module whose payload states only its availability (the fixture's rating
    # scorecard has no entitled market data) has no figure to copy
    with_figures = {
        live.module
        for live in lives
        if any(key not in ("availability", "reason") for key in live.metrics)
    }
    assert with_figures == set(LIVE_MODULES) - {"rating"}
    assert {row.module for row in rows if row.tier == "live"} == with_figures
    assert all(row.tier == "live" for row in rows)  # no sealed run exists yet
    assert all(row.institution_class == "bank" for row in rows)
    assert all(row.reconciliation_blocked is False for row in rows)
    assert all(row.computed_at is not None for row in rows)
    npl = next(row for row in rows if row.module == "credit" and row.metric_id == "npl_ratio_pct")
    authority = resolve_authority("npl_ratio_pct", regime="crd", institution_class="bank")
    assert authority is not None
    assert npl.regime == authority.regime.value
    assert npl.advisory_designation == designation_of(authority)
    assert npl.unit == "pct" and npl.value is not None
    live = db_session.scalar(
        select(LiveMetric).where(
            LiveMetric.bank_id == SAMPLE_BANK_ID, LiveMetric.module == "credit"
        )
    )
    assert live is not None
    assert npl.value == Decimal(str(live.metrics["npl_ratio_pct"]))
    assert npl.input_hash == live.computed_from_input_hash
    assert npl.pipeline_state == live.pipeline_state


def test_official_tier_copies_the_latest_succeeded_baseline_run_per_module(
    db_session: Session,
) -> None:
    seed_book(db_session)
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID, BankReportingPeriod.period_end == AS_OF
        )
    )
    assert period is not None

    def run(
        created: datetime,
        metrics: dict[str, str],
        *,
        status: str = "succeeded",
        scenario: str = "baseline",
    ) -> RegulatoryRun:
        row = RegulatoryRun(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            reporting_period_id=period.id,
            module="capital",
            scenario_code=scenario,
            status=status,
            engine_version="test-engine",
            input_schema_version="v1",
            output_schema_version="v1",
            input_hash="a" * 64,
            inputs={},
            metrics=metrics,
            completed_at=None,
            created_by=USER_1,
        )
        db_session.add(row)
        db_session.flush()
        row.created_at = created
        db_session.flush()
        return row

    run(datetime(2026, 7, 1, tzinfo=UTC), {"car_pct": "12.5"})
    latest = run(datetime(2026, 7, 2, tzinfo=UTC), {"car_pct": "13.75"})
    run(datetime(2026, 7, 3, tzinfo=UTC), {"car_pct": "99"}, status="failed")
    run(datetime(2026, 7, 4, tzinfo=UTC), {"car_pct": "98"}, scenario="severe")
    db_session.commit()

    outcome = build(db_session)
    official = db_session.scalars(
        select(BiFactEngineMetric).where(
            BiFactEngineMetric.bank_id == SAMPLE_BANK_ID,
            BiFactEngineMetric.as_of_date == AS_OF,
            BiFactEngineMetric.tier == "official",
        )
    ).all()
    assert [(row.module, row.metric_id) for row in official] == [("capital", "car_pct")]
    car = official[0]
    assert car.value == Decimal("13.75")
    assert car.run_id == latest.id
    assert car.reporting_period_id == period.id
    assert car.input_hash == "a" * 64
    assert car.pipeline_state is None
    # a run without completed_at is stamped with the build instant, never NULL
    started_at = build_records(db_session)["engine"].started_at
    # Compared as INSTANTS, not as wall clocks. SQLite hands both back naive, so
    # stripping tzinfo agreed by accident; Postgres hands back `timestamptz` as
    # aware datetimes, and dropping the offset compares 06:47 EDT with 10:47 UTC
    # -- the same moment, asserted unequal.
    assert _as_utc(car.computed_at) == _as_utc(started_at)
    assert outcome.row_counts["bi_fact_engine_metric"] == len(official) + sum(
        1 for row in db_session.scalars(select(BiFactEngineMetric)) if row.tier == "live"
    )


def test_live_tier_is_the_current_edge_only(db_session: Session) -> None:
    seed_book(db_session)
    build(db_session)
    assert _count(db_session, BiFactEngineMetric, tier="live") > 0
    # the live plane moves to another date: its rows for the old date must go
    db_session.execute(
        update(LiveMetric)
        .where(LiveMetric.bank_id == SAMPLE_BANK_ID)
        .values(source_as_of_date=MID_MONTH)
    )
    db_session.commit()
    build(db_session, AS_OF)  # fingerprint changed (live left this date) → rebuilt
    assert _count(db_session, BiFactEngineMetric, tier="live") == 0


# --- dimensions ---------------------------------------------------------------------------------


def test_dimensions_are_type1_and_name_the_unmapped_branch(db_session: Session) -> None:
    seed_book(db_session, live=False)
    db_session.add(
        Outlet(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            outlet_type="branch",
            name="Osu",
            outlet_number="002",
            status="active",
        )
    )
    common = new_batch(db_session, AS_OF)
    add_position(
        db_session, common, "DEP/ELSEWHERE", "DEPOSIT", "GHS", balance="10", balance_ghs="10",
        product="DEP.RET.SAV", branch="BR-999",
    )  # fmt: skip
    db_session.commit()
    build(db_session)

    branches = {
        row.branch_code: row
        for row in db_session.scalars(
            select(BiDimBranch).where(BiDimBranch.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert set(branches) == {"BR-001", "BR-002", "BR-999"}
    assert (branches["BR-001"].name, branches["BR-001"].mapped) == ("Head Office", True)
    assert branches["BR-001"].region == UNASSIGNED_REGION  # declared, never inferred (D-020)
    assert branches["BR-001"].outlet_id is None
    osu = branches["BR-002"]
    assert osu.outlet_id is not None and osu.outlet_type == "branch" and osu.status == "active"
    unmapped = branches["BR-999"]
    assert (unmapped.name, unmapped.mapped, unmapped.region) == (
        UNMAPPED_BRANCH_NAME,
        False,
        UNASSIGNED_REGION,
    )

    products = {
        row.product_code: row
        for row in db_session.scalars(
            select(BiDimProduct).where(BiDimProduct.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert len(products) == 12
    assert products["LN.RET.MORT"].product_family == "mortgages"
    assert products["LN.RET.MORT"].regulatory_category == "RESIDENTIAL_MORTGAGE"
    assert products["DEP.RET.SAV"].product_family == "other_deposits"  # what the book stated
    assert products["SEC.TBILL.91"].product_family == "securities"
    counterparties = db_session.scalars(
        select(BiDimCounterparty).where(BiDimCounterparty.bank_id == SAMPLE_BANK_ID)
    ).all()
    assert {row.name for row in counterparties} == {"Ama Mensah", "Volta Agro Ltd"}
    accounts = {
        row.account_code: row
        for row in db_session.scalars(
            select(BiDimGlAccount).where(BiDimGlAccount.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert len(accounts) == 11
    assert accounts["1301"].name == "Loans to Customers"
    assert accounts["1301"].account_class == "ASSET" and accounts["1301"].pl_line is None

    # Type 1: a re-pushed register renames in place, never a second row
    db_session.add(
        CanonicalReferenceRow(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            ingestion_batch_id=new_batch(db_session, AS_OF)["ingestion_batch_id"],
            as_of_date=AS_OF,
            dataset_kind="business_units",
            row_index=0,
            payload={"unit_id": "BR-999", "name": "Tema", "region": "Greater Accra"},
            source_reference="bu#0",
            lineage_id=common["lineage_id"],
        )
    )
    db_session.commit()
    build(db_session)
    branches = {
        row.branch_code: row
        for row in db_session.scalars(
            select(BiDimBranch).where(BiDimBranch.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert set(branches) == {"BR-001", "BR-002", "BR-999"}  # BR-001/002 kept (Type 1 never deletes)
    assert (branches["BR-999"].name, branches["BR-999"].mapped, branches["BR-999"].region) == (
        "Tema",
        True,
        "Greater Accra",
    )
    assert branches["BR-001"].mapped is False  # the latest register no longer names it
    assert branches["BR-001"].name == UNMAPPED_BRANCH_NAME


# --- failure and control ------------------------------------------------------------------------


def test_a_failing_scope_rolls_every_mart_back_and_records_the_failure(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_book(db_session, live=False)

    def explode(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("engine copy exploded")

    monkeypatch.setattr(mart_builder, "_build_engine", explode)
    with pytest.raises(RuntimeError, match="engine copy exploded"):
        build(db_session)
    assert _count(db_session, BiFactPositionDaily) == 0  # the savepoint took the positions with it
    assert _count(db_session, BiDimDate) == 0
    records = build_records(db_session)
    assert set(records) == set(MART_BUILD_SCOPES)
    assert all(row.status == "failed" for row in records.values())
    assert all(row.error == "RuntimeError: engine copy exploded" for row in records.values())
    assert all(row.finished_at is not None for row in records.values())

    monkeypatch.undo()
    outcome = build(db_session)  # a failed record never satisfies the skip
    assert outcome.status == "succeeded"
    assert all(row.status == "succeeded" for row in build_records(db_session).values())


# --- audit A360 H1: the aggregate the builder writes says what the fact rows say ---------------


def _kpi_on_both_paths(
    db: Session, monkeypatch: pytest.MonkeyPatch, as_of: date, **overrides: Any
) -> tuple[Any, Any]:
    """One no-dimension figure read through the compiler twice: as the compiler
    picks its source (the aggregate table, for these measures) and forced onto the
    fact table. ``(aggregate, fact)``."""
    query = BiQuery.model_validate({"time": {"as_of": as_of}, **overrides})
    cat = catalogue()

    def _read() -> Any:
        compiled = compile_query(db, cat, query, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        return compiled.used_aggregate, db.execute(compiled.select).one()[0]

    used, on_aggregate = _read()
    assert used is True, "the case must reach the aggregate source, or it proves nothing about it"
    with monkeypatch.context() as patch:
        patch.setattr(compiler, "aggregate_table_covers", lambda *_: False)
        used, on_fact = _read()
    assert used is False
    return on_aggregate, on_fact


def test_the_builders_aggregate_answers_an_absent_figure_exactly_as_the_fact_rows_do(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit A360 H1, on rows the BUILDER wrote. The fixture book states no
    collateral anywhere, and the branch added here holds nothing but unconverted
    foreign currency. Every no-dimension measure is served from
    ``bi_agg_position_daily``; a cell that started at 0 there turned "no collateral
    stated" into "collateral of 0" on every KPI tile while the fact path said
    nothing — and the alerts, target attainment and insights read the 0 as a
    figure."""
    seed_book(db_session, live=False)
    common = new_batch(db_session, AS_OF)
    for index in range(2):
        add_position(
            db_session,
            common,
            f"LOAN/FXONLY{index}",
            "LOAN",
            "USD",
            balance="500000",
            product="LN.CORP.5Y",
            stage=1,
            branch="BR-FX",
        )
    db_session.commit()
    build(db_session)

    # A book that states no collateral has no collateral figure — on both paths.
    assert _kpi_on_both_paths(db_session, monkeypatch, AS_OF, measures=["loans.collateral_rc"]) == (
        None,
        None,
    )
    # A branch whose whole book is unconverted has no reporting-currency balance…
    fx_only = [{"member": "branch.code", "op": "in", "values": ["BR-FX"]}]
    assert _kpi_on_both_paths(
        db_session, monkeypatch, AS_OF, measures=["loans.balance_rc"], filters=fx_only
    ) == (None, None)
    # …and is not an empty population: both loans are counted, both unconverted.
    assert _kpi_on_both_paths(
        db_session, monkeypatch, AS_OF, measures=["loans.count"], filters=fx_only
    ) == (2, 2)
    assert _kpi_on_both_paths(
        db_session, monkeypatch, AS_OF, measures=["loans.unconverted_count"], filters=fx_only
    ) == (2, 2)
    # The control: a figure the book does state agrees on both paths, as a number.
    on_aggregate, on_fact = _kpi_on_both_paths(
        db_session, monkeypatch, AS_OF, measures=["loans.balance_rc"]
    )
    assert Decimal(str(on_aggregate)) == Decimal(str(on_fact)) == FIXTURE_LOANS_RC

    # And where the aggregate answer came from: the cells the builder wrote hold
    # NULL, not 0, wherever no row carried the value.
    cells = db_session.scalars(
        select(BiAggPositionDaily).where(
            BiAggPositionDaily.bank_id == SAMPLE_BANK_ID, BiAggPositionDaily.as_of_date == AS_OF
        )
    ).all()
    assert cells and all(cell.collateral_rc_sum is None for cell in cells)
    fx_cells = [cell for cell in cells if cell.branch_code == "BR-FX"]
    assert fx_cells and all(
        cell.balance_rc_sum is None and cell.row_count == 2 and cell.fx_unconverted_count == 2
        for cell in fx_cells
    )


# --- audit A360 H2: a failed rebuild must be served as STALE, and attributably ------------------


def _window_stale(db: Session, *dates: date) -> tuple[date, ...]:
    return provenance.stale_dates(
        db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, window=(min(dates), max(dates))
    )


def _window_fingerprint(db: Session, *dates: date) -> str | None:
    return provenance.build_fingerprint(
        db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, window=(min(dates), max(dates))
    )


def test_a_failed_rebuild_is_served_as_stale_and_attributably(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape the auditor reproduced: a build succeeds, new data arrives, the
    rebuild fails, and the mart still holds the old rows (the savepoint rolled the
    new ones back). A reader is then served last night's figures — which is the
    design — but the state must be NAMED as stale: the date appears in
    ``stale_dates`` (which is what stops an alert firing and a board pack mailing
    over those rows), and the fingerprint is not ``None`` and not one any
    successful build stamped."""
    seed_book(db_session)
    first = build(db_session)
    db_session.commit()
    assert _window_stale(db_session, AS_OF) == ()
    assert _window_fingerprint(db_session, AS_OF) == first.fingerprint

    # New data: one more deposit lands at AS_OF, which moves the fingerprint…
    add_position(
        db_session,
        new_batch(db_session, AS_OF),
        "DEP/NEW",
        "DEPOSIT",
        "GHS",
        balance="1000",
        balance_ghs="1000",
        product="DEP.RET.CUR",
    )
    db_session.commit()

    # …and the rebuild fails.
    def explode(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("engine copy exploded")

    monkeypatch.setattr(mart_builder, "_build_engine", explode)
    with pytest.raises(RuntimeError, match="engine copy exploded"):
        build(db_session)
    monkeypatch.undo()

    # The mart still holds the OLD rows — the new deposit is not in it — and every
    # scope's record says failed.
    rows = daily_rows(db_session)
    assert len(rows) == FIXTURE_ROWS
    assert "DEP/NEW" not in rows
    assert all(row.status == "failed" for row in build_records(db_session).values())
    # The date is named as stale, so nothing may be judged from its rows.
    assert _window_stale(db_session, AS_OF) == (AS_OF,)
    # And the served state is attributable: a digest that joins back to the failed
    # records, never ``None`` (which the query log read as "no build at all") and
    # never the fingerprint a successful build stamped.
    fingerprint = _window_fingerprint(db_session, AS_OF)
    assert fingerprint is not None
    assert len(fingerprint) == 64
    assert fingerprint != first.fingerprint

    # A successful rebuild makes the date current again.
    second = build(db_session)
    assert second.status == "succeeded"
    assert second.fingerprint != first.fingerprint
    assert _window_stale(db_session, AS_OF) == ()
    assert _window_fingerprint(db_session, AS_OF) == second.fingerprint
    assert "DEP/NEW" in daily_rows(db_session)


def test_a_build_record_left_running_counts_as_stale_too(db_session: Session) -> None:
    """Not succeeded is the rule. A ``running`` record is never visible from another
    session in practice (the builder commits only on success or failure), but one
    left by a dead process would otherwise read as current."""
    seed_book(db_session, live=False)
    outcome = build(db_session)
    assert _window_stale(db_session, AS_OF) == ()
    build_records(db_session)["engine"].status = "running"
    db_session.flush()
    assert _window_stale(db_session, AS_OF) == (AS_OF,)
    fingerprint = _window_fingerprint(db_session, AS_OF)
    assert fingerprint is not None and fingerprint != outcome.fingerprint


def test_one_stale_date_is_named_in_a_window_that_also_holds_a_good_one(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A window over several dates names exactly the dates that are stale, so a
    consumer refusing on staleness refuses the right ones and no others."""
    seed_book(db_session, live=False)
    resnapshot_loan_1(db_session, MID_MONTH, balance="29000000")
    db_session.commit()
    build(db_session, AS_OF)
    build(db_session, MID_MONTH)
    db_session.commit()
    assert _window_stale(db_session, MID_MONTH, AS_OF) == ()

    add_position(
        db_session,
        new_batch(db_session, MID_MONTH),
        "DEP/MID",
        "DEPOSIT",
        "GHS",
        balance="5",
        balance_ghs="5",
        product="DEP.RET.CUR",
    )
    db_session.commit()

    def explode(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("engine copy exploded")

    monkeypatch.setattr(mart_builder, "_build_engine", explode)
    with pytest.raises(RuntimeError, match="engine copy exploded"):
        build(db_session, MID_MONTH)
    monkeypatch.undo()

    assert _window_stale(db_session, MID_MONTH, AS_OF) == (MID_MONTH,)
    assert _window_stale(db_session, AS_OF) == ()
    assert _window_stale(db_session, MID_MONTH) == (MID_MONTH,)
    # And the window's fingerprint is a digest over the mixed state, not the good
    # date's own fingerprint.
    mixed = _window_fingerprint(db_session, MID_MONTH, AS_OF)
    assert mixed is not None and mixed != _window_fingerprint(db_session, AS_OF)


# --- audit A360 H3: an attribute wider than its mart column never fails the build ---------------


def test_an_over_wide_attribute_is_carried_as_absent_never_truncated_and_never_fails_the_build(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """``bi_fact_position_daily.branch_code`` is ``String(120)``; the attributes bag
    is unbounded. On Postgres a 121-character ``branch_id`` failed the tenant's
    WHOLE nightly build inside the one savepoint, every night; SQLite stored it, so
    no hermetic test saw either. The extractor now carries the value as absent: the
    row lands, the column is NULL, the refusal is counted and logged once per build
    — and no code of exactly 120 characters, the truncation, exists anywhere,
    because that would be a different branch."""
    seed_book(db_session, live=False)
    common = new_batch(db_session, MID_MONTH)
    wide = "BR-" + "W" * 118  # 121 characters
    at_limit = "BR-" + "L" * 117  # 120 characters
    add_position(
        db_session,
        common,
        "LOAN/WIDE",
        "LOAN",
        "GHS",
        balance="1000",
        balance_ghs="1000",
        product="LN.CORP.5Y",
        stage=1,
        branch=wide,
        extra={"sector": "S" * 121, "employer": "E" * 255},
    )
    add_position(
        db_session,
        common,
        "LOAN/NARROW",
        "LOAN",
        "GHS",
        balance="2000",
        balance_ghs="2000",
        product="LN.CORP.5Y",
        stage=1,
        branch=at_limit,
    )
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger=mart_builder.logger.name):
        outcome = build(db_session, MID_MONTH)
    assert outcome.status == "succeeded"
    assert outcome.row_counts["bi_fact_position_daily"] == 2

    rows = daily_rows(db_session, MID_MONTH)
    wide_row = rows["LOAN/WIDE"]
    assert wide_row.balance_rc == Decimal("1000")  # the row itself landed
    assert wide_row.branch_code is None
    assert wide_row.sector is None
    assert wide_row.employer == "E" * 255  # at the limit: verbatim
    assert rows["LOAN/NARROW"].branch_code == at_limit

    fact_codes = {row.branch_code for row in rows.values()}
    aggregate_codes = {
        cell.branch_code
        for cell in db_session.scalars(
            select(BiAggPositionDaily).where(
                BiAggPositionDaily.bank_id == SAMPLE_BANK_ID,
                BiAggPositionDaily.as_of_date == MID_MONTH,
            )
        )
    }
    dimension_codes = {
        row.branch_code
        for row in db_session.scalars(
            select(BiDimBranch).where(BiDimBranch.bank_id == SAMPLE_BANK_ID)
        )
    }
    for codes in (fact_codes, aggregate_codes, dimension_codes):
        assert wide not in codes
        assert wide[:120] not in codes, "a truncated branch code is a different branch"
    assert at_limit in fact_codes and at_limit in aggregate_codes and at_limit in dimension_codes

    # Logged once per build, per key, as a COUNT — never a value.
    (record,) = [
        record
        for record in caplog.records
        if record.getMessage() == "bi.positions.attribute_text_overflow"
    ]
    assert record.__dict__["refused"] == {"branch_id": 1, "sector": 1}
    assert record.__dict__["bank_id"] == SAMPLE_BANK_ID
    assert wide not in repr(record.__dict__)


def test_a_register_row_the_branch_dimension_cannot_store_is_rejected_not_truncated(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """A ``business_units`` row stored before the kind was enforced (or around the
    door) can carry a unit id wider than ``bi_dim_branch.branch_code``. It is
    skipped and counted, exactly as a malformed row is — never written as its
    120-character prefix, which would be a branch the bank did not declare."""
    seed_book(db_session, live=False)
    common = new_batch(db_session, AS_OF)
    wide = "U" * 121
    for index, (unit_id, name) in enumerate((("BR-001", "Accra Main"), (wide, "Too wide"))):
        db_session.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=common["ingestion_batch_id"],
                as_of_date=AS_OF,
                dataset_kind="business_units",
                row_index=index,
                payload={"business_unit_id": unit_id, "business_unit_name": name},
                source_reference=f"bu#{index}",
                lineage_id=common["lineage_id"],
            )
        )
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger=mart_builder.logger.name):
        outcome = build(db_session)
    assert outcome.status == "succeeded"
    branches = {
        row.branch_code: row
        for row in db_session.scalars(
            select(BiDimBranch).where(BiDimBranch.bank_id == SAMPLE_BANK_ID)
        )
    }
    assert branches["BR-001"].name == "Accra Main"
    assert branches["BR-001"].mapped is True
    assert wide not in branches
    assert wide[:120] not in branches
    (record,) = [
        record
        for record in caplog.records
        if record.getMessage() == "bi.dim_branch.register_rows_rejected"
    ]
    assert record.__dict__["rows"] == 1


def test_build_records_carry_row_counts_and_timings_per_scope(db_session: Session) -> None:
    seed_book(db_session, live=False)
    outcome = build(db_session)
    records = build_records(db_session)
    positions = records["positions"]
    assert positions.row_counts["bi_fact_position_daily"] == FIXTURE_ROWS
    assert positions.row_counts["bi_fact_position_eom"] == FIXTURE_ROWS
    assert "elapsed_ms" in positions.row_counts
    assert records["dims"].row_counts["bi_dim_branch"] == 2
    assert positions.builder_version == BUILDER_VERSION
    assert positions.finished_at is not None and positions.finished_at >= positions.started_at
    assert outcome.status == "succeeded"


def test_unknown_bank_is_refused_before_anything_is_written(db_session: Session) -> None:
    seed_book(db_session, live=False)
    with pytest.raises(mart_builder.BankNotFoundError):
        mart_builder.refresh_bank_as_of(
            db_session, organization_id=ORG_1, bank_id="BK-NOPE0001", as_of=AS_OF, reason="test"
        )
    with pytest.raises(mart_builder.BankNotFoundError):
        mart_builder.refresh_bank_as_of(
            db_session,
            organization_id="OR-1S000002",
            bank_id=SAMPLE_BANK_ID,
            as_of=AS_OF,
            reason="test",
        )
    assert _count(db_session, BiMartBuild) == 0


def test_baseline_scenario_matches_the_modules_own_constant() -> None:
    from app.services import (  # noqa: PLC0415
        regulatory_capital,
        regulatory_credit,
        regulatory_liquidity,
    )

    for module in (regulatory_capital, regulatory_credit, regulatory_liquidity):
        assert module.BASELINE_SCENARIO == mart_builder.BASELINE_SCENARIO


# --- backfill ---------------------------------------------------------------------------------


def test_backfill_walks_dates_newest_first_within_the_budget(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_book(db_session, live=False)
    may = date(2026, 5, 29)
    for day in (MID_MONTH, may):
        resnapshot_loan_1(db_session, day, balance="1")
    db_session.commit()
    built: list[date] = []
    real = mart_builder.refresh_bank_as_of

    def spy(db: Session, **kwargs: Any) -> BuildOutcome:
        built.append(kwargs["as_of"])
        return real(db, **kwargs)

    monkeypatch.setattr(mart_builder, "refresh_bank_as_of", spy)
    # a zero budget builds exactly one date per hop
    next_cursor = mart_builder.backfill_step(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        cursor=AS_OF,
        until=may,
        budget_seconds=0,
    )
    assert built == [AS_OF] and next_cursor == MID_MONTH
    assert next_cursor is not None
    next_cursor = mart_builder.backfill_step(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        cursor=next_cursor,
        until=may,
        budget_seconds=0,
    )
    assert built == [AS_OF, MID_MONTH] and next_cursor == may
    assert next_cursor is not None
    next_cursor = mart_builder.backfill_step(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        cursor=next_cursor,
        until=may,
        budget_seconds=0,
    )
    assert built == [AS_OF, MID_MONTH, may] and next_cursor is None
    # a generous budget finishes in one hop; ``until`` is inclusive and a floor
    built.clear()
    assert (
        mart_builder.backfill_step(
            db_session,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            cursor=AS_OF,
            until=MID_MONTH,
            budget_seconds=600,
        )
        is None
    )
    assert built == [AS_OF, MID_MONTH]
    assert {row.as_of_date for row in db_session.scalars(select(BiMartBuild))} == {
        AS_OF,
        MID_MONTH,
        may,
    }


# --- retention (D-039) ----------------------------------------------------------------------------


def test_retention_names_only_the_daily_parents_and_is_a_no_op_off_postgres(
    db_session: Session,
) -> None:
    assert BiFactPositionEom.__tablename__ not in mart_builder.RETENTION_PARENTS
    assert BiFactLoanEvent.__tablename__ not in mart_builder.RETENTION_PARENTS
    assert set(mart_builder.RETENTION_PARENTS) == {
        "bi_fact_position_daily",
        "bi_agg_position_daily",
    }
    assert set(mart_builder.RETENTION_PARENTS) <= set(partitions.MONTHLY_BUILD_PARENTS)
    assert BiFactPositionEom.__tablename__ in partitions.YEARLY_BUILD_PARENTS
    assert mart_builder.apply_retention(db_session, retention_days=95) == ()
    with pytest.raises(ValueError, match="positive"):
        mart_builder.apply_retention(db_session, retention_days=0)


def test_partition_wrapper_is_inert_off_postgres(db_session: Session) -> None:
    """What the wrapper does on a backend WITHOUT partitions, which is SQLite.

    The Postgres behaviour is the opposite and is proven where it can actually be
    proven -- `tests/db`, against a real server with the `SECURITY DEFINER`
    ensure/drop functions installed. Run here on Postgres this asserts the
    negation of the truth, so it skips by dialect exactly as the compiler
    snapshots do.
    """
    if db_session.get_bind().dialect.name != "sqlite":
        pytest.skip("the SQLite path; tests/db proves the Postgres partition guards")
    assert partitions.is_postgres(db_session) is False
    assert partitions.is_partitioned(db_session, "bi_fact_position_daily") is False
    assert partitions.ensure_for_build(db_session, as_of=AS_OF) == []
    assert partitions.drop_month_partition(db_session, "bi_fact_position_daily", AS_OF) is False


def test_snapshot_dates_and_month_end_helpers(db_session: Session) -> None:
    seed_book(db_session, live=False)
    assert mart_builder.snapshot_dates(db_session, ORG_1, SAMPLE_BANK_ID) == [AS_OF]
    assert (
        mart_builder.last_snapshot_date_in_month(db_session, ORG_1, SAMPLE_BANK_ID, MID_MONTH)
        == AS_OF
    )
    assert (
        mart_builder.last_snapshot_date_in_month(
            db_session, ORG_1, SAMPLE_BANK_ID, date(2026, 5, 1)
        )
        is None
    )
    assert mart_builder.month_bounds(date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))
