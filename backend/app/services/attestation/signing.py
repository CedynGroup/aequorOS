"""Certification — the act that produces a signature record.

This is the orchestrator: it recomputes the binding, enforces every guard,
consumes the single-use authorisation, obtains the cryptographic signature and
a trusted timestamp, appends the (append-only) signature row, links it into the
tenant hash chain, and moves the attestation state — all in one transaction, so
there is no state in which a signature exists without its guards having passed.

The cryptographic modules are injected rather than imported at module scope:
signing must remain testable without an HSM, and the platform must boot with no
signing backend configured at all (identity provisioning and the evidential
hardening are always on; producing signatures is opt-in via
``ATTESTATION_SIGNING_ENABLED``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import (
    AdoptedSignatureAppearance,
    AttestationSignature,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    SignerKey,
)
from app.services import filing_reconciliation
from app.services.attestation import appearance, digests, routing, stepup, workflow
from app.services.attestation.identity import require_signer_identity, resolve_signer_display
from app.services.attestation.policy import SigningPolicy
from app.services.attestation.workflow import AttestationConflict

if TYPE_CHECKING:
    # Types only. The PDF stack stays out of this module's runtime imports for
    # the same reason the signing backends do: the platform must boot, and
    # every non-signing surface must work, with no signing capability wired.
    from pyhanko.sign.signers import Signer as PdfSigner
    from pyhanko.sign.timestamps import TimeStamper

#: The wording a signer commits to. Covered by the signature (what-you-see-is-
#: what-you-sign), so changing this text changes the signature — which is the
#: point: the commitment is to the words the person actually read.
PREPARER_STATEMENT = (
    "I certify that I prepared this return from the institution's records, that "
    "the figures shown are complete and accurate to the best of my knowledge and "
    "belief, and that they correspond to the calculation inputs identified by the "
    "figures digest shown. I understand that an inaccurate, incomplete, delayed or "
    "non-submitted return attracts penalties on the institution and on responsible "
    "key management personnel under section 93(3) of the Banks and Specialised "
    "Deposit-Taking Institutions Act, 2016 (Act 930)."
)
APPROVER_STATEMENT = (
    "I certify that I have reviewed this return and the identical figures certified "
    "by the preparer, identified by the figures digest shown, that I approve its "
    "submission to the Bank of Ghana, and that the figures are complete and accurate "
    "to the best of my knowledge and belief. I understand that an inaccurate, "
    "incomplete, delayed or non-submitted return attracts penalties on the "
    "institution and on responsible key management personnel under section 93(3) of "
    "the Banks and Specialised Deposit-Taking Institutions Act, 2016 (Act 930)."
)

STATEMENTS: dict[str, str] = {
    "preparer": PREPARER_STATEMENT,
    "approver": APPROVER_STATEMENT,
    "board": APPROVER_STATEMENT,
    "witness": APPROVER_STATEMENT,
}


class RawSignerPort(Protocol):
    """The narrow signing surface. Implemented by HSM/KMS/software backends."""

    def sign_digest(self, digest: bytes, *, key_ref: str) -> bytes: ...


class TimestamperPort(Protocol):
    """RFC 3161 trusted time over a digest. Only a hash ever leaves."""

    def timestamp_digest(self, digest: bytes) -> tuple[bytes, datetime]: ...


@dataclass(frozen=True)
class SigningBackends:
    """Injected cryptography. ``None`` means that capability is unavailable.

    The two PDF slots are seams for tests and for callers that have already
    built the objects; production leaves them ``None`` and the artifact path
    constructs the pyHanko ``Signer`` from the signer's own enrolled key. They
    are NOT an opt-out: a policy that requires a signed PDF is honoured whether
    or not anything is injected here, and fails loudly when it cannot be.
    """

    raw_signer: RawSignerPort | None = None
    timestamper: TimestamperPort | None = None
    pdf_signer: PdfSigner | None = None
    pdf_timestamper: TimeStamper | None = None


def statement_for(role: str) -> str:
    return STATEMENTS.get(role, APPROVER_STATEMENT)


def _active_signer_key(db: Session, ctx: TenantContext, signer_id: str) -> SignerKey:
    key = db.scalar(
        select(SignerKey)
        .where(
            SignerKey.organization_id == ctx.organization_id,
            SignerKey.signer_id == signer_id,
            SignerKey.status == "active",
        )
        .order_by(SignerKey.created_at.desc())
    )
    if key is None:
        raise AttestationConflict(
            "no_signing_key",
            "No active signing key is enrolled for this signer. An administrator "
            "must enrol a key before this person can certify a return.",
        )
    return key


def preview_certification(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, *, role: str
) -> dict[str, Any]:
    """What the signer must be shown before they commit.

    Deliberately includes the digest and the exact statement: the UI must render
    what will actually be signed, not a paraphrase.
    """
    policy = workflow.package_policy(db, ctx, package)
    binding = workflow.compute_binding(package)
    signatures = workflow.current_signatures(db, ctx, package)
    return {
        "package_id": str(package.id),
        "return_code": package.return_code,
        "reporting_date": package.reporting_date.isoformat(),
        "basis": package.basis,
        "attestation_state": package.attestation_state,
        "binding_class": binding.binding_class,
        "certification_digest": binding.certification_digest,
        "content_digest": binding.content_digest,
        "register_state_digest": binding.register_state_digest,
        "signed_source_runs": binding.source_runs,
        "statement": statement_for(role),
        "policy": policy.as_dict(),
        "outstanding": [
            {"role": r, "count": c} for r, c in workflow.outstanding_slots(policy, signatures)
        ],
        "frozen_certification_digest": package.certification_digest,
        "matches_frozen": (
            package.certification_digest is None
            or package.certification_digest == binding.certification_digest
        ),
    }


def _signed_document(  # noqa: PLR0913 - the ceremony's full identity context
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    policy: SigningPolicy,
    role: str,
    key: SignerKey,
    backends: SigningBackends,
    signatures: list[AttestationSignature],
    signer_id: str,
    display_name: str | None,
    job_title: str | None,
    signed_at: datetime,
    supplied_version_id: UUID | None,
    adopted: AdoptedSignatureAppearance | None,
) -> UUID | None:
    """Put this officer's signature on the return PDF, when the policy says so.

    ``require_signed_pdf`` is what makes the difference between a signature that
    exists in our database and a signature a Bank of Ghana examiner can see on
    the filed document. When it is set there is no tolerant path: the artifact
    module raises on every failure and the exception travels out through
    ``certify``, so a signature row is never written alongside a document that
    was not signed.

    ``supplied_version_id`` short-circuits this: the caller has already produced
    and archived the signed bytes and is only asking us to pin them. It is
    validated against the package rather than trusted, because "these are the
    bytes I signed" is the one claim on the row that nothing else can check.
    """
    if supplied_version_id is not None:
        pinned = db.scalar(
            select(RegulatoryArtifactVersion.id).where(
                RegulatoryArtifactVersion.id == supplied_version_id,
                RegulatoryArtifactVersion.organization_id == ctx.organization_id,
                RegulatoryArtifactVersion.package_id == package.id,
            )
        )
        if pinned is None:
            raise AttestationConflict(
                "artifact_version_unknown",
                "The artifact version this signature would pin does not belong to this "
                "return, so the signature could not be shown to cover any filed document.",
            )
        return supplied_version_id
    if not policy.require_signed_pdf:
        return None

    # Imported here, not at module scope: the PDF stack pulls in the export
    # engine and object storage, and a deployment that signs nothing must not
    # pay for either (see the module docstring).
    from app.services.attestation import artifact_signing, pdf_signing  # noqa: PLC0415

    mark = (
        None
        if adopted is None
        else pdf_signing.AdoptedMark(
            kind=adopted.kind,
            image_png=adopted.image_png,
            typed_name=adopted.typed_name,
            typed_font=adopted.typed_font,
        )
    )
    version = artifact_signing.sign_package_pdf(
        db,
        ctx,
        package,
        role=role,
        appearance=pdf_signing.SignatureAppearance(
            role_label=pdf_signing.label_for_role(role),
            signer_name=display_name or "(name withheld)",
            officer_title=job_title,
            signer_id=signer_id,
            signed_at=signed_at,
            # The page may only claim trusted time when the DOCUMENT will carry
            # a token — which is the PDF timestamper's presence, not the
            # detached attestation's.
            timestamped=backends.pdf_timestamper is not None,
            mark=mark,
        ),
        key=key,
        signatures=signatures,
        pdf_signer=backends.pdf_signer,
        timestamper=backends.pdf_timestamper,
    )
    return version.id


def certify(  # noqa: PLR0913 - one transactional act with irreducible inputs
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    role: str,
    authorization_token: str,
    backends: SigningBackends,
    artifact_version_id: UUID | None = None,
    commit: bool = True,
    reason: str | None = None,
) -> AttestationSignature:
    """Record one signature and advance the attestation state.

    Order matters and is deliberate: guards first, then the authorisation is
    burned, then cryptography — the detached attestation AND the signature on
    the document itself — then the append. A failure anywhere rolls the whole
    thing back, so a consumed authorisation never leaves a package
    half-certified, and no signature row survives a document that could not be
    signed.

    ``commit=False`` leaves the transaction open for a caller that has more to do
    in the SAME act — "certify and send" nominates the approver after certifying,
    and a nominee the policy rejects has to take the signature down with it.

    ``reason`` is the signer's own words for why they are signing. It is carried
    into the approval decision a checker's certification writes, so the recorded
    decision quotes the approver rather than the platform.
    """
    settings = get_settings()
    if not settings.attestation.signing_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "signing_disabled",
                "message": (
                    "Attestation signing is not enabled for this deployment "
                    "(ATTESTATION_SIGNING_ENABLED)."
                ),
            },
        )
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )

    actor_user_id = ctx.actor_user_id
    identity = require_signer_identity(db, ctx, actor_user_id)
    display_name, job_title = resolve_signer_display(db, ctx, actor_user_id)

    policy = workflow.package_policy(db, ctx, package)
    binding = workflow.compute_binding(package)
    signatures = workflow.current_signatures(db, ctx, package)

    workflow.ensure_certifiable(package, policy, role)
    workflow.ensure_digest_unchanged(package, binding)
    workflow.ensure_maker_checker(
        package,
        policy,
        signatures,
        role=role,
        user_id=actor_user_id,
        signer_id=identity.signer_id,
        job_title=job_title,
    )
    # Additive to the guards above, and silent unless somebody routed this slot
    # to named people: the policy decides whether a signature is acceptable, the
    # routing decides whose turn it is.
    routing.ensure_routed_signer(db, ctx, package, role=role, user_id=actor_user_id)
    # Data-integrity gate (audit 2026-08-22 D-2): an officer certifies that the
    # figures are true, so the platform must not accept that certification over
    # a book its own control says does not balance. Placed before the step-up
    # authorization is consumed, so a refusal does not burn the one-shot token.
    filing_reconciliation.assert_package_reconciled(
        db, ctx, package, purpose="package_certification"
    )

    authorization = stepup.consume_authorization(
        db,
        ctx,
        token=authorization_token,
        user_id=actor_user_id,
        package_id=package.id,
        signing_role=role,
        certification_digest=binding.certification_digest,
    )

    if backends.raw_signer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "signing_backend_unavailable",
                "message": "No signing backend is configured for this deployment.",
            },
        )

    key = _active_signer_key(db, ctx, identity.signer_id)
    declared_at = datetime.now(UTC)
    payload = digests.attestation_payload(
        certification_digest_value=binding.certification_digest,
        signer_id=identity.signer_id,
        signing_role=role,
        officer_title=job_title,
        statement=statement_for(role),
        declared_at=declared_at.isoformat(),
    )
    payload_digest = digests.digest_of(payload)
    signature_value = backends.raw_signer.sign_digest(
        bytes.fromhex(payload_digest), key_ref=key.key_ref
    )

    tsa_token: bytes | None = None
    tsa_time: datetime | None = None
    tsa_url: str | None = None
    if backends.timestamper is not None:
        tsa_token, tsa_time = backends.timestamper.timestamp_digest(bytes.fromhex(payload_digest))
        tsa_url = getattr(backends.timestamper, "url", None)

    signed_artifact_version_id = _signed_document(
        db,
        ctx,
        package,
        policy=policy,
        role=role,
        key=key,
        backends=backends,
        signatures=signatures,
        signer_id=identity.signer_id,
        display_name=display_name,
        job_title=job_title,
        signed_at=tsa_time or declared_at,
        supplied_version_id=artifact_version_id,
        adopted=appearance.get_adopted(db, ctx, identity.signer_id),
    )

    prev_hash = workflow.chain_head(db, ctx)
    signature = AttestationSignature(
        organization_id=ctx.organization_id,
        bank_id=package.bank_id,
        package_id=package.id,
        package_version=package.version,
        signing_role=role,
        officer_title=job_title,
        signer_id=identity.signer_id,
        signer_user_id=actor_user_id,
        signer_display_name=display_name,
        binding_class=binding.binding_class,
        certification_digest=binding.certification_digest,
        content_digest=binding.content_digest,
        snapshot_sha256=package.snapshot_sha256,
        register_state_digest=binding.register_state_digest,
        signed_source_runs=binding.source_runs,
        statement=statement_for(role),
        attestation_payload=payload,
        payload_digest=payload_digest,
        signature_method=(
            "detached_ecdsa_p256_sha256"
            if "ecdsa" in key.algorithm.lower()
            else "detached_rsa_pss_sha256"
        ),
        signature_value=signature_value,
        certificate_pem=key.certificate_pem,
        certificate_sha256=key.certificate_sha256,
        certificate_chain_pem=key.certificate_chain_pem,
        tsa_token=tsa_token,
        tsa_time=tsa_time,
        tsa_url=tsa_url,
        declared_at=declared_at,
        auth_evidence=authorization.auth_evidence,
        artifact_version_id=signed_artifact_version_id,
        prev_hash=prev_hash,
        entry_hash=workflow.chain_entry_hash(
            prev_hash=prev_hash,
            payload_digest=payload_digest,
            signature_value=signature_value,
        ),
        attestation_cycle=package.attestation_cycle,
    )
    db.add(signature)
    db.flush()

    routing.mark_signed(db, ctx, package, signature)
    workflow.apply_certification(
        db,
        ctx,
        package,
        role=role,
        binding=binding,
        policy=policy,
        signatures_after=[*signatures, signature],
        reason=reason,
    )
    if commit:
        db.commit()
    return signature
