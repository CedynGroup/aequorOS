"""Error taxonomy contract: every code renders, placeholders are business-level
only, and raw core internals never reach the bank-facing surface."""

from __future__ import annotations

import pytest

from app.adapters.temenos_t24.errors import (
    MESSAGE_TEMPLATES,
    TemenosError,
    TemenosErrorCode,
    render_bank_facing,
)

_PARAMS = {
    "core_system": "Temenos T24",
    "timestamp": "2026-06-30",
    "domain": "loan positions",
    "mode": "OFS",
}


def test_every_code_has_a_template() -> None:
    for code in TemenosErrorCode:
        assert code in MESSAGE_TEMPLATES


def test_every_template_renders_with_the_standard_params() -> None:
    for code in TemenosErrorCode:
        rendered = render_bank_facing(code, **_PARAMS)
        assert rendered.code is code
        assert rendered.message
        assert "{" not in rendered.message and "}" not in rendered.message
        assert rendered.actions
        assert rendered.severity in {"informational", "warning", "urgent"}


def test_missing_placeholder_fails_loud() -> None:
    # DOMAIN_NOT_PERMITTED needs {domain}; omitting it must raise, never ship
    # a message with a raw {placeholder}.
    with pytest.raises(KeyError):
        render_bank_facing(TemenosErrorCode.DOMAIN_NOT_PERMITTED, core_system="T24")


def test_str_of_error_renders_only_bank_facing_message() -> None:
    bank_facing = render_bank_facing(TemenosErrorCode.CREDENTIAL_INVALID, **_PARAMS)
    secret = "X-T24-INTERNAL sign-on rejected: OFS.SOURCE=BANK, USER=SVC.AEQUOROS, pw hash 0xdead"
    err = TemenosError(bank_facing, internal_detail=secret)
    assert str(err) == bank_facing.message
    assert secret not in str(err)
    assert "SVC.AEQUOROS" not in str(err)
    # the secret is retained for internal logging, just never in str()
    assert err.internal_detail == secret
    assert err.code is TemenosErrorCode.CREDENTIAL_INVALID


def test_templates_carry_no_core_internal_vocabulary() -> None:
    # bank-facing templates must not leak T24 wire vocabulary
    forbidden = ("OFS", "@FM", "@VM", "ENQUIRY.SELECT", "TAFJ", "http", "HTTP")
    for code in TemenosErrorCode:
        message = MESSAGE_TEMPLATES[code].message
        for token in forbidden:
            assert token not in message, f"{code.name} template leaks {token!r}"
