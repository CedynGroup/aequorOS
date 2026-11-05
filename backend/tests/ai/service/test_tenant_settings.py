"""The tenant toggle: consent to switch on, nothing to switch off.

This module's other-suite cases live in ``test_tenant_settings_integration.py``.
"""

from __future__ import annotations

import pytest

from app.ai.service import tenant_settings
from app.core.config import get_settings
from tests.ai.service.test_tenant_settings_integration import _update

pytestmark = pytest.mark.usefixtures("ai_enabled")


def test_an_unknown_feature_is_refused_by_the_schema() -> None:
    with pytest.raises(ValueError, match="Unknown AI features"):
        _update(enabled_features=["icaap_drafting", "mind_reading"])


def test_the_consent_text_for_the_configured_version_is_shipped() -> None:
    """A configured version naming no shipped text is a packaging fault."""
    tenant_settings.assert_consent_text_available()


def test_the_consent_text_names_what_leaves_and_what_never_does() -> None:
    """The text is the tenant's evidence of what it agreed to; pin its substance.

    Each phrase corresponds to a promise the code actually keeps, so a rewrite
    that drops one has also dropped the disclosure behind a guarantee.
    """
    text = tenant_settings.consent().text.casefold()
    for promise in (
        "people",  # individuals are never sent
        "descriptors only",  # the strict per-tenant mode
        "member of your staff",  # a person always reviews and accepts
        # v2 covers TWO features (drafting and questions), so the kill-switch
        # promise names either one rather than "the feature".
        "switch either feature off at any time",  # the tenant kill-switch
        "anthropic",  # the named sub-processor
        "monetary amounts and dates",  # withheld in both modes
        "does not run in africa",  # residency, recorded per call
    ):
        assert promise in text, f"the consent text no longer states: {promise}"


def test_the_boot_warning_fires_only_in_a_deployed_unapproved_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WARN, never refuse: a boot refusal locks out whoever could fix it."""
    assert tenant_settings.deployment_warning() is None  # test env

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AI_PRODUCTION_APPROVAL_REF", "")
    get_settings.cache_clear()
    warning = tenant_settings.deployment_warning()
    assert warning is not None
    assert "AI_PRODUCTION_APPROVAL_REF" in warning

    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()
    assert tenant_settings.deployment_warning() is None
