"""Enterprise-stress attestation requires an exact-bank Signer ceremony."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.attestation.models import SigningAuthorization
from app.core import security
from app.core.authorization import (
    BindingStatus,
    ModuleScope,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.identity.models.user import User
from app.models import EnterpriseStressSignoff
from app.models.audit_event import AuditEvent
from app.stress.service import enterprise_stress_signoff
from tests.data_engine.api.test_ingestion_integration import seed_bank
from tests.stress.api.test_enterprise_stress_integration import (
    _approve_scenario,
    _create_scenario,
    _period_id,
    _seed_checker,
)
from tests.stress.api.test_enterprise_stress_signoff_integration import _create_signoff
from tests.stress.authority import grant
from tests.support.helpers import ORG_1, USER_1, headers

pytestmark = pytest.mark.usefixtures(
    "fx_run_authority", "irrbb_run_authority", "stress_run_authority"
)

RUNS_URL = "/api/v1/banks/{bank_id}/enterprise-stress/runs"
SIGNOFF_URL = "/api/v1/banks/{bank_id}/enterprise-stress/signoffs"
PASSWORD = "stress-signer-test-password-not-production"  # noqa: S105 - synthetic fixture


@pytest.fixture(autouse=True)
def signer_identity_pepper(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIGNER_ID_PEPPER", "stress-test-signer-pepper-not-for-production")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run(client: TestClient, bank_id: str) -> str:
    period_id = _period_id(client, bank_id)
    checker = _seed_checker(client)
    scenario_id = _create_scenario(client)
    _approve_scenario(client, scenario_id, checker)
    response = client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Annual ICAAP stress test.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def _seed_officer(
    db: Session,
    bank_id: str,
    *,
    signer: bool = True,
    read: bool = True,
) -> tuple[UUID, UUID | None, UUID | None]:
    user_id = uuid4()
    db.add(
        User(
            id=user_id,
            organization_id=ORG_1,
            email=f"stress-signer-{user_id.hex[:8]}@aequoros.example",
            display_name="Stress Signing Officer",
            role="approver",
            password_hash=security.hash_password(PASSWORD),
            auth_provider="password",
        )
    )
    db.commit()
    viewer_binding = (
        grant(
            db,
            RoleBundle.VIEWER,
            user_id=user_id,
            bank_id=bank_id,
            sensitivity=SensitivityScope.CONFIDENTIAL,
        )
        if read
        else None
    )
    signer_binding = (
        grant(
            db,
            RoleBundle.SIGNER,
            user_id=user_id,
            bank_id=bank_id,
            sensitivity=SensitivityScope.RESTRICTED,
        )
        if signer
        else None
    )
    return user_id, signer_binding, viewer_binding


def _submit(client: TestClient, bank_id: str, signoff_id: str, recipient: UUID) -> None:
    response = client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/submit",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "attestation_recipient_user_id": str(recipient),
            "reason": "Route this sign-off to the named Board signer.",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["attestation_recipient_user_id"] == str(recipient)


def _pending(client: TestClient, db: Session) -> tuple[str, str, UUID, UUID, UUID]:
    bank_id = seed_bank(client)
    run_id = _run(client, bank_id)
    signoff_id = _create_signoff(client, bank_id, run_id)
    recipient, signer_binding, viewer_binding = _seed_officer(db, bank_id)
    assert signer_binding is not None and viewer_binding is not None
    _submit(client, bank_id, signoff_id, recipient)
    return bank_id, signoff_id, recipient, signer_binding, viewer_binding


def _attest(  # noqa: PLR0913 - the route and its two ceremony proofs are explicit
    client: TestClient,
    bank_id: str,
    signoff_id: str,
    actor: UUID,
    *,
    token: str,
    digest: str,
) -> Response:
    return client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/attest",
        headers=headers(user_id=actor, roles=("approver",)),
        json={
            "credibility_rationale": "Reviewed and challenged; results are credible.",
            "board_challenge": "Challenged the scenario severity and retained it.",
            "reason": "Board attestation after fresh authentication.",
            "authorization_token": token,
            "expected_signing_digest": digest,
        },
    )


def _step_up(client: TestClient, bank_id: str, signoff_id: str, actor: UUID) -> Response:
    return client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/step-up",
        headers=headers(user_id=actor, roles=("approver",)),
        json={"password": PASSWORD},
    )


def _stored_signoff(db: Session, signoff_id: str) -> EnterpriseStressSignoff:
    db.expire_all()
    row = db.get(EnterpriseStressSignoff, UUID(signoff_id))
    assert row is not None
    return row


def _authorization_count(db: Session, signoff_id: str, user_id: UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(SigningAuthorization)
            .where(
                SigningAuthorization.package_id == UUID(signoff_id),
                SigningAuthorization.user_id == user_id,
            )
        )
        or 0
    )


def test_named_signer_with_genuine_step_up_attests_once(
    db_client: TestClient, db_session: Session
) -> None:
    bank_id, signoff_id, signer, _, _ = _pending(db_client, db_session)
    detail_url = SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}"
    first = db_client.get(detail_url, headers=headers(user_id=signer, roles=("viewer",)))
    second = db_client.get(detail_url, headers=headers(user_id=signer, roles=("viewer",)))
    assert first.status_code == second.status_code == 200
    assert first.json()["signing_digest"] == second.json()["signing_digest"]
    digest = first.json()["signing_digest"]

    granted = _step_up(db_client, bank_id, signoff_id, signer)
    assert granted.status_code == 200, granted.text
    assert granted.json()["signing_digest"] == digest
    assert granted.json()["method"] == "password_reauth"
    token = granted.json()["authorization_token"]

    attested = _attest(db_client, bank_id, signoff_id, signer, token=token, digest=digest)
    assert attested.status_code == 200, attested.text
    assert attested.json()["status"] == "attested"
    assert attested.json()["attested_by"] == str(signer)
    assert attested.json()["signing_digest"] == digest
    authorization_id = UUID(attested.json()["signing_authorization_id"])
    authorization = db_session.get(SigningAuthorization, authorization_id)
    assert authorization is not None
    assert authorization.package_id == UUID(signoff_id)
    assert authorization.user_id == signer
    assert authorization.signing_role == "board"
    assert authorization.certification_digest == digest
    assert authorization.consumed_at is not None
    assert authorization.auth_evidence["method"] == "password_reauth"
    signed = _stored_signoff(db_session, signoff_id)
    # Recording the signer's challenge cannot make the just-signed source stale.
    assert enterprise_stress_signoff._current_signing_digest(signed) == digest

    before = signed.updated_at
    replay = _attest(db_client, bank_id, signoff_id, signer, token=token, digest=digest)
    assert replay.status_code == 409
    assert _stored_signoff(db_session, signoff_id).updated_at == before


def test_approver_alone_is_denied_without_token_or_attestation_mutation(
    db_client: TestClient, db_session: Session
) -> None:
    bank_id, signoff_id, signer, signer_binding_id, _ = _pending(db_client, db_session)
    signer_binding = db_session.get(AuthorizationBinding, signer_binding_id)
    assert signer_binding is not None
    signer_binding.role_bundle = RoleBundle.APPROVER.value
    signer_binding.sensitivity_scope = SensitivityScope.CONFIDENTIAL.value
    db_session.commit()
    detail = db_client.get(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}",
        headers=headers(user_id=signer, roles=("approver",)),
    )
    assert detail.status_code == 200
    before = _stored_signoff(db_session, signoff_id).updated_at

    stepped_up = _step_up(db_client, bank_id, signoff_id, signer)
    assert stepped_up.status_code == 403, stepped_up.text
    refused = _attest(
        db_client,
        bank_id,
        signoff_id,
        signer,
        token="not-a-genuine-step-up",
        digest=detail.json()["signing_digest"],
    )
    assert refused.status_code == 403, refused.text
    assert _authorization_count(db_session, signoff_id, signer) == 0
    row = _stored_signoff(db_session, signoff_id)
    assert row.status == "pending_attestation"
    assert row.updated_at == before
    assert row.attested_by is None
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "enterprise_stress_signoff.attested")
        )
        == 0
    )


def test_configure_only_caller_cannot_disclose_write_responses(
    db_client: TestClient, db_session: Session
) -> None:
    bank_id, signoff_id, _, _, _ = _pending(db_client, db_session)
    configurator, _, _ = _seed_officer(db_session, bank_id, signer=False, read=False)
    grant(
        db_session,
        RoleBundle.CONFIGURATOR,
        user_id=configurator,
        bank_id=bank_id,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    db_session.commit()
    before = _stored_signoff(db_session, signoff_id).updated_at

    for response in (
        _step_up(db_client, bank_id, signoff_id, configurator),
        _attest(
            db_client,
            bank_id,
            signoff_id,
            configurator,
            token="not-a-genuine-step-up",
            digest="0" * 64,
        ),
    ):
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"
        assert "scenario_narrative" not in response.text
    row = _stored_signoff(db_session, signoff_id)
    assert row.status == "pending_attestation" and row.updated_at == before


@pytest.mark.parametrize(
    "scope_failure",
    [
        "organization",
        "wrong_bank",
        "wrong_module",
        "wrong_sensitivity",
        "expired",
        "future",
        "suspended",
        "revoked",
    ],
)
def test_mismatched_or_inactive_signer_binding_is_denied(
    db_client: TestClient, db_session: Session, scope_failure: str
) -> None:
    bank_id, signoff_id, signer, signer_binding_id, _ = _pending(db_client, db_session)
    binding = db_session.get(AuthorizationBinding, signer_binding_id)
    assert binding is not None
    if scope_failure == "organization":
        binding.institution_scope = "organization"
        binding.institution_id = None
    elif scope_failure == "wrong_bank":
        own_bank = db_session.get(Bank, bank_id)
        assert own_bank is not None
        sibling_id = "BK-1S000099"
        db_session.add(
            Bank(
                id=sibling_id,
                organization_id=ORG_1,
                name="Sibling stress bank",
                short_name="Sibling",
                currency=own_bank.currency,
                jurisdiction_code=own_bank.jurisdiction_code,
                license_type=own_bank.license_type,
                institution_type=own_bank.institution_type,
            )
        )
        db_session.flush()
        binding.institution_id = sibling_id
    elif scope_failure == "wrong_module":
        binding.module_scope = ModuleScope.REGULATORY.value
    elif scope_failure == "wrong_sensitivity":
        binding.sensitivity_scope = SensitivityScope.CONFIDENTIAL.value
    elif scope_failure == "expired":
        binding.valid_from = datetime.now(UTC) - timedelta(hours=2)
        binding.valid_until = datetime.now(UTC) - timedelta(hours=1)
    elif scope_failure == "future":
        binding.valid_from = datetime.now(UTC) + timedelta(hours=1)
    elif scope_failure == "suspended":
        binding.status = BindingStatus.SUSPENDED.value
    else:
        binding.status = BindingStatus.REVOKED.value
        binding.revoked_at = datetime.now(UTC)
        binding.revoked_by_type = "system"
        binding.revoked_by_id = "stress-signer-test"
        binding.revoked_reason = "Prove revocation is immediate"
    db_session.commit()
    before = _stored_signoff(db_session, signoff_id).updated_at

    step_up = _step_up(db_client, bank_id, signoff_id, signer)
    assert step_up.status_code == 403, step_up.text
    assert _authorization_count(db_session, signoff_id, signer) == 0
    row = _stored_signoff(db_session, signoff_id)
    assert row.status == "pending_attestation" and row.updated_at == before


def test_authorization_evaluator_failure_denies_without_minting(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank_id, signoff_id, signer, _, _ = _pending(db_client, db_session)
    before = _stored_signoff(db_session, signoff_id).updated_at

    def fail_closed(*args: object, **kwargs: object) -> object:
        raise RuntimeError("authorization store unavailable")

    monkeypatch.setattr(
        "app.stress.service.scope_authorization.authorization.evaluate_structural_permission",
        fail_closed,
    )
    step_up = _step_up(db_client, bank_id, signoff_id, signer)
    assert step_up.status_code == 403, step_up.text
    assert _authorization_count(db_session, signoff_id, signer) == 0
    row = _stored_signoff(db_session, signoff_id)
    assert row.status == "pending_attestation" and row.updated_at == before


def test_signer_without_source_read_is_hidden_and_cannot_attest(
    db_client: TestClient, db_session: Session
) -> None:
    bank_id, signoff_id, signer, _, viewer_binding_id = _pending(db_client, db_session)
    viewer_binding = db_session.get(AuthorizationBinding, viewer_binding_id)
    assert viewer_binding is not None
    db_session.delete(viewer_binding)
    db_session.commit()
    before = _stored_signoff(db_session, signoff_id).updated_at

    refused = _attest(
        db_client,
        bank_id,
        signoff_id,
        signer,
        token="not-a-genuine-step-up",
        digest="0" * 64,
    )
    assert refused.status_code == 404
    row = _stored_signoff(db_session, signoff_id)
    assert row.status == "pending_attestation" and row.updated_at == before


def test_wrong_named_recipient_cannot_step_up_or_attest(
    db_client: TestClient, db_session: Session
) -> None:
    bank_id, signoff_id, named, _, _ = _pending(db_client, db_session)
    other, _, _ = _seed_officer(db_session, bank_id)
    detail = db_client.get(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}",
        headers=headers(user_id=other, roles=("viewer",)),
    )
    assert detail.status_code == 200
    before = _stored_signoff(db_session, signoff_id).updated_at

    step_up = _step_up(db_client, bank_id, signoff_id, other)
    assert step_up.status_code == 409
    assert step_up.json()["error"]["details"]["error_code"] == "not_the_named_signer"
    refused = _attest(
        db_client,
        bank_id,
        signoff_id,
        other,
        token="not-a-genuine-step-up",
        digest=detail.json()["signing_digest"],
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["error_code"] == "not_the_named_signer"
    assert _authorization_count(db_session, signoff_id, other) == 0
    row = _stored_signoff(db_session, signoff_id)
    assert row.status == "pending_attestation"
    assert row.attestation_recipient_user_id == named
    assert row.updated_at == before


def test_digest_drift_refuses_attestation_without_spending_step_up(
    db_client: TestClient, db_session: Session
) -> None:
    bank_id, signoff_id, signer, _, _ = _pending(db_client, db_session)
    detail = db_client.get(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}",
        headers=headers(user_id=signer, roles=("viewer",)),
    )
    digest = detail.json()["signing_digest"]
    granted = _step_up(db_client, bank_id, signoff_id, signer)
    assert granted.status_code == 200, granted.text
    token = granted.json()["authorization_token"]
    signoff = _stored_signoff(db_session, signoff_id)
    signoff.assumptions_rationale = "The stored rationale changed after the signer's review."
    db_session.commit()

    refused = _attest(db_client, bank_id, signoff_id, signer, token=token, digest=digest)
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["error_code"] == "signing_digest_changed"
    row = _stored_signoff(db_session, signoff_id)
    assert row.status == "pending_attestation" and row.attested_by is None
    authorization = db_session.scalar(
        select(SigningAuthorization).where(SigningAuthorization.package_id == UUID(signoff_id))
    )
    assert authorization is not None and authorization.consumed_at is None


def test_authorized_recipient_still_needs_genuine_step_up(
    db_client: TestClient, db_session: Session
) -> None:
    bank_id, signoff_id, signer, _, _ = _pending(db_client, db_session)
    detail = db_client.get(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}",
        headers=headers(user_id=signer, roles=("viewer",)),
    )
    before = _stored_signoff(db_session, signoff_id).updated_at

    refused = _attest(
        db_client,
        bank_id,
        signoff_id,
        signer,
        token="invented-authorization-token",
        digest=detail.json()["signing_digest"],
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["error_code"] == "step_up_failed"
    row = _stored_signoff(db_session, signoff_id)
    assert row.status == "pending_attestation" and row.updated_at == before


def test_submission_rejects_self_routing_and_leaves_draft(
    db_client: TestClient, db_session: Session
) -> None:
    bank_id = seed_bank(db_client)
    run_id = _run(db_client, bank_id)
    signoff_id = _create_signoff(db_client, bank_id, run_id)
    _seed_officer(db_session, bank_id)
    response = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/submit",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "attestation_recipient_user_id": str(USER_1),
            "reason": "Attempt to route the attestation to its maker.",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["details"]["error_code"] == "maker_is_checker"
    assert _stored_signoff(db_session, signoff_id).status == "draft"


def test_submission_rejects_self_routing_by_a_non_creator(
    db_client: TestClient, db_session: Session
) -> None:
    """The current submitter is a maker before submitted_by is persisted."""
    bank_id = seed_bank(db_client)
    run_id = _run(db_client, bank_id)
    signoff_id = _create_signoff(db_client, bank_id, run_id)
    submitter, _, _ = _seed_officer(db_session, bank_id)
    grant(
        db_session,
        RoleBundle.ANALYST,
        user_id=submitter,
        bank_id=bank_id,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )

    response = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/submit",
        headers=headers(user_id=submitter, roles=("approver",)),
        json={
            "attestation_recipient_user_id": str(submitter),
            "reason": "Attempt to submit and attest as the same officer.",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["details"]["error_code"] == "maker_is_checker"
    stored = _stored_signoff(db_session, signoff_id)
    assert stored.status == "draft"
    assert stored.submitted_by is None
    assert stored.attestation_recipient_user_id is None
