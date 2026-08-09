"""The shared §4.3 contract suite run against the AequorOS desk adapter.

The valid "credential" is a handle to an approved determination carrying the
full derived-values fixture (all three AEQ curve families, both reference
rates, the USD/GHS anchor), so every advertised scope is pullable. Invalid
credentials are a determination handle that resolves to nothing; the
vendor-internal canary marker rides in that handle so the leak tests prove
raw internals never reach a bank-facing surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.market_data.base import CredentialSet, MarketDataPullResult
from app.adapters.market_data.scope_taxonomy import DataScope
from app.models import (
    Bank,
    CanonicalFxRate,
    CanonicalMarketIndex,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    DeskDetermination,
)
from tests.adapters.market_data.aequor_desk.conftest import FIXTURE_COB
from tests.adapters.market_data.contract import (
    VENDOR_INTERNAL_MARKER,
    CountCurrentRecordsHook,
    MarketDataContractSuite,
    ProducedRecordsHook,
)
from tests.adapters.market_data.manual_upload.fixtures import produced_batch_records

DESK_INDEX_CODES = ("GHS.GRR", "GHS.MPR")


def _credentials_for(bank: Bank, determination_handle: str) -> CredentialSet:
    return CredentialSet(
        institution_id=str(bank.id),
        vendor="aequor_desk",
        credentials={"determination_id": determination_handle},
        issued_at=datetime.now(UTC),
        expires_at=None,
    )


class TestAequorDeskContract(MarketDataContractSuite):
    # ``adapter`` comes from this package's conftest.

    @pytest.fixture
    def as_of(self) -> date:
        return FIXTURE_COB

    @pytest.fixture
    def valid_credentials(
        self, bank: Bank, approved_determination: DeskDetermination
    ) -> CredentialSet:
        return _credentials_for(bank, str(approved_determination.id))

    @pytest.fixture
    def invalid_credentials(self, bank: Bank) -> CredentialSet:
        # A handle that resolves to nothing; the canary marker rides in the
        # internal detail and must never surface bank-facing.
        return _credentials_for(bank, f"missing/{VENDOR_INTERNAL_MARKER}")

    @pytest.fixture
    def pull_scopes(self) -> list[DataScope]:
        return [
            DataScope.YIELD_CURVE_GHS,
            DataScope.MACRO_GHANA_POLICY_RATE_PATH,
            DataScope.FX_SPOT_USD_GHS,
        ]

    @pytest.fixture
    def produced_records(self, db_session: Session) -> ProducedRecordsHook:
        def hook(result: MarketDataPullResult) -> Sequence[Any]:
            return produced_batch_records(db_session, UUID(result.batch_id))

        return hook

    @pytest.fixture
    def count_current_records(self, db_session: Session, bank: Bank) -> CountCurrentRecordsHook:
        """Desk-specific current-generation counts.

        The desk publishes AEQ curve names and its own index codes, so the
        manual-upload counting helper's scope-name-derived keys do not apply
        for indices; counts here key on the desk's source system.
        """

        def hook(scope: DataScope, as_of: date) -> int:
            if scope is DataScope.YIELD_CURVE_GHS:
                curve_ids = list(
                    db_session.scalars(
                        select(CanonicalYieldCurve.id).where(
                            CanonicalYieldCurve.bank_id == bank.id,
                            CanonicalYieldCurve.as_of_date == as_of,
                            CanonicalYieldCurve.source_system == "AEQUOR_DESK",
                            CanonicalYieldCurve.superseded_by.is_(None),
                        )
                    )
                )
                if not curve_ids:
                    return 0
                points = db_session.scalar(
                    select(func.count())
                    .select_from(CanonicalYieldCurvePoint)
                    .where(CanonicalYieldCurvePoint.yield_curve_id.in_(curve_ids))
                )
                return len(curve_ids) + int(points or 0)
            if scope is DataScope.MACRO_GHANA_POLICY_RATE_PATH:
                count = db_session.scalar(
                    select(func.count())
                    .select_from(CanonicalMarketIndex)
                    .where(
                        CanonicalMarketIndex.bank_id == bank.id,
                        CanonicalMarketIndex.as_of_date == as_of,
                        CanonicalMarketIndex.index_code.in_(DESK_INDEX_CODES),
                        CanonicalMarketIndex.superseded_by.is_(None),
                    )
                )
                return int(count or 0)
            if scope is DataScope.FX_SPOT_USD_GHS:
                count = db_session.scalar(
                    select(func.count())
                    .select_from(CanonicalFxRate)
                    .where(
                        CanonicalFxRate.bank_id == bank.id,
                        CanonicalFxRate.as_of_date == as_of,
                        CanonicalFxRate.base_currency == "USD",
                        CanonicalFxRate.quote_currency == "GHS",
                        CanonicalFxRate.superseded_by.is_(None),
                    )
                )
                return int(count or 0)
            msg = f"no desk counting handler for {scope.value}"
            raise AssertionError(msg)

        return hook
