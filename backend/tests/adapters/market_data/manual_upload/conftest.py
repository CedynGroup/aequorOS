from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.adapters.market_data.manual_upload.adapter import ManualUploadAdapter
from app.models import Bank
from app.services.ingestion import bank_slug
from tests.api.helpers import ORG_1, USER_1
from tests.storage.inmemory import InMemoryStorageClient

# Module paths whose ``get_storage_client`` must resolve to the in-memory
# client: the pull runner (raw tier + cache hand-off), the cache module, and
# the adapter's staged-upload resolver.
STORAGE_FACTORY_TARGETS = (
    "app.adapters.market_data.pull_runner.get_storage_client",
    "app.adapters.market_data.cache.get_storage_client",
    "app.adapters.market_data.manual_upload.adapter.get_storage_client",
)


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
        name="Manual Upload Test Bank",
        short_name="manual-upload-test",
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
def adapter(
    db_session: Session, bank: Bank, slug: str, storage: InMemoryStorageClient
) -> ManualUploadAdapter:
    return ManualUploadAdapter(db_session, bank, slug, actor_user_id=USER_1, storage=storage)
