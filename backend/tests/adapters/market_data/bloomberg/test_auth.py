"""SimulatedSessionProvider behavior: §6.1 shape validation, no vendor leaks."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.adapters.market_data.base import CredentialSet
from app.adapters.market_data.bloomberg import (
    BloombergAdapter,
    BloombergSession,
    SimulatedSessionProvider,
    ensure_scope_permitted,
)
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.scope_taxonomy import DataScope
from app.models import Bank
from tests.adapters.market_data.bloomberg.conftest import VALID_CREDENTIAL_PAYLOAD
from tests.adapters.market_data.contract import VENDOR_INTERNAL_MARKER


def _credentials(payload: dict[str, Any]) -> CredentialSet:
    return CredentialSet(
        institution_id="test-bank",
        vendor="bloomberg",
        credentials=payload,
        issued_at=datetime(2026, 1, 5, 9, 0, tzinfo=UTC),
        expires_at=None,
    )


def test_valid_credentials_open_a_session() -> None:
    session = SimulatedSessionProvider().open_session(_credentials(dict(VALID_CREDENTIAL_PAYLOAD)))
    assert session.application_identifier == "AEQUOROS_MKTDATA_PROD"
    assert session.serial_number == "889900"
    assert session.authentication_endpoint == "https://bpipe.sample-bank.example:8194"
    assert session.subscription_tier == "b-pipe-enterprise"
    assert session.scopes_permitted is True


@pytest.mark.parametrize(
    "missing_field", ["application_identifier", "serial_number", "certificate"]
)
def test_missing_required_field_is_credential_invalid(missing_field: str) -> None:
    payload = dict(VALID_CREDENTIAL_PAYLOAD)
    del payload[missing_field]
    payload["simulated_vendor_error"] = f"blpapi.AuthorizationFailure {VENDOR_INTERNAL_MARKER}"
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedSessionProvider().open_session(_credentials(payload))
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.CREDENTIAL_INVALID
    # The bank-facing message references the bank's Bloomberg administrator...
    assert "ama.mensah@samplebank.example" in error.bank_facing.message
    # ...but never the raw vendor rejection, which stays internal-only.
    assert VENDOR_INTERNAL_MARKER not in error.bank_facing.message
    assert VENDOR_INTERNAL_MARKER not in str(error)
    assert VENDOR_INTERNAL_MARKER in error.internal_detail
    assert missing_field in error.internal_detail


def test_credential_invalid_without_contact_admin_omits_the_reference() -> None:
    payload = dict(VALID_CREDENTIAL_PAYLOAD)
    del payload["certificate"]
    del payload["contact_admin"]
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedSessionProvider().open_session(_credentials(payload))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.CREDENTIAL_INVALID
    assert "administrator on record" not in excinfo.value.bank_facing.message


def test_simulated_lapsed_subscription() -> None:
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedSessionProvider().open_session(_credentials({"simulate": "lapsed"}))
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.SUBSCRIPTION_LAPSED
    assert "Bloomberg" in error.bank_facing.message


def test_simulated_not_permitted_opens_session_but_blocks_pulls() -> None:
    payload = dict(VALID_CREDENTIAL_PAYLOAD)
    payload["simulate"] = "not_permitted"
    session = SimulatedSessionProvider().open_session(_credentials(payload))
    assert session.scopes_permitted is False
    with pytest.raises(MarketDataError) as excinfo:
        ensure_scope_permitted(session, DataScope.YIELD_CURVE_GHS)
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.SCOPE_NOT_PERMITTED
    assert "YIELD_CURVE_GHS" in error.bank_facing.message


def test_ensure_scope_permitted_passes_for_permitted_session() -> None:
    session = BloombergSession(
        application_identifier="APP",
        serial_number="1",
        authentication_endpoint=None,
        subscription_tier=None,
    )
    ensure_scope_permitted(session, DataScope.YIELD_CURVE_GHS)  # does not raise


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
