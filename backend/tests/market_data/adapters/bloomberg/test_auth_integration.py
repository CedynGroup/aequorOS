"""SimulatedSessionProvider behavior: §6.1 shape validation, no vendor leaks.

This module's other-suite cases live in ``test_auth.py``.
"""

from __future__ import annotations

from datetime import date

from app.market_data.adapters.bloomberg import (
    BloombergAdapter,
)
from app.market_data.adapters.scope_taxonomy import DataScope
from app.models import Bank
from tests.market_data.adapters.bloomberg.conftest import VALID_CREDENTIAL_PAYLOAD
from tests.market_data.adapters.bloomberg.test_auth import _credentials


def test_not_permitted_credentials_surface_per_scope_pull_errors(
    adapter: BloombergAdapter,
    bank: Bank,
) -> None:
    """SCOPE_NOT_PERMITTED fires at pull time: auth succeeds, scopes fail."""
    payload = dict(VALID_CREDENTIAL_PAYLOAD)
    payload["simulate"] = "not_permitted"
    credentials = _credentials(payload)
    assert adapter.authenticate(credentials).success

    result = adapter.pull(
        credentials,
        [DataScope.YIELD_CURVE_GHS, DataScope.FX_SPOT_USD_GHS],
        date(2026, 7, 15),
        str(bank.id),
        "auth-test-batch",
    )
    assert result.canonical_records_produced == 0
    assert len(result.errors) == 2
    assert all("does not include access" in message for message in result.errors)
