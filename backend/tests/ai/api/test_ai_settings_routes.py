"""AI settings are the Organisation Owner's, and nobody else's.

Deciding that this organisation's figures may leave the platform is not an
account-configuration task. A scoped Account administrator configures account
surfaces; only the persisted Owner binding may consent to egress.

This module's other-suite cases live in ``test_ai_settings_routes_integration.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai import schemas as schemas_ai
from app.ai.schemas import AiCommentarySettingsUpdate
from app.ai.service.features import (
    CONSENT_COVERED_FEATURES,
)


def test_a_tenant_cannot_consent_to_a_surface_the_consent_TEXT_does_not_describe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit A11-F2: the gate was believed to hold "by construction" and did not exist.

    `consent/ai-consent-2026-09-v1.md` promises in its own words that no "name,
    title, email address or identifier of any individual — staff, officer, director,
    shareholder or customer" is ever sent, and that monetary amounts and dates are
    "replaced by placeholders". A natural-language question is a sentence a person
    typed, and "the exposure to <a customer> at <a date> above <an amount>" contains
    all three, so `bi_nlq` and that text cannot both stand.

    What made it a real hole rather than a theoretical one: `gates.evaluate` checks
    the consent version for EQUALITY only, so a tenant who had accepted this text for
    ICAAP drafting could add `bi_nlq` under the same version and never be asked
    again. The belief that no settings panel existed yet is not a gate — the API
    accepted the field.

    Both directions are asserted, because refusing the pending surface is only
    correct if the described ones still work. Driven off the constants rather than a
    hardcoded name, so amending the consent text and moving a feature into
    `CONSENT_COVERED_FEATURES` makes this test follow rather than fail.
    """

    def build(features: list[str]) -> AiCommentarySettingsUpdate:
        return AiCommentarySettingsUpdate(
            enabled=True,
            enabled_features=features,
            consent_version="ai-consent-2026-09-v1",
            acknowledged=True,
            reason="exercise the consent coverage gate",
        )

    # The rule is proven against a SYNTHETIC pending feature rather than against
    # whichever surface happens to be uncovered today. On 2026-09-29 the consent
    # text was amended to v2 and `bi_nlq` became covered, which emptied
    # CONSENT_PENDING_FEATURES and would have made the original loop iterate
    # nothing — passing while proving nothing. The rule outlives any particular
    # surface, so the test does too: hold one real feature OUT of the covered set
    # and require the refusal.
    assert len(CONSENT_COVERED_FEATURES) >= 2, (
        "this test needs one covered feature to withhold and one to keep"
    )
    withheld, *still_covered = CONSENT_COVERED_FEATURES
    monkeypatch.setattr(schemas_ai, "CONSENT_PENDING_FEATURES", (withheld,))

    with pytest.raises(ValidationError) as refused:
        build([withheld])
    assert "not described by the current consent text" in str(refused.value)
    # And it cannot be smuggled in beside a covered one.
    with pytest.raises(ValidationError):
        build([still_covered[0], withheld])
    monkeypatch.undo()

    # The positive half. Without it, a change refusing EVERY feature would pass.
    assert CONSENT_COVERED_FEATURES
    for feature in CONSENT_COVERED_FEATURES:
        assert build([feature]).enabled_features == [feature]
    every_covered = build(list(CONSENT_COVERED_FEATURES))
    assert every_covered.enabled_features == sorted(CONSENT_COVERED_FEATURES)
