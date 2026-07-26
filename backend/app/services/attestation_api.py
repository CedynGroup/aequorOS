"""API-facing orchestration for attestation (routes → services → schemas).

Kept out of ``app/services/attestation/`` deliberately: that package is the
domain core and must stay free of HTTP and response-model concerns so it can be
exercised — and verified — without FastAPI. This module is the seam.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import security
from app.core.config import get_settings
from app.models import (
    AdoptedSignatureAppearance,
    AttestationSignature,
    PackageSignatureRecipient,
    RegulatoryPackage,
    ReturnSignaturePlacement,
    ReturnSigningPolicy,
    SignerKey,
)
from app.schemas.attestation import (
    AdoptedSignatureRead,
    AdoptSignatureRequest,
    AttestationStatusRead,
    AwaitingSignatureListRead,
    CertificationPreviewRead,
    CertifyAndSendRequest,
    CertifyRequest,
    PackageSignaturePlacementRequest,
    PolicyListRead,
    PolicyRead,
    PolicyUpsertRequest,
    ResolvedSignaturePlacementsRead,
    SendBackForCorrectionsRequest,
    SignatureFieldPlacement,
    SignaturePlacementTemplateListRead,
    SignaturePlacementTemplateRead,
    SignaturePlacementTemplateUpsertRequest,
    SignatureRecipientNomination,
    SignatureRoutingUpdateRequest,
    SignerIdentityRead,
    StepUpGrantedRead,
    StepUpRequest,
    VerificationReportRead,
)
from app.services.attestation import (
    appearance,
    pdf_signing,
    placements,
    routing,
    signing,
    stepup,
    workflow,
)
from app.services.attestation.identity import (
    require_signer_identity,
    resolve_signer_display,
)
from app.services.attestation.typed_fonts import available_face_keys
from app.services.attestation.workflow import AttestationConflict
from app.services.audit import record_event
from app.services.banks import resolve_bank_reference

if TYPE_CHECKING:
    from pyhanko.sign.timestamps import TimeStamper

SIGNING_ROLES = ("preparer", "approver", "board", "witness")
#: Roles that additionally require the approver gate. A preparer certification
#: is an analyst act; everything downstream is a checker act.
CHECKER_ROLES = frozenset({"approver", "board"})


def _require_actor(ctx: TenantContext) -> UUID:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    return ctx.actor_user_id


def _validate_role(role: str) -> str:
    if role not in SIGNING_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown signing role '{role}'.",
        )
    return role


def _ensure_checker_authority(ctx: TenantContext, role: str) -> None:
    """A checker slot needs the approver role, whatever the route's own gate."""
    if role in CHECKER_ROLES and not security.has_role(list(ctx.roles), "approver"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Certifying as '{role}' requires the approver role — "
                "maker-checker cannot be satisfied by a preparer."
            ),
        )


def _get_package(
    db: Session, ctx: TenantContext, bank_reference: str, package_id: UUID
) -> RegulatoryPackage:
    bank = resolve_bank_reference(db, ctx, bank_reference)
    package = db.scalar(
        select(RegulatoryPackage).where(
            RegulatoryPackage.id == package_id,
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
        )
    )
    if package is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory package not found."
        )
    return package


def _signature_read(signature: AttestationSignature) -> dict[str, Any]:
    return {
        "id": signature.id,
        "signing_role": signature.signing_role,
        "officer_title": signature.officer_title,
        "signer_id": signature.signer_id,
        "signer_display_name": signature.signer_display_name,
        "binding_class": signature.binding_class,
        "certification_digest": signature.certification_digest,
        "content_digest": signature.content_digest,
        "register_state_digest": signature.register_state_digest,
        "signed_source_runs": list(signature.signed_source_runs or []),
        "statement": signature.statement,
        "signature_method": signature.signature_method,
        "certificate_sha256": signature.certificate_sha256,
        "tsa_time": signature.tsa_time,
        "tsa_url": signature.tsa_url,
        "declared_at": signature.declared_at,
        "auth_evidence": dict(signature.auth_evidence or {}),
        "attestation_cycle": signature.attestation_cycle,
        "created_at": signature.created_at,
    }


def _recipient_read(row: PackageSignatureRecipient) -> dict[str, Any]:
    return {
        "id": row.id,
        "signing_role": row.signing_role,
        "recipient_user_id": row.recipient_user_id,
        "recipient_signer_id": row.recipient_signer_id,
        "recipient_display_name": row.recipient_display_name,
        "recipient_job_title": row.recipient_job_title,
        "routing_order": row.routing_order,
        "status": row.status,
        "notified_at": row.notified_at,
        "signed_at": row.signed_at,
        "attestation_cycle": row.attestation_cycle,
    }


def _status_payload(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> dict[str, Any]:
    policy = workflow.package_policy(db, ctx, package)
    signatures = workflow.current_signatures(db, ctx, package)
    outstanding = workflow.outstanding_slots(policy, signatures)
    can_submit = (
        not policy.require_signature
        or (package.attestation_state == "fully_certified" and not outstanding)
    )
    return {
        "package_id": package.id,
        "attestation_state": package.attestation_state,
        "attestation_cycle": package.attestation_cycle,
        "certification_digest": package.certification_digest,
        "certified_at": package.certified_at,
        "fully_certified_at": package.fully_certified_at,
        "voided_at": package.voided_at,
        "void_reason": package.void_reason,
        "policy": policy.as_dict(),
        "outstanding": [{"role": role, "count": count} for role, count in outstanding],
        "signatures": [_signature_read(signature) for signature in signatures],
        "recipients": [
            _recipient_read(row) for row in routing.current_recipients(db, ctx, package)
        ],
        "can_submit": can_submit,
    }


def my_signer_identity(db: Session, ctx: TenantContext) -> SignerIdentityRead:
    actor = _require_actor(ctx)
    identity = require_signer_identity(db, ctx, actor)
    display_name, job_title = resolve_signer_display(db, ctx, actor)
    has_key = (
        db.scalar(
            select(SignerKey.id).where(
                SignerKey.organization_id == ctx.organization_id,
                SignerKey.signer_id == identity.signer_id,
                SignerKey.status == "active",
            )
        )
        is not None
    )
    db.commit()
    return SignerIdentityRead(
        signer_id=identity.signer_id,
        user_id=identity.user_id,
        display_name=display_name,
        job_title=job_title,
        provisioned_at=identity.provisioned_at,
        has_active_key=has_key,
    )


def attestation_status(
    db: Session, ctx: TenantContext, bank_reference: str, package_id: UUID
) -> AttestationStatusRead:
    package = _get_package(db, ctx, bank_reference, package_id)
    return AttestationStatusRead.model_validate(_status_payload(db, ctx, package))


def preview(
    db: Session,
    ctx: TenantContext,
    bank_reference: str,
    package_id: UUID,
    signing_role: str,
) -> CertificationPreviewRead:
    role = _validate_role(signing_role)
    package = _get_package(db, ctx, bank_reference, package_id)
    return CertificationPreviewRead.model_validate(
        signing.preview_certification(db, ctx, package, role=role)
    )


def step_up(  # noqa: PLR0913 - request context is part of the evidence
    db: Session,
    ctx: TenantContext,
    bank_reference: str,
    package_id: UUID,
    payload: StepUpRequest,
    request: Request,
) -> StepUpGrantedRead:
    """Re-authenticate, then mint a single-use authorisation for these figures."""
    actor = _require_actor(ctx)
    role = _validate_role(payload.signing_role)
    _ensure_checker_authority(ctx, role)
    package = _get_package(db, ctx, bank_reference, package_id)

    policy = workflow.package_policy(db, ctx, package)
    binding = workflow.compute_binding(package)
    identity = require_signer_identity(db, ctx, actor)
    _display_name, job_title = resolve_signer_display(db, ctx, actor)

    # Guard BEFORE asking the user to re-authenticate: making someone prove
    # presence only to be told they were never eligible is a poor ceremony.
    workflow.ensure_certifiable(package, policy, role)
    workflow.ensure_digest_unchanged(package, binding)
    workflow.ensure_maker_checker(
        package,
        policy,
        workflow.current_signatures(db, ctx, package),
        role=role,
        user_id=actor,
        signer_id=identity.signer_id,
        job_title=job_title,
    )
    routing.ensure_routed_signer(db, ctx, package, role=role, user_id=actor)

    client = request.client
    evidence = stepup.verify_step_up(
        db,
        ctx,
        actor,
        id_token=payload.id_token,
        password=payload.password,
        request_metadata={
            "ip": client.host if client else None,
            "user_agent": request.headers.get("user-agent"),
        },
    )
    token, row = stepup.mint_authorization(
        db,
        ctx,
        user_id=actor,
        signer_id=identity.signer_id,
        package_id=package.id,
        signing_role=role,
        certification_digest=binding.certification_digest,
        auth_evidence=evidence,
    )
    return StepUpGrantedRead(
        authorization_token=token,
        expires_at=row.expires_at,
        certification_digest=binding.certification_digest,
        signing_role=role,  # pyright: ignore[reportArgumentType] - validated above
        method=evidence.get("method"),
    )


def certify(
    db: Session,
    ctx: TenantContext,
    bank_reference: str,
    package_id: UUID,
    payload: CertifyRequest,
) -> AttestationStatusRead:
    role = _validate_role(payload.signing_role)
    _ensure_checker_authority(ctx, role)
    package = _get_package(db, ctx, bank_reference, package_id)

    binding = workflow.compute_binding(package)
    # The client tells us which figures it believed it was signing. A stale
    # browser tab must never be able to certify something the user never saw.
    if payload.expected_certification_digest != binding.certification_digest:
        raise AttestationConflict(
            "figures_changed_since_preview",
            "The figures changed since this screen was loaded. Reload the return "
            "and review it again before certifying.",
        )

    backends = signing.SigningBackends(
        raw_signer=_raw_signer_or_none(),
        timestamper=_timestamper_or_none(),
        pdf_timestamper=_pdf_timestamper_or_none(),
    )
    signing.certify(
        db,
        ctx,
        package,
        role=role,
        authorization_token=payload.authorization_token,
        backends=backends,
    )
    db.refresh(package)
    return AttestationStatusRead.model_validate(_status_payload(db, ctx, package))


def void(
    db: Session, ctx: TenantContext, bank_reference: str, package_id: UUID, reason: str
) -> AttestationStatusRead:
    package = _get_package(db, ctx, bank_reference, package_id)
    workflow.void_attestation(db, ctx, package, reason=reason)
    return AttestationStatusRead.model_validate(_status_payload(db, ctx, package))


def send_back_for_corrections(
    db: Session,
    ctx: TenantContext,
    bank_reference: str,
    package_id: UUID,
    payload: SendBackForCorrectionsRequest,
) -> AttestationStatusRead:
    """The reviewing approver's other exit — one transaction, both halves.

    Reviewing and deciding are one act, so this sits beside ``certify`` rather
    than on a separate queue screen: the reviewer who has the figures in front of
    them either signs them or sends them back. Both halves commit together
    because either alone is a broken state — a rejection with the figures still
    frozen is a package nobody can correct, and a withdrawn certification with no
    recorded decision is a return that silently un-approved itself.

    The decision runs FIRST: it carries the maker-checker and
    ``pending_approval`` guards, so nothing is withdrawn on behalf of somebody
    who was never entitled to decide.
    """
    from app.services.regulatory_reporting import workflow as reporting  # noqa: PLC0415

    actor = _require_actor(ctx)
    package = _get_package(db, ctx, bank_reference, package_id)
    reason = payload.reason.strip()
    reporting.send_back_for_corrections(
        db, ctx, package, actor_user_id=actor, reason=reason
    )
    # A relaxed return has no attestation to withdraw, and ``void_attestation``
    # says so rather than no-opping — so ask only when there is one.
    if package.attestation_state != "unsigned":
        workflow.void_attestation(db, ctx, package, reason=reason, commit=False)
    db.commit()
    db.refresh(package)
    return AttestationStatusRead.model_validate(_status_payload(db, ctx, package))


def verify(
    db: Session, ctx: TenantContext, bank_reference: str, package_id: UUID
) -> VerificationReportRead:
    package = _get_package(db, ctx, bank_reference, package_id)
    from app.services.attestation import verify as verifier  # noqa: PLC0415 - optional module

    # Institutional trust anchors when configured. Without them the verifier
    # falls back to the chain the signature carries and SAYS SO in the report —
    # internal consistency is not the same as recognised issuance.
    trust_roots = get_settings().attestation.load_trust_roots()
    return VerificationReportRead.model_validate(
        verifier.verify_attestation(
            db, ctx, package, trust_roots=trust_roots or None
        )
    )


def list_policies(db: Session, ctx: TenantContext) -> PolicyListRead:
    rows = db.scalars(
        select(ReturnSigningPolicy)
        .where(ReturnSigningPolicy.organization_id == ctx.organization_id)
        .order_by(ReturnSigningPolicy.effective_from.desc(), ReturnSigningPolicy.created_at.desc())
    )
    return PolicyListRead(policies=[_policy_read(row) for row in rows])


def _policy_read(row: ReturnSigningPolicy) -> PolicyRead:
    return PolicyRead.model_validate(
        {
            "id": row.id,
            "bank_id": row.bank_id,
            "return_code": row.return_code,
            "return_family": row.return_family,
            "basis": row.basis,
            "required_signatures": list(row.required_signatures or []),
            "required_attachments": list(row.required_attachments or []),
            "require_signature": row.require_signature,
            "require_signed_pdf": row.require_signed_pdf,
            "distinct_signers": row.distinct_signers,
            "effective_from": row.effective_from,
            "effective_to": row.effective_to,
            "reason": row.reason,
            "updated_by": row.updated_by,
            "updated_at": row.updated_at,
        }
    )


def upsert_policy(
    db: Session, ctx: TenantContext, payload: PolicyUpsertRequest
) -> PolicyRead:
    """Create or supersede a signing policy for a scope.

    Policies are versioned by effective date rather than edited in place, so a
    return filed under an earlier policy can always be shown to have satisfied
    the rules that were actually in force at its reporting date.
    """
    actor = _require_actor(ctx)
    if not payload.return_code and not payload.return_family:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A policy must scope to a return code or a return family.",
        )
    _close_open_policy(db, ctx, payload)

    row = ReturnSigningPolicy(
        organization_id=ctx.organization_id,
        bank_id=payload.bank_id,
        return_code=payload.return_code,
        return_family=payload.return_family,
        basis=payload.basis,
        required_signatures=[slot.model_dump() for slot in payload.required_signatures],
        required_attachments=list(payload.required_attachments),
        require_signature=payload.require_signature,
        require_signed_pdf=payload.require_signed_pdf,
        distinct_signers=payload.distinct_signers,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        updated_by=actor,
        reason=payload.reason,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="attestation.signing_policy_updated",
        entity_type="return_signing_policy",
        entity_id=row.id,
        details={
            "scope": {
                "bank_id": payload.bank_id,
                "return_code": payload.return_code,
                "return_family": payload.return_family,
                "basis": payload.basis,
            },
            "require_signature": payload.require_signature,
            "require_signed_pdf": payload.require_signed_pdf,
            "reason": payload.reason,
        },
    )
    db.commit()
    return _policy_read(row)


def _close_open_policy(
    db: Session, ctx: TenantContext, payload: PolicyUpsertRequest
) -> None:
    """End-date any identically scoped open policy the day before the new one."""
    existing = db.scalars(
        select(ReturnSigningPolicy).where(
            ReturnSigningPolicy.organization_id == ctx.organization_id,
            ReturnSigningPolicy.bank_id == payload.bank_id,
            ReturnSigningPolicy.return_code == payload.return_code,
            ReturnSigningPolicy.return_family == payload.return_family,
            ReturnSigningPolicy.basis == payload.basis,
            ReturnSigningPolicy.effective_to.is_(None),
        )
    )
    for row in existing:
        if row.effective_from < payload.effective_from:
            row.effective_to = date.fromordinal(payload.effective_from.toordinal() - 1)


# --- signature field placement ---------------------------------------------


def _placement_write(spec: SignatureFieldPlacement) -> pdf_signing.FieldPlacement:
    """Wire floats → whole points.

    Rounded rather than rejected because a browser drag legitimately produces
    fractions; rounded HERE rather than in the service so there is exactly one
    conversion between the contract and the domain. ``field_index`` is dropped
    on the way in — ``placements`` assigns it from list order.
    """
    return pdf_signing.FieldPlacement(
        signing_role=spec.signing_role,
        page_index=spec.page_index,
        box=(round(spec.x1), round(spec.y1), round(spec.x2), round(spec.y2)),
        field_type=spec.field_type,
    )


def _placement_read(placement: pdf_signing.FieldPlacement) -> dict[str, Any]:
    return {
        "signing_role": placement.signing_role,
        "field_type": placement.field_type,
        "field_index": placement.field_index,
        "page_index": placement.page_index,
        "x1": float(placement.box[0]),
        "y1": float(placement.box[1]),
        "x2": float(placement.box[2]),
        "y2": float(placement.box[3]),
    }


def _field_types_read() -> list[dict[str, Any]]:
    return [
        {
            "field_type": field_type,
            "min_box_width": size[0],
            "min_box_height": size[1],
        }
        for field_type, size in pdf_signing.MIN_BOX_SIZES.items()
    ]


def _template_read(
    rows: list[ReturnSignaturePlacement],
) -> SignaturePlacementTemplateRead:
    """One scope's rows as a single template.

    The reason and the actor come from any row in the set: it is always written as
    a unit (delete-then-insert), so every row carries the same pair.
    """
    return SignaturePlacementTemplateRead.model_validate(
        {
            "bank_id": rows[0].bank_id,
            "return_code": rows[0].return_code,
            "placements": [
                _placement_read(
                    pdf_signing.FieldPlacement(
                        signing_role=row.signing_role,
                        page_index=row.page_index,
                        box=(row.x1, row.y1, row.x2, row.y2),
                        field_type=row.field_type,
                        field_index=row.field_index,
                    )
                )
                for row in rows
            ],
            "reason": rows[0].reason,
            "updated_by": rows[0].updated_by,
            "updated_at": max(row.updated_at for row in rows),
        }
    )


def list_placement_templates(
    db: Session, ctx: TenantContext, *, return_code: str | None = None
) -> SignaturePlacementTemplateListRead:
    grouped: dict[tuple[str, str | None], list[ReturnSignaturePlacement]] = {}
    for row in placements.list_templates(db, ctx, return_code=return_code):
        grouped.setdefault((row.return_code, row.bank_id), []).append(row)
    return SignaturePlacementTemplateListRead(
        templates=[
            _template_read(group)
            for _scope, group in sorted(
                grouped.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        ]
    )


def upsert_placement_template(
    db: Session, ctx: TenantContext, payload: SignaturePlacementTemplateUpsertRequest
) -> SignaturePlacementTemplateRead:
    _require_actor(ctx)
    if payload.bank_id is not None:
        resolve_bank_reference(db, ctx, payload.bank_id)
    rows = placements.upsert_template(
        db,
        ctx,
        return_code=payload.return_code,
        bank_id=payload.bank_id,
        placements=[_placement_write(spec) for spec in payload.placements],
        reason=payload.reason,
    )
    db.commit()
    return _template_read(rows)


def resolved_placements(
    db: Session, ctx: TenantContext, bank_reference: str, package_id: UUID
) -> ResolvedSignaturePlacementsRead:
    package = _get_package(db, ctx, bank_reference, package_id)
    resolved = placements.resolve(db, ctx, package)
    return ResolvedSignaturePlacementsRead.model_validate(
        {
            "package_id": package.id,
            "return_code": package.return_code,
            "source": resolved.source,
            "placements": [
                _placement_read(placement) for placement in resolved.placements
            ],
            "field_types": _field_types_read(),
            "editable": package.attestation_state == "unsigned",
        }
    )


def set_package_placements(
    db: Session,
    ctx: TenantContext,
    bank_reference: str,
    package_id: UUID,
    payload: PackageSignaturePlacementRequest,
) -> ResolvedSignaturePlacementsRead:
    package = _get_package(db, ctx, bank_reference, package_id)
    placements.set_package_override(
        db,
        ctx,
        package,
        placements=[_placement_write(spec) for spec in payload.placements],
        reason=payload.reason,
    )
    db.commit()
    return resolved_placements(db, ctx, bank_reference, package_id)


# --- adopted signature appearance ------------------------------------------


def _adopted_read(signer_id: str, row: AdoptedSignatureAppearance | None) -> AdoptedSignatureRead:
    return AdoptedSignatureRead.model_validate(
        {
            "adopted": row is not None,
            "signer_id": signer_id,
            "kind": row.kind if row else None,
            "image_png_base64": (
                base64.b64encode(row.image_png).decode("ascii")
                if row is not None and row.image_png is not None
                else None
            ),
            "image_width": row.image_width if row else None,
            "image_height": row.image_height if row else None,
            "typed_name": row.typed_name if row else None,
            "typed_font": row.typed_font if row else None,
            "adopted_at": row.adopted_at if row else None,
            # What this deployment can actually stamp, not the whole
            # vocabulary: a face whose bundled file is missing would be
            # offered, adopted, and then refused at signing time.
            "available_fonts": available_face_keys(),
        }
    )


def my_adopted_signature(db: Session, ctx: TenantContext) -> AdoptedSignatureRead:
    actor = _require_actor(ctx)
    identity = require_signer_identity(db, ctx, actor)
    row = appearance.get_adopted(db, ctx, identity.signer_id)
    db.commit()
    return _adopted_read(identity.signer_id, row)


def adopt_my_signature(
    db: Session, ctx: TenantContext, payload: AdoptSignatureRequest
) -> AdoptedSignatureRead:
    actor = _require_actor(ctx)
    identity = require_signer_identity(db, ctx, actor)
    drawn: bytes | None = None
    if payload.image_png_base64 is not None:
        try:
            drawn = base64.b64decode(payload.image_png_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The drawn signature is not valid base64.",
            ) from exc
    try:
        row = appearance.adopt(
            db,
            ctx,
            signer_id=identity.signer_id,
            kind=payload.kind,
            drawn=drawn,
            typed_name=payload.typed_name,
            typed_font=payload.typed_font,
        )
    except appearance.SignatureMarkRejected as exc:
        # 422, not 409: the upload itself is unacceptable — nothing about the
        # return's state is in conflict.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    return _adopted_read(identity.signer_id, row)


# --- named recipient routing ------------------------------------------------


def _nominations(
    payload: list[SignatureRecipientNomination],
) -> list[routing.Nomination]:
    return [
        routing.Nomination(
            signing_role=_validate_role(item.signing_role), user_id=item.user_id
        )
        for item in payload
    ]


def certify_and_send(
    db: Session,
    ctx: TenantContext,
    bank_reference: str,
    package_id: UUID,
    payload: CertifyAndSendRequest,
) -> AttestationStatusRead:
    """Certify, then route the remaining signatures — one transaction.

    The order is forced by the DocMDP certification: the fields must exist before
    the signature, so an optional placement is applied first, and the nomination
    can only be validated against the signature list that INCLUDES this
    certification. ``commit=False`` on ``signing.certify`` is what makes a rejected
    nominee take the signature down with it, rather than leaving a certified
    return routed to nobody.
    """
    actor = _require_actor(ctx)
    role = _validate_role(payload.signing_role)
    _ensure_checker_authority(ctx, role)
    package = _get_package(db, ctx, bank_reference, package_id)

    binding = workflow.compute_binding(package)
    if payload.expected_certification_digest != binding.certification_digest:
        raise AttestationConflict(
            "figures_changed_since_preview",
            "The figures changed since this screen was loaded. Reload the return "
            "and review it again before certifying.",
        )
    # Empty means "leave placement as it resolves". Clearing an override is a
    # placement decision, made through the placement endpoint, not a side effect
    # of signing.
    if payload.placements:
        placements.set_package_override(
            db,
            ctx,
            package,
            placements=[_placement_write(spec) for spec in payload.placements],
            reason=payload.reason,
        )

    backends = signing.SigningBackends(
        raw_signer=_raw_signer_or_none(),
        timestamper=_timestamper_or_none(),
        pdf_timestamper=_pdf_timestamper_or_none(),
    )
    signature = signing.certify(
        db,
        ctx,
        package,
        role=role,
        authorization_token=payload.authorization_token,
        backends=backends,
        commit=False,
        # A checker's certification writes the approval decision; the reviewer's
        # own words belong on that record rather than a platform paraphrase.
        reason=payload.reason,
    )
    routing.route(
        db,
        ctx,
        package,
        policy=workflow.package_policy(db, ctx, package),
        nominations=_nominations(payload.recipients),
        # Re-read AFTER certifying: ``certify`` flushed its row, so this list now
        # includes the preparer's signature. That is exactly what makes
        # ``ensure_maker_checker`` reject a preparer nominating themselves, and
        # what makes the outstanding-slot count the one the approver will face.
        signatures=workflow.current_signatures(db, ctx, package),
        reason=payload.reason,
    )
    record_event(
        db,
        ctx,
        event_type="attestation.certified_and_sent",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "signing_role": role,
            "signer_id": signature.signer_id,
            "nominated_by": str(actor),
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(package)
    return AttestationStatusRead.model_validate(_status_payload(db, ctx, package))


def update_routing(
    db: Session,
    ctx: TenantContext,
    bank_reference: str,
    package_id: UUID,
    payload: SignatureRoutingUpdateRequest,
) -> AttestationStatusRead:
    """Re-assign the outstanding signatures with a recorded reason."""
    _require_actor(ctx)
    package = _get_package(db, ctx, bank_reference, package_id)
    routing.reroute(
        db,
        ctx,
        package,
        policy=workflow.package_policy(db, ctx, package),
        nominations=_nominations(payload.recipients),
        signatures=workflow.current_signatures(db, ctx, package),
        reason=payload.reason,
    )
    db.commit()
    return AttestationStatusRead.model_validate(_status_payload(db, ctx, package))


def awaiting_my_signature(db: Session, ctx: TenantContext) -> AwaitingSignatureListRead:
    actor = _require_actor(ctx)
    return AwaitingSignatureListRead.model_validate(
        {
            "items": [
                {
                    "package_id": package.id,
                    "bank_id": package.bank_id,
                    "return_code": package.return_code,
                    "reporting_date": package.reporting_date,
                    "basis": package.basis,
                    "package_version": package.version,
                    "package_status": package.status,
                    "attestation_state": package.attestation_state,
                    "signing_role": recipient.signing_role,
                    "routing_order": recipient.routing_order,
                    "requested_at": recipient.created_at,
                    "notified_at": recipient.notified_at,
                }
                for recipient, package in routing.awaiting_signature(db, ctx, actor)
            ]
        }
    )


def _raw_signer_or_none() -> signing.RawSignerPort | None:
    try:
        from app.services.attestation import signers  # noqa: PLC0415 - optional backend
    except ImportError:  # pragma: no cover - backend module absent
        return None
    factory = getattr(signers, "get_raw_signer", None)
    if factory is None:  # pragma: no cover - backend not yet wired
        return None
    try:
        return factory()
    except Exception:  # noqa: BLE001 - a missing/misconfigured backend must not 500
        return None


def _timestamper_or_none() -> signing.TimestamperPort | None:
    try:
        from app.services.attestation import tsa  # noqa: PLC0415 - optional capability
    except ImportError:  # pragma: no cover
        return None
    factory = getattr(tsa, "get_timestamper", None)
    if factory is None:
        return None
    try:
        return factory()
    except Exception:  # noqa: BLE001 - no TSA configured is a normal state
        return None


def _pdf_timestamper_or_none() -> TimeStamper | None:
    """The pyHanko ``TimeStamper`` for the PAdES path, from the same authority.

    Built independently of the detached one rather than derived from it so the
    two capabilities can be reasoned about separately — but from the same
    settings, so one deployment never ends up with a timestamped attestation
    beside an untimestamped document.
    """
    try:
        from app.services.attestation import tsa  # noqa: PLC0415 - optional capability
    except ImportError:  # pragma: no cover
        return None
    try:
        return tsa.build_pdf_timestamper()
    except Exception:  # noqa: BLE001 - no TSA configured is a normal state
        return None


def utc_now() -> datetime:
    return datetime.now(UTC)
