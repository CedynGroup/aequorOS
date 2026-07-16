"""OAuth credential classification (§7.1 / §12): no raises, no raw leaks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.market_data.base import CredentialSet
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.refinitiv.auth import (
    SimulatedTokenProvider,
    acquire_session_token,
    authenticate_credentials,
)
from tests.adapters.market_data.contract import VENDOR_INTERNAL_MARKER

VALID = {"client_id": "app-1", "client_secret": "s3cret"}


def _credential_set(
    credentials: dict[str, str], expires_at: datetime | None = None
) -> CredentialSet:
    return CredentialSet(
        institution_id="inst-1",
        vendor="refinitiv",
        credentials=credentials,
        issued_at=datetime(2026, 1, 5, tzinfo=UTC),
        expires_at=expires_at,
    )


def test_valid_credentials_yield_a_short_lived_token() -> None:
    token, expires_at = SimulatedTokenProvider().acquire(dict(VALID))
    assert token
    assert expires_at > datetime.now(UTC)
    assert expires_at <= datetime.now(UTC) + timedelta(hours=4)


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"client_id": "app-1"},
        {"client_id": "app-1", "client_secret": ""},
        {"client_id": "   ", "client_secret": "s3cret"},
        {"client_id": "app-1", "client_secret": None},
    ],
)
def test_malformed_credentials_classify_as_invalid(credentials: dict) -> None:
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedTokenProvider().acquire(credentials)
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.CREDENTIAL_INVALID


@pytest.mark.parametrize(
    ("simulate", "expected"),
    [
        ("expired", BankFacingErrorCode.CREDENTIAL_EXPIRED),
        ("revoked", BankFacingErrorCode.CREDENTIAL_REVOKED),
    ],
)
def test_simulated_vendor_states_classify(simulate: str, expected: BankFacingErrorCode) -> None:
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedTokenProvider().acquire({**VALID, "simulate": simulate})
    assert excinfo.value.bank_facing.code is expected


def test_expired_credential_set_classifies_before_the_vendor_is_asked() -> None:
    expired = _credential_set(dict(VALID), expires_at=datetime.now(UTC) - timedelta(days=1))
    with pytest.raises(MarketDataError) as excinfo:
        acquire_session_token(SimulatedTokenProvider(), expired)
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.CREDENTIAL_EXPIRED


def test_authenticate_never_raises_for_credential_problems() -> None:
    for credentials in (
        {"client_id": "", "client_secret": ""},
        {**VALID, "simulate": "expired"},
        {**VALID, "simulate": "revoked"},
    ):
        result = authenticate_credentials(SimulatedTokenProvider(), _credential_set(credentials))
        assert not result.success
        assert result.error_code in {code.value for code in BankFacingErrorCode}
        assert result.error_message


def test_authenticate_success_carries_session_token() -> None:
    result = authenticate_credentials(SimulatedTokenProvider(), _credential_set(dict(VALID)))
    assert result.success
    assert result.session_token
    assert result.expires_at is not None
    assert result.error_code is None
    assert result.error_message is None


def test_raw_vendor_detail_stays_internal() -> None:
    # The canary rides in the (non-secret) client_id; the simulated raw
    # vendor rejection quotes it into internal_detail only.
    marked = {"client_id": f"rogue-{VENDOR_INTERNAL_MARKER}", "client_secret": ""}
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedTokenProvider().acquire(marked)
    error = excinfo.value
    assert VENDOR_INTERNAL_MARKER in error.internal_detail
    assert VENDOR_INTERNAL_MARKER not in error.bank_facing.message
    assert VENDOR_INTERNAL_MARKER not in str(error)

    result = authenticate_credentials(SimulatedTokenProvider(), _credential_set(marked))
    assert not result.success
    assert result.error_message is not None
    assert VENDOR_INTERNAL_MARKER not in result.error_message


def test_client_secret_never_reaches_internal_detail() -> None:
    secret = "super-sensitive-secret-value"
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedTokenProvider().acquire({"client_id": "", "client_secret": secret})
    assert secret not in excinfo.value.internal_detail
