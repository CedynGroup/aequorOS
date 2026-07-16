"""The shared §4.3 contract suite run against the Manual Upload adapter.

The staged fixture file covers every scope ``list_available_scopes``
advertises, so the "every listed scope is pullable" tests exercise full
coverage. Invalid credentials are a staged-upload handle that does not
resolve; the vendor-internal canary marker is embedded in that handle's path
so the leak tests prove raw internals never reach a bank-facing surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.adapters.market_data.base import CredentialSet, MarketDataPullResult
from app.adapters.market_data.scope_taxonomy import DataScope
from app.models import Bank
from tests.adapters.market_data.contract import (
    VENDOR_INTERNAL_MARKER,
    CountCurrentRecordsHook,
    MarketDataContractSuite,
    ProducedRecordsHook,
)
from tests.adapters.market_data.manual_upload.fixtures import (
    FIXTURE_AS_OF,
    build_full_coverage_workbook,
    count_current_canonical,
    credentials_for,
    produced_batch_records,
    stage_upload,
)
from tests.storage.inmemory import InMemoryStorageClient


class TestManualUploadContract(MarketDataContractSuite):
    # ``adapter`` comes from this package's conftest.

    @pytest.fixture
    def as_of(self) -> date:
        return FIXTURE_AS_OF

    @pytest.fixture
    def valid_credentials(
        self, bank: Bank, slug: str, storage: InMemoryStorageClient
    ) -> CredentialSet:
        location = stage_upload(storage, slug, build_full_coverage_workbook(), "full_coverage.xlsx")
        return credentials_for(bank, location)

    @pytest.fixture
    def invalid_credentials(self, bank: Bank) -> CredentialSet:
        # A staged handle that resolves to nothing; the canary marker rides in
        # the internal path and must never surface bank-facing.
        return credentials_for(bank, f"temp://uploads/{VENDOR_INTERNAL_MARKER}/missing.xlsx")

    @pytest.fixture
    def pull_scopes(self) -> list[DataScope]:
        return [
            DataScope.YIELD_CURVE_GHS,
            DataScope.FX_SPOT_USD_GHS,
            DataScope.FX_FORWARD_USD_GHS_3M,
            DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
            DataScope.MACRO_GHANA_GDP_FORECAST,
        ]

    @pytest.fixture
    def produced_records(self, db_session: Session) -> ProducedRecordsHook:
        def hook(result: MarketDataPullResult) -> Sequence[Any]:
            return produced_batch_records(db_session, UUID(result.batch_id))

        return hook

    @pytest.fixture
    def count_current_records(self, db_session: Session, bank: Bank) -> CountCurrentRecordsHook:
        def hook(scope: DataScope, as_of: date) -> int:
            return count_current_canonical(db_session, bank, scope, as_of)

        return hook
