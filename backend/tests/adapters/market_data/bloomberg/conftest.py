"""Shared fixtures for the Bloomberg adapter suite.

Everything runs against recorded fixtures (§6.5) and an in-memory storage
client: no live Bloomberg dependency, no network. The invalid-credential
fixture embeds the contract suite's canary marker in its simulated raw vendor
error so the leak tests can prove raw vendor messages never surface.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.adapters.market_data.cache as market_data_cache
import app.adapters.market_data.pull_runner as pull_runner_module
from app.adapters.market_data.base import CredentialSet, MarketDataPullResult
from app.adapters.market_data.bloomberg import BloombergAdapter, FixtureTransport
from app.adapters.market_data.scope_taxonomy import DataScope
from app.models import (
    Bank,
    CanonicalCounterpartyRating,
    CanonicalFxRate,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
)
from app.services.ingestion import bank_slug as derive_bank_slug
from app.services.sample_bank_seed import SAMPLE_BANK_ID, seed_sample_bank
from tests.adapters.market_data.contract import VENDOR_INTERNAL_MARKER
from tests.storage.inmemory import InMemoryStorageClient

FIXTURES_DIR = Path(__file__).with_name("fixtures")

# Recorded-response filenames per scope (FixtureTransport falls back to
# "<SCOPE>.json" for anything not listed here).
FIXTURE_FILENAMES: dict[str, str] = {
    "YIELD_CURVE_GHS": "ghs_yield_curve.json",
    "FX_SPOT_USD_GHS": "usdghs_spot.json",
    "CREDIT_RATING_GHANA_SOVEREIGN": "ghana_sovereign_ratings.json",
}

VALID_CREDENTIAL_PAYLOAD: dict[str, Any] = {
    "application_identifier": "AEQUOROS_MKTDATA_PROD",
    "serial_number": "889900",
    "authentication_endpoint": "https://bpipe.sample-bank.example:8194",
    "certificate": (
        "-----BEGIN CERTIFICATE-----\nMIIBsTESTFIXTURECERTIFICATEBODY\n-----END CERTIFICATE-----"
    ),
    "subscription_tier": "b-pipe-enterprise",
    "contact_admin": "ama.mensah@samplebank.example",
}


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    monkeypatch.setattr(pull_runner_module, "get_storage_client", lambda: client)
    monkeypatch.setattr(market_data_cache, "get_storage_client", lambda: client)
    return client


@pytest.fixture
def bank(db_session: Session) -> Bank:
    seed_sample_bank(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    return bank


@pytest.fixture
def bank_slug(db_session: Session, bank: Bank) -> str:
    return derive_bank_slug(db_session, bank)


@pytest.fixture
def fixture_transport() -> FixtureTransport:
    return FixtureTransport(FIXTURES_DIR, filenames=FIXTURE_FILENAMES)


@pytest.fixture
def adapter(
    db_session: Session,
    bank: Bank,
    bank_slug: str,
    fixture_transport: FixtureTransport,
    storage: InMemoryStorageClient,
) -> BloombergAdapter:
    _ = storage  # patched before any pull persists
    return BloombergAdapter(db_session, bank, bank_slug, transport=fixture_transport)


@pytest.fixture
def valid_credentials(bank: Bank) -> CredentialSet:
    return CredentialSet(
        institution_id=str(bank.id),
        vendor="bloomberg",
        credentials=dict(VALID_CREDENTIAL_PAYLOAD),
        issued_at=datetime(2026, 1, 5, 9, 0, tzinfo=UTC),
        expires_at=None,
    )


@pytest.fixture
def invalid_credentials(bank: Bank) -> CredentialSet:
    # application_identifier and certificate are missing; the simulated raw
    # vendor rejection carries the canary marker and must never surface.
    return CredentialSet(
        institution_id=str(bank.id),
        vendor="bloomberg",
        credentials={
            "serial_number": "889900",
            "contact_admin": "ama.mensah@samplebank.example",
            "simulated_vendor_error": (
                f"blpapi.AuthorizationFailure code=35 {VENDOR_INTERNAL_MARKER}"
            ),
        },
        issued_at=datetime(2026, 1, 5, 9, 0, tzinfo=UTC),
        expires_at=None,
    )


@pytest.fixture
def pull_scopes() -> list[DataScope]:
    return [
        DataScope.YIELD_CURVE_GHS,
        DataScope.FX_SPOT_USD_GHS,
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
    ]


@pytest.fixture
def as_of() -> date:
    return date(2026, 7, 15)


_CANONICAL_MODELS = (
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    CanonicalFxRate,
    CanonicalCounterpartyRating,
)


@pytest.fixture
def produced_records(
    db_session: Session,
) -> Callable[[MarketDataPullResult], Sequence[Any]]:
    def _hook(result: MarketDataPullResult) -> Sequence[Any]:
        batch_id = UUID(result.batch_id)
        records: list[Any] = []
        for model in _CANONICAL_MODELS:
            records.extend(
                db_session.scalars(select(model).where(model.ingestion_batch_id == batch_id))
            )
        return records

    return _hook


@pytest.fixture
def count_current_records(db_session: Session) -> Callable[[DataScope, date], int]:
    """Current-generation canonical rows per scope/as-of.

    Curve points are never superseded directly — their parent curve header
    is — so the curve count is current headers plus the points that belong
    to them.
    """

    def _hook(scope: DataScope, as_of: date) -> int:
        if scope is DataScope.YIELD_CURVE_GHS:
            curve_ids = list(
                db_session.scalars(
                    select(CanonicalYieldCurve.id).where(
                        CanonicalYieldCurve.currency == "GHS",
                        CanonicalYieldCurve.curve_name == scope.value,
                        CanonicalYieldCurve.as_of_date == as_of,
                        CanonicalYieldCurve.superseded_by.is_(None),
                    )
                )
            )
            points = 0
            if curve_ids:
                points = int(
                    db_session.scalar(
                        select(func.count())
                        .select_from(CanonicalYieldCurvePoint)
                        .where(
                            CanonicalYieldCurvePoint.yield_curve_id.in_(curve_ids),
                            CanonicalYieldCurvePoint.superseded_by.is_(None),
                        )
                    )
                    or 0
                )
            return len(curve_ids) + points
        if scope is DataScope.FX_SPOT_USD_GHS:
            return int(
                db_session.scalar(
                    select(func.count())
                    .select_from(CanonicalFxRate)
                    .where(
                        CanonicalFxRate.base_currency == "USD",
                        CanonicalFxRate.quote_currency == "GHS",
                        CanonicalFxRate.rate_type == "spot",
                        CanonicalFxRate.as_of_date == as_of,
                        CanonicalFxRate.superseded_by.is_(None),
                    )
                )
                or 0
            )
        if scope is DataScope.CREDIT_RATING_GHANA_SOVEREIGN:
            return int(
                db_session.scalar(
                    select(func.count())
                    .select_from(CanonicalCounterpartyRating)
                    .where(
                        CanonicalCounterpartyRating.issuer == "GHANA_SOVEREIGN",
                        CanonicalCounterpartyRating.as_of_date == as_of,
                        CanonicalCounterpartyRating.superseded_by.is_(None),
                    )
                )
                or 0
            )
        msg = f"count_current_records has no query for scope {scope.value}"
        raise AssertionError(msg)

    return _hook
