"""Placement resolution and named recipient routing.

The properties asserted here are the ones that would be DEFECTS if they changed:

* placement resolves override → bank template → organization template → the
  built-in default, and the default still applies to a return nobody has placed
  (nothing breaks for an institution that never opens the editor);
* a placement cannot be moved once the document has been certified, because the
  DocMDP policy would make the stored placement a lie about the filed document;
* a nominee the signing policy cannot accept is refused — including the preparer
  themselves, which is ``ensure_maker_checker``'s rule reached through routing
  rather than a second implementation of it;
* certify-and-send is one transaction: a rejected nominee takes the signature with
  it, so no certified return is ever left routed to nobody;
* the approver's review is ONE act with two exits — the signature and the approval
  decision commit together (neither can exist without the other, in either
  direction), and sending the return back records the note AND withdraws the
  certification that froze the figures.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import (
    AttestationSignature,
    BankReportingPeriod,
    Notification,
    PackageSignatureRecipient,
    RegulatoryPackage,
    RegulatoryPackageApproval,
    ReturnSigningPolicy,
    User,
)
from app.schemas.attestation import SendBackForCorrectionsRequest
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
)
from app.services import attestation_api, regulatory_liquidity
from app.services.attestation import (
    pdf_signing,
    placements,
    routing,
    signing,
    stepup,
    workflow,
)
from app.services.attestation.identity import ensure_signer_identity
from app.services.attestation.keys import SignerKeyService
from app.services.attestation.signers import get_raw_signer
from app.services.attestation.workflow import AttestationConflict
from app.services.regulatory_reporting import generation, validation
from app.services.regulatory_reporting import workflow as reporting_workflow
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from tests.storage.inmemory import InMemoryStorageClient

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
APPROVER_USER_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
ANALYST_USER_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
OTHER_APPROVER_USER_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
APPROVER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=APPROVER_USER_ID)
REPORTING_DATE = date(2026, 3, 31)
VAULT_KEY = "test-vault-master-key-not-for-production-0004"

#: Big enough to pass every authoring check; on the figures page, which the old
#: attestation-page guard would have refused outright.
FIGURES_PAGE = 2
PLACED = (
    pdf_signing.FieldPlacement("preparer", FIGURES_PAGE, (60, 260, 300, 345)),
    pdf_signing.FieldPlacement("approver", FIGURES_PAGE, (310, 260, 550, 345)),
)


@pytest.fixture(autouse=True)
def signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-pepper-not-for-production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNING_BACKEND", "software")
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", VAULT_KEY)
    monkeypatch.setenv("SIGNING_SOFTWARE_KEY_DIR", str(tmp_path / "signing_keys"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client", lambda: client
    )
    monkeypatch.setattr(
        "app.services.attestation.artifact_signing.get_storage_client", lambda: client
    )
    return client


def _users(db: Session) -> None:
    wanted = [
        (APPROVER_USER_ID, "routing.approver@example.test", "Ama Mensah", "approver"),
        (ANALYST_USER_ID, "routing.analyst@example.test", "Kojo Asare", "analyst"),
        (OTHER_APPROVER_USER_ID, "routing.other@example.test", "Yaa Boateng", "approver"),
    ]
    for user_id, email, name, role in wanted:
        # Scoped to the org: the sample seed plants a second tenant with its own
        # users, and an unscoped existence check would silently skip a nominee
        # this tenant needs.
        existing = db.scalar(
            select(User.id).where(
                User.id == user_id, User.organization_id == DEMO_ORG_ID
            )
        )
        if existing is None:
            db.add(
                User(
                    id=user_id,
                    organization_id=DEMO_ORG_ID,
                    email=email,
                    display_name=name,
                    job_title="Chief Financial Officer",
                    role=role,
                )
            )
    # The preparer holds approver rights too, which is common in a small treasury
    # team. Load-bearing for the self-nomination test: with the seeded 'viewer'
    # role that refusal would come from the role gate, and would pass while
    # proving nothing about maker-checker.
    preparer = db.scalar(select(User).where(User.id == DEMO_USER_ID))
    assert preparer is not None
    preparer.role = "approver"
    preparer.job_title = "Chief Financial Officer"
    db.commit()


def _seed(
    db: Session, *, require_signed_pdf: bool = False, officer_titles: Any = None
) -> RegulatoryPackage:
    seed_sample_bank(db)
    _users(db)
    db.add(
        ReturnSigningPolicy(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            return_code="BSD3",
            required_signatures=[
                {"role": "preparer", "min_count": 1, "officer_titles": []},
                {
                    "role": "approver",
                    "min_count": 1,
                    "officer_titles": list(officer_titles or []),
                },
            ],
            required_attachments=[],
            require_signature=True,
            require_signed_pdf=require_signed_pdf,
            distinct_signers=True,
            effective_from=date(2026, 1, 1),
            reason="Test policy for routing and placement.",
        )
    )
    db.commit()
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    run = regulatory_liquidity.create_liquidity_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"
    read = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="BSD3", reporting_date=REPORTING_DATE),
    )
    validation.validate_package(db, MAKER, SAMPLE_BANK_ID, read.id)
    package = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert package is not None
    return package


def _certify(  # noqa: PLR0913 - mirrors signing.certify's own irreducible inputs
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    role: str,
    commit: bool = True,
    reason: str | None = None,
) -> AttestationSignature:
    assert ctx.actor_user_id is not None
    identity = ensure_signer_identity(db, ctx, ctx.actor_user_id)
    service = SignerKeyService(db, ctx)
    if service.active_key(identity.signer_id) is None:
        service.issue(
            signer_id=identity.signer_id,
            display_name="Test Signer",
            organization_name="Sample Bank",
        )
    db.commit()
    token, _row = stepup.mint_authorization(
        db,
        ctx,
        user_id=ctx.actor_user_id,
        signer_id=identity.signer_id,
        package_id=package.id,
        signing_role=role,
        certification_digest=workflow.compute_binding(package).certification_digest,
        auth_evidence={"method": "password_reauth"},
    )
    return signing.certify(
        db,
        ctx,
        package,
        role=role,
        authorization_token=token,
        backends=signing.SigningBackends(raw_signer=get_raw_signer()),
        commit=commit,
        reason=reason,
    )


def _nominate(role: str, user_id: UUID) -> routing.Nomination:
    return routing.Nomination(signing_role=role, user_id=user_id)


def _signature_count(db: Session) -> int:
    return len(list(db.scalars(select(AttestationSignature.id))))


# --- placement resolution ---------------------------------------------------


def test_a_return_nobody_has_placed_falls_back_to_the_built_in_default(
    db_session: Session,
) -> None:
    """Nothing breaks for an institution that never opens the placement editor."""
    package = _seed(db_session)
    resolved = placements.resolve(db_session, MAKER, package)
    assert resolved.source == "default"
    assert resolved.placements == pdf_signing.DEFAULT_PLACEMENTS


def test_resolution_is_override_then_bank_then_organization_then_default(
    db_session: Session,
) -> None:
    """Each source must beat the next, and only the winning source contributes.

    Merging across sources would put an approver box from a template beside a
    preparer box a colleague dragged elsewhere — an overlap nobody placed.
    """
    package = _seed(db_session)
    org_boxes = (
        pdf_signing.FieldPlacement("preparer", 1, (51, 400, 291, 480)),
        pdf_signing.FieldPlacement("approver", 1, (304, 400, 544, 480)),
    )
    placements.upsert_template(
        db_session,
        MAKER,
        return_code="BSD3",
        bank_id=None,
        placements=org_boxes,
        reason="organization default",
    )
    db_session.commit()
    assert placements.resolve(db_session, MAKER, package).source == "organization_template"
    # Compared as sets: resolution orders rows by role for determinism, not by the
    # order they were authored in.
    assert set(placements.resolve(db_session, MAKER, package).placements) == set(org_boxes)

    bank_boxes = (
        pdf_signing.FieldPlacement("preparer", 1, (51, 200, 291, 280)),
        pdf_signing.FieldPlacement("approver", 1, (304, 200, 544, 280)),
    )
    placements.upsert_template(
        db_session,
        MAKER,
        return_code="BSD3",
        bank_id=SAMPLE_BANK_ID,
        placements=bank_boxes,
        reason="this bank differs",
    )
    db_session.commit()
    assert placements.resolve(db_session, MAKER, package).source == "bank_template"
    assert set(placements.resolve(db_session, MAKER, package).placements) == set(bank_boxes)

    placements.set_package_override(
        db_session, MAKER, package, placements=PLACED, reason="preparer dragged them"
    )
    db_session.commit()
    resolved = placements.resolve(db_session, MAKER, package)
    assert resolved.source == "package"
    assert set(resolved.placements) == set(PLACED)


def test_a_template_write_replaces_the_whole_scope(db_session: Session) -> None:
    package = _seed(db_session)
    placements.upsert_template(
        db_session,
        MAKER,
        return_code="BSD3",
        bank_id=SAMPLE_BANK_ID,
        placements=PLACED,
        reason="first",
    )
    replacement = (
        pdf_signing.FieldPlacement("preparer", 1, (51, 400, 291, 480)),
        pdf_signing.FieldPlacement("approver", 1, (304, 400, 544, 480)),
    )
    placements.upsert_template(
        db_session,
        MAKER,
        return_code="BSD3",
        bank_id=SAMPLE_BANK_ID,
        placements=replacement,
        reason="second",
    )
    db_session.commit()
    assert set(placements.resolve(db_session, MAKER, package).placements) == set(replacement)
    assert len(placements.list_templates(db_session, MAKER, return_code="BSD3")) == 2


@pytest.mark.parametrize(
    ("boxes", "code"),
    [
        ((PLACED[0],), "placement_incomplete"),
        (
            (
                pdf_signing.FieldPlacement(
                    "preparer",
                    FIGURES_PAGE,
                    (60, 260, 60 + pdf_signing.MIN_BOX_SIZES["signature"][0] - 1, 345),
                ),
                PLACED[1],
            ),
            "placement_too_small",
        ),
        # A date box measured against the SIGNATURE floor would pass; it is
        # refused on its own, which is the whole point of per-kind minimums.
        (
            (
                *PLACED,
                pdf_signing.FieldPlacement(
                    "preparer",
                    FIGURES_PAGE,
                    (60, 200, 60 + pdf_signing.MIN_BOX_SIZES["date_signed"][0] - 1, 216),
                    "date_signed",
                ),
            ),
            "placement_too_small",
        ),
        ((PLACED[0], PLACED[0]), "placement_role_duplicated"),
        (
            (*PLACED, pdf_signing.FieldPlacement("board", FIGURES_PAGE, (60, 100, 300, 185))),
            "placement_role_unsupported",
        ),
        (
            (
                *PLACED,
                pdf_signing.FieldPlacement(
                    "preparer", FIGURES_PAGE, (60, 100, 300, 185), "stamp"
                ),
            ),
            "placement_field_type_unsupported",
        ),
        (
            (pdf_signing.FieldPlacement("preparer", -1, (60, 260, 300, 345)), PLACED[1]),
            "placement_out_of_bounds",
        ),
    ],
    ids=[
        "incomplete",
        "signature_too_small",
        "date_too_small",
        "duplicated",
        "unsupported_role",
        "unsupported_field_type",
        "negative_page",
    ],
)
def test_the_authoring_endpoint_refuses_an_unusable_placement_set(
    db_session: Session, boxes: tuple[pdf_signing.FieldPlacement, ...], code: str
) -> None:
    """Told at save time, not hours later on a filing deadline.

    ``pdf_signing`` enforces the same rules against the real PDF, but a preparer
    who saves a 40-point-tall box must learn that immediately rather than have a
    colleague's certification fail with it.
    """
    package = _seed(db_session)
    with pytest.raises(AttestationConflict) as raised:
        placements.set_package_override(
            db_session, MAKER, package, placements=boxes, reason="bad set"
        )
    assert raised.value.error_code == code


def test_a_placement_cannot_be_moved_after_certification(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The DocMDP consequence, at the service boundary.

    The fields are part of the certified revision. Re-placing them afterwards
    could only produce a stored placement that does not describe the filed
    document, or a field the certification forbids — so it is refused, and a void
    is the way back.
    """
    package = _seed(db_session, require_signed_pdf=True)
    placements.set_package_override(
        db_session, MAKER, package, placements=PLACED, reason="placed before signing"
    )
    db_session.commit()
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        placements.set_package_override(
            db_session,
            MAKER,
            package,
            placements=pdf_signing.DEFAULT_PLACEMENTS,
            reason="too late",
        )
    assert raised.value.error_code == "placement_locked"


def test_clearing_an_override_returns_the_package_to_its_template(
    db_session: Session,
) -> None:
    package = _seed(db_session)
    placements.set_package_override(
        db_session, MAKER, package, placements=PLACED, reason="placed"
    )
    db_session.commit()
    assert placements.resolve(db_session, MAKER, package).source == "package"

    placements.set_package_override(db_session, MAKER, package, placements=[], reason="cleared")
    db_session.commit()
    assert placements.resolve(db_session, MAKER, package).source == "default"


# --- named recipient routing -------------------------------------------------


def _route(
    db: Session, package: RegulatoryPackage, *nominations: routing.Nomination
) -> list[PackageSignatureRecipient]:
    return routing.route(
        db,
        MAKER,
        package,
        policy=workflow.package_policy(db, MAKER, package),
        nominations=nominations,
        signatures=workflow.current_signatures(db, MAKER, package),
        reason="routing test",
    )


def test_routing_records_the_nominee_and_notifies_them(db_session: Session) -> None:
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    rows = _route(db_session, package, _nominate("approver", APPROVER_USER_ID))
    db_session.commit()

    assert len(rows) == 1
    assert rows[0].recipient_user_id == APPROVER_USER_ID
    assert rows[0].recipient_display_name == "Ama Mensah"
    assert rows[0].recipient_signer_id.startswith("SGN-")
    assert rows[0].status == "pending"
    assert rows[0].notified_at is not None
    addressed = db_session.scalars(
        select(Notification).where(
            Notification.type == "attestation.signature_requested",
            Notification.recipient_user_id == APPROVER_USER_ID,
        )
    ).all()
    assert len(addressed) == 1
    assert "awaits your signature" in addressed[0].title


def test_the_preparer_cannot_nominate_themselves(db_session: Session) -> None:
    """Maker-checker, reached through routing rather than reimplemented.

    ``ensure_maker_checker`` compares the candidate against the signatures that
    already exist, so nominating the preparer for the approver slot is rejected by
    the very same code that would reject them at signing time.
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _route(db_session, package, _nominate("approver", DEMO_USER_ID))
    assert raised.value.error_code == "maker_checker"


def test_a_nominee_without_the_approver_role_is_refused(db_session: Session) -> None:
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _route(db_session, package, _nominate("approver", ANALYST_USER_ID))
    assert raised.value.error_code == "recipient_role_insufficient"


def test_a_nominee_whose_title_the_policy_names_is_refused(db_session: Session) -> None:
    """The officer-title rule bites at nomination, not only at signing."""
    package = _seed(db_session, officer_titles=["Chief Risk Officer"])
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _route(db_session, package, _nominate("approver", APPROVER_USER_ID))
    assert raised.value.error_code == "officer_title_mismatch"


def test_a_role_the_policy_does_not_require_cannot_be_routed(db_session: Session) -> None:
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _route(db_session, package, _nominate("board", APPROVER_USER_ID))
    assert raised.value.error_code == "recipients_do_not_match_policy"


def test_routing_must_cover_every_outstanding_slot_and_no_others(
    db_session: Session,
) -> None:
    """A partially routed return would sit in nobody's queue."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _route(db_session, package)
    assert raised.value.error_code == "recipients_do_not_match_policy"

    with pytest.raises(AttestationConflict) as second:
        _route(
            db_session,
            package,
            _nominate("approver", APPROVER_USER_ID),
            _nominate("approver", OTHER_APPROVER_USER_ID),
        )
    assert second.value.error_code == "recipients_do_not_match_policy"


def test_a_deactivated_nominee_is_refused(db_session: Session) -> None:
    package = _seed(db_session)
    user = db_session.scalar(select(User).where(User.id == APPROVER_USER_ID))
    assert user is not None
    user.is_active = False
    db_session.commit()
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _route(db_session, package, _nominate("approver", APPROVER_USER_ID))
    assert raised.value.error_code == "recipient_inactive"


def test_only_the_named_signer_may_fill_a_routed_slot(db_session: Session) -> None:
    """A nomination the audit trail cannot rely on would be decoration.

    The gate exists only where somebody created it: an unrouted return is
    unaffected (every other attestation test proves that by passing).
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    _route(db_session, package, _nominate("approver", APPROVER_USER_ID))
    db_session.commit()

    other = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=OTHER_APPROVER_USER_ID)
    with pytest.raises(AttestationConflict) as raised:
        _certify(db_session, other, package, role="approver")
    assert raised.value.error_code == "not_the_named_signer"
    db_session.rollback()

    # …and the named signer can, which closes their recipient row.
    _certify(db_session, APPROVER, package, role="approver")
    db_session.refresh(package)
    assert package.attestation_state == "fully_certified"
    closed = routing.current_recipients(db_session, MAKER, package)
    assert [row.status for row in closed] == ["signed"]
    assert closed[0].signed_at is not None
    assert closed[0].signature_id is not None


def test_rerouting_frees_a_return_whose_nominee_is_unavailable(
    db_session: Session,
) -> None:
    """Without this, an absent approver could only be escaped by voiding."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    _route(db_session, package, _nominate("approver", APPROVER_USER_ID))
    db_session.commit()

    routing.reroute(
        db_session,
        MAKER,
        package,
        policy=workflow.package_policy(db_session, MAKER, package),
        nominations=[_nominate("approver", OTHER_APPROVER_USER_ID)],
        signatures=workflow.current_signatures(db_session, MAKER, package),
        reason="the CFO is on leave",
    )
    db_session.commit()

    rerouted = routing.current_recipients(db_session, MAKER, package)
    assert [row.recipient_user_id for row in rerouted] == [OTHER_APPROVER_USER_ID]
    other = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=OTHER_APPROVER_USER_ID)
    _certify(db_session, other, package, role="approver")
    db_session.refresh(package)
    assert package.attestation_state == "fully_certified"


def test_a_void_leaves_the_old_routing_out_of_the_new_queue(db_session: Session) -> None:
    """A withdrawn cycle's nomination is history, not an outstanding request."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    _route(db_session, package, _nominate("approver", APPROVER_USER_ID))
    db_session.commit()
    assert routing.awaiting_signature(db_session, MAKER, APPROVER_USER_ID)

    workflow.void_attestation(db_session, MAKER, package, reason="figures restated")
    db_session.refresh(package)

    assert routing.current_recipients(db_session, MAKER, package) == []
    assert routing.awaiting_signature(db_session, MAKER, APPROVER_USER_ID) == []
    # The withdrawn cycle's row survives: it is a record of what was asked of whom.
    assert db_session.scalars(select(PackageSignatureRecipient.id)).all()


def test_awaiting_signature_lists_only_this_users_pending_requests(
    db_session: Session,
) -> None:
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    _route(db_session, package, _nominate("approver", APPROVER_USER_ID))
    db_session.commit()

    queued = routing.awaiting_signature(db_session, MAKER, APPROVER_USER_ID)
    assert [package.return_code for _recipient, package in queued] == ["BSD3"]
    assert routing.awaiting_signature(db_session, MAKER, OTHER_APPROVER_USER_ID) == []

    _certify(db_session, APPROVER, package, role="approver")
    assert routing.awaiting_signature(db_session, MAKER, APPROVER_USER_ID) == []


def test_a_rejected_nominee_takes_the_certification_with_it(db_session: Session) -> None:
    """Certify-and-send is one transaction, asserted at the service level.

    ``signing.certify(commit=False)`` is what makes this true. Committing the
    signature first and routing afterwards would leave a certified return that
    nobody had been asked to sign — the exact state this feature exists to remove.
    """
    package = _seed(db_session)
    signature = _certify(db_session, MAKER, package, role="preparer", commit=False)
    assert signature.id is not None

    with pytest.raises(AttestationConflict) as raised:
        routing.route(
            db_session,
            MAKER,
            package,
            policy=workflow.package_policy(db_session, MAKER, package),
            nominations=[_nominate("approver", ANALYST_USER_ID)],
            signatures=workflow.current_signatures(db_session, MAKER, package),
            reason="one act",
        )
    assert raised.value.error_code == "recipient_role_insufficient"

    # The API dependency rolls the request's session back; do the same and prove
    # neither half of the act survived.
    db_session.rollback()
    assert _signature_count(db_session) == 0
    db_session.refresh(package)
    assert package.attestation_state == "unsigned"
    assert package.status == "validated"
    assert db_session.scalars(select(PackageSignatureRecipient.id)).all() == []


# --- the approver's one act: review, sign, approve ---------------------------
#
# The defect these close: the approver used to sign in the ceremony and record an
# approval decision on a separate queue screen, so a return could be
# signed-but-not-approved or approved-but-not-signed. Both halves now commit
# together, and the bare decision is refused while a signature is owed.


def _approvals(db: Session, package: RegulatoryPackage) -> list[RegulatoryPackageApproval]:
    return list(
        db.scalars(
            select(RegulatoryPackageApproval)
            .where(RegulatoryPackageApproval.package_id == package.id)
            .order_by(RegulatoryPackageApproval.occurred_at, RegulatoryPackageApproval.id)
        )
    )


def test_the_approver_signature_and_the_approval_decision_are_one_act(
    db_session: Session,
) -> None:
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    assert package.status == "pending_approval"
    assert [row.action for row in _approvals(db_session, package)] == []

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
    assert [row.action for row in decisions] == ["approved"]
    assert decisions[0].actor_user_id == APPROVER_USER_ID
    # The approver's own words, not a platform paraphrase.
    assert decisions[0].reason == "Cross-checked the HQLA stock against the buffer dashboard."
    # And the maker is told, by the same code path a bare decision used.
    told = db_session.scalars(
        select(Notification).where(
            Notification.type == "reporting.package.approved",
            Notification.recipient_user_id == DEMO_USER_ID,
        )
    ).all()
    assert len(told) == 1


def test_a_failure_after_the_signature_takes_the_approval_decision_with_it(
    db_session: Session,
) -> None:
    """One transaction: neither half of the act can survive alone."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    _certify(db_session, APPROVER, package, role="approver", commit=False)
    # Nothing is outstanding after that signature, so nominating anybody is a
    # routing error — raised AFTER the signature and the decision were written.
    with pytest.raises(AttestationConflict) as raised:
        routing.route(
            db_session,
            APPROVER,
            package,
            policy=workflow.package_policy(db_session, APPROVER, package),
            nominations=[_nominate("approver", OTHER_APPROVER_USER_ID)],
            signatures=workflow.current_signatures(db_session, APPROVER, package),
            reason="one act",
        )
    assert raised.value.error_code == "recipients_do_not_match_policy"

    # The API dependency rolls the request's session back; do the same.
    db_session.rollback()
    db_session.refresh(package)
    assert _signature_count(db_session) == 1  # the preparer's, committed earlier
    assert _approvals(db_session, package) == []
    assert package.attestation_state == "preparer_certified"
    assert package.status == "pending_approval"


def test_the_preparer_cannot_approve_their_own_return(db_session: Session) -> None:
    """Maker-checker still refuses the preparer, and writes no decision either."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(AttestationConflict) as raised:
        _certify(db_session, MAKER, package, role="approver")
    assert raised.value.error_code == "maker_checker"
    db_session.rollback()
    db_session.refresh(package)
    assert _approvals(db_session, package) == []
    assert package.status == "pending_approval"


def test_a_bare_approval_is_refused_while_a_signature_is_outstanding(
    db_session: Session,
) -> None:
    """The other direction: approved-but-not-signed is unreachable too."""
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(HTTPException) as raised:
        reporting_workflow.decide_approval(
            db_session,
            APPROVER,
            SAMPLE_BANK_ID,
            package.id,
            PackageApprovalDecisionCreate(action="approved"),
        )
    assert raised.value.status_code == 409
    assert cast("dict[str, Any]", raised.value.detail)["error_code"] == (
        "approval_requires_signature"
    )
    db_session.rollback()
    db_session.refresh(package)
    assert package.status == "pending_approval"
    assert _approvals(db_session, package) == []


def test_a_bare_approval_still_works_where_signing_is_relaxed(db_session: Session) -> None:
    """A bank that opted out of signing has no ceremony to route the decision to."""
    package = _seed(db_session)
    # Edited in the same scope an administrator would edit in Settings, so the
    # policy IN FORCE for this package is the relaxed one.
    in_force = db_session.scalar(
        select(ReturnSigningPolicy).where(
            ReturnSigningPolicy.organization_id == DEMO_ORG_ID,
            ReturnSigningPolicy.bank_id == SAMPLE_BANK_ID,
            ReturnSigningPolicy.return_code == "BSD3",
        )
    )
    assert in_force is not None
    in_force.require_signature = False
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


def test_send_back_returns_the_package_for_rework_and_unfreezes_the_figures(
    db_session: Session,
) -> None:
    """The reviewer's other exit, with the note, in one transaction.

    Recording the rejection without withdrawing the certification would leave the
    figures frozen — a package returned for corrections that nobody can correct.
    """
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    cycle = package.attestation_cycle

    status_read = attestation_api.send_back_for_corrections(
        db_session,
        APPROVER,
        SAMPLE_BANK_ID,
        package.id,
        SendBackForCorrectionsRequest(reason="Line 12 double-counts the placement maturing 2 Apr."),
    )
    db_session.refresh(package)

    assert package.status == "generated"
    assert package.attestation_state == "unsigned"
    assert package.attestation_cycle == cycle + 1
    assert package.certification_digest is None
    assert package.void_reason == "Line 12 double-counts the placement maturing 2 Apr."
    decisions = _approvals(db_session, package)
    assert [(row.action, row.actor_user_id) for row in decisions] == [
        ("rejected", APPROVER_USER_ID)
    ]
    assert decisions[0].reason == "Line 12 double-counts the placement maturing 2 Apr."
    # The preparer's signature is history, never deleted — and no longer current.
    assert _signature_count(db_session) == 1
    assert status_read.signatures == []
    assert not status_read.can_submit
    # The preparer is told, with the note.
    told = db_session.scalars(
        select(Notification).where(
            Notification.type == "reporting.package.approval_rejected",
            Notification.recipient_user_id == DEMO_USER_ID,
        )
    ).all()
    assert len(told) == 1
    assert "double-counts" in told[0].body

    # Rework really is open: the same package validates and re-certifies.
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    db_session.refresh(package)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)
    assert package.attestation_state == "preparer_certified"


@pytest.mark.parametrize("blank", ["", "   "])
def test_send_back_requires_a_note(db_session: Session, blank: str) -> None:
    """The note IS the instruction; a blank one leaves the preparer guessing."""
    _ = db_session
    with pytest.raises(ValidationError):
        SendBackForCorrectionsRequest(reason=blank)


def test_send_back_refuses_the_officer_who_prepared_the_return(
    db_session: Session,
) -> None:
    package = _seed(db_session)
    _certify(db_session, MAKER, package, role="preparer")
    db_session.refresh(package)

    with pytest.raises(HTTPException) as raised:
        attestation_api.send_back_for_corrections(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            package.id,
            SendBackForCorrectionsRequest(reason="I would like another look at this."),
        )
    assert raised.value.status_code == 409
    db_session.rollback()
    db_session.refresh(package)
    assert package.attestation_state == "preparer_certified"
    assert package.status == "pending_approval"
