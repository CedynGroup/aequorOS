"""AI settings are the Organisation Owner's, and nobody else's.

Deciding that this organisation's figures may leave the platform is not an
account-configuration task. A scoped Account administrator configures account
surfaces; only the persisted Owner binding may consent to egress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuditEvent, AuthorizationBinding, User
from app.models.ai import AiCommentarySettings
from app.schemas import ai as schemas_ai
from app.schemas.ai import AiCommentarySettingsUpdate
from app.services import authorization
from app.services.ai import features as ai_features
from app.services.ai import gates
from app.services.ai.features import (
    CONSENT_COVERED_FEATURES,
)
from tests.api.helpers import ORG_1, USER_1, headers

URL = "/api/v1/organization/ai-settings"


@pytest.fixture(autouse=True)
def ai_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    get_settings.cache_clear()


def _bind(
    bundle: RoleBundle, *, module: ModuleScope, sensitivity: SensitivityScope
) -> dict[str, str]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION, None, module, sensitivity
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise the AI settings surface over HTTP.",
        )
        session.commit()
        user = session.get(User, USER_1)
        assert user is not None
        session.refresh(user)
        return headers(roles=("admin",), authorization_version=user.authorization_version)
    finally:
        session.close()


@pytest.fixture
def owner(db_client: TestClient) -> dict[str, str]:
    _ = db_client
    return _bind(
        RoleBundle.ORG_OWNER, module=ModuleScope.ACCOUNT, sensitivity=SensitivityScope.RESTRICTED
    )


@pytest.fixture
def analyst(db_client: TestClient) -> dict[str, str]:
    _ = db_client
    return _bind(
        RoleBundle.ANALYST, module=ModuleScope.CAPITAL, sensitivity=SensitivityScope.CONFIDENTIAL
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "enabled": True,
        "enabled_features": ["icaap_drafting"],
        "descriptor_only": True,
        "consent_version": get_settings().ai.consent_version,
        "acknowledged": True,
        "reason": "Approved at the risk committee.",
    }
    body.update(overrides)
    return body


def test_an_owner_reads_the_consent_text_and_the_current_state(
    db_client: TestClient, owner: dict[str, str]
) -> None:
    response = db_client.get(URL, headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["descriptor_only"] is True
    assert body["consent"]["version"] == get_settings().ai.consent_version
    assert body["consent"]["text"]
    assert body["deployment_enabled"] is True


def test_an_owner_can_switch_it_on_and_off(db_client: TestClient, owner: dict[str, str]) -> None:
    on = db_client.put(URL, json=_payload(), headers=owner)
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True
    assert on.json()["consent_current"] is True

    off = db_client.put(
        URL,
        json=_payload(
            enabled=False,
            enabled_features=[],
            consent_version=None,
            acknowledged=False,
            reason="Pausing pending the data-protection opinion.",
        ),
        headers=owner,
    )
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False


def test_switching_on_without_accepting_the_current_terms_is_refused(
    db_client: TestClient, owner: dict[str, str]
) -> None:
    response = db_client.put(URL, json=_payload(acknowledged=False), headers=owner)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["error_code"] == "consent_version_mismatch"


def test_an_analyst_may_not_read_or_change_the_settings(
    db_client: TestClient, analyst: dict[str, str]
) -> None:
    assert db_client.get(URL, headers=analyst).status_code == 403
    assert db_client.put(URL, json=_payload(), headers=analyst).status_code == 403


def test_a_change_is_audited(db_client: TestClient, owner: dict[str, str]) -> None:
    assert db_client.put(URL, json=_payload(), headers=owner).status_code == 200
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        event = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.event_type == "ai.settings.updated")
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )
        assert event is not None
        assert event.details["reason"] == "Approved at the risk committee."
        assert event.details["after"]["enabled"] is True
    finally:
        session.close()


def test_an_unauthenticated_caller_is_refused(db_client: TestClient) -> None:
    assert db_client.get(URL).status_code == 401


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


@pytest.mark.parametrize("phase", ["enqueue", "run"])
def test_a_row_that_smuggles_an_undescribed_surface_is_refused_by_the_egress_gate(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Audit A360-5 M2: the rule above was the consent text's ONLY enforcement.

    The schema refuses the PUT, but ``ai_commentary_settings`` is a mutable row with
    other writers — ``psql`` on the primary, a data migration, a future staff fix
    endpoint — and ``gates.evaluate``, the function that decides whether a request may
    reach a model at BOTH enqueue and run, checked only ``feature in enabled_features``.
    A row written past the schema therefore out-ranked the document the tenant signed.
    Here the row is written directly and the gate itself must refuse, at either phase,
    with its own code — while the described surfaces on the same row still pass, so a
    change refusing everything cannot satisfy this test either.
    """

    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    # Synthetic, for the reason given on the schema-side test above: the rule must
    # stay proven after the consent text grows to cover every shipped surface.
    assert len(CONSENT_COVERED_FEATURES) >= 2, (
        "this test needs one covered feature to withhold and one to keep"
    )
    withheld, *still_covered = CONSENT_COVERED_FEATURES
    monkeypatch.setattr(ai_features, "CONSENT_COVERED_FEATURES", tuple(still_covered))
    row = gates.tenant_row(db_session, ORG_1)
    if row is None:
        row = AiCommentarySettings(organization_id=ORG_1, updated_by=USER_1)
        db_session.add(row)
    row.enabled = True
    row.enabled_features = [*still_covered, withheld]
    row.descriptor_only = True
    row.consent_version = get_settings().ai.consent_version
    row.consented_by = USER_1
    row.consented_at = utc_now()
    row.updated_by = USER_1
    db_session.commit()

    # (withheld,) — NOT `CONSENT_PENDING_FEATURES`. That tuple is imported at module
    # level and frozen at import, and under consent v2 it is EMPTY, so iterating it
    # ran zero assertions: the test passed with the gate neutralised entirely
    # (caught by verification auditor V4). The synthetic withholding above is the
    # only pending feature this test has, so it is the one to assert on.
    for feature in (withheld,):
        decision = gates.evaluate(
            db_session,
            ORG_1,
            feature,
            phase=phase,  # type: ignore[arg-type]
            prompt_version="any-prompt-v1",
            requested_at=datetime.now(UTC),
        )
        assert not decision.allowed
        assert decision.code == "consent_not_covered"
        assert decision.message == gates.GATE_MESSAGES["consent_not_covered"]
        assert "Settings" not in decision.message, (
            "the refusal must not send an Owner to a panel that will refuse them"
        )

    for feature in still_covered:
        decision = gates.evaluate(
            db_session,
            ORG_1,
            feature,
            phase=phase,  # type: ignore[arg-type]
            prompt_version="any-prompt-v1",
            requested_at=datetime.now(UTC),
        )
        # The run phase may still refuse for a credential this process does not
        # hold; what it may not do is confuse a described surface with an undescribed one.
        assert decision.code != "consent_not_covered"
        if phase == "enqueue":
            assert decision.allowed, decision.code
