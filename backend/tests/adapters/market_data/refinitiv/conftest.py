"""Shared fixtures for the Refinitiv adapter test suite.

Everything runs against recorded fixtures (§7.4): FixtureTransport replays
RDP-shaped JSON, SimulatedTokenProvider stands in for the RDP token
endpoint, and storage is monkeypatched to the in-memory client — zero live
vendor calls, zero network.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.market_data.base import CredentialSet, MarketDataPullResult
from app.adapters.market_data.refinitiv.adapter import RefinitivAdapter
from app.adapters.market_data.refinitiv.auth import SimulatedTokenProvider
from app.adapters.market_data.refinitiv.transport import FixtureTransport
from app.adapters.market_data.scope_taxonomy import DataScope
from app.models import (
    Bank,
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
)
from app.services.ingestion import bank_slug as resolve_bank_slug
from app.services.sample_bank_seed import SAMPLE_BANK_ID, seed_sample_bank
from tests.adapters.market_data.contract import VENDOR_INTERNAL_MARKER
from tests.storage.inmemory import InMemoryStorageClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"

SUPPORTED_SCOPES = [
    DataScope.YIELD_CURVE_GHS,
    DataScope.FX_SPOT_USD_GHS,
    DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
]

VALID_CREDENTIALS_DICT: dict[str, Any] = {
    "client_id": "aequoros-sample-bank-app",
    "client_secret": "not-a-real-secret",
    "scope": "trapi",
    "subscription_type": "rdp-standard",
    "refresh_token": "not-a-real-refresh-token",
    "token_endpoint": "https://api.refinitiv.test/auth/oauth2/v1/token",
    "contact_admin": "treasury-data-admin@samplebank.example",
}


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.adapters.market_data.pull_runner.get_storage_client", lambda: client
    )
    monkeypatch.setattr("app.adapters.market_data.cache.get_storage_client", lambda: client)
    return client


@pytest.fixture
def bank(db_session: Session) -> Bank:
    seed_sample_bank(db_session)
    db_session.commit()
    seeded = db_session.get(Bank, SAMPLE_BANK_ID)
    assert seeded is not None
    return seeded


@pytest.fixture
def adapter(
    db_session: Session, bank: Bank, storage: InMemoryStorageClient
) -> RefinitivAdapter:
    slug = resolve_bank_slug(db_session, bank)
    db_session.commit()
    return RefinitivAdapter(
        db_session,
        bank,
        slug,
        token_provider=SimulatedTokenProvider(),
        transport=FixtureTransport(FIXTURES_DIR),
    )


@pytest.fixture
def valid_credentials() -> CredentialSet:
    return CredentialSet(
        institution_id=str(SAMPLE_BANK_ID),
        vendor="refinitiv",
        credentials=dict(VALID_CREDENTIALS_DICT),
        issued_at=datetime(2026, 1, 5, tzinfo=UTC),
        expires_at=None,
    )


@pytest.fixture
def invalid_credentials() -> CredentialSet:
    # The client_id embeds the contract suite's canary: the simulated raw
    # vendor rejection quotes it into internal_detail, so the leak tests can
    # prove raw vendor error content never reaches a bank-facing surface.
    return CredentialSet(
        institution_id=str(SAMPLE_BANK_ID),
        vendor="refinitiv",
        credentials={"client_id": f"rogue-{VENDOR_INTERNAL_MARKER}", "client_secret": ""},
        issued_at=datetime(2026, 1, 5, tzinfo=UTC),
        expires_at=None,
    )


@pytest.fixture
def pull_scopes() -> list[DataScope]:
    return list(SUPPORTED_SCOPES)


@pytest.fixture
def as_of() -> date:
    return date(2026, 7, 15)


@pytest.fixture
def produced_records(db_session: Session):
    """Contract hook: every canonical record a pull's batch persisted."""

    def _hook(result: MarketDataPullResult) -> Sequence[Any]:
        batch_id = UUID(result.batch_id)
        records: list[Any] = []
        for model in (
            CanonicalYieldCurve,
            CanonicalYieldCurvePoint,
            CanonicalFxRate,
            CanonicalCounterpartyRating,
        ):
            records.extend(
                db_session.scalars(select(model).where(model.ingestion_batch_id == batch_id))
            )
        return records

    return _hook


@pytest.fixture
def count_current_records(db_session: Session):
    """Contract hook: current-generation canonical records per scope/as-of.

    Curve points are not individually superseded (the runner supersedes the
    curve header and inserts a fresh point set under the new header), so
    "current" points are the ones attached to a current curve header.
    """

    def _count(scope: DataScope, as_of: date) -> int:
        if scope is DataScope.YIELD_CURVE_GHS:
            curves = list(
                db_session.scalars(
                    select(CanonicalYieldCurve).where(
                        CanonicalYieldCurve.currency == "GHS",
                        CanonicalYieldCurve.as_of_date == as_of,
                        CanonicalYieldCurve.superseded_by.is_(None),
                    )
                )
            )
            if not curves:
                return 0
            points = list(
                db_session.scalars(
                    select(CanonicalYieldCurvePoint).where(
                        CanonicalYieldCurvePoint.yield_curve_id.in_(
                            [curve.id for curve in curves]
                        )
                    )
                )
            )
            return len(curves) + len(points)
        if scope is DataScope.FX_SPOT_USD_GHS:
            rows = db_session.scalars(
                select(CanonicalFxRate).where(
                    CanonicalFxRate.base_currency == "USD",
                    CanonicalFxRate.quote_currency == "GHS",
                    CanonicalFxRate.rate_type == "spot",
                    CanonicalFxRate.as_of_date == as_of,
                    CanonicalFxRate.superseded_by.is_(None),
                )
            )
            return len(list(rows))
        if scope is DataScope.CREDIT_RATING_GHANA_SOVEREIGN:
            rows = db_session.scalars(
                select(CanonicalCounterpartyRating).where(
                    CanonicalCounterpartyRating.issuer == "GHANA_SOVEREIGN",
                    CanonicalCounterpartyRating.as_of_date == as_of,
                    CanonicalCounterpartyRating.superseded_by.is_(None),
                )
            )
            return len(list(rows))
        msg = f"count_current_records has no query for scope {scope.value}"
        raise AssertionError(msg)

    return _count
