"""The egress gates, at BOTH enqueue and run.

The asymmetry these tests pin is the point of the design: a request can sit in
the queue across a toggle change, so a gate checked only at enqueue would let
every queued request through after someone pulled the switch. Each case flips a
switch BETWEEN the two phases and asserts the run refuses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai import AiCommentarySettings
from app.services.ai import approvals, gates
from tests.api.helpers import ORG_1

pytestmark = pytest.mark.usefixtures("ai_enabled")

_PROMPT = "icaap-draft-v1"


def _evaluate(db: Session, phase: str, **kwargs: object) -> gates.GateDecision:
    return gates.evaluate(
        db,
        ORG_1,
        "icaap_drafting",
        phase=phase,  # type: ignore[arg-type]
        prompt_version=_PROMPT,
        **kwargs,  # type: ignore[arg-type]
    )


def test_allowed_when_deployment_and_tenant_agree(
    db_session: Session, consented_tenant: AiCommentarySettings
) -> None:
    _ = consented_tenant
    assert _evaluate(db_session, "enqueue").allowed


# --- the deployment kill-switch --------------------------------------------


def test_kill_switch_refuses_at_enqueue(
    db_session: Session, consented_tenant: AiCommentarySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = consented_tenant
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()
    decision = _evaluate(db_session, "enqueue")
    assert not decision.allowed
    assert decision.code == "deployment_disabled"


def test_kill_switch_flipped_between_enqueue_and_run_refuses_the_run(
    db_session: Session, consented_tenant: AiCommentarySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The queued request must not be the one call that still goes out."""
    _ = consented_tenant
    assert _evaluate(db_session, "enqueue").allowed
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()
    decision = _evaluate(db_session, "run", requested_at=datetime.now(UTC))
    assert not decision.allowed
    assert decision.code == "deployment_disabled"


def test_deployment_gate_is_answerable_without_a_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route dependency asks this BEFORE resolving a tenant, for its 404."""
    assert gates.deployment_gate().allowed
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "0")
    get_settings.cache_clear()
    assert not gates.deployment_gate().allowed


# --- the tenant toggle ------------------------------------------------------


def test_tenant_without_a_row_is_refused(db_session: Session) -> None:
    decision = _evaluate(db_session, "enqueue")
    assert not decision.allowed
    assert decision.code == "tenant_disabled"


def test_tenant_toggle_flipped_between_enqueue_and_run_refuses_the_run(
    db_session: Session, consented_tenant: AiCommentarySettings
) -> None:
    assert _evaluate(db_session, "enqueue").allowed
    consented_tenant.enabled = False
    db_session.commit()
    decision = _evaluate(db_session, "run", requested_at=datetime.now(UTC))
    assert not decision.allowed
    assert decision.code == "tenant_disabled"


def test_feature_removed_between_enqueue_and_run_refuses_the_run(
    db_session: Session, consented_tenant: AiCommentarySettings
) -> None:
    assert _evaluate(db_session, "enqueue").allowed
    consented_tenant.enabled_features = ["bi_commentary"]
    db_session.commit()
    decision = _evaluate(db_session, "run", requested_at=datetime.now(UTC))
    assert decision.code == "feature_disabled"


def test_consent_version_bump_switches_every_tenant_off(
    db_session: Session, consented_tenant: AiCommentarySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-consent is a gate, not a reminder."""
    _ = consented_tenant
    assert _evaluate(db_session, "enqueue").allowed
    monkeypatch.setenv("AI_CONSENT_VERSION", "ai-consent-2099-01-v9")
    get_settings.cache_clear()
    decision = _evaluate(db_session, "enqueue")
    assert decision.code == "consent_outdated"


# --- deployed-environment approval -----------------------------------------


def test_deployed_environment_without_an_approval_ref_is_refused(
    db_session: Session, consented_tenant: AiCommentarySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is what makes "not production-ready" enforceable in code."""
    _ = consented_tenant
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("AI_PRODUCTION_APPROVAL_REF", "")
    get_settings.cache_clear()
    assert _evaluate(db_session, "enqueue").code == "deployment_not_approved"


def test_deployed_environment_with_a_ref_but_no_approved_configuration_is_refused(
    db_session: Session, consented_tenant: AiCommentarySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = consented_tenant
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AI_PRODUCTION_APPROVAL_REF", "EVAL-2026-09-A")
    get_settings.cache_clear()
    assert _evaluate(db_session, "enqueue").code == "configuration_not_approved"


def test_the_shipped_approvals_file_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """It ships empty on purpose; adding an entry is a reviewed commit."""
    _ = monkeypatch
    approvals.load_approvals.cache_clear()
    assert approvals.load_approvals() == ()


def test_an_approved_configuration_admits_exactly_its_own_tuple(
    db_session: Session, consented_tenant: AiCommentarySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing the prompt, model or effort silently falls OUT of approval."""
    _ = consented_tenant
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AI_PRODUCTION_APPROVAL_REF", "EVAL-2026-09-A")
    get_settings.cache_clear()
    settings = get_settings()
    entry = approvals.ApprovedConfiguration(
        feature="icaap_drafting",
        prompt_version=_PROMPT,
        model=settings.ai.model,
        effort=settings.ai.effort,
        app_env="production",
        eval_report_sha256=None,
        approved_by=None,
        approved_on=None,
        reference=None,
    )
    monkeypatch.setattr(approvals, "load_approvals", lambda: (entry,))
    assert _evaluate(db_session, "enqueue").allowed

    monkeypatch.setenv("AI_EFFORT", "low")
    get_settings.cache_clear()
    assert _evaluate(db_session, "enqueue").code == "configuration_not_approved"


def test_local_and_test_environments_need_no_approval(
    db_session: Session, consented_tenant: AiCommentarySettings
) -> None:
    _ = consented_tenant
    assert get_settings().ai.production_approval_ref is None
    assert _evaluate(db_session, "enqueue").allowed


# --- run-only gates ---------------------------------------------------------


def test_a_request_that_waited_too_long_is_expired(
    db_session: Session, consented_tenant: AiCommentarySettings
) -> None:
    _ = consented_tenant
    stale = datetime.now(UTC) - timedelta(seconds=get_settings().ai.queue_expiry_seconds + 60)
    assert _evaluate(db_session, "run", requested_at=stale).code == "queue_expired"


def test_run_refuses_without_a_usable_backend(
    db_session: Session, consented_tenant: AiCommentarySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API never concludes this — only the process that would make the call."""
    _ = consented_tenant
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    assert _evaluate(db_session, "run", requested_at=datetime.now(UTC)).code == "not_configured"
    # The same state at ENQUEUE is fine: the API does not hold the key.
    assert _evaluate(db_session, "enqueue").allowed


def test_recorded_backend_is_refused_in_a_deployed_environment(
    db_session: Session, consented_tenant: AiCommentarySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replaying a fixture in staging would look exactly like a working feature."""
    _ = consented_tenant
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AI_PRODUCTION_APPROVAL_REF", "EVAL-2026-09-A")
    monkeypatch.setenv("AI_MODEL_BACKEND", "recorded")
    get_settings.cache_clear()
    settings = get_settings()
    entry = approvals.ApprovedConfiguration(
        feature="icaap_drafting",
        prompt_version=_PROMPT,
        model=settings.ai.model,
        effort=settings.ai.effort,
        app_env="production",
        eval_report_sha256=None,
        approved_by=None,
        approved_on=None,
        reference=None,
    )
    monkeypatch.setattr(approvals, "load_approvals", lambda: (entry,))
    assert _evaluate(db_session, "run", requested_at=datetime.now(UTC)).code == "not_configured"


# --- descriptor-only default ------------------------------------------------


def test_descriptor_only_defaults_to_the_strict_answer(db_session: Session) -> None:
    """A tenant that has not chosen has not chosen to send figures."""
    assert gates.descriptor_only(db_session, ORG_1) is True


def test_descriptor_only_follows_the_tenant_row(
    db_session: Session, consented_tenant: AiCommentarySettings
) -> None:
    assert gates.descriptor_only(db_session, ORG_1) is False
    consented_tenant.descriptor_only = True
    db_session.commit()
    assert gates.descriptor_only(db_session, ORG_1) is True


def test_every_gate_code_has_user_facing_copy() -> None:
    """A raw enum must never reach a page (UI copy is production copy)."""
    from typing import get_args  # noqa: PLC0415

    for code in get_args(gates.GateCode):
        message = gates.GATE_MESSAGES[code]
        assert message and message != code
        assert message[0].isupper() and message.endswith(".")
