"""Concentration monitor service (credit PR-3): loading, regime basis, registers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from app.schemas.regulatory_credit import (
    ConcentrationLimitEntry,
    ConcentrationLimitUpdate,
    CreditThresholdUpdate,
)
from app.services import credit_concentration, credit_params, fact_derivation
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _prepare(db_session: Session) -> Bank:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    fact_derivation.derive_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    fact_derivation.derive_current_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


def _stamp_employers(db_session: Session) -> None:
    """Add employer attributes to two loan snapshots, ingested-style."""
    rows = db_session.execute(
        select(CanonicalPositionSnapshot)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPosition.position_type == "LOAN",
            CanonicalPositionSnapshot.superseded_by.is_(None),
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).scalars()
    for index, snapshot in enumerate(list(rows)[:2]):
        snapshot.attributes = {
            **(snapshot.attributes or {}),
            "employer": "Ghana Education Service" if index == 0 else "ACME Mining",
        }
    db_session.commit()


def test_monitor_reads_the_book_with_employer_coverage_disclosed(db_session: Session) -> None:
    bank = _prepare(db_session)
    _stamp_employers(db_session)
    result = credit_concentration.monitor(db_session, CTX, bank, FIXTURE_AS_OF)
    employer = result.dimension("employer")
    assert employer is not None
    assert employer.bucket_count == 2
    assert Decimal("0") < employer.coverage_pct < Decimal("100")
    single = result.dimension("single_name")
    assert single is not None
    assert single.coverage_pct == Decimal("100")
    # The bank fixture derives capital components, so the Tier-1 basis resolves.
    assert result.capital_base_ghs is not None


def test_board_limits_flow_from_the_register_into_breaches(db_session: Session) -> None:
    bank = _prepare(db_session)
    _stamp_employers(db_session)
    credit_params.update_concentration_limit_register(
        db_session,
        CTX,
        SAMPLE_BANK_ID,
        ConcentrationLimitUpdate(
            effective_from=date(2026, 1, 1),
            approved_by="Board Credit Committee",
            reason="Initial concentration limit structure",
            limits=[
                ConcentrationLimitEntry(
                    dimension="employer", limit_kind="share_of_book_pct", value=Decimal("0.5")
                )
            ],
        ),
    )
    result = credit_concentration.monitor(db_session, CTX, bank, FIXTURE_AS_OF)
    assert result.breaches, "a 0.5%-of-book employer limit must breach on this fixture"
    assert all(b.limit_status == "above_limit" for b in result.breaches)


def _seed_sector_loans(
    db_session: Session, loans: tuple[tuple[str, str, str, str, str | None], ...]
) -> Bank:
    """Sample Bank with ONLY these LOAN rows
    ``(reference, currency, native balance, sector, balance_ghs)``;
    ``balance_ghs=None`` means no ingested conversion."""
    materialize_canonical_test_book(db_session)
    db_session.flush()
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
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
        operation_ref="sector-fx-book",
        input_lineage_ids=[],
    )
    db_session.add(lineage)
    db_session.flush()
    common = {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": FIXTURE_AS_OF,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }
    for ref, currency, balance, sector, balance_ghs in loans:
        position = CanonicalPosition(
            **common, source_reference=ref, position_type="LOAN", currency=currency
        )
        db_session.add(position)
        db_session.flush()
        attributes: dict[str, object] = {"sector": sector}
        if balance_ghs is not None:
            attributes["balance_ghs"] = balance_ghs
        db_session.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=ref,
                position_id=position.id,
                balance=Decimal(balance),
                ifrs9_stage=1,
                attributes=attributes,
            )
        )
    db_session.commit()
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


def test_concentration_excludes_an_unconverted_foreign_currency_loan(db_session: Session) -> None:
    """A foreign-currency loan with no ingested ``balance_ghs`` is UNCONVERTED
    and leaves every concentration total and share (H-010, the same rule as
    ``regulatory_credit._employer_par30_stats`` / ``_event_amount_ghs``). It
    used to fall back to the native face value — USD 1m entered the book as
    1m in the reporting unit — so Mining read 91.67% of a 1.2m book and the
    sector HHI 8472; over the two base-currency loans it is 50% of 200k and
    5000. A base-currency loan with no ``balance_ghs`` still counts at its
    (base-currency) face value."""
    bank = _seed_sector_loans(
        db_session,
        (
            ("LOAN/FX/1", "GHS", "100000", "Agriculture", "100000"),
            ("LOAN/FX/2", "GHS", "100000", "Mining", None),
            ("LOAN/FX/3", "USD", "1000000", "Mining", None),
        ),
    )

    exposures = credit_concentration.load_credit_exposures(db_session, CTX, bank, FIXTURE_AS_OF)
    assert [e.exposure_id for e in exposures] == ["LOAN/FX/1", "LOAN/FX/2"], (
        "the unconverted USD loan must not enter the exposure book (it used to, at USD face value)"
    )
    assert exposures[1].ead == Decimal("100000"), "a base-currency loan keeps its native balance"

    result = credit_concentration.monitor(db_session, CTX, bank, FIXTURE_AS_OF)
    assert result.total_book_ghs == Decimal("200000"), (
        f"total book {result.total_book_ghs} — the old face-value fallback gave 1200000"
    )
    sector = result.dimension("sector")
    assert sector is not None
    mining = {b.key: b for b in sector.buckets}["Mining"]
    assert mining.exposure_ghs == Decimal("100000"), (
        f"Mining exposure {mining.exposure_ghs} — the old fallback counted USD 1m as 1100000"
    )
    assert mining.share_of_book_pct == Decimal("50"), (
        f"Mining share {mining.share_of_book_pct}% — the old fallback gave 91.666667%"
    )
    assert sector.hhi == Decimal("5000"), f"sector HHI {sector.hhi} — the old fallback gave 8472"
    single = result.dimension("single_name")
    assert single is not None
    assert single.bucket_count == 2, "the unconverted loan is not a single-name bucket either"


def test_threshold_register_rejects_unknown_codes(db_session: Session) -> None:
    _prepare(db_session)
    with pytest.raises(HTTPException) as raised:
        credit_params.update_credit_threshold_register(
            db_session,
            CTX,
            SAMPLE_BANK_ID,
            CreditThresholdUpdate(
                effective_from=date(2026, 1, 1),
                approved_by="Board",
                reason="typo test",
                thresholds={"npl_bored_trigger_pct": Decimal("8")},
            ),
        )
    assert raised.value.status_code == 422
