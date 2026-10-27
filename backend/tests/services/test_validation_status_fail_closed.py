"""Absence is not acceptance (enterprise audit 2026-08-20, P0-11).

``ingestion._persist_canonical`` resolved each record's validation status with
``record_statuses.get(key, "accepted")``. A canonical record for which
validation produced NO status was therefore persisted as *accepted* and was
thereafter indistinguishable — in every engine and every filed return — from a
record that genuinely passed.

The default is now ``pending``: a status every calculation reader excludes (all
of them admit only ``("accepted", "warning")``), so an unvalidated row is
persisted, visible and traceable, but cannot reach a regulatory number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import Table, select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import Base
from app.domain.ingestion.constants import (
    INCLUDED_VALIDATION_STATUSES,
    VALIDATION_STATUSES,
)
from app.domain.ingestion.contracts import CanonicalRecords, GlAccountData
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalGlAccount,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from app.reporting.service.bog_forms.catalog import all_form_specs
from app.reporting.service.bog_forms.engine import FormResult
from app.reporting.service.bog_forms.generation import compute_with_dependencies
from app.reporting.service.bog_forms.sources import ResolveContext, get_resolver
from app.reporting.service.bog_forms.sources_ext.bsd11 import largest_exposures
from app.services import ingestion
from app.services.fact_derivation import _INCLUDED_VALIDATION_STATUSES
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.factories.canonical import seed_canonical_fixture
from tests.support.helpers import ORG_1, USER_1


def test_unvalidated_status_is_a_real_status_that_no_engine_reads() -> None:
    assert ingestion.UNVALIDATED_STATUS in VALIDATION_STATUSES
    assert ingestion.UNVALIDATED_STATUS not in INCLUDED_VALIDATION_STATUSES
    # The derivation's own constant must BE the shared scope, not a copy that
    # happens to agree with it today (re-audit D-4: the literal was duplicated
    # into eighteen module-private constants and the reporting layer simply
    # omitted it).
    assert _INCLUDED_VALIDATION_STATUSES == INCLUDED_VALIDATION_STATUSES


def test_absent_status_persists_as_pending_not_accepted(db_session: Session) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="Synthetic validation persistence bank",
        short_name="status-fixture",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        as_of_date=date(2026, 7, 31),
        adapter_version="1.0",
        extraction_mode="full",
        status="validating",
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        bank_id=bank.id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="missing-validation-status",
    )
    db_session.add(lineage)
    db_session.flush()
    records = CanonicalRecords(
        gl_accounts=[
            GlAccountData(
                source_reference=reference,
                source_locator=f"GL:{index}",
                account_code=reference,
                name="Synthetic validation control",
                account_class="ASSET",
                currency="GHS",
                balance=Decimal("100"),
            )
            for index, reference in enumerate(("KNOWN", "NEVER_VALIDATED"), start=2)
        ]
    )
    # Exercise the production persistence boundary, including its missing-status fallback.
    ingestion._persist_canonical(  # pyright: ignore[reportPrivateUsage]
        db_session,
        TenantContext(organization_id=ORG_1, actor_user_id=USER_1),
        bank,
        batch,
        lineage,
        records,
        {("gl_account", "KNOWN"): "warning"},
        (),
    )
    db_session.commit()
    bank_id = bank.id
    db_session.expire_all()
    persisted = db_session.scalars(
        select(CanonicalGlAccount).where(CanonicalGlAccount.bank_id == bank_id)
    )
    assert {row.source_reference: row.validation_status for row in persisted} == {
        "KNOWN": "warning",
        "NEVER_VALIDATED": "pending",
    }


@dataclass(frozen=True)
class _Record:
    source_reference: str


@dataclass(frozen=True)
class _Records:
    gl_accounts: tuple[_Record, ...] = ()
    counterparties: tuple[_Record, ...] = ()
    products: tuple[_Record, ...] = ()
    positions: tuple[_Record, ...] = ()
    loan_events: tuple[_Record, ...] = ()


def test_unvalidated_records_are_counted_per_entity_type() -> None:
    records = _Records(
        positions=(_Record("P1"), _Record("P2"), _Record("P3")),
        products=(_Record("PR1"),),
    )
    statuses = {("position", "P1"): "accepted"}

    missing = ingestion._unvalidated_records(records, statuses)  # type: ignore[arg-type]

    assert missing == {"position": 2, "product": 1}


def test_a_fully_validated_batch_reports_no_unvalidated_records() -> None:
    """The healthy path: ``run_validation`` seeds a status for every record."""
    records = _Records(positions=(_Record("P1"),), gl_accounts=(_Record("G1"),))
    statuses = {("position", "P1"): "accepted", ("gl_account", "G1"): "accepted"}

    assert ingestion._unvalidated_records(records, statuses) == {}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The filed-return half of the claim (forensic re-audit 2026-08-22, D-4)
# ---------------------------------------------------------------------------


def test_the_shared_bog_position_resolver_reads_the_same_scope_as_the_engines(
    db_session: Session,
) -> None:
    """``positions.sum`` is the resolver behind BSD2 and BSD5A.

    Until 2026-08-22 it filtered the current generation
    (``superseded_by``/``withdrawn_at``) but NOT ``validation_status``, so the
    Capital Adequacy Return read canonical rows the capital engine refuses —
    while the module docstring on ``ingestion.status_of`` asserted the exclusion
    held "in every engine and every filed return". Only 2 of the 14
    ``sources_ext`` modules carried the predicate.

    The fixture's ``LOAN/BAD`` snapshot is deliberately enormous
    (888,000,000) and carries ``validation_status="error"``, so a leak is
    unmistakable rather than a rounding argument.
    """
    materialize_canonical_test_book(db_session)
    db_session.flush()
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    period = db_session.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
        )
        .order_by(BankReportingPeriod.period_end.desc())
    )
    assert period is not None
    # The resolver reads "latest snapshot on or before the period end", so the
    # canonical book is seeded AT the period end to be in scope at all.
    seed_canonical_fixture(
        db_session,
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        as_of=period.period_end,
    )
    db_session.flush()

    rejected = db_session.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.organization_id == DEMO_ORG_ID,
            CanonicalPositionSnapshot.bank_id == SAMPLE_BANK_ID,
            CanonicalPositionSnapshot.validation_status == "error",
        )
    )
    assert rejected is not None, "the fixture must carry a rejected row for this to mean anything"

    rc = ResolveContext(
        db=db_session,
        ctx=TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID),
        bank=bank,
        period=period,
        column="total",
    )
    total = get_resolver("positions.sum")(rc, {"position_types": ["LOAN"]})

    assert total is not None
    assert Decimal(str(total)) > 0, "the resolver must still read the accepted book"
    assert Decimal(str(total)) < Decimal("888000000"), (
        "the rejected 888,000,000 snapshot reached a filed BoG return line"
    )


def _report_book(db_session: Session) -> tuple[Bank, BankReportingPeriod, TenantContext]:
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    period = db_session.scalar(
        select(BankReportingPeriod)
        .where(BankReportingPeriod.bank_id == bank.id)
        .order_by(BankReportingPeriod.period_end.desc())
    )
    assert period is not None
    ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
    return bank, period, ctx


def test_every_bog_form_excludes_an_ineligible_canonical_book(
    db_session: Session,
) -> None:
    bank, period, ctx = _report_book(db_session)

    def figures() -> dict[str, dict[tuple[str, str], object]]:
        seen: dict[str, FormResult] = {}
        return {
            spec.code: cast(
                dict[tuple[str, str], object],
                compute_with_dependencies(
                    db_session, ctx, bank, period, spec.code, _seen=seen
                ).all_values(),
            )
            for spec in all_form_specs()
        }

    empty_book = figures()
    seed_canonical_fixture(
        db_session, organization_id=DEMO_ORG_ID, bank_id=bank.id, as_of=period.period_end
    )
    db_session.flush()
    accepted_book = figures()
    # These forms need inputs outside this compact canonical book. Keep the
    # exceptions explicit so a new form cannot silently get a vacuous control.
    unpopulated = {
        "BSD1A": "No withdrawal-transactions register",
        "BSD1B": "No daily FX run at the weekly reporting anchor",
        "BSD3B": "No subsidiary exposure register",
        "BSD5A": "Capital facts and persisted runs are unchanged by this canonical overlay",
        "BSD5B": "No consolidated capital inputs",
        "BSD7A": "No income and expense GL accounts",
        "BSD7B": "No consolidated income and expense inputs",
        "BSD10": "No capital-expenditure register",
        "BSD14": "No contractual rates at the weekly reporting anchor",
        "BSD15A": "No domestic tariff register",
        "BSD15B": "No foreign tariff register",
        "BSD16": "No ATM operations register",
        "BSD17": "No remittance-flows register",
    }
    assert set(unpopulated) < set(accepted_book)
    for code, admitted in accepted_book.items():
        if code in unpopulated:
            assert admitted == empty_book[code], unpopulated[code]
        else:
            assert admitted != empty_book[code], f"{code} must read an admitted sentinel"
    tables = [
        mapper.local_table
        for mapper in Base.registry.mappers
        if isinstance(mapper.local_table, Table)
        and mapper.local_table.name.startswith("canonical_")
        and "validation_status" in mapper.local_table.columns
    ]
    for table in tables:
        db_session.execute(
            update(table)
            .where(
                table.columns["bank_id"] == bank.id,
                table.columns["validation_status"] == "accepted",
            )
            .values(validation_status="warning")
        )
    db_session.expire_all()
    assert figures() == accepted_book
    for state in ("pending", "error", "blocked", "withdrawn", "superseded"):
        savepoint = db_session.begin_nested()
        try:
            for table in tables:
                if state == "withdrawn":
                    values: dict[str, object] = {"withdrawn_at": datetime.now(UTC)}
                elif state == "superseded":
                    values = {"superseded_by": table.columns["id"]}
                else:
                    values = {"validation_status": state}
                db_session.execute(
                    update(table).where(table.columns["bank_id"] == bank.id).values(**values)
                )
            db_session.expire_all()
            assert figures() == empty_book, state
        finally:
            savepoint.rollback()
            db_session.expire_all()


@pytest.mark.parametrize("state", ["pending", "error", "blocked", "withdrawn", "superseded"])
def test_bsd11_keeps_the_last_admitted_snapshot_when_the_newest_is_ineligible(
    db_session: Session, state: str
) -> None:
    bank, period, ctx = _report_book(db_session)
    seed_canonical_fixture(
        db_session, organization_id=ctx.organization_id, bank_id=bank.id, as_of=period.period_end
    )
    db_session.flush()
    previous = db_session.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.source_reference == "LOAN/1",
        )
    )
    assert previous is not None
    previous.as_of_date = period.period_end - timedelta(days=1)
    db_session.flush()

    def exposures() -> dict[str, Decimal]:
        rc = ResolveContext(
            db=db_session,
            ctx=ctx,
            bank=bank,
            period=period,
            column="total",
            dependencies={},
            cache={},
        )
        return {row.key: row.total for row in largest_exposures(rc)}

    expected = exposures()
    assert f"cp:{previous.counterparty_id}" in expected
    newest = CanonicalPositionSnapshot(
        organization_id=previous.organization_id,
        bank_id=bank.id,
        source_reference=previous.source_reference,
        source_system=previous.source_system,
        as_of_date=period.period_end,
        ingestion_batch_id=previous.ingestion_batch_id,
        lineage_id=previous.lineage_id,
        position_id=previous.position_id,
        counterparty_id=previous.counterparty_id,
        product_id=previous.product_id,
        balance=Decimal("888000000"),
        attributes={"balance_ghs": "888000000"},
        validation_status="accepted",
    )
    db_session.add(newest)
    db_session.flush()
    assert exposures() != expected
    if state == "withdrawn":
        newest.withdrawn_at = datetime.now(UTC)
    elif state == "superseded":
        newest.superseded_by = previous.id
    else:
        newest.validation_status = state
    db_session.flush()
    assert exposures() == expected
