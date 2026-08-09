"""Publication fan-out: every bank, through the adapter seam, zero quota.

The desk never writes canonical rows directly — publish drives
``execute_pull`` per bank, so every published row carries batch + lineage +
raw-tier provenance, supersedes idempotently, and coexists with vendor rows
(AEQ curve names never collide with vendor curve names).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.market_data import pull_runner
from app.db.base import utc_now
from app.models import (
    Bank,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    DeskPublication,
    IngestionBatch,
    LineageRecord,
    MarketDataQuotaUsage,
)
from app.services.market_desk import determinations, observations, publication, register
from app.storage.client import StorageLocation
from tests.api.helpers import ORG_1, ORG_2
from tests.storage.inmemory import InMemoryStorageClient

COB = date(2026, 8, 7)
ANALYST = "analyst@aequoros.com"
LEAD = "lead@aequoros.com"

DERIVED_VALUES: dict[str, Any] = {
    "curves": {
        "AEQ.GHS.SOV.ZERO": {
            "curve_type": "zero",
            "points": [
                {"tenor_months": 3, "rate_pct": 24.5},
                {"tenor_months": 6, "rate_pct": 24.9},
                {"tenor_months": 12, "rate_pct": 25.4},
            ],
        },
        "AEQ.GHS.SOV.FWD": {
            "curve_type": "forward",
            "points": [
                {"tenor_months": 3, "rate_pct": 24.7},
                {"tenor_months": 6, "rate_pct": 25.3},
            ],
        },
        "AEQ.GHS.OIS": {
            "curve_type": "discount",
            "points": [
                {"tenor_months": 1, "rate_pct": 13.4},
                {"tenor_months": 3, "rate_pct": 13.6},
            ],
        },
    },
    "reference_rates": {"GHS.MPR": 15.0, "GHS.GRR": 21.3},
    "fx_rates": {"USD/GHS": 12.85},
}

STORAGE_FACTORY_TARGETS = (
    "app.adapters.market_data.pull_runner.get_storage_client",
    "app.adapters.market_data.cache.get_storage_client",
)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    for target in STORAGE_FACTORY_TARGETS:
        monkeypatch.setattr(target, lambda: client)
    return client


def _make_bank(db: Session, org_id: str, name: str) -> Bank:
    bank = Bank(
        organization_id=org_id,
        name=name,
        short_name=name.lower().replace(" ", "-"),
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    db.add(bank)
    db.commit()
    return bank


@pytest.fixture
def banks(db_session: Session) -> tuple[Bank, Bank]:
    return (
        _make_bank(db_session, ORG_1, "Desk Pub Bank One"),
        _make_bank(db_session, ORG_2, "Desk Pub Bank Two"),
    )


def _approved_determination(db: Session) -> Any:
    register.ensure_default_methodology(db)
    register.approve_version(
        db,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 1, 1),
    )
    observations.record_manual_observation(
        db,
        series_code="GHS.MPR",
        as_of_date=date(2026, 7, 22),
        value=Decimal("15.00"),
        unit="pct",
        entered_by=ANALYST,
    )
    draft = determinations.create_draft(db, cob_date=COB, prepared_by=ANALYST)
    determinations.set_results(
        db, draft.id, derived_values=DERIVED_VALUES, qa_results={"forward_positivity": "pass"}
    )
    determinations.submit_for_review(db, draft.id)
    determinations.approve(db, draft.id, reviewed_by=LEAD)
    db.commit()
    return draft


def _current_curves(db: Session, bank: Bank, curve_name: str) -> list[CanonicalYieldCurve]:
    return list(
        db.scalars(
            select(CanonicalYieldCurve).where(
                CanonicalYieldCurve.organization_id == bank.organization_id,
                CanonicalYieldCurve.bank_id == bank.id,
                CanonicalYieldCurve.as_of_date == COB,
                CanonicalYieldCurve.curve_name == curve_name,
                CanonicalYieldCurve.superseded_by.is_(None),
            )
        )
    )


def test_publish_writes_canonical_market_data_for_every_bank(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    determination = _approved_determination(db_session)
    result = publication.publish(db_session, determination.id, actor=LEAD)

    assert result.status == "complete"
    assert len(result.results) == 2
    assert all(entry["status"] == "complete" for entry in result.results)
    assert {entry["bank_id"] for entry in result.results} == {banks[0].id, banks[1].id}

    for bank in banks:
        curves = _current_curves(db_session, bank, "AEQ.GHS.SOV.ZERO")
        assert len(curves) == 1
        curve = curves[0]
        assert curve.source_system == "AEQUOR_DESK"
        assert curve.curve_type == "zero"
        assert curve.source_reference == f"desk:determination:{determination.id}"

        # Points landed as decimal fractions (24.5% -> 0.245).
        points = list(
            db_session.scalars(
                select(CanonicalYieldCurvePoint).where(
                    CanonicalYieldCurvePoint.yield_curve_id == curve.id
                )
            )
        )
        assert {p.tenor_months for p in points} == {3, 6, 12}
        assert {Decimal(str(p.rate)) for p in points} == {
            Decimal("0.245"),
            Decimal("0.249"),
            Decimal("0.254"),
        }

        # The other curve families and record types landed too.
        assert len(_current_curves(db_session, bank, "AEQ.GHS.OIS")) == 1
        assert len(_current_curves(db_session, bank, "AEQ.GHS.SOV.FWD")) == 1
        indices = list(
            db_session.scalars(
                select(CanonicalMarketIndex).where(
                    CanonicalMarketIndex.bank_id == bank.id,
                    CanonicalMarketIndex.superseded_by.is_(None),
                )
            )
        )
        assert {i.index_code for i in indices} == {"GHS.MPR", "GHS.GRR"}
        assert all(i.scenario == "base" for i in indices)
        fx = list(
            db_session.scalars(
                select(CanonicalFxRate).where(
                    CanonicalFxRate.bank_id == bank.id,
                    CanonicalFxRate.superseded_by.is_(None),
                )
            )
        )
        assert len(fx) == 1
        assert (fx[0].base_currency, fx[0].quote_currency) == ("USD", "GHS")

        # Batch + lineage + raw-tier provenance (the no-seeding order's spine).
        batch_ids = {
            entry["ingestion_batch_id"]
            for entry in result.results
            if entry["bank_id"] == bank.id
        }
        assert len(batch_ids) == 1
        batch = db_session.get(IngestionBatch, UUID(next(iter(batch_ids))))
        assert batch is not None
        assert batch.source_system == "AEQUOR_DESK"
        assert batch.status in ("accepted", "accepted_with_warnings")
        lineage_count = db_session.scalar(
            select(func.count())
            .select_from(LineageRecord)
            .where(LineageRecord.ingestion_batch_id == batch.id)
        )
        assert (lineage_count or 0) >= 3  # extract -> translate -> validation
        raw = storage.read(
            StorageLocation(
                institution_slug=bank.storage_slug,
                tier="raw",
                object_path=f"{batch.raw_artifact_path}/YIELD_CURVE_GHS.json",
            )
        )
        assert raw is not None

    # Zero vendor quota consumed: not even a zero-unit ledger row.
    assert (
        db_session.scalar(select(func.count()).select_from(MarketDataQuotaUsage)) or 0
    ) == 0

    # Bitemporality: valid time on the rows, transaction time on the record.
    refreshed = determinations.get(db_session, determination.id)
    assert refreshed.status == "published"
    assert refreshed.published_at is not None


def test_republish_supersedes_rather_than_duplicates(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    determination = _approved_determination(db_session)
    publication.publish(db_session, determination.id, actor=LEAD)
    second = publication.publish(db_session, determination.id, actor=LEAD)

    assert second.status == "complete"
    for bank in banks:
        current = _current_curves(db_session, bank, "AEQ.GHS.SOV.ZERO")
        assert len(current) == 1
        total = db_session.scalar(
            select(func.count())
            .select_from(CanonicalYieldCurve)
            .where(
                CanonicalYieldCurve.bank_id == bank.id,
                CanonicalYieldCurve.curve_name == "AEQ.GHS.SOV.ZERO",
            )
        )
        assert total == 2  # one superseded generation + one current

    publications = publication.list_publications(db_session, determination_id=determination.id)
    assert len(publications) == 2


def test_desk_curves_coexist_with_vendor_curves(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    bank = banks[0]
    # Pre-existing REFINITIV sovereign curve for the SAME bank and date.
    batch = IngestionBatch(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        source_system="REFINITIV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=COB,
        started_at=utc_now(),
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=bank.organization_id,
        ingestion_batch_id=batch.id,
        operation_type="VALIDATION",
        operation_ref="test_fixture",
        input_lineage_ids=[],
        details={},
    )
    db_session.add(lineage)
    db_session.flush()
    vendor_curve = CanonicalYieldCurve(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        as_of_date=COB,
        source_system="REFINITIV",
        source_reference="refinitiv:GHS_SOVEREIGN",
        ingestion_batch_id=batch.id,
        validation_status="accepted",
        lineage_id=lineage.id,
        currency="GHS",
        curve_name="GHS_SOVEREIGN",
        curve_type="sovereign",
    )
    db_session.add(vendor_curve)
    db_session.commit()

    determination = _approved_determination(db_session)
    publication.publish(db_session, determination.id, actor=LEAD)

    surviving = db_session.get(CanonicalYieldCurve, vendor_curve.id)
    assert surviving is not None
    assert surviving.superseded_by is None  # AEQ names never collide with vendor names
    assert len(_current_curves(db_session, bank, "AEQ.GHS.SOV.ZERO")) == 1


def test_partial_failure_records_error_and_keeps_successes(
    db_session: Session,
    banks: tuple[Bank, Bank],
    storage: InMemoryStorageClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    determination = _approved_determination(db_session)
    failing_bank = banks[1]
    real_execute_pull = pull_runner.execute_pull

    def flaky_execute_pull(db: Session, **kwargs: Any) -> Any:
        if kwargs["bank"].id == failing_bank.id:
            msg = "simulated storage outage: X-INTERNAL-DETAIL-DO-NOT-SURFACE"
            raise RuntimeError(msg)
        return real_execute_pull(db, **kwargs)

    monkeypatch.setattr(
        "app.services.market_desk.publication.execute_pull", flaky_execute_pull
    )

    result = publication.publish(db_session, determination.id, actor=LEAD)

    assert result.status == "partial"
    by_bank = {entry["bank_id"]: entry for entry in result.results}
    assert by_bank[banks[0].id]["status"] == "complete"
    assert by_bank[failing_bank.id]["status"] == "failed"
    # Bank-facing error only; internals never surface.
    assert "X-INTERNAL-DETAIL" not in by_bank[failing_bank.id]["error"]

    # The successful bank's rows were NOT rolled back.
    assert len(_current_curves(db_session, banks[0], "AEQ.GHS.SOV.ZERO")) == 1
    assert len(_current_curves(db_session, failing_bank, "AEQ.GHS.SOV.ZERO")) == 0
    # The determination is published; the failed bank heals via re-publish.
    assert determinations.get(db_session, determination.id).status == "published"

    row = db_session.get(DeskPublication, result.id)
    assert row is not None and row.status == "partial"


def test_publish_guards_status_and_content(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    _ = banks
    register.ensure_default_methodology(db_session)
    register.approve_version(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 1, 1),
    )
    observations.record_manual_observation(
        db_session,
        series_code="GHS.MPR",
        as_of_date=date(2026, 7, 22),
        value=Decimal("15.00"),
        unit="pct",
        entered_by=ANALYST,
    )
    draft = determinations.create_draft(db_session, cob_date=COB, prepared_by=ANALYST)

    # A draft cannot publish.
    with pytest.raises(HTTPException) as excinfo:
        publication.publish(db_session, draft.id, actor=LEAD)
    assert excinfo.value.status_code == 409

    # An approved determination without derived values cannot publish.
    determinations.submit_for_review(db_session, draft.id)
    determinations.approve(db_session, draft.id, reviewed_by=LEAD)
    with pytest.raises(HTTPException) as excinfo:
        publication.publish(db_session, draft.id, actor=LEAD)
    assert excinfo.value.status_code == 409
