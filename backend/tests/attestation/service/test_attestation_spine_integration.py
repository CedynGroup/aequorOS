"""The attestation spine: digests, identity, policy, guards, chain.

These tests pin the properties the whole capability rests on. They are
deliberately about BEHAVIOUR that would be a defect if it changed, not about
implementation shape.

This module's other-suite cases live in ``test_attestation_spine.py``.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.attestation.service import workflow
from app.attestation.service.identity import (
    SignerIdentityError,
    ensure_signer_identity,
)
from app.attestation.service.policy import (
    SigningPolicy,
    resolve_policy,
)
from app.core.config import get_settings
from app.models import (
    ReturnSigningPolicy,
    User,
)
from tests.attestation.service.test_attestation_spine import _package
from tests.fixtures.canonical_bank_fixture import DEMO_USER_ID, SAMPLE_BANK_ID
from tests.support.factories.attestation import relax_signing
from tests.support.helpers import ORG_1


def _ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=DEMO_USER_ID)


def test_ensure_signer_identity_is_idempotent(db_session: Session) -> None:
    ctx = _ctx()
    first = ensure_signer_identity(db_session, ctx, DEMO_USER_ID)
    db_session.commit()
    second = ensure_signer_identity(db_session, ctx, DEMO_USER_ID)
    assert first.id == second.id
    assert first.signer_id == second.signer_id


def test_service_accounts_cannot_hold_a_signer_identity(db_session: Session) -> None:
    """Machines do not attest — this is what makes it structurally impossible
    for an integration key to produce a signature."""
    service_user = User(
        organization_id=ORG_1,
        email="integration.test@service.aequoros.invalid",
        display_name="Integration — test",
        role="analyst",
        auth_provider="service",
        is_active=True,
    )
    db_session.add(service_user)
    db_session.flush()
    with pytest.raises(SignerIdentityError, match="Service accounts"):
        ensure_signer_identity(db_session, _ctx(), service_user.id)


def test_configured_policy_makes_signature_mandatory(db_session: Session) -> None:
    """Opting in is what turns the submission gate on."""
    db_session.add(
        ReturnSigningPolicy(
            organization_id=ORG_1,
            return_family="liquidity",
            required_signatures=[{"role": "preparer"}, {"role": "approver"}],
            require_signature=True,
            effective_from=date(2026, 1, 1),
            reason="bank opts in",
        )
    )
    db_session.flush()
    resolved = resolve_policy(
        db_session,
        _ctx(),
        bank_id=SAMPLE_BANK_ID,
        return_code="LCR-NSFR",
        return_family="liquidity",
        basis="solo",
        as_at=date(2026, 6, 30),
    )
    assert resolved.source == "configured"
    assert resolved.require_signature is True


def _resolve_bsd3(db_session: Session) -> SigningPolicy:
    return resolve_policy(
        db_session,
        _ctx(),
        bank_id=SAMPLE_BANK_ID,
        return_code="LCR-NSFR",
        return_family="liquidity",
        basis="solo",
        as_at=date(2026, 6, 30),
    )


def test_esign_kill_switch_relaxes_the_platform_default(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ATTESTATION_ESIGN_REQUIRED=0 suspends the requirement with no rows at all."""
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()

    resolved = _resolve_bsd3(db_session)

    assert resolved.require_signature is False
    assert resolved.require_signed_pdf is False
    assert resolved.source == "esign_disabled"
    assert resolved.policy_id is None
    # Slots are kept: inert once the requirement is off, they still show what
    # WOULD be required the moment the flag returns.
    assert [slot.role for slot in resolved.slots] == ["preparer", "approver"]
    get_settings.cache_clear()


def test_esign_kill_switch_puts_configured_mandatory_rows_dormant(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill-switch overrides a configured mandatory row — and re-enabling
    restores the untouched row, which is what makes it dormant rather than gone."""
    row = ReturnSigningPolicy(
        organization_id=ORG_1,
        return_family="liquidity",
        required_signatures=[{"role": "preparer"}, {"role": "approver"}],
        require_signature=True,
        effective_from=date(2026, 1, 1),
        reason="bank opts in",
    )
    db_session.add(row)
    db_session.flush()

    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()
    dormant = _resolve_bsd3(db_session)
    assert dormant.require_signature is False
    assert dormant.source == "esign_disabled"
    # Audit can still see exactly which row went dormant.
    assert dormant.policy_id == str(row.id)

    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "1")
    get_settings.cache_clear()
    resumed = _resolve_bsd3(db_session)
    assert resumed.require_signature is True
    assert resumed.source == "configured"
    assert resumed.policy_id == str(row.id)
    get_settings.cache_clear()


def test_esign_kill_switch_keeps_admin_relaxed_rows_attributed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A policy an administrator already relaxed keeps its `configured` source:
    the flag changed nothing for it, and claiming otherwise would misattribute."""
    relax_signing(db_session, organization_id=ORG_1, return_code="LCR-NSFR")
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()

    resolved = _resolve_bsd3(db_session)

    assert resolved.require_signature is False
    assert resolved.source == "configured"
    get_settings.cache_clear()


def test_policy_resolution_prefers_the_most_specific_row(db_session: Session) -> None:
    ctx = _ctx()
    db_session.add_all(
        [
            ReturnSigningPolicy(
                organization_id=ORG_1,
                bank_id=None,
                return_code=None,
                return_family="liquidity",
                required_signatures=[{"role": "preparer"}, {"role": "approver"}],
                effective_from=date(2026, 1, 1),
                reason="family default",
            ),
            ReturnSigningPolicy(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                return_code="LCR-NSFR",
                return_family=None,
                required_signatures=[
                    {"role": "preparer"},
                    {"role": "approver", "officer_titles": ["Chief Financial Officer"]},
                ],
                effective_from=date(2026, 1, 1),
                reason="bank+return specific",
            ),
        ]
    )
    db_session.flush()

    resolved = resolve_policy(
        db_session,
        ctx,
        bank_id=SAMPLE_BANK_ID,
        return_code="LCR-NSFR",
        return_family="liquidity",
        basis="solo",
        as_at=date(2026, 6, 30),
    )
    approver = resolved.slot_for("approver")
    assert approver is not None
    assert approver.officer_titles == ("Chief Financial Officer",), (
        "the bank+return row must win over the family row"
    )


def test_policy_not_yet_effective_is_ignored(db_session: Session) -> None:
    """A policy change must never retroactively invalidate a filed return."""
    ctx = _ctx()
    db_session.add(
        ReturnSigningPolicy(
            organization_id=ORG_1,
            return_family="liquidity",
            required_signatures=[{"role": "preparer"}, {"role": "board"}],
            effective_from=date(2026, 12, 1),
            reason="future policy",
        )
    )
    db_session.flush()
    resolved = resolve_policy(
        db_session,
        ctx,
        bank_id=SAMPLE_BANK_ID,
        return_code="LCR-NSFR",
        return_family="liquidity",
        basis="solo",
        as_at=date(2026, 6, 30),
    )
    assert resolved.source == "platform_default"
    # Falling back to the default must not fall back to "nobody signs": a policy
    # dated in the future leaves the platform requirement in force, not a hole.
    assert resolved.require_signature is True


@pytest.mark.parametrize(
    "package_status", ["generated", "validated", "pending_approval", "approved"]
)
@pytest.mark.parametrize("engine_backed", [False, True])
def test_unsubmitted_package_without_register_commitment_requires_regeneration(
    db_session: Session, package_status: str, engine_backed: bool
) -> None:
    package = _package(status=package_status, register_state_digest=None)
    if not engine_backed:
        package.source_runs = []
    with pytest.raises(workflow.AttestationConflict, match="register_state_changed"):
        workflow.ensure_register_current(db_session, _ctx(), package)
    assert package.register_state_digest is None


def test_submitted_legacy_engine_package_keeps_historical_binding(db_session: Session) -> None:
    package = _package(status="submitted", register_state_digest=None)
    original = workflow.compute_binding(package)
    workflow.ensure_register_current(db_session, _ctx(), package)
    assert workflow.compute_binding(package) == original
    assert package.register_state_digest is None


def test_an_unsigned_return_cannot_be_filed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: no signatures, no filing — with no policy configured."""
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "spine-test-pepper")
    get_settings.cache_clear()

    # Transient, like every other guard test here: the gate reads the policy
    # table and the signature table, not the package row.
    package = _package(status="approved")

    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_submittable(db_session, _ctx(), package)
    assert excinfo.value.error_code == "attestation_incomplete"
    # The message must name who is still outstanding, not merely refuse.
    assert "preparer" in str(excinfo.value.detail)
    assert "approver" in str(excinfo.value.detail)
    get_settings.cache_clear()


def test_a_deployment_that_cannot_sign_says_so_instead_of_deadlocking(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mandatory signing + no signing key must not read as a missing signature.

    Without this the operator sees "outstanding: preparer, approver" and goes
    looking for a colleague, when the real cause is unset configuration that no
    amount of signing would fix — because the ceremony refuses too.
    """
    # setenv("") not delenv: deleting lets pydantic-settings read the value
    # back out of a developer's .env (see tests/conftest.py).
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    get_settings.cache_clear()

    package = _package(status="approved")

    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_submittable(db_session, _ctx(), package)
    assert excinfo.value.error_code == "signing_not_configured"
    message = str(excinfo.value.detail)
    assert "ATTESTATION_SIGNING_ENABLED" in message
    assert "SIGNER_ID_PEPPER" in message
    get_settings.cache_clear()


def test_relaxing_the_policy_is_what_reopens_filing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented escape hatch works — and is the ONLY thing that opens it.

    This is the behaviour every relaxed test suite depends on, so it is asserted
    rather than assumed.
    """
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    get_settings.cache_clear()

    package = _package(status="approved")
    relax_signing(db_session, organization_id=ORG_1, return_code="LCR-NSFR")

    workflow.ensure_submittable(db_session, _ctx(), package)  # no raise
    get_settings.cache_clear()


def test_esign_kill_switch_opens_the_submission_gate_platform_wide(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No policy row anywhere: the env flag alone reopens filing — even on a
    deployment that cannot sign — and turning it back on closes the gate again
    for an approved-but-unsigned package."""
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()

    package = _package(status="approved")
    workflow.ensure_submittable(db_session, _ctx(), package)  # no raise

    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "1")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    get_settings.cache_clear()
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_submittable(db_session, _ctx(), package)
    assert excinfo.value.error_code == "attestation_incomplete"
    get_settings.cache_clear()


def test_the_ceremony_is_refused_when_esign_is_disabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OFF means no ceremony at all, not optional signing: certification against
    a kill-switched policy refuses with the existing error code."""
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()

    policy = _resolve_bsd3(db_session)
    with pytest.raises(workflow.AttestationConflict) as excinfo:
        workflow.ensure_certifiable(_package(), policy, "preparer")
    assert excinfo.value.error_code == "signature_not_required"
    get_settings.cache_clear()
