"""Market data consumption views API: happy path, tenant isolation, empty bank."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import (
    Bank,
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    IngestionBatch,
    LineageRecord,
)
from tests.api.helpers import ORG_1, ORG_2, headers

AS_OF = date(2026, 7, 15)
INGESTED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _seed_bank(session: Session) -> UUID:
    bank = Bank(
        organization_id=ORG_1,
        name="Market Data Views Test Bank",
        short_name="MDVB",
        license_type="universal",
    )
    session.add(bank)
    session.flush()
    return bank.id


def _meta(
    session: Session,
    bank_id: UUID,
    *,
    source_system: str,
    as_of: date,
    ingested_at: datetime = INGESTED_AT,
) -> dict[str, Any]:
    """One batch + lineage node per seeded generation, like a real pull."""
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank_id,
        source_system=source_system,
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    session.add(batch)
    session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="market-data-views-test-fixture",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()
    return {
        "organization_id": ORG_1,
        "bank_id": bank_id,
        "as_of_date": as_of,
        "ingested_at": ingested_at,
        "source_system": source_system,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }


def _seed_curve(session: Session, bank_id: UUID, *, rates: dict[int, str]) -> None:
    meta = _meta(session, bank_id, source_system="BLOOMBERG", as_of=AS_OF)
    curve = CanonicalYieldCurve(
        **meta,
        source_reference="BLOOMBERG/GHS_SOVEREIGN",
        currency="GHS",
        curve_name="GHS_SOVEREIGN",
        curve_type="sovereign",
    )
    session.add(curve)
    session.flush()
    for tenor_months, rate in rates.items():
        session.add(
            CanonicalYieldCurvePoint(
                **meta,
                source_reference=f"BLOOMBERG/GHS_SOVEREIGN/{tenor_months}m",
                yield_curve_id=curve.id,
                tenor_months=tenor_months,
                rate=Decimal(rate),
            )
        )
    session.flush()


def _seed_spot(session: Session, bank_id: UUID, *, rate: str, as_of: date) -> None:
    meta = _meta(session, bank_id, source_system="REFINITIV", as_of=as_of)
    session.add(
        CanonicalFxRate(
            **meta,
            source_reference=f"REFINITIV/USDGHS/{as_of.isoformat()}",
            base_currency="USD",
            quote_currency="GHS",
            rate_type="spot",
            tenor_months=None,
            rate=Decimal(rate),
        )
    )
    session.flush()


def _seed_rating(session: Session, bank_id: UUID) -> None:
    meta = _meta(session, bank_id, source_system="MANUAL_UPLOAD", as_of=AS_OF)
    session.add(
        CanonicalCounterpartyRating(
            **meta,
            source_reference="rating/GHANA_SOVEREIGN",
            issuer="GHANA_SOVEREIGN",
            agency="fitch",
            rating="B-",
            watch_status="stable",
            rating_date=AS_OF - timedelta(days=3),
        )
    )
    session.flush()


def _seed_index(session: Session, bank_id: UUID) -> None:
    meta = _meta(session, bank_id, source_system="REFINITIV", as_of=AS_OF)
    session.add(
        CanonicalMarketIndex(
            **meta,
            source_reference="index/GHANA_GDP_FORECAST/base",
            index_code="GHANA_GDP_FORECAST",
            value=Decimal("0.05"),
            scenario="base",
            horizon_months=12,
        )
    )
    session.flush()


def _views_url(bank_id: UUID | str) -> str:
    return f"/api/v1/banks/{bank_id}/market-data/views?as_of={AS_OF.isoformat()}"


def test_views_serve_curves_fx_ratings_and_indices(db_client: TestClient) -> None:
    _ = db_client  # initializes the app engine/DB before the direct session
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        _seed_curve(session, bank_id, rates={12: "0.185", 1: "0.14", 60: "0.22"})
        _seed_spot(session, bank_id, rate="12.70", as_of=AS_OF - timedelta(days=1))
        _seed_spot(session, bank_id, rate="12.85", as_of=AS_OF)
        _seed_rating(session, bank_id)
        _seed_index(session, bank_id)
        session.commit()
    finally:
        session.close()

    response = db_client.get(_views_url(bank_id), headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == str(bank_id)
    assert body["as_of_date"] == AS_OF.isoformat()

    (curve,) = body["curves"]
    assert curve["currency"] == "GHS"
    assert curve["curve_name"] == "GHS_SOVEREIGN"
    assert [point["tenor_months"] for point in curve["points"]] == [1, 12, 60]
    assert Decimal(curve["points"][1]["rate"]) == Decimal("0.185")
    assert curve["attribution"]["source_system"] == "BLOOMBERG"
    assert isinstance(curve["attribution"]["stale"], bool)
    assert curve["attribution"]["age_seconds"] >= 0

    (fx,) = body["fx_rates"]
    assert (fx["base"], fx["quote"]) == ("USD", "GHS")
    assert fx["rate_type"] == "spot"
    assert fx["tenor_months"] is None
    assert Decimal(fx["rate"]) == Decimal("12.85")
    assert fx["as_of_date"] == AS_OF.isoformat()
    history = fx["history"]
    assert [Decimal(point["rate"]) for point in history] == [Decimal("12.70"), Decimal("12.85")]
    assert [point["as_of_date"] for point in history] == sorted(
        point["as_of_date"] for point in history
    )
    assert fx["attribution"]["source_system"] == "REFINITIV"

    (rating,) = body["ratings"]
    assert rating["issuer"] == "GHANA_SOVEREIGN"
    assert rating["agency"] == "fitch"
    assert rating["rating"] == "B-"
    assert rating["watch_status"] == "stable"
    assert rating["attribution"]["source_system"] == "MANUAL_UPLOAD"

    (index,) = body["indices"]
    assert index["index_code"] == "GHANA_GDP_FORECAST"
    assert index["scenario"] == "base"
    assert Decimal(index["value"]) == Decimal("0.05")
    assert index["horizon_months"] == 12


def test_views_are_tenant_isolated(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        _seed_curve(session, bank_id, rates={12: "0.185"})
        session.commit()
    finally:
        session.close()

    response = db_client.get(_views_url(bank_id), headers=headers(ORG_2))
    assert response.status_code == 404, response.text

    missing = db_client.get(_views_url(uuid4()), headers=headers(ORG_1))
    assert missing.status_code == 404, missing.text


def test_empty_bank_returns_empty_lists(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        bank_id = _seed_bank(session)
        session.commit()
    finally:
        session.close()

    response = db_client.get(_views_url(bank_id), headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == str(bank_id)
    assert body["curves"] == []
    assert body["fx_rates"] == []
    assert body["ratings"] == []
    assert body["indices"] == []
