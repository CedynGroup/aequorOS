"""The shared canonical exposure book (``app/services/credit_exposure_book.py``).

Extracted from ``enterprise_stress`` so the ICAAP granularity adjustment reads
the same slice of the book. Two things are pinned here:

1. the slice itself — current generation only, filtered by position type, with
   the connected-group identity resolved the way ``le_generation`` resolves it;
2. the distinction the extraction exists for: an amount with no conversion into
   the reporting currency is ``None``, not zero. ``enterprise_stress._reported``
   is the adapter that keeps the stress test's historical reading, and it is
   asserted here so a future edit cannot quietly change what a sealed run means.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
)
from app.services import credit_exposure_book, enterprise_stress
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _prepare(db_session: Session) -> Bank:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    db_session.commit()
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


def _add_position(  # noqa: PLR0913 - one keyword per canonical column under test
    db_session: Session,
    *,
    source_reference: str,
    currency: str,
    balance: str,
    attributes: dict[str, Any] | None = None,
    counterparty: CanonicalCounterparty | None = None,
    as_of: date = FIXTURE_AS_OF,
) -> None:
    """One extra LOAN row, reusing the fixture's ingestion provenance."""
    template = db_session.scalar(
        select(CanonicalPositionSnapshot)
        .where(CanonicalPositionSnapshot.source_reference == "LOAN/1")
        .limit(1)
    )
    assert template is not None
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": as_of,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": template.ingestion_batch_id,
        "lineage_id": template.lineage_id,
        "validation_status": "accepted",
    }
    position = CanonicalPosition(
        **common, source_reference=source_reference, position_type="LOAN", currency=currency
    )
    db_session.add(position)
    db_session.flush()
    db_session.add(
        CanonicalPositionSnapshot(
            **common,
            source_reference=source_reference,
            position_id=position.id,
            counterparty_id=counterparty.id if counterparty is not None else None,
            balance=Decimal(balance),
            attributes=attributes or {},
        )
    )
    db_session.commit()


def _rows(db_session: Session, bank: Bank) -> dict[str, credit_exposure_book.ExposureRow]:
    loaded = credit_exposure_book.load_exposure_rows(
        db_session,
        CTX,
        bank,
        FIXTURE_AS_OF,
        credit_exposure_book.CONCENTRATION_POSITION_TYPES,
    )
    return {row.source_reference: row for row in loaded}


def test_the_book_is_the_current_generation_only(db_session: Session) -> None:
    """A superseded snapshot and a rejected one are not part of the book."""
    bank = _prepare(db_session)
    rows = _rows(db_session, bank)
    assert "LOAN/1" in rows
    assert "LOAN/OLD" not in rows  # superseded
    assert "LOAN/BAD" not in rows  # validation_status="error"


def test_the_position_type_filter_selects_the_asked_for_book(db_session: Session) -> None:
    bank = _prepare(db_session)
    credit_only = credit_exposure_book.load_exposure_rows(
        db_session, CTX, bank, FIXTURE_AS_OF, credit_exposure_book.CREDIT_POSITION_TYPES
    )
    with_securities = credit_exposure_book.load_exposure_rows(
        db_session, CTX, bank, FIXTURE_AS_OF, credit_exposure_book.CONCENTRATION_POSITION_TYPES
    )
    funding = credit_exposure_book.load_exposure_rows(
        db_session, CTX, bank, FIXTURE_AS_OF, credit_exposure_book.FUNDING_POSITION_TYPES
    )
    assert {row.position_type for row in credit_only} <= {"LOAN", "INTERBANK_PLACEMENT"}
    assert "SECURITY_HOLDING" in {row.position_type for row in with_securities}
    assert {row.position_type for row in funding} <= {"DEPOSIT", "INTERBANK_BORROWING"}
    # The rows arrive sorted by source reference, so a caller's order is stable.
    assert [row.source_reference for row in with_securities] == sorted(
        row.source_reference for row in with_securities
    )


def test_a_position_in_the_reporting_currency_is_taken_at_face_value(
    db_session: Session,
) -> None:
    bank = _prepare(db_session)
    _add_position(db_session, source_reference="LOAN/BASE", currency="GHS", balance="2500000")
    row = _rows(db_session, bank)["LOAN/BASE"]
    assert row.is_foreign_currency is False
    assert row.balance_rep == Decimal("2500000")
    assert row.unconverted is False


def test_an_ingested_conversion_wins_over_the_native_balance(db_session: Session) -> None:
    bank = _prepare(db_session)
    row = _rows(db_session, bank)["LOAN/USD"]
    assert row.currency == "USD"
    assert row.is_foreign_currency is True
    # 1,000,000 USD ingested as 12,850,000 in the reporting currency.
    assert row.balance_rep == Decimal("12850000")
    assert row.unconverted is False


def test_a_foreign_position_with_no_ingested_conversion_is_unconverted(
    db_session: Session,
) -> None:
    """The distinction the extraction exists for: unknown is not zero."""
    bank = _prepare(db_session)
    _add_position(db_session, source_reference="LOAN/EUR", currency="EUR", balance="750000")
    row = _rows(db_session, bank)["LOAN/EUR"]
    assert row.is_foreign_currency is True
    assert row.balance_rep is None
    assert row.unconverted is True
    # And the stress test still reads it as contributing nothing, as it always has.
    assert enterprise_stress._reported(row.balance_rep) == Decimal(0)  # noqa: SLF001


def test_an_empty_conversion_attribute_is_not_a_conversion(db_session: Session) -> None:
    bank = _prepare(db_session)
    _add_position(
        db_session,
        source_reference="LOAN/EMPTY",
        currency="EUR",
        balance="750000",
        attributes={"balance_ghs": ""},
    )
    assert _rows(db_session, bank)["LOAN/EMPTY"].balance_rep is None


def test_an_empty_position_in_the_reporting_currency_is_zero_not_unknown(
    db_session: Session,
) -> None:
    """A balance of nothing is a measured nothing; an unstated notional is not.

    ``canonical_position_snapshots.balance`` is NOT NULL, so a position in the
    bank's own currency always states one — ``None`` on ``balance_rep`` can
    therefore only ever mean an unconverted foreign position, which is exactly
    what the granularity adjustment relies on to exclude one. The notional is
    nullable, because most positions do not have one at all.
    """
    bank = _prepare(db_session)
    _add_position(db_session, source_reference="LOAN/ZERO", currency="GHS", balance="0")
    row = _rows(db_session, bank)["LOAN/ZERO"]
    assert row.balance_rep == Decimal(0)
    assert row.unconverted is False
    assert row.notional_rep is None
    assert enterprise_stress._reported(row.notional_rep) == Decimal(0)  # noqa: SLF001


def test_the_group_key_follows_the_connected_group_then_the_name(
    db_session: Session,
) -> None:
    bank = _prepare(db_session)
    rows = _rows(db_session, bank)
    # The fixture's corporate counterparty carries no group reference, so the
    # name is the identity, and both of its facilities share it.
    assert rows["LOAN/1"].group_key == "cp:Volta Agro Ltd"
    assert rows["LOAN/USD"].group_key == rows["LOAN/1"].group_key
    # A position with no counterparty at all is its own name — never merged.
    assert rows["LOAN/2"].group_key == "pos:LOAN/2"


def test_a_connected_group_reference_beats_the_counterparty_name() -> None:
    class _Counterparty:
        group_reference = "GRP-7"
        attributes: dict[str, Any] = {}
        name = "Subsidiary Ltd"

    class _Unconnected:
        group_reference = None
        attributes = {"parent": "Holdco Plc"}
        name = "Subsidiary Ltd"

    assert credit_exposure_book.canonical_group_key("LOAN/9", _Counterparty()) == "group:GRP-7"
    assert credit_exposure_book.canonical_group_key("LOAN/9", _Unconnected()) == "group:Holdco Plc"
    assert credit_exposure_book.canonical_group_key("LOAN/9", None) == "pos:LOAN/9"


def test_a_counterparty_less_security_is_identified_by_its_issuer(
    db_session: Session,
) -> None:
    bank = _prepare(db_session)
    _add_position(
        db_session,
        source_reference="SEC/ISSUER",
        currency="GHS",
        balance="5000000",
        attributes={"issuer": "Ghana Cocoa Board"},
    )
    assert _rows(db_session, bank)["SEC/ISSUER"].group_key == "issuer:Ghana Cocoa Board"


def test_the_row_carries_the_counterparty_and_product_context(db_session: Session) -> None:
    bank = _prepare(db_session)
    row = _rows(db_session, bank)["LOAN/1"]
    assert row.counterparty_type == "CORPORATE"
    assert row.product_code == "LN.CORP.5Y"
    assert row.regulatory_category == "CORPORATE_UNRATED"
    assert row.ifrs9_stage == 1
    assert row.attributes["ecl_provision_ghs"] == "300000"


def test_another_dates_book_is_not_this_dates_book(db_session: Session) -> None:
    bank = _prepare(db_session)
    _add_position(
        db_session,
        source_reference="LOAN/NEXT",
        currency="GHS",
        balance="1000000",
        as_of=date(2026, 9, 30),
    )
    assert "LOAN/NEXT" not in _rows(db_session, bank)
    later = credit_exposure_book.load_exposure_rows(
        db_session,
        CTX,
        bank,
        date(2026, 9, 30),
        credit_exposure_book.CONCENTRATION_POSITION_TYPES,
    )
    assert [row.source_reference for row in later] == ["LOAN/NEXT"]


def test_a_withdrawn_snapshot_leaves_the_book(db_session: Session) -> None:
    bank = _prepare(db_session)
    snapshot = db_session.scalar(
        select(CanonicalPositionSnapshot)
        .where(CanonicalPositionSnapshot.source_reference == "LOAN/3")
        .limit(1)
    )
    assert snapshot is not None
    snapshot.withdrawn_at = datetime.combine(FIXTURE_AS_OF, time(), tzinfo=UTC)
    db_session.commit()
    assert "LOAN/3" not in _rows(db_session, bank)


def test_the_stress_service_reads_the_shared_book(db_session: Session) -> None:
    """The extraction is a delegation, not a copy: one definition, two readers."""
    assert enterprise_stress._ExposureRow is credit_exposure_book.ExposureRow  # noqa: SLF001
    assert enterprise_stress._load_exposure_rows is (  # noqa: SLF001
        credit_exposure_book.load_exposure_rows
    )
    assert (
        enterprise_stress._CONCENTRATION_POSITION_TYPES  # noqa: SLF001
        == credit_exposure_book.CONCENTRATION_POSITION_TYPES
    )
