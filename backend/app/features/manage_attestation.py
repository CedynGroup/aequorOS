"""Attestation & e-signature API (docs/attestation_esignature.md §4.6).

Role gating mirrors the capability's meaning rather than inventing a new
hierarchy: preparing is an analyst act, approving is an approver act, and
policy administration is an admin act. pyHanko and ``cryptography`` are invoked
server-side only — nothing in this module is user-facing crypto.

The signing flow is deliberately two calls, not one:

    POST …/attestation/step-up   → re-authenticate, receive a single-use token
    POST …/attestation/certify   → spend the token, produce the signature

so that presence is proved against the exact figures being signed, and the
authorisation cannot outlive them.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.deps import (
    ApproverTenant,
    DbSession,
    MutationTenant,
    Tenant,
    TenantContext,
    require_role,
)
from app.features.manage_banks import BankReference
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
    SignaturePlacementTemplateListRead,
    SignaturePlacementTemplateRead,
    SignaturePlacementTemplateUpsertRequest,
    SignatureRoutingUpdateRequest,
    SignerIdentityRead,
    StepUpGrantedRead,
    StepUpRequest,
    VerificationReportRead,
    VoidAttestationRequest,
)
from app.services import attestation_api

router = APIRouter(tags=["attestation"])

AdminCtx = Annotated[TenantContext, Depends(require_role("admin"))]


@router.get(
    "/attestation/signer-identity",
    response_model=SignerIdentityRead,
    operation_id="getMySignerIdentity",
)
def get_my_signer_identity(db: DbSession, ctx: MutationTenant) -> SignerIdentityRead:
    """The caller's own permanent signer identity, provisioning it if absent."""
    return attestation_api.my_signer_identity(db, ctx)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation",
    response_model=AttestationStatusRead,
    operation_id="getPackageAttestation",
)
def get_package_attestation(
    bank_id: BankReference, package_id: UUID, db: DbSession, ctx: Tenant
) -> AttestationStatusRead:
    """Who has signed, who must still sign, and whether submission is unlocked."""
    return attestation_api.attestation_status(db, ctx, bank_id, package_id)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/preview",
    response_model=CertificationPreviewRead,
    operation_id="previewCertification",
)
def preview_certification(
    bank_id: BankReference,
    package_id: UUID,
    signing_role: str,
    db: DbSession,
    ctx: MutationTenant,
) -> CertificationPreviewRead:
    """Exactly what will be signed — digest, figures, and the statement."""
    return attestation_api.preview(db, ctx, bank_id, package_id, signing_role)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/step-up",
    response_model=StepUpGrantedRead,
    operation_id="stepUpForSigning",
)
def step_up_for_signing(  # noqa: PLR0913 - FastAPI injects db/ctx/request
    bank_id: BankReference,
    package_id: UUID,
    payload: StepUpRequest,
    request: Request,
    db: DbSession,
    ctx: MutationTenant,
) -> StepUpGrantedRead:
    """Prove presence now; receive a single-use authorisation bound to the figures."""
    return attestation_api.step_up(db, ctx, bank_id, package_id, payload, request)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/certify",
    response_model=AttestationStatusRead,
    operation_id="certifyPackage",
)
def certify_package(
    bank_id: BankReference,
    package_id: UUID,
    payload: CertifyRequest,
    db: DbSession,
    ctx: MutationTenant,
) -> AttestationStatusRead:
    """Record a signature.

    Gated at ``analyst`` here because a preparer certification is an analyst
    act; the service additionally requires the ``approver`` role for an
    approver slot, so the checker gate is not weakened.
    """
    return attestation_api.certify(db, ctx, bank_id, package_id, payload)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/certify-and-send",
    response_model=AttestationStatusRead,
    operation_id="certifyAndSendPackage",
)
def certify_and_send_package(
    bank_id: BankReference,
    package_id: UUID,
    payload: CertifyAndSendRequest,
    db: DbSession,
    ctx: MutationTenant,
) -> AttestationStatusRead:
    """Certify and nominate the remaining signers in one act.

    The signature and the routing land in one transaction, so a nominee the
    policy cannot accept takes the certification down with it — the alternative
    is a certified return sitting in nobody's queue.
    """
    return attestation_api.certify_and_send(db, ctx, bank_id, package_id, payload)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/send-back",
    response_model=AttestationStatusRead,
    operation_id="sendPackageBackForCorrections",
)
def send_package_back_for_corrections(
    bank_id: BankReference,
    package_id: UUID,
    payload: SendBackForCorrectionsRequest,
    db: DbSession,
    ctx: ApproverTenant,
) -> AttestationStatusRead:
    """The reviewing approver's second exit: return it with a note, unsigned.

    Approver-gated for the same reason certifying as a checker is: this is a
    maker-checker decision, recorded against the reviewer's login. The note and
    the withdrawal of the frozen figures commit together — sending a return back
    while its figures stay frozen would leave a package nobody can correct.
    """
    return attestation_api.send_back_for_corrections(db, ctx, bank_id, package_id, payload)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/placements",
    response_model=ResolvedSignaturePlacementsRead,
    operation_id="getPackageSignaturePlacements",
)
def get_package_signature_placements(
    bank_id: BankReference, package_id: UUID, db: DbSession, ctx: Tenant
) -> ResolvedSignaturePlacementsRead:
    """Where this return's signature fields will be created, and from which source."""
    return attestation_api.resolved_placements(db, ctx, bank_id, package_id)


@router.put(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/placements",
    response_model=ResolvedSignaturePlacementsRead,
    operation_id="setPackageSignaturePlacements",
)
def set_package_signature_placements(
    bank_id: BankReference,
    package_id: UUID,
    payload: PackageSignaturePlacementRequest,
    db: DbSession,
    ctx: MutationTenant,
) -> ResolvedSignaturePlacementsRead:
    """Place this return's signature fields; an empty list falls back to the template.

    Reason-required and audited: the placement decides where an officer's name and
    permanent signer ID appear on a document filed with the regulator.
    """
    return attestation_api.set_package_placements(db, ctx, bank_id, package_id, payload)


@router.put(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/recipients",
    response_model=AttestationStatusRead,
    operation_id="updatePackageSignatureRouting",
)
def update_package_signature_routing(
    bank_id: BankReference,
    package_id: UUID,
    payload: SignatureRoutingUpdateRequest,
    db: DbSession,
    ctx: ApproverTenant,
) -> AttestationStatusRead:
    """Re-assign outstanding signatures — the escape hatch for an absent nominee.

    Approver-gated and reason-required, because moving a signature away from the
    person it was sent to is a segregation-of-duties act, not an edit.
    """
    return attestation_api.update_routing(db, ctx, bank_id, package_id, payload)


@router.get(
    "/attestation/awaiting-my-signature",
    response_model=AwaitingSignatureListRead,
    operation_id="listReturnsAwaitingMySignature",
)
def list_returns_awaiting_my_signature(
    db: DbSession, ctx: MutationTenant
) -> AwaitingSignatureListRead:
    """Returns routed to the caller and still unsigned."""
    return attestation_api.awaiting_my_signature(db, ctx)


@router.get(
    "/attestation/my-signature-appearance",
    response_model=AdoptedSignatureRead,
    operation_id="getMyAdoptedSignature",
)
def get_my_adopted_signature(db: DbSession, ctx: MutationTenant) -> AdoptedSignatureRead:
    """The caller's adopted mark, plus the font choices available for a typed one."""
    return attestation_api.my_adopted_signature(db, ctx)


@router.put(
    "/attestation/my-signature-appearance",
    response_model=AdoptedSignatureRead,
    operation_id="adoptMySignature",
)
def adopt_my_signature(
    payload: AdoptSignatureRequest, db: DbSession, ctx: MutationTenant
) -> AdoptedSignatureRead:
    """Adopt or re-adopt the caller's own signature mark.

    Only the caller's own: a mark adopted on somebody else's behalf would be a
    signature appearance its owner never chose. Drawn bytes are normalised
    server-side before storage; the raw upload is never persisted.
    """
    return attestation_api.adopt_my_signature(db, ctx, payload)


@router.get(
    "/attestation/signature-placements",
    response_model=SignaturePlacementTemplateListRead,
    operation_id="listSignaturePlacementTemplates",
)
def list_signature_placement_templates(
    db: DbSession, ctx: Tenant, return_code: str | None = None
) -> SignaturePlacementTemplateListRead:
    return attestation_api.list_placement_templates(db, ctx, return_code=return_code)


@router.put(
    "/attestation/signature-placements",
    response_model=SignaturePlacementTemplateRead,
    operation_id="upsertSignaturePlacementTemplate",
)
def upsert_signature_placement_template(
    payload: SignaturePlacementTemplateUpsertRequest, db: DbSession, ctx: AdminCtx
) -> SignaturePlacementTemplateRead:
    """Author the reusable placement template for a return (optionally per bank).

    Admin-only and reason-required, matching the signing-policy endpoints: this is
    the default every future filing of that return inherits.
    """
    return attestation_api.upsert_placement_template(db, ctx, payload)


@router.post(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/void",
    response_model=AttestationStatusRead,
    operation_id="voidAttestation",
)
def void_attestation(
    bank_id: BankReference,
    package_id: UUID,
    payload: VoidAttestationRequest,
    db: DbSession,
    ctx: ApproverTenant,
) -> AttestationStatusRead:
    """Withdraw the current attestation. Signatures are retained, never deleted."""
    return attestation_api.void(db, ctx, bank_id, package_id, payload.reason)


@router.get(
    "/banks/{bank_id}/regulatory-packages/{package_id}/attestation/verify",
    response_model=VerificationReportRead,
    operation_id="verifyPackageAttestation",
)
def verify_package_attestation(
    bank_id: BankReference, package_id: UUID, db: DbSession, ctx: Tenant
) -> VerificationReportRead:
    """Run every independent verification check and report each separately."""
    return attestation_api.verify(db, ctx, bank_id, package_id)


@router.get(
    "/attestation/signing-policies",
    response_model=PolicyListRead,
    operation_id="listSigningPolicies",
)
def list_signing_policies(db: DbSession, ctx: Tenant) -> PolicyListRead:
    return attestation_api.list_policies(db, ctx)


@router.put(
    "/attestation/signing-policies",
    response_model=PolicyRead,
    operation_id="upsertSigningPolicy",
)
def upsert_signing_policy(
    payload: PolicyUpsertRequest, db: DbSession, ctx: AdminCtx
) -> PolicyRead:
    """Configure who must sign which return.

    Admin-only and reason-required: this is the control that decides whether a
    filed return is properly attested, so changing it is itself an audited act.
    """
    return attestation_api.upsert_policy(db, ctx, payload)
