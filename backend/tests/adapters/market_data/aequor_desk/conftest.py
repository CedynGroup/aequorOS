from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.adapters.market_data.aequor_desk.adapter import AequorDeskAdapter
from app.models import Bank, DeskDetermination
from app.services.ingestion import bank_slug
from app.services.market_desk import determinations, observations, register
from tests.api.helpers import ORG_1, USER_1
from tests.storage.inmemory import InMemoryStorageClient

# Module paths whose ``get_storage_client`` must resolve to the in-memory
# client: the pull runner (raw tier) and the cache module. The desk adapter
# itself reads determinations from the DB, not from storage.
STORAGE_FACTORY_TARGETS = (
    "app.adapters.market_data.pull_runner.get_storage_client",
    "app.adapters.market_data.cache.get_storage_client",
)

FIXTURE_COB = date(2026, 8, 7)

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


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    for target in STORAGE_FACTORY_TARGETS:
        monkeypatch.setattr(target, lambda: client)
    return client


@pytest.fixture
def bank(db_session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Aequor Desk Test Bank",
        short_name="aequor-desk-test",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    db_session.add(bank)
    db_session.commit()
    return bank


@pytest.fixture
def slug(db_session: Session, bank: Bank) -> str:
    value = bank_slug(db_session, bank)
    db_session.commit()
    return value


@pytest.fixture
def approved_determination(db_session: Session) -> DeskDetermination:
    """An approved determination carrying the full derived-values fixture."""
    register.ensure_default_methodology(db_session)
    register.approve_version(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by="lead@aequoros.com",
        effective_from=date(2026, 1, 1),
    )
    observations.record_manual_observation(
        db_session,
        series_code="GHS.MPR",
        as_of_date=date(2026, 7, 22),
        value=Decimal("15.00"),
        unit="pct",
        entered_by="analyst@aequoros.com",
    )
    draft = determinations.create_draft(
        db_session, cob_date=FIXTURE_COB, prepared_by="analyst@aequoros.com"
    )
    determinations.set_results(
        db_session,
        draft.id,
        derived_values=DERIVED_VALUES,
        qa_results={"forward_positivity": "pass"},
    )
    determinations.submit_for_review(db_session, draft.id)
    determinations.approve(db_session, draft.id, reviewed_by="lead@aequoros.com")
    db_session.commit()
    return draft


@pytest.fixture
def adapter(
    db_session: Session, bank: Bank, slug: str, storage: InMemoryStorageClient
) -> AequorDeskAdapter:
    _ = storage  # patched factories; the adapter itself takes no client
    return AequorDeskAdapter(db_session, bank, slug, actor_user_id=USER_1)
