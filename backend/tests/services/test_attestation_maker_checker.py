"""Structural maker-checker on the certification path (audit finding P0-7).

Before this suite, separation of duties on the route to ``approved`` was a
property of *configuration*: ``ensure_maker_checker`` compares a candidate
signer against prior signatures only when the policy leaves ``distinct_signers``
true, and it never asks whether the policy named a checker slot at all. A
``return_signing_policies`` row of ``[preparer]`` alone, or one with
``distinct_signers=False``, therefore drove ``pending_approval -> approved`` on
one officer's signature — no checker, no approval decision, and a filing the
Bank of Ghana would receive as attested.

What is asserted here is that the rule now holds *structurally*: it is read off
the signatures that exist rather than the policy that asked for them, at the one
moment certification releases a package, so no row an administrator can write
and no environment variable an operator can set produces a return that one
officer both prepared and approved.

The harness (`_seed`, `_certify`, the signing environment) is imported from
``test_attestation_workspace`` deliberately — these tests must run the REAL
ceremony, keys and all. A hand-built package would prove only that a guard
function raises when called.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import (
    AttestationSignature,
    AuditEvent,
    RegulatoryPackage,
    RegulatoryPackageApproval,
    ReturnSigningPolicy,
)
from app.schemas.attestation import CertifyRequest
from app.schemas.regulatory_reporting import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
)
from app.services import attestation_api
from app.services.attestation import routing, workflow
from app.services.attestation.policy import SignatureSlot, SigningPolicy
from app.services.attestation.workflow import AttestationConflict
from app.services.regulatory_reporting import workflow as reporting_workflow
from tests.fixtures.canonical_bank_fixture import DEMO_ORG_ID, DEMO_USER_ID, SAMPLE_BANK_ID
from tests.services.test_attestation_workspace import (
    ANALYST_USER_ID,
    APPROVER,
    APPROVER_USER_ID,
    MAKER,
    OTHER_APPROVER_USER_ID,
    _approvals,
    _certify,
    _seed,
    _signature_count,
)

#: The analyst has no ``approver`` platform role — the identity the entitlement
#: gate must refuse.
ANALYST = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=ANALYST_USER_ID, roles=("analyst",)
)
OTHER_APPROVER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=OTHER_APPROVER_USER_ID, roles=("approver",)
)


@pytest.fixture(autouse=True)
def signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The imported harness signs for real, so it needs a real key directory."""
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-pepper-not-for-production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "1")
    monkeypatch.setenv("SIGNING_BACKEND", "software")
    monkeypatch.setenv(
        "CREDENTIAL_VAULT_MASTER_KEY", "test-vault-master-key-not-for-production-0004"
    )
    monkeypatch.setenv("SIGNING_SOFTWARE_KEY_DIR", str(tmp_path / "signing_keys"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _policy_row(db: Session) -> ReturnSigningPolicy:
    """The mandatory row ``_seed`` writes, so a test can bend one field of it."""
    row = db.scalar(
        select(ReturnSigningPolicy).where(
            ReturnSigningPolicy.organization_id == DEMO_ORG_ID,
            ReturnSigningPolicy.bank_id == SAMPLE_BANK_ID,
            ReturnSigningPolicy.return_code == "LCR-NSFR",
        )
    )
    assert row is not None
    return row


def _signature_fingerprints(db: Session) -> list[tuple[UUID, str, str]]:
    """Every signature's identity and chain link — what must never be rewritten."""
    return [
        (row.id, row.entry_hash, row.prev_hash)
        for row in db.scalars(
            select(AttestationSignature).order_by(
                AttestationSignature.created_at, AttestationSignature.id
            )
        )
    ]


def _certification_events(db: Session, package: RegulatoryPackage) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.entity_id == str(package.id),
                AuditEvent.event_type.like("attestation.certified_%"),
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )


# --- 1. the preparer prepares ------------------------------------------------


def test_the_preparer_can_prepare_and_certify_but_that_alone_approves_nothing(
    db_session: Session,
) -> None:
    """Case 1. The maker's half of the act works, and stops where it should.

    The preparer's signature routes the return for approval; it must not release
    it. Asserted together because "the preparer can prepare" is only interesting
    alongside "and the package is still waiting".
    """
    package = _seed(db_session)
    signature = _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    assert signature.signing_role == "preparer"
    assert signature.signer_user_id == DEMO_USER_ID
    assert package.attestation_state == "preparer_certified"
    assert package.status == "pending_approval"
    assert package.certification_digest is not None  # the figures are frozen
    assert _approvals(db_session, package) == []


# --- 2. the same person cannot be both ---------------------------------------


def test_the_preparer_cannot_certify_as_the_approver(db_session: Session) -> None:
    """Case 2. The signing route: same human, second slot, refused."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _certify(db_session, MAKER, package, role="approver")
    assert raised.value.error_code == "maker_checker"

    db_session.rollback()
    db_session.refresh(package)
    assert package.status == "pending_approval"
    assert _approvals(db_session, package) == []


def test_the_preparer_cannot_take_the_bare_approval_decision_either(
    db_session: Session,
) -> None:
    """Case 2, other route. Both ways to ``approved`` refuse the same person."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(HTTPException) as raised:
        reporting_workflow.decide_approval(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            package.id,
            PackageApprovalDecisionCreate(action="approved"),
        )
    assert raised.value.status_code == 409
    assert "Maker-checker" in str(raised.value.detail)
    db_session.rollback()


def test_a_policy_without_a_checker_slot_cannot_release_the_package(
    db_session: Session,
) -> None:
    """Case 2, the structural hole this finding is about.

    ``[preparer]`` alone is a policy an administrator can write in Settings
    today. Under the previous code the preparer's own signature satisfied every
    slot, ``is_fully_certified`` went true, and the package moved straight to
    ``approved`` with no checker and no approval decision — a filing attested by
    one officer. The release now reads the signatures rather than the policy, so
    the misconfiguration deadlocks instead of releasing.
    """
    package = _seed(db_session)
    row = _policy_row(db_session)
    row.required_signatures = [{"role": "preparer", "min_count": 1, "officer_titles": []}]
    db_session.commit()

    with pytest.raises(AttestationConflict) as raised:
        _certify(db_session, MAKER, package, role="preparer")
    assert raised.value.error_code == "maker_checker_not_satisfied"
    # And it says what to fix, rather than merely refusing.
    assert "approver" in str(raised.value.detail)

    db_session.rollback()
    db_session.refresh(package)
    assert package.status != "approved"
    assert _approvals(db_session, package) == []


def test_distinct_signers_false_cannot_let_one_officer_fill_both_slots(
    db_session: Session,
) -> None:
    """Case 2, the second structural hole.

    ``distinct_signers=False`` short-circuits ``ensure_maker_checker``'s
    comparison against prior signatures, and its unconditional check is only
    against ``generated_by``. So an officer who did not GENERATE the package
    could sign it as preparer and then approve it as approver. The release guard
    compares checkers against the preparer signatures whatever the row says.
    """
    package = _seed(db_session)
    row = _policy_row(db_session)
    row.distinct_signers = False
    db_session.commit()

    # Signed as preparer by somebody other than the generator, so the
    # ``generated_by`` check cannot be what refuses the approval.
    _certify(db_session, APPROVER, package, role="preparer")
    db_session.refresh(package)
    assert package.generated_by == DEMO_USER_ID
    assert package.status == "pending_approval"

    with pytest.raises(AttestationConflict) as raised:
        _certify(db_session, APPROVER, package, role="approver")
    assert raised.value.error_code == "maker_checker"

    db_session.rollback()
    db_session.refresh(package)
    assert package.status == "pending_approval"
    assert _approvals(db_session, package) == []


# --- 3. a distinct authorized approver can ------------------------------------


def test_a_distinct_authorised_approver_releases_the_package(db_session: Session) -> None:
    """Case 3. The control refuses the wrong person without blocking the right one."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    _certify(
        db_session,
        APPROVER,
        package,
        role="approver",
        reason="Cross-checked the HQLA stock against the buffer dashboard.",
    )
    db_session.refresh(package)

    assert package.attestation_state == "fully_certified"
    assert package.status == "approved"
    decisions = _approvals(db_session, package)
    assert [(row.action, row.actor_user_id) for row in decisions] == [
        ("approved", APPROVER_USER_ID)
    ]
    workflow.ensure_submittable(db_session, MAKER, package)  # no raise


# --- 4. an unentitled role cannot approve -------------------------------------


def test_an_analyst_cannot_certify_as_a_checker(db_session: Session) -> None:
    """Case 4. The checker slot needs the approver platform role.

    Driven through ``attestation_api`` rather than the service, because the
    entitlement gate is what the API layer adds and it is the surface a real
    caller reaches.
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(HTTPException) as raised:
        attestation_api.certify(
            db_session,
            ANALYST,
            SAMPLE_BANK_ID,
            package.id,
            CertifyRequest(
                signing_role="approver",
                authorization_token="irrelevant-the-gate-runs-first",
                expected_certification_digest=workflow.compute_binding(
                    package
                ).certification_digest,
            ),
        )
    assert raised.value.status_code == 403
    assert "approver role" in str(raised.value.detail)

    db_session.rollback()
    db_session.refresh(package)
    assert package.status == "pending_approval"


def test_an_analyst_cannot_be_routed_into_a_checker_slot(db_session: Session) -> None:
    """Case 4, early enforcement: an ineligible nominee is refused at nomination."""
    package = _seed(db_session)

    with pytest.raises(AttestationConflict) as raised:
        routing.route(
            db_session,
            MAKER,
            package,
            policy=workflow.package_policy(db_session, MAKER, package),
            nominations=[
                routing.Nomination(signing_role="preparer", user_id=DEMO_USER_ID),
                routing.Nomination(signing_role="approver", user_id=ANALYST_USER_ID),
            ],
            signatures=[],
            reason="nominating the wrong colleague",
        )
    assert raised.value.error_code == "recipient_role_insufficient"
    db_session.rollback()


# --- 5. no submission without the approval ------------------------------------


def test_a_package_cannot_reach_submission_without_its_approval(
    db_session: Session,
) -> None:
    """Case 5. Both gates in front of a channel refuse a package still pending.

    The transition table refuses ``pending_approval -> submitted`` and the
    attestation gate refuses the outstanding approver, so neither the status
    machine nor the signature gate is carrying this alone.
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(HTTPException) as raised:
        reporting_workflow.ensure_transition_allowed(package, "submitted")
    assert raised.value.status_code == 409

    with pytest.raises(AttestationConflict) as conflict:
        workflow.ensure_submittable(db_session, MAKER, package)
    assert conflict.value.error_code == "attestation_incomplete"
    assert "approver" in str(conflict.value.detail)


# --- 6. a missing policy is not an exemption ----------------------------------


def test_a_missing_policy_does_not_disable_the_control(
    db_session: Session,
    storage_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 6. No configured row anywhere: the DEFAULT is the strict one.

    An institution that never opened Settings must not be the institution whose
    returns file unattested. With every policy row deleted, resolution falls to
    ``default_policy`` — preparer AND approver, signature required — so the
    preparer's certification still leaves the approver outstanding and the
    package unreleased.

    This is the only case in this file that reaches object storage, and it needs
    the in-memory client to say so. Every other case relaxes the policy, so no
    signed PDF is produced and no storage client is ever built; here the strict
    default means one is. Without the fixture the test resolves REAL storage —
    which passes on a developer's machine, where ``.env`` configures MinIO, and
    fails in CI, where nothing does. Reaching a live backend from the hermetic
    suite is the defect either way.
    """
    monkeypatch.setattr(
        "app.services.attestation.artifact_signing.get_storage_client",
        lambda: storage_engine,
    )
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client",
        lambda: storage_engine,
    )
    package = _seed(db_session)
    for row in db_session.scalars(select(ReturnSigningPolicy)):
        db_session.delete(row)
    db_session.commit()

    policy = workflow.package_policy(db_session, MAKER, package)
    assert policy.source == "platform_default"
    assert policy.require_signature is True
    assert {slot.role for slot in policy.slots} == {"preparer", "approver"}

    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    assert package.status == "pending_approval"  # NOT approved
    assert _approvals(db_session, package) == []

    with pytest.raises(AttestationConflict) as raised:
        workflow.ensure_submittable(db_session, MAKER, package)
    assert raised.value.error_code == "attestation_incomplete"


# --- 7. configuration cannot switch maker-checker off -------------------------


def test_the_esign_kill_switch_suspends_signing_but_never_maker_checker(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 7. ``ATTESTATION_ESIGN_REQUIRED=0`` is a SIGNATURE control, not a
    separation control.

    Turning it off legitimately suspends the e-signature requirement across the
    deployment — that is what it is for, and it is why the ceremony refuses
    outright rather than becoming optional. What it must never do is leave the
    package reachable by its own preparer: with no ceremony to route the
    decision through, the bare decision is the whole of the checker act, and it
    still refuses the officer who generated the return.
    """
    package = _seed(db_session)
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()

    policy = workflow.package_policy(db_session, MAKER, package)
    assert policy.require_signature is False
    assert policy.source == "esign_disabled"
    # OFF means no ceremony at all — so the certification path cannot be the
    # route to 'approved' while the flag is down.
    with pytest.raises(AttestationConflict) as refused:
        workflow.ensure_certifiable(package, policy, "preparer")
    assert refused.value.error_code == "signature_not_required"

    reporting_workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    # The preparer still cannot approve their own return.
    with pytest.raises(HTTPException) as raised:
        reporting_workflow.decide_approval(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            package.id,
            PackageApprovalDecisionCreate(action="approved"),
        )
    assert raised.value.status_code == 409
    assert "Maker-checker" in str(raised.value.detail)
    db_session.rollback()
    db_session.refresh(package)
    assert package.status == "pending_approval"

    # A second officer can, and that is the only way through.
    decided = reporting_workflow.decide_approval(
        db_session,
        APPROVER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    assert decided.status == "approved"
    approvals = _approvals(db_session, package)
    assert approvals[-1].actor_user_id == APPROVER_USER_ID
    get_settings.cache_clear()


def test_a_relaxed_policy_still_cannot_be_approved_by_its_preparer(
    db_session: Session,
) -> None:
    """Case 7, the other configuration lever. Relaxing SIGNING is an audited,
    per-return decision an administrator is entitled to make; relaxing
    SEPARATION is not on offer, and the relaxed row does not smuggle it in."""
    package = _seed(db_session)
    row = _policy_row(db_session)
    row.require_signature = False
    db_session.commit()

    reporting_workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    with pytest.raises(HTTPException) as raised:
        reporting_workflow.decide_approval(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            package.id,
            PackageApprovalDecisionCreate(action="approved"),
        )
    assert raised.value.status_code == 409
    db_session.rollback()


# --- 8. the audit trail names both officers -----------------------------------


def test_the_audit_trail_records_the_preparer_and_the_checker(
    db_session: Session,
) -> None:
    """Case 8. An examiner asking "who prepared this, who approved it" is
    answerable from the IMMUTABLE trail alone.

    ``regulatory_package_approvals`` is a CASCADE child of the package and is
    not trigger-guarded (migration 202607250027 says so, and why); the audit
    event is. So both identities have to be on the event, not only on the row.
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    _certify(db_session, APPROVER, package, role="approver", reason="Checked.")
    db_session.refresh(package)

    events = _certification_events(db_session, package)
    assert [event.event_type for event in events] == [
        "attestation.certified_preparer",
        "attestation.certified_approver",
    ]
    release = cast("dict[str, Any]", events[-1].details)
    signatories = {
        entry["signing_role"]: entry["signer_user_id"] for entry in release["signatories"]
    }
    assert signatories["preparer"] == str(DEMO_USER_ID)
    assert signatories["approver"] == str(APPROVER_USER_ID)
    # The signer ids are on the event too — the identity that survives a user
    # row being re-provisioned.
    assert all(entry["signer_id"].startswith("SGN-") for entry in release["signatories"])

    # And the decision row agrees with the trail about who the checker was.
    decisions = _approvals(db_session, package)
    assert [(row.action, row.actor_user_id) for row in decisions] == [
        ("approved", APPROVER_USER_ID)
    ]


# --- 9. replay cannot walk around the separation ------------------------------


def test_a_replayed_or_repeated_approval_does_not_bypass_separation(
    db_session: Session,
) -> None:
    """Case 9. Three replays, none of which reach a second approval.

    A control that holds once but not twice is not a control: an operator who
    can re-run the approving act is an operator who can re-run it as somebody
    else, or re-run it after a void has reopened the figures.
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    _certify(db_session, APPROVER, package, role="approver")
    db_session.refresh(package)
    assert package.status == "approved"
    approvals_after_first = _approvals(db_session, package)
    assert len(approvals_after_first) == 1

    # (a) the same approver certifying again
    with pytest.raises(AttestationConflict) as repeated:
        _certify(db_session, APPROVER, package, role="approver")
    assert repeated.value.error_code == "already_fully_certified"
    db_session.rollback()

    # (b) a DIFFERENT approver certifying again
    with pytest.raises(AttestationConflict) as second_checker:
        _certify(db_session, OTHER_APPROVER, package, role="approver")
    assert second_checker.value.error_code == "already_fully_certified"
    db_session.rollback()

    # (c) the preparer replaying the bare decision on the approved package
    with pytest.raises(HTTPException) as bare:
        reporting_workflow.decide_approval(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            package.id,
            PackageApprovalDecisionCreate(action="approved"),
        )
    assert bare.value.status_code == 409
    db_session.rollback()

    db_session.refresh(package)
    assert package.status == "approved"
    assert len(_approvals(db_session, package)) == 1


def test_a_voided_attestation_reopens_the_ceremony_without_reopening_the_rule(
    db_session: Session,
) -> None:
    """Case 9, the reset path. A void must not be a way to re-approve as the
    preparer: the new cycle starts from ``unsigned`` and refuses them again."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    workflow.void_attestation(db_session, APPROVER, package, reason="Figures restated.")
    db_session.refresh(package)
    assert package.attestation_state == "unsigned"
    assert package.status == "generated"

    from app.services.regulatory_reporting import validation  # noqa: PLC0415

    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    db_session.refresh(package)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _certify(db_session, MAKER, package, role="approver")
    assert raised.value.error_code == "maker_checker"
    db_session.rollback()


# --- 10. the evidence stays append-only ---------------------------------------


def test_refused_approvals_leave_the_signature_evidence_untouched(
    db_session: Session,
) -> None:
    """Case 10. The guard reads evidence; it never rewrites it.

    Every refusal above happens after signatures already exist, so the cheapest
    way for the guard to be wrong would be to mutate one on its way out. The
    fingerprints and the per-tenant hash chain are compared across a refusal —
    the same properties the Postgres append-only trigger enforces at the row
    level, asserted at the level the hermetic suite can reach.
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    before = _signature_fingerprints(db_session)
    assert len(before) == 1
    assert workflow.verify_chain(db_session, MAKER) == (True, None)

    with pytest.raises(AttestationConflict):
        _certify(db_session, MAKER, package, role="approver")
    db_session.rollback()

    assert _signature_fingerprints(db_session) == before
    assert _signature_count(db_session) == 1
    assert workflow.verify_chain(db_session, MAKER) == (True, None)

    # The approval decision the refusal did not make also left no row, and the
    # completed act appends rather than replacing anything.
    assert _approvals(db_session, package) == []
    _certify(db_session, APPROVER, package, role="approver")
    db_session.refresh(package)
    after = _signature_fingerprints(db_session)
    assert after[: len(before)] == before  # the preparer's row is byte-identical
    assert len(after) == 2
    assert workflow.verify_chain(db_session, MAKER) == (True, None)
    assert len(_approvals(db_session, package)) == 1


def test_the_void_of_an_approved_package_keeps_the_decision_row(
    db_session: Session,
) -> None:
    """Case 10, continued. Withdrawing an attestation increments the cycle; it
    never deletes the decision or the signatures that justified it."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    _certify(db_session, APPROVER, package, role="approver")
    db_session.refresh(package)
    fingerprints = _signature_fingerprints(db_session)
    cycle = package.attestation_cycle

    workflow.void_attestation(db_session, APPROVER, package, reason="Restated figures.")
    db_session.refresh(package)

    assert package.attestation_cycle == cycle + 1
    assert package.status == "generated"
    assert _signature_fingerprints(db_session) == fingerprints
    assert len(_approvals(db_session, package)) == 1
    # Superseded, not current: the new cycle carries no signatures.
    assert workflow.current_signatures(db_session, MAKER, package) == []


# --- regression: the legitimate workflows still work --------------------------


def test_a_signature_relaxed_return_still_completes_its_bare_approval(
    db_session: Session,
) -> None:
    """Regression. The non-ceremonial workflow is the one every non-attestation
    suite depends on (tests/factories/attestation.py), and it must be untouched:
    an institution that relaxed signing for a return has no ceremony, takes the
    bare decision, and files.
    """
    package = _seed(db_session)
    row = _policy_row(db_session)
    row.require_signature = False
    db_session.commit()

    reporting_workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    decided = reporting_workflow.decide_approval(
        db_session,
        APPROVER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    assert decided.status == "approved"
    db_session.refresh(package)
    workflow.ensure_submittable(db_session, MAKER, package)  # no raise
    reporting_workflow.ensure_transition_allowed(package, "submitted")  # no raise
    # No signature was demanded and none was invented.
    assert _signature_count(db_session) == 0


def test_a_witness_slot_signing_last_still_releases_the_package(
    db_session: Session,
) -> None:
    """Regression. The rule is "a checker signed", NOT "the last signature was a
    checker's".

    A ``[preparer, approver, witness]`` policy is legitimate — a bank that wants
    a third officer present — and nothing constrains the order in which the two
    non-preparer slots are filled. Reading the release as "the signature that
    completed the set must be a checker's" would refuse the witness who happens
    to sign second, which is a control that punishes a correct configuration.
    """
    package = _seed(db_session)
    row = _policy_row(db_session)
    row.required_signatures = [
        {"role": "preparer", "min_count": 1, "officer_titles": []},
        {"role": "approver", "min_count": 1, "officer_titles": []},
        {"role": "witness", "min_count": 1, "officer_titles": []},
    ]
    db_session.commit()

    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    _certify(db_session, APPROVER, package, role="approver")
    db_session.refresh(package)
    assert package.status == "pending_approval"  # the witness is still outstanding

    _certify(db_session, ANALYST, package, role="witness")
    db_session.refresh(package)

    assert package.attestation_state == "fully_certified"
    assert package.status == "approved"
    # The decision is attributed to the CHECKER, never to the witness who
    # happened to sign last.
    decisions = _approvals(db_session, package)
    assert [(row_.action, row_.actor_user_id) for row_ in decisions] == [
        ("approved", APPROVER_USER_ID)
    ]


def test_a_witness_alone_cannot_stand_in_for_a_checker(db_session: Session) -> None:
    """Regression's mirror. A witness is present, not accountable: a
    ``[preparer, witness]`` policy names two officers and still has no checker,
    so it must not release."""
    package = _seed(db_session)
    row = _policy_row(db_session)
    row.required_signatures = [
        {"role": "preparer", "min_count": 1, "officer_titles": []},
        {"role": "witness", "min_count": 1, "officer_titles": []},
    ]
    db_session.commit()

    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _certify(db_session, ANALYST, package, role="witness")
    assert raised.value.error_code == "maker_checker_not_satisfied"
    db_session.rollback()


def test_checker_roles_do_not_drift(db_session: Session) -> None:
    """The release guard and the two role gates must agree on what a checker is.

    ``routing`` and ``attestation_api`` cannot import the canonical set without
    closing an import cycle, so they mirror it. A mirror nobody checks is a
    mirror that drifts — and drift here means a slot the API gates but the
    release does not count, or the reverse.
    """
    _ = db_session
    assert workflow.CHECKER_ROLES == routing.CHECKER_ROLES
    assert workflow.CHECKER_ROLES == attestation_api.CHECKER_ROLES


def test_the_release_guard_is_the_only_certification_route_to_approved(
    db_session: Session,
) -> None:
    """The guard is reached by construction, not by a caller remembering it.

    ``apply_certification`` is the one function that moves a package to
    ``approved`` by certification; this pins that it cannot do so without the
    guard having run, by handing it a fully-satisfying set of signatures that
    contains no checker.
    """
    package = _seed(db_session)
    preparer_signature = _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    assert package.status == "pending_approval"

    preparer_only = SigningPolicy(slots=(SignatureSlot(role="preparer"),), require_signature=True)
    with pytest.raises(AttestationConflict) as raised:
        workflow.apply_certification(
            db_session,
            MAKER,
            package,
            role="preparer",
            binding=workflow.compute_binding(package),
            policy=preparer_only,
            signatures_after=[preparer_signature],
        )
    assert raised.value.error_code == "maker_checker_not_satisfied"
    db_session.rollback()
    db_session.refresh(package)
    assert package.status == "pending_approval"


def test_certification_writes_no_approval_row_when_the_guard_refuses(
    db_session: Session,
) -> None:
    """Belt and braces on case 2: the refusal must precede the state change, or
    the trail would record an approval the platform then rolled back."""
    package = _seed(db_session)
    row = _policy_row(db_session)
    row.distinct_signers = False
    db_session.commit()
    _certify(db_session, APPROVER, package, role="preparer")
    db_session.refresh(package)

    approvals_before = len(_approvals(db_session, package))
    with pytest.raises(AttestationConflict):
        _certify(db_session, APPROVER, package, role="approver")
    db_session.rollback()
    db_session.refresh(package)

    assert len(_approvals(db_session, package)) == approvals_before
    assert package.attestation_state == "preparer_certified"
    assert package.status == "pending_approval"


def test_no_approval_row_is_written_when_the_ceremony_is_refused(
    db_session: Session,
) -> None:
    """The refusal is complete: no decision row survives it anywhere.

    Queried directly rather than through the helper, because "the approval table
    is empty" is the claim, and a helper filtering by package could hide a row
    written against the wrong package id.
    """
    package = _seed(db_session)
    row = _policy_row(db_session)
    row.required_signatures = [{"role": "preparer", "min_count": 1, "officer_titles": []}]
    db_session.commit()

    with pytest.raises(AttestationConflict) as raised:
        _certify(db_session, MAKER, package, role="preparer")
    assert raised.value.error_code == "maker_checker_not_satisfied"
    db_session.rollback()

    assert db_session.scalars(select(RegulatoryPackageApproval)).all() == []
