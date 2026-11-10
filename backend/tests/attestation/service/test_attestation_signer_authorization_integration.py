"""Scoped Signer authority on the regulatory certification ceremony.

Signing needs three independent things: source-read authority, an exact-bank
REG/restricted Signer binding, and a named recipient in the current routing.
Checker slots additionally keep their separate APPROVE duty. These tests drive
the API-facing service and the real certification service, rather than only the
policy kernel, and prove denied ceremonies mint or mutate nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.deps import TenantContext
from app.attestation.schemas import (
    CertifyAndSendRequest,
    SignatureFieldPlacement,
    SignatureRecipientNomination,
    StepUpRequest,
)
from app.attestation.service import api as attestation_api
from app.attestation.service import routing, signing, stepup, workflow
from app.attestation.service.identity import ensure_signer_identity
from app.attestation.service.keys import SignerKeyService
from app.attestation.service.signers import get_raw_signer
from app.attestation.service.workflow import AttestationConflict
from app.core import security
from app.core.authorization import (
    BindingGrant,
    BindingStatus,
    DataScope,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.identity.service import authorization
from app.models import (
    AttestationSignature,
    AuditEvent,
    AuthorizationBinding,
    Bank,
    PackageSignatureRecipient,
    SignerIdentity,
    SigningAuthorization,
)
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from tests.attestation.service.test_attestation_workspace_integration import (
    APPROVER,
    APPROVER_USER_ID,
    MAKER,
    OTHER_APPROVER_USER_ID,
    PLACED,
    _certify,
    _name_certifier,
    _seed,
    signing_env,
)
from tests.fixtures.canonical_bank_fixture import DEMO_ORG_ID, DEMO_USER_ID, SAMPLE_BANK_ID
from tests.support.authorization_fixtures import tenant_user

__all__ = ["signing_env"]

OTHER_BANK_ID = "BK-SIGN0001"


def _seed_without_signer(db: Session) -> Any:
    package = _seed(db)
    for binding in db.scalars(
        select(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == DEMO_ORG_ID,
            AuthorizationBinding.role_bundle == RoleBundle.SIGNER.value,
        )
    ):
        binding.status = BindingStatus.SUSPENDED.value
    db.commit()
    return package


def _signer_binding(
    db: Session,
    user_id: UUID,
    *,
    bank_id: str | None = SAMPLE_BANK_ID,
    module: ModuleScope = ModuleScope.REGULATORY,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
) -> AuthorizationBinding:
    scope = authorization.BindingScope(
        InstitutionScope.INSTITUTION if bank_id is not None else InstitutionScope.ORGANIZATION,
        bank_id,
        module,
        sensitivity,
    )
    binding = authorization.create_role_binding(
        db,
        organization_id=DEMO_ORG_ID,
        principal_user_id=user_id,
        principal_type=authorization.PrincipalType.HUMAN,
        role_bundle=RoleBundle.SIGNER,
        scope=scope,
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "signer-gate-test"),
        reason="Authorize this exact signing ceremony",
        commit=False,
    )
    user = tenant_user(db, organization_id=DEMO_ORG_ID, user_id=user_id)
    user.authorization_version = 1
    db.flush()
    return binding


def _mint_existing_signer_token(
    db: Session,
    package: Any,
    role: str = "preparer",
    ctx: TenantContext = MAKER,
) -> str:
    assert ctx.actor_user_id is not None
    identity = ensure_signer_identity(db, ctx, ctx.actor_user_id)
    key_service = SignerKeyService(db, ctx)
    if key_service.active_key(identity.signer_id) is None:
        key_service.issue(
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
    return token


def _route_two_signers(db: Session, package: Any) -> None:
    routing.route(
        db,
        MAKER,
        package,
        policy=workflow.package_policy(db, MAKER, package),
        nominations=[
            routing.Nomination(signing_role="preparer", user_id=DEMO_USER_ID),
            routing.Nomination(signing_role="approver", user_id=APPROVER_USER_ID),
        ],
        signatures=workflow.current_signatures(db, MAKER, package),
        reason="Name both officers before the first certification",
    )
    db.commit()


def _preview(db: Session, ctx: TenantContext, package: Any, role: str = "preparer") -> Any:
    return attestation_api.preview(db, ctx, SAMPLE_BANK_ID, package.id, role)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 5555),
            "headers": [(b"user-agent", b"pytest")],
        }
    )


def _mutation_counts(db: Session) -> tuple[int, int, int, int, int]:
    def count(model: type[Any]) -> int:
        return db.scalar(select(func.count()).select_from(model)) or 0

    success_audits = db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type.like("attestation.%"))
    )
    return (
        count(SigningAuthorization),
        count(SignerIdentity),
        count(AttestationSignature),
        count(PackageSignatureRecipient),
        success_audits or 0,
    )


def test_preview_denies_an_ordinary_preparer_without_signer(db_session: Session) -> None:
    package = _seed_without_signer(db_session)

    with pytest.raises(HTTPException) as refused:
        _preview(db_session, MAKER, package)

    assert refused.value.status_code == 403


def test_preview_denies_a_signer_until_a_named_route_exists(db_session: Session) -> None:
    package = _seed_without_signer(db_session)
    _signer_binding(db_session, DEMO_USER_ID)
    db_session.commit()

    with pytest.raises(AttestationConflict) as refused:
        _preview(db_session, MAKER, package)

    assert refused.value.error_code == "signer_not_routed"


def test_preview_allows_the_routed_signer_who_can_read_the_source(
    db_session: Session,
) -> None:
    package = _seed_without_signer(db_session)
    _signer_binding(db_session, DEMO_USER_ID)
    _signer_binding(db_session, APPROVER_USER_ID)
    _route_two_signers(db_session, package)

    preview = _preview(db_session, MAKER, package)

    assert preview.package_id == package.id
    assert preview.certification_digest == workflow.compute_binding(package).certification_digest


def test_structural_signer_authority_is_not_execution_authority(db_session: Session) -> None:
    _seed_without_signer(db_session)
    _signer_binding(db_session, DEMO_USER_ID)
    db_session.commit()
    decision = authorization.evaluate_permission(
        db_session,
        authorization.principal_locator(MAKER),
        Permission.SIGN_OFF,
        ResourceLocator(
            DEMO_ORG_ID,
            InstitutionScope.INSTITUTION,
            SAMPLE_BANK_ID,
            Module.REGULATORY,
            Sensitivity.RESTRICTED,
        ),
    )

    assert decision.allowed is False
    assert len(decision.condition_trace) >= 2
    assert any(not condition.passed for condition in decision.condition_trace)


def test_preview_denies_a_signer_who_is_not_the_named_recipient(db_session: Session) -> None:
    package = _seed_without_signer(db_session)
    for user_id in (DEMO_USER_ID, APPROVER_USER_ID, OTHER_APPROVER_USER_ID):
        _signer_binding(db_session, user_id)
    routing.route(
        db_session,
        MAKER,
        package,
        policy=workflow.package_policy(db_session, MAKER, package),
        nominations=[
            routing.Nomination(signing_role="preparer", user_id=APPROVER_USER_ID),
            routing.Nomination(signing_role="approver", user_id=OTHER_APPROVER_USER_ID),
        ],
        signatures=[],
        reason="Route the preparer slot to another officer",
    )
    db_session.commit()

    with pytest.raises(AttestationConflict) as refused:
        _preview(db_session, MAKER, package)

    assert refused.value.error_code == "not_the_named_signer"


@pytest.mark.parametrize(
    ("dimension", "value"),
    [
        ("bank", "other"),
        ("bank", "organization"),
        ("module", ModuleScope.LIQUIDITY),
        ("sensitivity", SensitivityScope.CONFIDENTIAL),
        ("status", BindingStatus.SUSPENDED),
        ("status", BindingStatus.REVOKED),
        ("validity", "future"),
        ("validity", "expired"),
    ],
)
def test_signer_scope_and_lifecycle_near_misses_never_authorize_preview(
    db_session: Session, dimension: str, value: Any
) -> None:
    package = _seed_without_signer(db_session)
    if dimension == "bank" and value == "other":
        db_session.add(
            Bank(
                id=OTHER_BANK_ID,
                organization_id=DEMO_ORG_ID,
                name="Sibling institution",
                short_name="Sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        db_session.flush()
    binding = _signer_binding(
        db_session,
        DEMO_USER_ID,
        bank_id=OTHER_BANK_ID
        if dimension == "bank" and value == "other"
        else (None if dimension == "bank" and value == "organization" else SAMPLE_BANK_ID),
        module=value if isinstance(value, ModuleScope) else ModuleScope.REGULATORY,
        sensitivity=(value if isinstance(value, SensitivityScope) else SensitivityScope.RESTRICTED),
    )
    if dimension == "status":
        assert isinstance(value, BindingStatus)
        binding.status = value.value
        if value is BindingStatus.REVOKED:
            binding.revoked_at = datetime.now(UTC)
            binding.revoked_by_type = authorization.GrantorType.SYSTEM.value
            binding.revoked_by_id = "signer-gate-test"
            binding.revoked_reason = "Revoke the signer before preview"
    if dimension == "validity":
        now = datetime.now(UTC)
        binding.valid_from = (
            now + timedelta(hours=1) if value == "future" else now - timedelta(days=2)
        )
        binding.valid_until = (
            now - timedelta(hours=1) if value == "expired" else binding.valid_until
        )
    db_session.commit()

    with pytest.raises(HTTPException) as refused:
        _preview(db_session, MAKER, package)

    assert refused.value.status_code == 403


def test_organization_wide_signer_cannot_be_named_for_an_exact_bank_signature(
    db_session: Session,
) -> None:
    package = _seed_without_signer(db_session)
    _signer_binding(db_session, DEMO_USER_ID, bank_id=None)
    _signer_binding(db_session, APPROVER_USER_ID)
    db_session.commit()

    with pytest.raises(AttestationConflict) as refused:
        routing.route(
            db_session,
            MAKER,
            package,
            policy=workflow.package_policy(db_session, MAKER, package),
            nominations=[
                routing.Nomination(signing_role="preparer", user_id=DEMO_USER_ID),
                routing.Nomination(signing_role="approver", user_id=APPROVER_USER_ID),
            ],
            signatures=[],
            reason="Attempt to route a signature using an organization-wide grant",
        )

    assert refused.value.error_code == "recipient_role_insufficient"


def test_composing_partial_signer_rows_never_authorize_preview(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An org-scope grant and a separate branch-narrowed grant never combine.

    The exact-bank SIGN_OFF binding itself must carry the whole-institution
    data scope; an organization-scope grant cannot lend its breadth to a
    branch-restricted exact-bank row, mirroring the stress ``require_signer``
    check. A narrowed REG pair cannot be stored today (the narrowed-module
    CHECK confines narrowed scopes to credit), so the effective-grant loader
    is patched to answer exactly as a database holding the historical pair
    would.
    """
    package = _seed_without_signer(db_session)
    org_binding = _signer_binding(db_session, DEMO_USER_ID, bank_id=None)
    _signer_binding(db_session, APPROVER_USER_ID)
    db_session.commit()

    org_grant = BindingGrant(
        binding_id=org_binding.id,
        organization_id=DEMO_ORG_ID,
        principal_id=DEMO_USER_ID,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.SIGNER,
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
        status=BindingStatus.ACTIVE,
        valid_from=datetime.now(UTC),
        valid_until=None,
        revoked_at=None,
    )
    branch_grant = BindingGrant(
        binding_id=uuid4(),
        organization_id=DEMO_ORG_ID,
        principal_id=DEMO_USER_ID,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.SIGNER,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=SAMPLE_BANK_ID,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
        status=BindingStatus.ACTIVE,
        valid_from=datetime.now(UTC),
        valid_until=None,
        revoked_at=None,
        data_scope=DataScope.BRANCH,
        data_scope_values=("Head office",),
    )
    from app.identity import public as authorization_service  # noqa: PLC0415

    real_load_effective_grants = authorization_service.load_effective_grants

    def historical_pair(
        db: Session, *, organization_id: str, binding_ids: Sequence[UUID]
    ) -> dict[UUID, BindingGrant]:
        if org_binding.id in binding_ids:
            return {org_grant.binding_id: org_grant, branch_grant.binding_id: branch_grant}
        return real_load_effective_grants(
            db, organization_id=organization_id, binding_ids=binding_ids
        )

    monkeypatch.setattr(authorization_service, "load_effective_grants", historical_pair)

    with pytest.raises(HTTPException) as refused:
        _preview(db_session, MAKER, package)

    assert refused.value.status_code == 403


def test_signer_without_source_read_is_hidden_and_mutates_nothing(db_session: Session) -> None:
    package = _seed_without_signer(db_session)
    _signer_binding(db_session, DEMO_USER_ID)
    for binding in db_session.scalars(
        select(AuthorizationBinding).where(
            AuthorizationBinding.principal_user_id == DEMO_USER_ID,
            AuthorizationBinding.role_bundle != RoleBundle.SIGNER.value,
        )
    ):
        binding.status = BindingStatus.SUSPENDED.value
    db_session.commit()
    before = _mutation_counts(db_session)

    with pytest.raises(HTTPException) as refused:
        _preview(db_session, MAKER, package)

    assert refused.value.status_code == 404
    assert _mutation_counts(db_session) == before


def test_a_viewless_signer_nominee_is_refused_and_no_route_row_is_persisted(
    db_session: Session,
) -> None:
    """A Signer-only nominee can never pass the ceremony's source READ.

    The SIGNER bundle carries no VIEW, so a nominee holding only an exact-bank
    Signer binding could never pass ``require_certification_sign_off``'s
    ``require_view`` gate. Routing must refuse them at nomination — before any
    recipient row or signature-request notification exists.
    """
    package = _seed_without_signer(db_session)
    _signer_binding(db_session, OTHER_APPROVER_USER_ID)
    _signer_binding(db_session, APPROVER_USER_ID)
    # Leave the preparer nominee Signer-only: suspend every non-SIGNER
    # binding, which removes the approver bundle's source VIEW.
    for binding in db_session.scalars(
        select(AuthorizationBinding).where(
            AuthorizationBinding.principal_user_id == OTHER_APPROVER_USER_ID,
            AuthorizationBinding.role_bundle != RoleBundle.SIGNER.value,
        )
    ):
        binding.status = BindingStatus.SUSPENDED.value
    db_session.commit()
    before = _mutation_counts(db_session)

    with pytest.raises(AttestationConflict) as refused:
        routing.route(
            db_session,
            MAKER,
            package,
            policy=workflow.package_policy(db_session, MAKER, package),
            nominations=[
                routing.Nomination(signing_role="preparer", user_id=OTHER_APPROVER_USER_ID),
                routing.Nomination(signing_role="approver", user_id=APPROVER_USER_ID),
            ],
            signatures=workflow.current_signatures(db_session, MAKER, package),
            reason="Name both officers before the first certification",
        )

    assert refused.value.error_code == "recipient_role_insufficient"
    db_session.rollback()
    assert _mutation_counts(db_session) == before


def test_a_signer_nominee_who_can_read_the_source_is_routed(db_session: Session) -> None:
    """The positive half of the nominee gate: view + exact-bank Signer routes.

    Same nomination shape as the refusal, with the nominee's own source VIEW
    restored — here the approver bundle's. That is exactly the authority the
    ceremony will demand, so both recipient rows persist.
    """
    package = _seed_without_signer(db_session)
    _signer_binding(db_session, OTHER_APPROVER_USER_ID)
    _signer_binding(db_session, APPROVER_USER_ID)

    routed = routing.route(
        db_session,
        MAKER,
        package,
        policy=workflow.package_policy(db_session, MAKER, package),
        nominations=[
            routing.Nomination(signing_role="preparer", user_id=OTHER_APPROVER_USER_ID),
            routing.Nomination(signing_role="approver", user_id=APPROVER_USER_ID),
        ],
        signatures=workflow.current_signatures(db_session, MAKER, package),
        reason="Name both officers before the first certification",
    )
    db_session.commit()

    assert [row.recipient_user_id for row in routed] == [
        OTHER_APPROVER_USER_ID,
        APPROVER_USER_ID,
    ]
    assert db_session.scalar(select(func.count()).select_from(PackageSignatureRecipient)) == 2


def test_step_up_without_signer_mints_no_token_and_writes_no_success_audit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _seed_without_signer(db_session)
    before = _mutation_counts(db_session)

    def unexpected_step_up(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("authentication ran before signing authority was established")

    monkeypatch.setattr(stepup, "verify_step_up", unexpected_step_up)
    with pytest.raises(HTTPException) as refused:
        attestation_api.step_up(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            package.id,
            StepUpRequest(signing_role="preparer", password="not-used"),
            _request(),
        )

    assert refused.value.status_code == 403
    assert _mutation_counts(db_session) == before


def test_certify_and_send_denial_precedes_placement_signature_and_recipient_writes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _seed_without_signer(db_session)
    before = _mutation_counts(db_session)

    def unexpected_placement(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a placement was written before signing authority was established")

    monkeypatch.setattr(
        "app.attestation.service.placements.set_package_override", unexpected_placement
    )
    with pytest.raises(HTTPException) as refused:
        attestation_api.certify_and_send(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            package.id,
            CertifyAndSendRequest(
                signing_role="preparer",
                authorization_token="not-a-token",
                expected_certification_digest=workflow.compute_binding(
                    package
                ).certification_digest,
                recipients=[
                    SignatureRecipientNomination(signing_role="approver", user_id=APPROVER_USER_ID)
                ],
                placements=[
                    SignatureFieldPlacement(
                        signing_role=(
                            "preparer" if placement.signing_role == "preparer" else "approver"
                        ),
                        field_type="signature",
                        field_index=placement.field_index,
                        page_index=placement.page_index,
                        x1=placement.box[0],
                        y1=placement.box[1],
                        x2=placement.box[2],
                        y2=placement.box[3],
                    )
                    for placement in PLACED
                ],
                reason="Attempt a combined certification without Signer authority",
            ),
        )

    assert refused.value.status_code == 403
    assert _mutation_counts(db_session) == before


def test_signer_only_binding_does_not_supply_the_separate_checker_approval(
    db_session: Session,
) -> None:
    package = _seed_without_signer(db_session)
    analyst_id = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    for user_id in (DEMO_USER_ID, APPROVER_USER_ID, analyst_id):
        _signer_binding(db_session, user_id)
    _route_two_signers(db_session, package)
    _certify(db_session, MAKER, package, role="preparer")

    existing = db_session.scalar(
        select(PackageSignatureRecipient).where(
            PackageSignatureRecipient.package_id == package.id,
            PackageSignatureRecipient.attestation_cycle == package.attestation_cycle,
            PackageSignatureRecipient.signing_role == "approver",
        )
    )
    assert existing is not None
    db_session.delete(existing)
    db_session.flush()
    analyst = TenantContext(
        organization_id=DEMO_ORG_ID, actor_user_id=analyst_id, authorization_version=1
    )
    identity = ensure_signer_identity(db_session, analyst, analyst_id)
    _name_certifier(
        db_session,
        package,
        role="approver",
        user_id=analyst_id,
        signer_id=identity.signer_id,
    )
    token = _mint_existing_signer_token(db_session, package, "approver", analyst)
    before = _mutation_counts(db_session)

    with pytest.raises(HTTPException) as refused:
        signing.certify(
            db_session,
            analyst,
            package,
            role="approver",
            authorization_token=token,
            backends=signing.SigningBackends(raw_signer=get_raw_signer()),
        )

    assert refused.value.status_code == 403
    db_session.rollback()
    assert _mutation_counts(db_session) == before
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AttestationSignature)
            .where(AttestationSignature.package_id == package.id)
        )
        == 1
    )


def test_revoking_signer_after_routing_closes_certification_before_token_spend(
    db_session: Session,
) -> None:
    package = _seed_without_signer(db_session)
    maker_signer = _signer_binding(db_session, DEMO_USER_ID)
    _signer_binding(db_session, APPROVER_USER_ID)
    _route_two_signers(db_session, package)
    token = _mint_existing_signer_token(db_session, package)
    maker_signer.status = BindingStatus.REVOKED.value
    maker_signer.revoked_at = datetime.now(UTC)
    maker_signer.revoked_by_type = authorization.GrantorType.SYSTEM.value
    maker_signer.revoked_by_id = "signer-gate-test"
    maker_signer.revoked_reason = "Revoke after routing but before certification"
    db_session.commit()
    before = _mutation_counts(db_session)

    with pytest.raises(HTTPException) as refused:
        signing.certify(
            db_session,
            MAKER,
            package,
            role="preparer",
            authorization_token=token,
            backends=signing.SigningBackends(raw_signer=get_raw_signer()),
        )

    assert refused.value.status_code == 403
    db_session.rollback()
    assert _mutation_counts(db_session) == before
    authorization_row = db_session.scalar(
        select(SigningAuthorization).where(SigningAuthorization.package_id == package.id)
    )
    assert authorization_row is not None
    assert authorization_row.consumed_at is None


def test_direct_certification_cannot_bypass_named_routing(db_session: Session) -> None:
    package = _seed_without_signer(db_session)
    _signer_binding(db_session, DEMO_USER_ID)
    _signer_binding(db_session, APPROVER_USER_ID)
    db_session.commit()
    before = _mutation_counts(db_session)

    with pytest.raises(AttestationConflict) as refused:
        signing.certify(
            db_session,
            MAKER,
            package,
            role="preparer",
            authorization_token="not-a-token",
            backends=signing.SigningBackends(raw_signer=get_raw_signer()),
        )

    assert refused.value.error_code == "signer_not_routed"
    db_session.rollback()
    assert _mutation_counts(db_session) == before


def test_routed_signer_with_genuine_step_up_certifies_and_replay_is_refused(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _seed_without_signer(db_session)
    _signer_binding(db_session, DEMO_USER_ID)
    _signer_binding(db_session, APPROVER_USER_ID)
    _route_two_signers(db_session, package)
    captured_tokens: list[str] = []
    real_mint = stepup.mint_authorization

    def capture_token(*args: Any, **kwargs: Any) -> tuple[str, SigningAuthorization]:
        token, row = real_mint(*args, **kwargs)
        captured_tokens.append(token)
        return token, row

    monkeypatch.setattr(stepup, "mint_authorization", capture_token)
    signature = _certify(db_session, MAKER, package, role="preparer")
    assert signature.signing_role == "preparer"
    authorization_row = db_session.scalar(
        select(SigningAuthorization).where(SigningAuthorization.package_id == package.id)
    )
    assert authorization_row is not None
    assert authorization_row.consumed_at is not None
    before = _mutation_counts(db_session)

    assert len(captured_tokens) == 1
    assert stepup._hash_token(captured_tokens[0]) == authorization_row.token_hash  # noqa: SLF001
    with pytest.raises(stepup.StepUpFailed, match="already been used"):
        stepup.consume_authorization(
            db_session,
            MAKER,
            token=captured_tokens[0],
            user_id=DEMO_USER_ID,
            package_id=package.id,
            signing_role="preparer",
            certification_digest=workflow.compute_binding(package).certification_digest,
        )
    with pytest.raises(AttestationConflict):
        signing.certify(
            db_session,
            MAKER,
            package,
            role="preparer",
            authorization_token=captured_tokens[0],
            backends=signing.SigningBackends(raw_signer=get_raw_signer()),
        )

    db_session.rollback()
    assert _mutation_counts(db_session) == before


CHECKER_STEP_UP_PASSWORD = "checker-step-up-fixture-0001"  # noqa: S105 - disposable fixture


def _route_and_certify_preparer(db_session: Session, package: Any) -> None:
    """A routed approver waiting on a preparer's certification: the live state."""
    _signer_binding(db_session, DEMO_USER_ID)
    _signer_binding(db_session, APPROVER_USER_ID)
    _route_two_signers(db_session, package)
    _certify(db_session, MAKER, package, role="preparer")


def _revoke_checker_approve(db_session: Session) -> None:
    """Remove only the checker's APPROVE, keeping VIEW and SIGN_OFF intact.

    The APPROVER bundle carried the checker's source VIEW too; an Analyst
    binding replaces that visibility so the refusal below is about APPROVE,
    not about a hidden return.
    """
    bindings = db_session.scalars(
        select(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == DEMO_ORG_ID,
            AuthorizationBinding.principal_user_id == APPROVER_USER_ID,
            AuthorizationBinding.role_bundle == RoleBundle.APPROVER.value,
        )
    ).all()
    assert bindings, "the fixture must have given the checker an APPROVE binding to revoke"
    for binding in bindings:
        binding.status = BindingStatus.REVOKED.value
        binding.revoked_at = datetime.now(UTC)
        binding.revoked_by_type = authorization.GrantorType.SYSTEM.value
        binding.revoked_by_id = "signer-gate-test"
        binding.revoked_reason = "Revoke the checker's APPROVE before their step-up"
    authorization.create_role_binding(
        db_session,
        organization_id=DEMO_ORG_ID,
        principal_user_id=APPROVER_USER_ID,
        principal_type=authorization.PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            SAMPLE_BANK_ID,
            ModuleScope.REGULATORY,
            SensitivityScope.RESTRICTED,
        ),
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "signer-gate-test"),
        reason="Keep the checker's source visibility after the APPROVE revocation",
        commit=False,
    )
    db_session.commit()


def test_step_up_for_a_checker_without_approve_mints_no_token_and_writes_no_success_audit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revoked APPROVE denies at PREFLIGHT, before the token and its audit exist.

    The routing/VIEW preflight used to admit a checker holding only SIGN_OFF;
    the separate APPROVE duty was re-evaluated only inside ``certify`` — after
    ``step-up`` had already minted a one-shot token and written its success
    audit. Denial before side effects puts the check with the other structural
    preflights, and the execution-time re-evaluation below it stays.
    """
    package = _seed_without_signer(db_session)
    _route_and_certify_preparer(db_session, package)
    _revoke_checker_approve(db_session)
    before = _mutation_counts(db_session)

    def unexpected_step_up(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("re-authentication ran before checker APPROVE was established")

    monkeypatch.setattr(stepup, "verify_step_up", unexpected_step_up)
    with pytest.raises(HTTPException) as refused:
        attestation_api.step_up(
            db_session,
            APPROVER,
            SAMPLE_BANK_ID,
            package.id,
            StepUpRequest(signing_role="approver", password="not-used"),
            _request(),
        )

    assert refused.value.status_code == 403
    assert _mutation_counts(db_session) == before


def test_step_up_for_a_checker_with_approve_mints_a_token(db_session: Session) -> None:
    """The allow path is unchanged: a routed checker holding APPROVE gets a token."""
    package = _seed_without_signer(db_session)
    _route_and_certify_preparer(db_session, package)

    checker = tenant_user(db_session, organization_id=DEMO_ORG_ID, user_id=APPROVER_USER_ID)
    checker.password_hash = security.hash_password(CHECKER_STEP_UP_PASSWORD)
    checker.auth_provider = "password"
    checker.failed_login_attempts = 0
    checker.locked_until = None
    db_session.commit()

    granted = attestation_api.step_up(
        db_session,
        APPROVER,
        SAMPLE_BANK_ID,
        package.id,
        StepUpRequest(signing_role="approver", password=CHECKER_STEP_UP_PASSWORD),
        _request(),
    )

    assert granted.authorization_token
    assert granted.signing_role == "approver"
    minted = db_session.scalar(
        select(SigningAuthorization).where(
            SigningAuthorization.package_id == package.id,
            SigningAuthorization.user_id == APPROVER_USER_ID,
            SigningAuthorization.signing_role == "approver",
        )
    )
    assert minted is not None
    assert minted.consumed_at is None
    success_audits = db_session.scalars(
        select(AuditEvent.event_type).where(
            AuditEvent.event_type == "attestation.step_up_authorized",
        )
    ).all()
    assert "attestation.step_up_authorized" in success_audits
