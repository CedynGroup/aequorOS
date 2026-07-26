"""Placing the signature ON the filed document (docs/attestation_esignature.md §3.2).

``pdf_signing`` knows how to sign PDF bytes. This module decides *which* bytes,
archives the result as an immutable ``regulatory_artifact_versions`` row, and
returns that row so the certification path can pin it — the missing half of "the
officer's signature is on the document that goes to the regulator".

**One render, then only appends.** The unsigned PDF is produced exactly once, by
the export engine, and archived before anything cryptographic happens. Every
signature afterwards is an incremental update appended to bytes read back out of
object storage at the version the archive pinned. Signing NEVER re-renders: a
re-render that shifted one byte would produce a document the preparer's byte
range does not cover, and the preparer's revision would stop verifying. Reading
the bytes back — rather than reusing the buffer the exporter still had in hand —
is deliberate for the same reason: what is signed is then provably what an
examiner will later fetch, and the checksum is re-verified on the way in.

**Every failure is loud.** No path here returns "unsigned, but fine". A
signature record claiming a signed PDF that does not exist would be worse
evidence than no signature at all, so an unavailable signer, an unreadable
archive, a moved attestation page, and a signing role with no field on the
document all raise — and because the caller runs this inside its own
transaction, the whole certification goes with them.

**Object writes are not transactional.** Signed bytes reach storage before the
transaction commits, so a rollback leaves an orphaned object. That is the same
trade ``attestation.keys`` makes with the soft-key file, in the same direction:
an unreferenced object is inert, whereas a committed row pointing at bytes that
were never written is a broken evidence chain.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Final

from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException, status
from pyhanko.sign.signers import Signer
from pyhanko.sign.timestamps import TimeStamper
from pyhanko_certvalidator import ValidationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings, get_settings
from app.models import (
    AttestationSignature,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    SignerKey,
)
from app.services.attestation import pdf_signing, placements, signers
from app.services.attestation.workflow import AttestationConflict
from app.services.audit import record_event
from app.services.ingestion import bank_slug
from app.services.regulatory_reporting.common import get_bank_or_404
from app.services.regulatory_reporting.exports import export_package_version
from app.storage.client import (
    ObjectMetadata,
    StorageClient,
    StorageError,
    StorageLocation,
)
from app.storage.factory import get_storage_client

#: The kind every signed revision is filed under. It must stay ``"pdf"``: the
#: verifier resolves the document to check by kind, and a signed revision under
#: any other kind would be invisible to checks 1, 2 and 5.
ARTIFACT_KIND: Final = "pdf"

WRITTEN_BY: Final = "attestation_signing"

#: The roles that have a field on the artifact — derived from ``pdf_signing``'s own
#: role→field map rather than restated, so the two can never disagree about which
#: signatures the document can carry. Those field names are part of the filed
#: document's structure. A policy demanding a board or witness signature ON THE
#: PDF is therefore a configuration we cannot honour — and one we must refuse out
#: loud, because the alternative is recording a board signature that exists only
#: in the database.
FIELD_SIGNING_ROLES: Final[frozenset[str]] = frozenset(pdf_signing.ROLE_FIELD_NAMES)


def signer_unavailable(message: str) -> HTTPException:
    """503, matching ``signing_backend_unavailable``: a deployment problem.

    Distinct from the 409s below, which are all statements about this package.
    An operator reading the log needs to know whether to fix configuration or
    fix the return.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error_code": "signed_pdf_signer_unavailable", "message": message},
    )


def sign_package_pdf(  # noqa: PLR0913 - one act, and every input is distinct
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    role: str,
    appearance: pdf_signing.SignatureAppearance,
    key: SignerKey,
    signatures: Sequence[AttestationSignature],
    pdf_signer: Signer | None = None,
    timestamper: TimeStamper | None = None,
    storage: StorageClient | None = None,
    settings: Settings | None = None,
) -> RegulatoryArtifactVersion:
    """Sign the return document for one officer and archive the result.

    ``signatures`` is the package's current attestation cycle in order; the
    approver's base document is resolved from the last of them that pinned an
    artifact version, so the chain of revisions follows the chain of signatures
    rather than whatever happens to be the newest row.

    ``pdf_signer`` is an injection seam: production leaves it ``None`` and the
    bridge is built from the enrolled key's custody backend.
    """
    if role not in FIELD_SIGNING_ROLES:
        raise AttestationConflict(
            "signed_pdf_role_unsupported",
            f"The signing policy requires a signed PDF and a '{role}' signature, but the "
            f"return artifact carries fields for {sorted(FIELD_SIGNING_ROLES)} only. "
            "Recording that signature without placing it on the document would attribute "
            "to the artifact something it does not contain — relax require_signed_pdf for "
            "this return, or drop the role from the policy.",
        )
    resolved = settings if settings is not None else get_settings()
    client = storage if storage is not None else get_storage_client()
    slug = bank_slug(db, get_bank_or_404(db, ctx, package.bank_id))

    base = (
        _unsigned_base(db, ctx, package, client, slug)
        if role == "preparer"
        else _previous_revision(db, ctx, package, signatures, client, slug)
    )
    profile, profile_name = _profile(resolved, timestamper)

    signer = pdf_signer
    if signer is None:
        try:
            with signers.get_pdf_signer(
                key_ref=key.key_ref,
                certificate_pem=key.certificate_pem,
                certificate_chain_pem=key.certificate_chain_pem,
                algorithm=key.algorithm,
                settings=resolved,
            ) as built:
                payload = _apply(base, role=role, signer=built, appearance=appearance,
                                 profile=profile)
        except signers.SignerBackendError as exc:
            raise signer_unavailable(
                f"The return PDF could not be signed with the key enrolled for "
                f"{key.signer_id}: {exc}"
            ) from exc
    else:
        payload = _apply(base, role=role, signer=signer, appearance=appearance, profile=profile)

    revision = sum(1 for signature in signatures if signature.artifact_version_id) + 1
    version = _archive(
        db, ctx, package, client, slug, payload=payload, role=role, revision=revision
    )
    record_event(
        db,
        ctx,
        event_type="attestation.artifact_signed",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "artifact_version_id": str(version.id),
            "signing_role": role,
            "signer_id": key.signer_id,
            "field_name": _field_name(role),
            # The profile actually delivered, not the one aimed at: a document
            # signed without trusted time is still evidence, but not the same
            # evidence, and the difference must be discoverable after the fact.
            "pades_profile": profile_name,
            "object_path": version.object_path,
            "storage_version_id": version.storage_version_id,
            "checksum_sha256": version.checksum_sha256,
        },
    )
    db.flush()
    return version


# --- the bytes --------------------------------------------------------------


def _unsigned_base(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    client: StorageClient,
    slug: str,
) -> bytes:
    """Export the return once, archive it, read it back, and add the fields.

    The export runs here rather than reusing whatever artifact is already on
    file because the document an officer certifies must be a render of the
    figures being certified *now* — an archived export can predate a
    regeneration, and nothing on the version row says which package version it
    came from. ``export_package_version`` is the single renderer and the single
    archiver; from here the bytes are only ever appended to.

    Both fields are created here, in the preparer's own step, from the placements
    resolved for this package. There is no later opportunity: the preparer's
    signature certifies the document at ``MDPPerm.FILL_FORMS``, so a field added
    afterwards would itself be tampering.
    """
    _artifact, version = export_package_version(db, ctx, package, ARTIFACT_KIND)
    base = _read(client, slug, version)
    resolved = placements.resolve(db, ctx, package)
    try:
        return pdf_signing.prepare_signature_fields(base, placements=resolved.placements)
    except pdf_signing.PdfSigningError as exc:
        raise AttestationConflict("signed_pdf_failed", str(exc)) from exc


def _previous_revision(  # noqa: PLR0913 - a lookup across five distinct inputs
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    signatures: Sequence[AttestationSignature],
    client: StorageClient,
    slug: str,
) -> bytes:
    """The document as the previous signer left it.

    Resolved from the artifact version that signature PINS, never from the
    newest row for the package: the pin is the only statement of which exact
    bytes were covered, and appending to anything else would produce a document
    whose two signatures cover unrelated revisions.
    """
    pinned = [s for s in signatures if s.artifact_version_id is not None]
    version = (
        db.scalar(
            select(RegulatoryArtifactVersion).where(
                RegulatoryArtifactVersion.organization_id == ctx.organization_id,
                RegulatoryArtifactVersion.package_id == package.id,
                RegulatoryArtifactVersion.id == pinned[-1].artifact_version_id,
            )
        )
        if pinned
        else None
    )
    if version is None:
        raise AttestationConflict(
            "signed_pdf_base_missing",
            "This return requires a signed PDF, but no signed document from the earlier "
            "signature could be resolved — the preparer certified before the policy "
            "required one. Void the attestation and certify again so both signatures "
            "land on the same document.",
        )
    return _read(client, slug, version)


def _read(client: StorageClient, slug: str, version: RegulatoryArtifactVersion) -> bytes:
    """Fetch the exact archived bytes a version row pins, and prove they are those.

    The checksum comparison is not belt-and-braces: signing bytes that differ
    from the archived record would produce a signature the artifact-binding
    check (§3.5 check 5) can never satisfy, and it is far better to discover
    that here than on the day an examiner runs the verifier.
    """
    location = StorageLocation(
        institution_slug=slug, tier="outputs", object_path=version.object_path
    )
    try:
        _descriptor, stream = client.read(location, version_id=version.storage_version_id)
        payload = stream.read()
    except StorageError as exc:
        raise AttestationConflict(
            "signed_pdf_unreadable",
            f"The archived return document ({version.object_path}) could not be read, so "
            f"there is nothing to sign: {exc}",
        ) from exc
    digest = hashlib.sha256(payload).hexdigest()
    if digest != version.checksum_sha256:
        raise AttestationConflict(
            "signed_pdf_unreadable",
            f"The archived return document ({version.object_path}) no longer matches the "
            f"checksum recorded when it was written (recorded {version.checksum_sha256[:12]}…, "
            f"read {digest[:12]}…). Do not sign it — this is object-store corruption.",
        )
    return payload


def _archive(  # noqa: PLR0913 - a write needs its full destination
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    client: StorageClient,
    slug: str,
    *,
    payload: bytes,
    role: str,
    revision: int,
) -> RegulatoryArtifactVersion:
    """Store the signed revision and append the row that pins it."""
    # One path per revision rather than one path with two object versions: on a
    # backend that cannot version (storage_version_id NULL), the approver's
    # write would otherwise overwrite the exact bytes the preparer's signature
    # pins, and the artifact-binding check would start failing on a document
    # nobody had touched. The attestation cycle is in the path for the same
    # reason: a void restarts the revision count, and a withdrawn attestation
    # has to stay legible — including the document it was made on (L15).
    object_path = (
        f"bog_returns/{package.reporting_date.isoformat()}/{package.id}/"
        f"{package.return_code}.signed-c{package.attestation_cycle}-{revision}-{role}.pdf"
    )
    checksum = hashlib.sha256(payload).hexdigest()
    location = StorageLocation(institution_slug=slug, tier="outputs", object_path=object_path)
    try:
        client.ensure_institution(slug)
        stored = client.write(
            location,
            io.BytesIO(payload),
            ObjectMetadata(
                institution_slug=slug,
                tier="outputs",
                checksum_sha256=checksum,
                written_at=datetime.now(UTC),
                written_by=WRITTEN_BY,
                as_of_date=package.reporting_date.isoformat(),
                source_reference=package.return_code,
            ),
            content_type="application/pdf",
        )
    except StorageError as exc:
        raise AttestationConflict(
            "signed_pdf_unreadable",
            f"The signed return document could not be archived, so the signature cannot "
            f"be recorded against it: {exc}",
        ) from exc
    version = RegulatoryArtifactVersion(
        organization_id=ctx.organization_id,
        package_id=package.id,
        kind=ARTIFACT_KIND,
        object_path=object_path,
        storage_version_id=stored.version_id,
        checksum_sha256=checksum,
        size_bytes=len(payload),
        created_by=ctx.actor_user_id,
    )
    db.add(version)
    db.flush()
    return version


# --- the signature ----------------------------------------------------------


def _field_name(role: str) -> str:
    return pdf_signing.ROLE_FIELD_NAMES[role]


def _apply(
    base: bytes,
    *,
    role: str,
    signer: Signer,
    appearance: pdf_signing.SignatureAppearance,
    profile: pdf_signing.PadesProfile,
) -> bytes:
    sign = (
        pdf_signing.sign_as_preparer if role == "preparer" else pdf_signing.sign_as_approver
    )
    try:
        return sign(base, signer=signer, appearance=appearance, profile=profile)
    except pdf_signing.PdfSigningError as exc:
        raise AttestationConflict("signed_pdf_failed", str(exc)) from exc


def _profile(
    settings: Settings, timestamper: TimeStamper | None
) -> tuple[pdf_signing.PadesProfile, str]:
    """The strongest PAdES profile this deployment can actually deliver.

    B-LTA is the design's target (§3.2) and needs two things the platform treats
    as optional: an RFC 3161 authority, and trust roots to build the embedded
    validation material against. Where either is missing the profile steps down
    — to B-T, or to a bare signature — and the appearance stops claiming what
    the file does not carry (``PadesProfile.require_deliverable`` enforces that
    agreement, and refuses to sign a page that over-claims).

    This is not a silent downgrade: it is printed on the document face and
    recorded on the audit event. The alternative would be refusing to sign at
    all wherever no TSA is reachable, which would take the filing pipeline down
    over a property that strengthens evidence rather than creating it.
    """
    if timestamper is None:
        return (
            pdf_signing.PadesProfile(
                timestamper=None,
                validation_context=None,
                use_pades_lta=False,
                embed_validation_info=False,
            ),
            "pades_b_b",
        )
    roots = settings.attestation.load_trust_roots()
    if not roots:
        return (
            pdf_signing.PadesProfile(
                timestamper=timestamper,
                validation_context=None,
                use_pades_lta=False,
                embed_validation_info=False,
            ),
            "pades_b_t",
        )
    return (
        pdf_signing.PadesProfile(
            timestamper=timestamper,
            validation_context=_validation_context(roots),
            use_pades_lta=True,
            embed_validation_info=True,
        ),
        "pades_b_lta",
    )


def _validation_context(roots: Sequence[bytes]) -> ValidationContext:
    """A ``ValidationContext`` over the configured roots, for LTV embedding.

    ``allow_fetching`` is ON here and OFF in the verifier, and the asymmetry is
    intentional: embedding validation material is precisely the act of
    collecting OCSP/CRL responses *once*, at signing time, so that verification
    never has to reach the network again (§3.5 check 1).
    """
    anchors = [
        asn1_x509.Certificate.load(certificate.public_bytes(serialization.Encoding.DER))
        for pem in roots
        for certificate in x509.load_pem_x509_certificates(pem)
    ]
    return ValidationContext(trust_roots=anchors, allow_fetching=True)


__all__ = [
    "ARTIFACT_KIND",
    "FIELD_SIGNING_ROLES",
    "sign_package_pdf",
    "signer_unavailable",
]
