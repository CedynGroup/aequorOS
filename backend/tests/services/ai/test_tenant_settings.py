"""The tenant toggle: consent to switch on, nothing to switch off."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import AuditEvent
from app.schemas.ai import AiCommentarySettingsUpdate
from app.services.ai import tenant_settings
from tests.api.helpers import ORG_1, USER_1

pytestmark = pytest.mark.usefixtures("ai_enabled")


@pytest.fixture
def ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


def _update(**overrides: object) -> AiCommentarySettingsUpdate:
    payload: dict[str, object] = {
        "enabled": True,
        "enabled_features": ["icaap_drafting"],
        "descriptor_only": True,
        "consent_version": get_settings().ai.consent_version,
        "acknowledged": True,
        "reason": "Approved at the risk committee.",
    }
    payload.update(overrides)
    return AiCommentarySettingsUpdate.model_validate(payload)


def test_a_tenant_with_no_row_reads_as_off_and_strict(
    db_session: Session, ctx: TenantContext
) -> None:
    read = tenant_settings.get(db_session, ctx)
    assert read.enabled is False
    assert read.descriptor_only is True
    assert read.consent_current is False
    assert read.consent.text


def test_enabling_requires_the_current_consent_version(
    db_session: Session, ctx: TenantContext
) -> None:
    with pytest.raises(HTTPException) as excinfo:
        tenant_settings.update(db_session, ctx, _update(consent_version="ai-consent-1999-v1"))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error_code"] == "consent_version_mismatch"  # type: ignore[index]


def test_enabling_requires_an_explicit_acknowledgement(
    db_session: Session, ctx: TenantContext
) -> None:
    """Ticking the box is the act; sending the version alone is not consent."""
    with pytest.raises(HTTPException) as excinfo:
        tenant_settings.update(db_session, ctx, _update(acknowledged=False))
    assert excinfo.value.detail["error_code"] == "consent_version_mismatch"  # type: ignore[index]


def test_enabling_stamps_who_consented_and_when(db_session: Session, ctx: TenantContext) -> None:
    read = tenant_settings.update(db_session, ctx, _update())
    assert read.enabled is True
    assert read.consent_current is True
    assert read.consent_version_accepted == get_settings().ai.consent_version
    assert read.consented_at is not None


def test_disabling_never_requires_consent_and_always_succeeds(
    db_session: Session, ctx: TenantContext
) -> None:
    """A kill-switch that a validation error can block is not a kill-switch."""
    tenant_settings.update(db_session, ctx, _update())
    read = tenant_settings.update(
        db_session,
        ctx,
        _update(
            enabled=False,
            enabled_features=[],
            consent_version=None,
            acknowledged=False,
            reason="Pausing pending the data-protection opinion.",
        ),
    )
    assert read.enabled is False


def test_a_consent_version_bump_makes_the_tenant_not_current(
    db_session: Session, ctx: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_settings.update(db_session, ctx, _update())
    monkeypatch.setenv("AI_CONSENT_VERSION", "ai-consent-2099-01-v9")
    monkeypatch.setattr(tenant_settings, "consent_text", lambda version: f"# terms {version}")
    get_settings.cache_clear()
    read = tenant_settings.get(db_session, ctx)
    assert read.enabled is True
    assert read.consent_current is False


def test_every_change_is_audited_without_free_text_beyond_the_reason(
    db_session: Session, ctx: TenantContext
) -> None:
    tenant_settings.update(db_session, ctx, _update())
    event = db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "ai.settings.updated")
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    assert event is not None
    assert set(event.details) >= {"before", "after", "reason"}
    assert set(event.details["after"]) == {
        "enabled",
        "enabled_features",
        "descriptor_only",
        "consent_version",
    }


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
        "switch the feature off at any time",  # the tenant kill-switch
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


def test_availability_is_what_a_non_owner_learns(db_session: Session, ctx: TenantContext) -> None:
    off = tenant_settings.availability(
        db_session, ctx, "icaap_drafting", prompt_version="icaap-draft-v1"
    )
    assert off.available is False
    assert off.reason == "tenant_disabled"
    assert off.poll_after_seconds == get_settings().ai.client_poll_seconds

    tenant_settings.update(db_session, ctx, _update())
    on = tenant_settings.availability(
        db_session, ctx, "icaap_drafting", prompt_version="icaap-draft-v1"
    )
    assert on.available is True
    assert on.descriptor_only is True
