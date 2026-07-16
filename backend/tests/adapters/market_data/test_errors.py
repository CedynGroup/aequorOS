from __future__ import annotations

import dataclasses

import pytest

from app.adapters.market_data.errors import (
    MESSAGE_TEMPLATES,
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)

_RENDER_PARAMS = {
    "vendor": "Bloomberg",
    "timestamp": "2026-07-15 17:00 UTC",
    "scope": "Ghana yield curve",
}


def test_every_code_has_a_template() -> None:
    assert set(MESSAGE_TEMPLATES) == set(BankFacingErrorCode)


@pytest.mark.parametrize("code", list(BankFacingErrorCode))
def test_render_leaves_no_raw_placeholders(code: BankFacingErrorCode) -> None:
    error = render_bank_facing(code, **_RENDER_PARAMS)
    assert error.code is code
    assert error.message
    assert "{" not in error.message
    assert "}" not in error.message


@pytest.mark.parametrize("code", list(BankFacingErrorCode))
def test_every_template_has_actions_and_valid_severity(code: BankFacingErrorCode) -> None:
    template = MESSAGE_TEMPLATES[code]
    assert template.actions
    assert all(action for action in template.actions)
    assert template.severity in ("informational", "warning", "urgent")


def test_render_with_missing_placeholder_fails_loudly() -> None:
    with pytest.raises(KeyError):
        render_bank_facing(BankFacingErrorCode.CREDENTIAL_INVALID, timestamp="now")


def test_spec_sample_message_verbatim() -> None:
    # §12.2 pre-authored CREDENTIAL_INVALID template.
    error = render_bank_facing(
        BankFacingErrorCode.CREDENTIAL_INVALID,
        vendor="Refinitiv",
        timestamp="2026-07-14 17:00 UTC",
    )
    assert error.message.startswith("Your Refinitiv credentials failed authentication.")
    assert "2026-07-14 17:00 UTC" in error.message
    assert error.actions == (
        "Update credentials",
        "View last successful pull",
        "Switch to manual upload",
    )
    assert error.severity == "urgent"


def test_bank_facing_error_is_frozen() -> None:
    error = render_bank_facing(BankFacingErrorCode.QUOTA_EXHAUSTED, vendor="Bloomberg")
    with pytest.raises(dataclasses.FrozenInstanceError):
        error.message = "tampered"  # type: ignore[misc]


def test_market_data_error_never_renders_internal_detail() -> None:
    bank_facing = render_bank_facing(
        BankFacingErrorCode.VENDOR_UNAVAILABLE, vendor="Bloomberg", timestamp="today"
    )
    internal = "HTTP 503 from //blp/refdata: session pool exhausted (host=bpipe-7)"
    error = MarketDataError(bank_facing, internal_detail=internal)
    assert error.bank_facing is bank_facing
    assert error.internal_detail == internal
    assert internal not in str(error)
    assert str(error) == bank_facing.message
