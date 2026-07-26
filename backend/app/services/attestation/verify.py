"""Verification — five independent checks, designed with equal rigour to signing.

``docs/attestation_esignature.md §3.5``. The design rule that shapes this whole
module: **a check that can only fail together with another check is not a second
check.** So the five run independently, each reports its own verdict, and a
partial failure stays legible — the report says *which* property broke, not
merely that "verification failed".

The five, and what each one alone can prove:

1. ``pdf_signature`` — the bytes of the filed PDF have not moved since signing,
   and the signer's certificate was valid at the trusted signing time
   (pyHanko + ``pyhanko_certvalidator``).
2. ``inter_signature_tamper`` — nothing beyond form filling happened *between*
   the preparer's revision and the approver's (pyHanko diff analysis). This is
   the direct answer to "how would we know if the figures changed between the
   two signatures".
3. ``detached_attestation`` — the signature record verifies on its own, with no
   PDF at all: the canonical payload recomputes, its digest matches, the
   detached signature verifies against the certificate's public key, and the
   RFC 3161 token covers that same digest inside the certificate's validity
   window. Confirmation register C1 may mean no signed PDF is ever required, so
   this path must stand alone.
4. ``content_binding`` — **the check cryptography cannot do.** A signature proves
   nobody altered *the digest*; it says nothing about whether the rows that
   digest was computed from still hold those values. So the binding is
   recomputed from live state — package digests, the snapshot seal, and each
   source run's ``input_hash`` re-derived from its stored inputs — and compared
   to what was signed. A mismatch here with checks 1–3 green is the precise,
   legible signal that *the stored figures diverged from the signed ones*.
5. ``artifact_binding`` — the stored object still hashes to the checksum the
   signature pinned, resolved at the exact object-store version.

Plus the per-tenant signature hash chain (``workflow.verify_chain``): removal of
a signature is detectable even though DELETE is reachable by deleting the owning
package.

**SKIPPED is not FAILED.** The signing policy may not require a signed PDF (C1),
in which case checks 1, 2 and 5 have nothing to examine. Reporting those as
failures would train operators to ignore red, so they report SKIPPED with the
reason, and ``overall_passed`` ignores them. ``VerificationCheckRead.passed`` is
a bool, so the tri-state lives in ``evidence["status"]`` and in the detail text.

**This module never writes.** Verification that can mutate what it certifies is
not verification: no ``db.add``, no ``db.flush``, no slug assignment, no audit
row. Callers that want the act audited record it themselves.

``pdf_signing``/``signers`` are deliberately NOT imported — a report must be
producible on a deployment with no signing backend and no PDF at all.
"""

from __future__ import annotations

import hashlib
import json
from base64 import b64encode
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from asn1crypto import cms as asn1_cms
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    AttestationSignature,
    Bank,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryRun,
)
from app.services.attestation import digests, workflow
from app.services.attestation.workflow import AttestationConflict
from app.services.regulatory_reporting.generation import snapshot_content_hash
from app.storage.client import StorageClient, StorageError, StorageLocation

CHECK_PDF_SIGNATURE = "pdf_signature"
CHECK_INTER_SIGNATURE_TAMPER = "inter_signature_tamper"
CHECK_DETACHED_ATTESTATION = "detached_attestation"
CHECK_CONTENT_BINDING = "content_binding"
CHECK_ARTIFACT_BINDING = "artifact_binding"

#: The order the report presents them in — cryptographic first, then the
#: value-based checks, so a reader sees "the bytes are intact BUT the figures
#: moved" in that order rather than the reverse.
CHECK_ORDER: tuple[str, ...] = (
    CHECK_PDF_SIGNATURE,
    CHECK_INTER_SIGNATURE_TAMPER,
    CHECK_DETACHED_ATTESTATION,
    CHECK_CONTENT_BINDING,
    CHECK_ARTIFACT_BINDING,
)

type CheckStatus = Literal["passed", "failed", "skipped"]

STATUS_PASSED: CheckStatus = "passed"
STATUS_FAILED: CheckStatus = "failed"
STATUS_SKIPPED: CheckStatus = "skipped"

#: The record export format the offline CLI consumes. Bump alongside any change
#: to the shape ``scripts/verify_attestation.py`` reads.
RECORD_SCHEMA = "aequoros-attestation-record-v1"

#: Detached ``signature_value`` conventions. The port is
#: ``sign_digest(digest: bytes, *, key_ref: str)`` (§3.3), and an HSM signing a
#: pre-computed digest is the ``prehashed`` case. A soft-key backend that passes
#: the 32 digest bytes to ``key.sign(data, ECDSA(SHA256()))`` produces the
#: ``rehashed`` case instead. Both commit to the same payload digest, so the
#: verifier accepts either and RECORDS WHICH ONE matched rather than guessing —
#: a verifier that silently tries one convention would report a false failure
#: against a signature that is in fact sound.
CONVENTION_PREHASHED = "prehashed_sha256"
CONVENTION_REHASHED = "sha256_of_digest"

#: RFC 3161 genTime versus the recorded ``tsa_time``: same instant, but a
#: database round-trip may truncate sub-second precision.
TSA_TIME_TOLERANCE = timedelta(seconds=1)

_PDF_KIND = "pdf"


class VerificationError(RuntimeError):
    """The verifier itself could not run (not: a check failed).

    Distinct from a failed check on purpose. "The figures diverged" is a
    finding; "the verifier crashed" is an outage, and conflating them would let
    an outage read as a clean bill of health.
    """


@dataclass(frozen=True)
class CheckResult:
    """One of the five checks (docs §3.5), with its own verdict and evidence."""

    check: str
    status: CheckStatus
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == STATUS_FAILED

    def as_dict(self) -> dict[str, Any]:
        """The ``VerificationCheckRead`` shape.

        ``passed`` is False only for a genuine failure; the tri-state survives
        in ``evidence["status"]`` because the response model is closed and a
        skipped check must not read as a failure.
        """
        return {
            "check": self.check,
            "passed": not self.failed,
            "detail": self.detail,
            "evidence": {"status": self.status, **self.evidence},
        }


def _passed(check: str, detail: str, **evidence: Any) -> CheckResult:
    return CheckResult(check=check, status=STATUS_PASSED, detail=detail, evidence=dict(evidence))


def _failed(check: str, detail: str, **evidence: Any) -> CheckResult:
    return CheckResult(check=check, status=STATUS_FAILED, detail=detail, evidence=dict(evidence))


def _skipped(check: str, reason: str, **evidence: Any) -> CheckResult:
    return CheckResult(
        check=check,
        status=STATUS_SKIPPED,
        detail=f"Skipped: {reason}",
        evidence=dict(evidence),
    )


# --- canonicalisation the engines own --------------------------------------


def engine_snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    """Re-derive a ``RegulatoryRun.input_hash`` from its stored inputs.

    Byte-identical to ``_snapshot_hash`` in ``regulatory_capital.py`` and its six
    peers (``calculations``, ``regulatory_liquidity``, ``regulatory_forecasting``,
    ``regulatory_irr``, ``regulatory_fx``, ``regulatory_ftp`` — all seven are the
    same three lines). Reproduced rather than imported because importing one
    engine's private helper would make the verifier depend on that engine
    staying the canonical one; if the recipe ever diverges between engines, this
    check is where it must be caught, not quietly inherited.
    """
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    """Naive timestamps mean UTC.

    SQLite drops the offset on ``DateTime(timezone=True)`` round-trips, so a
    naive value here is a storage artefact, never a local-time claim. Treating
    it as host-local would reintroduce exactly the unmonitored wall clock the
    TSA exists to replace.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def signature_read(signature: AttestationSignature) -> dict[str, Any]:
    """The ``SignatureRead`` shape.

    Duplicated from ``attestation_api._signature_read`` deliberately: this
    package is the domain core and importing the HTTP seam would invert the
    layering documented in ``attestation/__init__.py``.
    """
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


# --- certificates -----------------------------------------------------------


def _load_certificate(pem: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(pem.encode("utf-8"))
    except ValueError as exc:
        raise VerificationError(
            f"Stored certificate_pem is not a readable certificate: {exc}"
        ) from exc


def _load_certificates(pem: str | None) -> list[x509.Certificate]:
    if not pem:
        return []
    try:
        return list(x509.load_pem_x509_certificates(pem.encode("utf-8")))
    except ValueError:
        # A chain we cannot parse is reported by the check that needs it rather
        # than aborting the whole report — the other four checks are unaffected.
        return []


def parse_trust_roots(trust_roots: Sequence[bytes] | None) -> list[x509.Certificate]:
    """Parse configured trust anchors from PEM or DER bytes."""
    roots: list[x509.Certificate] = []
    for blob in trust_roots or ():
        if b"-----BEGIN" in blob:
            roots.extend(x509.load_pem_x509_certificates(blob))
        else:
            roots.append(x509.load_der_x509_certificate(blob))
    return roots


def _asn1(cert: x509.Certificate) -> asn1_x509.Certificate:
    """pyHanko speaks asn1crypto; ``cryptography`` is our parser everywhere else."""
    return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))


def _verify_chain_links(
    leaf: x509.Certificate,
    chain: Sequence[x509.Certificate],
    roots: Sequence[x509.Certificate],
) -> tuple[bool, list[str], dict[str, Any]]:
    """Build leaf → … → anchor and verify each issuer signature.

    Scope stated honestly: this is chain *building and signature* verification
    with ``cryptography`` (§3.3), not full RFC 5280 path validation — no name
    constraints, no policy tree, no revocation. Revocation-aware validation of
    the PDF signature is check 1's job (``pyhanko_certvalidator`` with the LTV
    material embedded in the file); here the point is that the certificate the
    record carries really was issued by the authority it names.
    """
    problems: list[str] = []
    evidence: dict[str, Any] = {
        "subject": leaf.subject.rfc4514_string(),
        "issuer": leaf.issuer.rfc4514_string(),
        "chain_length": len(chain),
        "trust_anchored": False,
    }
    current = leaf
    for issuer in chain:
        try:
            current.verify_directly_issued_by(issuer)
        except (ValueError, TypeError, InvalidSignature) as exc:
            problems.append(
                f"certificate {current.subject.rfc4514_string()!r} is not validly issued by "
                f"{issuer.subject.rfc4514_string()!r}: {exc}"
            )
            return False, problems, evidence
        current = issuer

    if not roots:
        # No configured anchor: say so rather than implying third-party trust.
        # An unanchored chain still proves issuance; it does not prove the
        # issuer is one an examiner accepts.
        evidence["trust_anchor"] = "none_configured"
        return True, problems, evidence

    evidence["trust_anchor"] = "configured"
    for root in roots:
        if current.fingerprint(hashes.SHA256()) == root.fingerprint(hashes.SHA256()):
            evidence["trust_anchored"] = True
            return True, problems, evidence
        try:
            current.verify_directly_issued_by(root)
        except (ValueError, TypeError, InvalidSignature):
            continue
        evidence["trust_anchored"] = True
        return True, problems, evidence

    problems.append(
        f"the certificate chain terminates at {current.subject.rfc4514_string()!r}, which "
        "is not one of the configured trust roots and is not issued by one"
    )
    return False, problems, evidence


# --- detached signature verification ---------------------------------------


def _verify_ecdsa(
    public_key: ec.EllipticCurvePublicKey, value: bytes, digest: bytes
) -> tuple[bool, str | None, str]:
    for convention, algorithm in (
        (CONVENTION_PREHASHED, ec.ECDSA(Prehashed(hashes.SHA256()))),
        (CONVENTION_REHASHED, ec.ECDSA(hashes.SHA256())),
    ):
        try:
            public_key.verify(value, digest, algorithm)
        except (InvalidSignature, UnsupportedAlgorithm, ValueError):
            continue
        return True, convention, "ECDSA P-256/SHA-256 signature verifies."
    return False, None, "the ECDSA signature does not verify against this certificate."


def _verify_rsa_pss(
    public_key: rsa.RSAPublicKey, value: bytes, digest: bytes
) -> tuple[bool, str | None, str]:
    # PSS.AUTO recovers whatever salt length the signer used; pinning one would
    # reject a sound signature from an HSM whose default differs from ours.
    pss = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO)
    for convention, algorithm in (
        (CONVENTION_PREHASHED, Prehashed(hashes.SHA256())),
        (CONVENTION_REHASHED, hashes.SHA256()),
    ):
        try:
            public_key.verify(value, digest, pss, algorithm)
        except (InvalidSignature, UnsupportedAlgorithm, ValueError):
            continue
        return True, convention, "RSA-PSS/SHA-256 signature verifies."
    return False, None, "the RSA-PSS signature does not verify against this certificate."


def _verify_detached_value(
    signature: AttestationSignature, certificate: x509.Certificate
) -> tuple[bool, str | None, str]:
    """``(verified, convention, detail)`` for the stored ``signature_value``.

    ``signature_method`` is on the record precisely so verification never has to
    guess the algorithm (§3.3).
    """
    digest = bytes.fromhex(signature.payload_digest)
    public_key = certificate.public_key()
    method = signature.signature_method
    value = bytes(signature.signature_value)

    if method == "detached_ecdsa_p256_sha256":
        if isinstance(public_key, ec.EllipticCurvePublicKey):
            return _verify_ecdsa(public_key, value, digest)
    elif method == "detached_rsa_pss_sha256":
        if isinstance(public_key, rsa.RSAPublicKey):
            return _verify_rsa_pss(public_key, value, digest)
    else:
        # A PAdES method means the cryptographic proof lives inside the PDF's CMS
        # blob, which check 1 validates in full. Re-parsing it here would
        # duplicate pyHanko badly rather than add an independent check.
        return False, None, (
            f"signature_method {method!r} carries its cryptography inside the PDF; "
            "the detached value cannot be verified standalone (see the pdf_signature check)"
        )
    return False, None, (
        f"signature_method is {method!r} but the certificate carries a "
        f"{type(public_key).__name__} public key"
    )


def _verify_timestamp(
    signature: AttestationSignature, certificate: x509.Certificate
) -> tuple[bool, list[str], dict[str, Any]]:
    """RFC 3161: does the token cover THIS digest, at a time the cert was valid?

    pyHanko's response handling checks PKI status and nonce at signing time; it
    does not check the imprint, so a token obtained over other data would
    otherwise sit in the record looking like evidence about this return.
    """
    problems: list[str] = []
    evidence: dict[str, Any] = {}
    token_der = signature.tsa_token
    if not token_der:
        # Trusted time is opt-in (TSA_URL); its absence is a known weakness of
        # the record, not a divergence. Long-term validity after certificate
        # expiry/revocation is what is missing, and the report says so.
        evidence["trusted_time"] = "absent"
        return True, problems, evidence

    try:
        # asn1crypto's static types are Void until the structure is parsed, so
        # the walk is deliberately dynamic and guarded by the except below.
        content: Any = asn1_cms.ContentInfo.load(bytes(token_der))
        encap = content["content"]["encap_content_info"]
        if encap["content_type"].native != "tst_info":
            problems.append(
                f"the timestamp token encapsulates {encap['content_type'].native!r}, not tst_info"
            )
            return False, problems, evidence
        tst_info: Any = encap["content"].parsed  # asn1_tsp.TSTInfo
        imprint = tst_info["message_imprint"]
        token_digest = bytes(imprint["hashed_message"].native)
        gen_time = tst_info["gen_time"].native
    except Exception as exc:  # asn1crypto raises a wide family on malformed DER
        problems.append(f"the timestamp token could not be parsed: {exc}")
        return False, problems, evidence

    evidence["trusted_time"] = "present"
    evidence["tsa_url"] = signature.tsa_url
    if token_digest != bytes.fromhex(signature.payload_digest):
        problems.append(
            "the timestamp token does not cover this signature's payload digest — it is "
            "evidence about other data and must not be read as trusted time for this return"
        )

    asserted = _as_utc(gen_time if isinstance(gen_time, datetime) else None)
    recorded = _as_utc(signature.tsa_time)
    if asserted is None:
        problems.append("the timestamp token carries no readable genTime")
    else:
        evidence["token_gen_time"] = asserted.isoformat()
        if recorded is None:
            problems.append("a timestamp token is stored but tsa_time was never recorded")
        elif abs(asserted - recorded) > TSA_TIME_TOLERANCE:
            problems.append(
                f"the token asserts {asserted.isoformat()} but the record says "
                f"{recorded.isoformat()}"
            )
        not_before = _as_utc(certificate.not_valid_before_utc)
        not_after = _as_utc(certificate.not_valid_after_utc)
        if not_before is not None and not_after is not None:
            evidence["certificate_validity"] = [not_before.isoformat(), not_after.isoformat()]
            if not not_before <= asserted <= not_after:
                problems.append(
                    f"the trusted signing time {asserted.isoformat()} falls outside the "
                    f"certificate validity window "
                    f"[{not_before.isoformat()}, {not_after.isoformat()}]"
                )
    return not problems, problems, evidence


# --- artifacts --------------------------------------------------------------


@dataclass(frozen=True)
class _ArtifactEvidence:
    """A pinned artifact version plus the bytes we could actually resolve."""

    signature_id: UUID
    version: RegulatoryArtifactVersion
    payload: bytes | None
    error: str | None


def _bank_storage_slug(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> str | None:
    """The bank's storage slug, read only.

    ``ingestion.bank_slug`` would ASSIGN and flush one if absent; a verifier
    must not write. A package with artifact versions always has a slug already,
    because the export that created them needed it.
    """
    return db.scalar(
        select(Bank.storage_slug).where(
            Bank.id == package.bank_id,
            Bank.organization_id == ctx.organization_id,
        )
    )


def _collect_artifacts(  # noqa: PLR0913 - one lookup with five distinct inputs
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    signatures: Sequence[AttestationSignature],
    storage: StorageClient | None,
    *,
    load_bytes: bool = True,
) -> list[_ArtifactEvidence]:
    """Resolve every artifact version a current signature pinned.

    ``load_bytes=False`` resolves metadata only — the offline export needs the
    checksums and object versions, not megabytes of PDF it would discard.
    """
    pinned = [s for s in signatures if s.artifact_version_id is not None]
    if not pinned:
        return []

    rows = {
        row.id: row
        for row in db.scalars(
            select(RegulatoryArtifactVersion).where(
                RegulatoryArtifactVersion.organization_id == ctx.organization_id,
                RegulatoryArtifactVersion.package_id == package.id,
                RegulatoryArtifactVersion.id.in_([s.artifact_version_id for s in pinned]),
            )
        )
    }
    slug = _bank_storage_slug(db, ctx, package) if load_bytes else None
    client = storage
    if client is None and load_bytes:
        # Lazy: a report must be producible where object storage is not wired.
        try:
            from app.storage.factory import get_storage_client  # noqa: PLC0415

            client = get_storage_client()
        except Exception as exc:  # pragma: no cover - deployment configuration
            logger.warning("attestation verification could not obtain a storage client: {}", exc)
            client = None

    collected: list[_ArtifactEvidence] = []
    cache: dict[tuple[str, str | None], tuple[bytes | None, str | None]] = {}
    for signature in pinned:
        pinned_id = signature.artifact_version_id
        version = rows.get(pinned_id) if pinned_id is not None else None
        if version is None:
            continue
        payload, error = (
            _read_once(cache, client, slug, version) if load_bytes else (None, "not requested")
        )
        collected.append(
            _ArtifactEvidence(
                signature_id=signature.id, version=version, payload=payload, error=error
            )
        )
    return collected


def _read_once(
    cache: dict[tuple[str, str | None], tuple[bytes | None, str | None]],
    client: StorageClient | None,
    slug: str | None,
    version: RegulatoryArtifactVersion,
) -> tuple[bytes | None, str | None]:
    """Read the pinned object version once per (path, version) pair."""
    key = (version.object_path, version.storage_version_id)
    if key in cache:
        return cache[key]
    if client is None:
        result: tuple[bytes | None, str | None] = (None, "no object storage client is available")
    elif slug is None:
        result = (None, "the institution has no storage slug, so no object can be resolved")
    else:
        location = StorageLocation(
            institution_slug=slug, tier="outputs", object_path=version.object_path
        )
        try:
            # The pinned version_id is the point of G2: "the artifact as filed"
            # must resolve to those exact bytes, not to whatever is current.
            _descriptor, stream = client.read(location, version_id=version.storage_version_id)
            result = (stream.read(), None)
        except StorageError as exc:
            result = (None, f"the stored object could not be read: {exc}")
    cache[key] = result
    return result


def _pdf_artifacts(artifacts: Sequence[_ArtifactEvidence]) -> list[_ArtifactEvidence]:
    """Distinct PDF versions, one entry per version row."""
    seen: set[UUID] = set()
    unique: list[_ArtifactEvidence] = []
    for artifact in artifacts:
        if artifact.version.kind != _PDF_KIND or artifact.version.id in seen:
            continue
        seen.add(artifact.version.id)
        unique.append(artifact)
    return unique


# --- check 1: PDF cryptographic validity ------------------------------------


def _validation_context(
    signature: AttestationSignature | None, roots: Sequence[x509.Certificate]
) -> Any:
    """A ``ValidationContext`` pinned to configured roots, with no live fetching.

    Revocation material must come from the LTV data embedded in the file (§3.5
    check 1): a verifier that reaches out to the internet gives a different
    answer on different days, which is the opposite of evidence.
    """
    from pyhanko_certvalidator import ValidationContext  # noqa: PLC0415 - heavy import

    anchors = list(roots)
    others: list[asn1_x509.Certificate] = []
    moment: datetime | None = None
    if signature is not None:
        leaf = _load_certificate(signature.certificate_pem)
        chain = _load_certificates(signature.certificate_chain_pem)
        others = [_asn1(cert) for cert in (leaf, *chain)]
        if not anchors:
            # Fall back to the chain the record carries. This proves issuance,
            # not third-party trust — reported as trust_anchor="embedded_chain".
            anchors = [chain[-1]] if chain else [leaf]
        moment = _as_utc(signature.tsa_time) or _as_utc(signature.declared_at)
    return ValidationContext(
        trust_roots=[_asn1(cert) for cert in anchors],
        other_certs=others,
        moment=moment,
        allow_fetching=False,
        revocation_mode="soft-fail",
    )


def attestation_diff_policy() -> Any:
    """pyHanko's revision-diff policy, minus one allowance that convicts us falsely.

    ``allow_in_place_appearance_stream_changes`` exists for producers that
    overwrite a form field's appearance stream in place. ``pdf_signing`` never
    does — every filled value gets a freshly allocated stream object — so the
    allowance can only ever produce a false positive here, and it does: the rule
    skips a field only when its appearance stream's last change is *equal to*
    the revision being diffed from, rather than *not later than* it. Every field
    the preparer filled is therefore reported as an in-place update the moment a
    revision exists after the approver's signature — and PAdES B-LTA always
    appends one (the DSS, then the document timestamp). A fully signed return
    with placed name/designation/date fields reported ``docmdp_ok = False``
    against a document nobody had touched.

    Switching the allowance OFF is strictly stricter than leaving it on: a real
    in-place appearance rewrite is now refused rather than whitelisted. The rest
    of pyHanko's default policy is reused as-is rather than restated, so an
    upgrade that tightens a global rule tightens this too.
    """
    from pyhanko.sign.diff_analysis import (  # noqa: PLC0415
        DEFAULT_DIFF_POLICY,
        FormUpdatingRule,
        GenericFieldModificationRule,
        SigFieldCreationRule,
        SigFieldModificationRule,
        StandardDiffPolicy,
    )

    return StandardDiffPolicy(
        global_rules=DEFAULT_DIFF_POLICY.global_rules,
        form_rule=FormUpdatingRule(
            field_rules=[
                SigFieldCreationRule(),
                SigFieldModificationRule(allow_in_place_appearance_stream_changes=False),
                GenericFieldModificationRule(allow_in_place_appearance_stream_changes=False),
            ],
        ),
    )


def _embedded_statuses(payload: bytes, context: Any) -> list[tuple[str, Any]]:
    """``[(field_name, PdfSignatureStatus)]`` for every signature in the file."""
    import io  # noqa: PLC0415

    from pyhanko.pdf_utils.reader import PdfFileReader  # noqa: PLC0415
    from pyhanko.sign.validation import validate_pdf_signature  # noqa: PLC0415

    reader = PdfFileReader(io.BytesIO(payload))
    policy = attestation_diff_policy()
    return [
        (
            embedded.field_name,
            validate_pdf_signature(
                embedded, signer_validation_context=context, diff_policy=policy
            ),
        )
        for embedded in reader.embedded_signatures
    ]


def _check_pdf_signature(
    signatures: Sequence[AttestationSignature],
    artifacts: Sequence[_ArtifactEvidence],
    roots: Sequence[x509.Certificate],
) -> CheckResult:
    pdfs = _pdf_artifacts(artifacts)
    if not pdfs:
        return _skipped(
            CHECK_PDF_SIGNATURE,
            "no signature pinned a signed PDF artifact — the signing policy in force may "
            "not require one (confirmation register C1), in which case the detached "
            "attestation is the whole cryptographic record",
            signed_pdf_versions=0,
        )

    by_id = {s.id: s for s in signatures}
    problems: list[str] = []
    unreadable: list[str] = []
    findings: list[dict[str, Any]] = []
    for artifact in pdfs:
        if artifact.payload is None:
            # Collected, not returned: a real failure on another artifact must
            # never be masked by a skip. Resolved after the loop.
            unreadable.append(f"artifact version {artifact.version.id} ({artifact.error})")
            continue
        context = _validation_context(by_id.get(artifact.signature_id), roots)
        try:
            statuses = _embedded_statuses(artifact.payload, context)
        except Exception as exc:
            problems.append(f"artifact version {artifact.version.id} could not be read as a PDF")
            findings.append({"artifact_version_id": str(artifact.version.id), "error": str(exc)})
            continue
        if not statuses:
            problems.append(
                f"artifact version {artifact.version.id} carries NO embedded signature, "
                "yet a signature record pins it as the signed PDF"
            )
        for field_name, status in statuses:
            finding = {
                "artifact_version_id": str(artifact.version.id),
                "field": field_name,
                "intact": bool(status.intact),
                "valid": bool(status.valid),
                "trusted": bool(status.trusted),
                "docmdp_ok": status.docmdp_ok,
                "mechanism": status.pkcs7_signature_mechanism,
                "trust_anchor": "configured" if roots else "embedded_chain",
            }
            findings.append(finding)
            if not status.intact:
                problems.append(
                    f"{field_name}: the signed byte range no longer hashes to the value in "
                    "the signature — the file has been altered"
                )
            if not status.valid:
                problems.append(f"{field_name}: the signature does not validate cryptographically")
            if status.docmdp_ok is False:
                problems.append(
                    f"{field_name}: a modification violated the document's DocMDP permissions"
                )
            # Only complain about trust when the signature is otherwise sound:
            # pyHanko reports an altered signature as untrusted too, and
            # repeating that as a PKI problem buries the actual cause.
            if roots and status.intact and status.valid and not status.trusted:
                problems.append(
                    f"{field_name}: the signer's certificate does not chain to a configured "
                    f"trust root at the signing time ({status.summary()})"
                )

    if problems:
        return _failed(
            CHECK_PDF_SIGNATURE,
            "The filed PDF does not validate: " + "; ".join(problems) + ".",
            signatures_examined=findings,
        )
    if unreadable:
        return _skipped(
            CHECK_PDF_SIGNATURE,
            "the signed PDF could not be retrieved for "
            + "; ".join(unreadable)
            + ". Unreadable bytes are an availability problem, not evidence of an invalid "
            "signature — but nothing was examined, so this is not a pass either",
            signatures_examined=findings,
        )
    return _passed(
        CHECK_PDF_SIGNATURE,
        f"Every embedded signature in {len(pdfs)} signed PDF version(s) is intact and "
        "cryptographically valid"
        + (
            " against the configured trust roots."
            if roots
            else "; trust was anchored on the certificate chain carried in the signature "
            "record, because no platform trust roots were supplied to the verifier."
        ),
        signatures_examined=findings,
    )


# --- check 2: tamper between signatures ------------------------------------


def _check_inter_signature_tamper(
    signatures: Sequence[AttestationSignature],
    artifacts: Sequence[_ArtifactEvidence],
    roots: Sequence[x509.Certificate],
) -> CheckResult:
    """pyHanko diff analysis across the incremental revisions.

    The assertion set (§3.5 check 2): every revision boundary introduces at most
    ``FORM_FILLING``; every signature but the last covers ``ENTIRE_REVISION``;
    the last covers ``ENTIRE_FILE``. Anything else — a redrawn table, an edited
    number, an appended page — is tampering, and the offending object is
    reported.
    """
    pdfs = _pdf_artifacts(artifacts)
    if not pdfs:
        return _skipped(
            CHECK_INTER_SIGNATURE_TAMPER,
            "no signature pinned a signed PDF artifact, so there are no incremental "
            "revisions to compare (the content_binding check is what proves the figures "
            "did not move in this case)",
            signed_pdf_versions=0,
        )

    from pyhanko.sign.diff_analysis import ModificationLevel  # noqa: PLC0415
    from pyhanko.sign.validation import SignatureCoverageLevel  # noqa: PLC0415

    by_id = {s.id: s for s in signatures}
    problems: list[str] = []
    unreadable: list[str] = []
    revisions: list[dict[str, Any]] = []
    for artifact in pdfs:
        if artifact.payload is None:
            # Same rule as check 1: never mask a failure with a skip.
            unreadable.append(f"artifact version {artifact.version.id} ({artifact.error})")
            continue
        try:
            statuses = _embedded_statuses(
                artifact.payload, _validation_context(by_id.get(artifact.signature_id), roots)
            )
        except Exception as exc:
            problems.append(
                f"artifact version {artifact.version.id} could not be analysed: {exc}"
            )
            continue

        last_index = len(statuses) - 1
        for index, (field_name, status) in enumerate(statuses):
            diff = status.diff_result
            level = status.modification_level
            expected_coverage = (
                SignatureCoverageLevel.ENTIRE_FILE
                if index == last_index
                else SignatureCoverageLevel.ENTIRE_REVISION
            )
            revisions.append(
                {
                    "artifact_version_id": str(artifact.version.id),
                    "field": field_name,
                    "position": index + 1,
                    "of": len(statuses),
                    "coverage": str(status.coverage),
                    "expected_coverage": str(expected_coverage),
                    "modification_level": str(level),
                    "diff": None if diff is None else str(diff),
                }
            )
            if level is not None and level > ModificationLevel.FORM_FILLING:
                # str(SuspiciousModification) names the offending object(s) —
                # "which number changed" is the question this check exists for.
                problems.append(
                    f"{field_name}: modifications after this signature reach {level} "
                    f"(the maximum permitted between signers is "
                    f"{ModificationLevel.FORM_FILLING}); offending change: {diff}"
                )
            if status.coverage != expected_coverage:
                problems.append(
                    f"{field_name} is signature {index + 1} of {len(statuses)} and must cover "
                    f"{expected_coverage}, but covers {status.coverage}"
                )

    if problems:
        return _failed(
            CHECK_INTER_SIGNATURE_TAMPER,
            "The document changed between signatures beyond what the policy permits: "
            + "; ".join(problems)
            + ".",
            revisions=revisions,
        )
    if unreadable:
        return _skipped(
            CHECK_INTER_SIGNATURE_TAMPER,
            "the signed PDF could not be retrieved for " + "; ".join(unreadable),
            revisions=revisions,
        )
    return _passed(
        CHECK_INTER_SIGNATURE_TAMPER,
        "Every incremental revision between signers introduced at most form filling, and "
        "each signature covers the revision expected of its position.",
        revisions=revisions,
    )


# --- check 3: detached attestation ----------------------------------------


def _recomputed_payload(signature: AttestationSignature) -> dict[str, Any]:
    declared_at = _as_utc(signature.declared_at)
    return digests.attestation_payload(
        certification_digest_value=signature.certification_digest,
        signer_id=signature.signer_id,
        signing_role=signature.signing_role,
        officer_title=signature.officer_title,
        statement=signature.statement,
        declared_at="" if declared_at is None else declared_at.isoformat(),
    )


def _payload_differences(stored: Mapping[str, Any], recomputed: Mapping[str, Any]) -> list[str]:
    """Keys where the stored payload disagrees with one rebuilt from the row.

    ``declared_at`` is compared as an instant, not as text: the payload was
    built from an aware datetime, and SQLite hands the column back naive, so a
    string comparison would report a difference that does not exist.
    """
    differences: list[str] = []
    for key in sorted(set(stored) | set(recomputed)):
        left, right = stored.get(key), recomputed.get(key)
        if key == "declared_at":
            if _parse_instant(left) != _parse_instant(right):
                differences.append(f"{key} ({left!r} vs {right!r})")
            continue
        if left != right:
            differences.append(f"{key} ({left!r} vs {right!r})")
    return differences


def _parse_instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _check_detached_attestation(
    signatures: Sequence[AttestationSignature], roots: Sequence[x509.Certificate]
) -> CheckResult:
    if not signatures:
        return _skipped(
            CHECK_DETACHED_ATTESTATION,
            "this package carries no signatures in its current attestation cycle",
            signatures_examined=0,
        )

    problems: list[str] = []
    findings: list[dict[str, Any]] = []
    for signature in signatures:
        label = f"{signature.signing_role} signature {signature.id}"
        finding: dict[str, Any] = {
            "signature_id": str(signature.id),
            "signing_role": signature.signing_role,
            "signer_id": signature.signer_id,
            "signature_method": signature.signature_method,
        }
        findings.append(finding)

        stored_payload = dict(signature.attestation_payload or {})
        differences = _payload_differences(stored_payload, _recomputed_payload(signature))
        finding["payload_recomputes"] = not differences
        if differences:
            problems.append(
                f"{label}: the stored attestation payload disagrees with one rebuilt from the "
                f"record's own fields — {', '.join(differences)}"
            )

        recomputed_digest = digests.digest_of(stored_payload)
        finding["payload_digest_matches"] = recomputed_digest == signature.payload_digest
        if recomputed_digest != signature.payload_digest:
            problems.append(
                f"{label}: payload_digest is {signature.payload_digest[:12]}… but the stored "
                f"payload canonicalises to {recomputed_digest[:12]}…"
            )
        if stored_payload.get("certification_digest") != signature.certification_digest:
            problems.append(
                f"{label}: the signed payload commits to certification digest "
                f"{stored_payload.get('certification_digest')!r}, but the record claims "
                f"{signature.certification_digest!r}"
            )

        try:
            certificate = _load_certificate(signature.certificate_pem)
        except VerificationError as exc:
            problems.append(f"{label}: {exc}")
            continue

        fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
        finding["certificate_sha256_matches"] = fingerprint == signature.certificate_sha256
        if fingerprint != signature.certificate_sha256:
            problems.append(
                f"{label}: certificate_sha256 does not match the stored certificate "
                f"(recorded {signature.certificate_sha256[:12]}…, computed {fingerprint[:12]}…)"
            )

        verified, convention, detail = _verify_detached_value(signature, certificate)
        finding["signature_verifies"] = verified
        finding["digest_convention"] = convention
        if not verified:
            problems.append(f"{label}: {detail}")

        chain_ok, chain_problems, chain_evidence = _verify_chain_links(
            certificate, _load_certificates(signature.certificate_chain_pem), roots
        )
        finding["certificate"] = chain_evidence
        if not chain_ok:
            problems.extend(f"{label}: {problem}" for problem in chain_problems)

        time_ok, time_problems, time_evidence = _verify_timestamp(signature, certificate)
        finding["timestamp"] = time_evidence
        if not time_ok:
            problems.extend(f"{label}: {problem}" for problem in time_problems)

    if problems:
        return _failed(
            CHECK_DETACHED_ATTESTATION,
            "The detached attestation does not verify: " + "; ".join(problems) + ".",
            signatures=findings,
        )
    untimestamped = sum(1 for s in signatures if not s.tsa_token)
    detail = (
        f"All {len(signatures)} detached signature(s) verify against their stored "
        "certificates, over payloads that recompute from the records themselves."
    )
    if untimestamped:
        detail += (
            f" {untimestamped} of them carries no RFC 3161 token, so its validity cannot be "
            "shown to predate certificate expiry or revocation (trusted time is configured "
            "by TSA_URL)."
        )
    return _passed(CHECK_DETACHED_ATTESTATION, detail, signatures=findings)


# --- check 4: content binding (what cryptography cannot do) ----------------


def _check_content_binding(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    signatures: Sequence[AttestationSignature],
) -> CheckResult:
    problems: list[str] = []
    evidence: dict[str, Any] = {}

    try:
        binding = workflow.compute_binding(package)
    except (AttestationConflict, ValueError) as exc:
        return _failed(
            CHECK_CONTENT_BINDING,
            "The package's certification digest can no longer be recomputed from its own "
            f"persisted state, so nothing can be compared to what was signed: {exc}",
            recomputable=False,
        )

    evidence["recomputed_certification_digest"] = binding.certification_digest
    evidence["recomputed_content_digest"] = binding.content_digest
    evidence["frozen_certification_digest"] = package.certification_digest
    evidence["binding_class"] = binding.binding_class

    frozen = package.certification_digest
    if frozen and frozen != binding.certification_digest:
        problems.append(
            f"the digest frozen on the package ({frozen[:12]}…) is not "
            f"what its current data recomputes to ({binding.certification_digest[:12]}…)"
        )

    signed_digests = {s.certification_digest for s in signatures}
    evidence["signed_certification_digests"] = sorted(signed_digests)
    if len(signed_digests) > 1:
        problems.append(
            "the signers did not all sign the same figures — the current cycle carries "
            f"{len(signed_digests)} distinct certification digests"
        )
    for signature in signatures:
        label = f"{signature.signing_role} signature {signature.id}"
        if signature.certification_digest != binding.certification_digest:
            problems.append(
                f"{label} certified {signature.certification_digest[:12]}… but the stored "
                f"figures now recompute to {binding.certification_digest[:12]}…"
            )
        if signature.content_digest != binding.content_digest:
            problems.append(
                f"{label} sealed content digest {signature.content_digest[:12]}…, current "
                f"{binding.content_digest[:12]}…"
            )
        if signature.register_state_digest != binding.register_state_digest:
            problems.append(
                f"{label} sealed register-state digest {signature.register_state_digest!r}, "
                f"current {binding.register_state_digest!r}"
            )
        # The version seal AS SIGNED. Independent of the recompute below: this
        # catches a snapshot that was re-sealed self-consistently, because the
        # signature remembers the seal value that stood at signing.
        if (
            signature.snapshot_sha256 is not None
            and signature.snapshot_sha256 != package.snapshot_sha256
        ):
            problems.append(
                f"{label} recorded snapshot seal {signature.snapshot_sha256[:12]}… but the "
                f"package now carries {str(package.snapshot_sha256)[:12]}…"
            )
        if digests.canonical_json(
            sorted(list(signature.signed_source_runs or []), key=digests.canonical_json)
        ) != digests.canonical_json(sorted(binding.source_runs, key=digests.canonical_json)):
            problems.append(
                f"{label} bound a different set of source runs than the package now carries"
            )

    problems.extend(_check_snapshot_seal(package, evidence))
    problems.extend(_check_source_run_hashes(db, ctx, package, evidence))

    if problems:
        return _failed(
            CHECK_CONTENT_BINDING,
            "The stored figures have diverged from the signed ones: " + "; ".join(problems) + ". "
            "This is provable independently of any PDF: nothing about a valid signature "
            "prevents the underlying rows from changing, which is exactly why this check "
            "exists.",
            **evidence,
        )
    return _passed(
        CHECK_CONTENT_BINDING,
        "The package's certification digest, content digest, snapshot seal and every source "
        "run's input hash all recompute to the values that were signed.",
        **evidence,
    )


def _check_snapshot_seal(package: RegulatoryPackage, evidence: dict[str, Any]) -> list[str]:
    """Recompute ``snapshot_sha256`` — the version seal set at generation (G3)."""
    stored = package.snapshot_sha256
    if stored is None:
        # Pre-seal rows predate the column. Absence cannot be a mismatch, and
        # calling it one would make every historical package fail forever.
        evidence["snapshot_seal"] = "absent"
        return []
    recomputed = snapshot_content_hash(dict(package.snapshot or {}))
    evidence["snapshot_seal"] = "verified" if recomputed == stored else "mismatch"
    evidence["recomputed_snapshot_sha256"] = recomputed
    if recomputed == stored:
        return []
    return [
        f"the immutable snapshot no longer matches its generation seal (sealed "
        f"{stored[:12]}…, computed {recomputed[:12]}…)"
    ]


def _check_source_run_hashes(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, evidence: dict[str, Any]
) -> list[str]:
    """Re-derive each engine run's ``input_hash`` from its stored inputs.

    Three values must agree: the hash the package pinned in ``source_runs``, the
    hash stored on the run, and the hash its ``inputs`` actually produce. Any
    two agreeing while the third differs localises the divergence exactly.
    """
    entries = list(package.source_runs or [])
    problems: list[str] = []
    findings: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            problems.append(f"source_runs carries a non-object entry: {entry!r}")
            continue
        raw_id = str(entry.get("run_id", ""))
        pinned_hash = entry.get("input_hash")
        try:
            run_id = UUID(raw_id)
        except ValueError:
            problems.append(f"source run id {raw_id!r} is not a UUID")
            continue
        run = db.scalar(
            select(RegulatoryRun).where(
                RegulatoryRun.id == run_id,
                RegulatoryRun.organization_id == ctx.organization_id,
                RegulatoryRun.bank_id == package.bank_id,
            )
        )
        if run is None:
            problems.append(
                f"the {entry.get('module')} run {raw_id} that this return was signed against "
                "no longer exists"
            )
            findings.append({"run_id": raw_id, "module": entry.get("module"), "present": False})
            continue
        recomputed = engine_snapshot_hash(dict(run.inputs or {}))
        findings.append(
            {
                "run_id": raw_id,
                "module": run.module,
                "present": True,
                "pinned_input_hash": pinned_hash,
                "stored_input_hash": run.input_hash,
                "recomputed_input_hash": recomputed,
            }
        )
        if pinned_hash != run.input_hash:
            problems.append(
                f"the package pinned input hash {str(pinned_hash)[:12]}… for {run.module} run "
                f"{raw_id}, but that run now records {run.input_hash[:12]}…"
            )
        if recomputed != run.input_hash:
            problems.append(
                f"{run.module} run {raw_id} records input hash {run.input_hash[:12]}… but its "
                f"stored inputs hash to {recomputed[:12]}… — the immutable run inputs changed"
            )
    evidence["source_runs"] = findings
    return problems


# --- check 5: artifact binding --------------------------------------------


def _check_artifact_binding(artifacts: Sequence[_ArtifactEvidence]) -> CheckResult:
    if not artifacts:
        return _skipped(
            CHECK_ARTIFACT_BINDING,
            "no signature pinned an artifact version, so there are no filed bytes to "
            "re-hash (confirmation register C1: a signed artifact may not be required)",
            artifacts_examined=0,
        )

    problems: list[str] = []
    findings: list[dict[str, Any]] = []
    for artifact in artifacts:
        version = artifact.version
        finding: dict[str, Any] = {
            "signature_id": str(artifact.signature_id),
            "artifact_version_id": str(version.id),
            "kind": version.kind,
            "object_path": version.object_path,
            "storage_version_id": version.storage_version_id,
            "recorded_checksum": version.checksum_sha256,
        }
        findings.append(finding)
        if artifact.payload is None:
            # The signature pinned specific bytes; if they cannot be resolved,
            # the binding cannot be proved. Unlike checks 1 and 2 — which can
            # honestly skip — the entire content of THIS check is the re-hash.
            problems.append(
                f"artifact version {version.id} ({version.object_path}) is unresolvable: "
                f"{artifact.error}"
            )
            finding["resolved"] = False
            continue
        digest = hashlib.sha256(artifact.payload).hexdigest()
        finding["resolved"] = True
        finding["recomputed_checksum"] = digest
        finding["size_bytes"] = len(artifact.payload)
        if digest != version.checksum_sha256:
            problems.append(
                f"artifact version {version.id} ({version.object_path}) hashes to "
                f"{digest[:12]}… but the signature pinned {version.checksum_sha256[:12]}…"
            )
        if len(artifact.payload) != version.size_bytes:
            problems.append(
                f"artifact version {version.id} is {len(artifact.payload)} bytes but "
                f"{version.size_bytes} were recorded at signing"
            )

    if problems:
        return _failed(
            CHECK_ARTIFACT_BINDING,
            "The filed artifact bytes are not the bytes that were signed: "
            + "; ".join(problems)
            + ".",
            artifacts=findings,
        )
    return _passed(
        CHECK_ARTIFACT_BINDING,
        f"All {len(artifacts)} pinned artifact version(s) still hash to the checksum recorded "
        "at signing, resolved at the exact object-store version.",
        artifacts=findings,
    )


# --- the report -------------------------------------------------------------


def verify_attestation(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    storage: StorageClient | None = None,
    trust_roots: Sequence[bytes] | None = None,
) -> dict[str, Any]:
    """Run the five checks and the chain walk. Returns the report payload.

    ``trust_roots`` (PEM or DER) pins the anchors for the PDF path and the
    certificate chain. Without them the checks still run and still detect
    tampering — they simply cannot assert that the issuer is one an examiner
    accepts, and the report says so rather than implying trust it did not test.
    """
    signatures = workflow.current_signatures(db, ctx, package)
    roots = parse_trust_roots(trust_roots)
    artifacts = _collect_artifacts(db, ctx, package, signatures, storage)

    checks = [
        _check_pdf_signature(signatures, artifacts, roots),
        _check_inter_signature_tamper(signatures, artifacts, roots),
        _check_detached_attestation(signatures, roots),
        _check_content_binding(db, ctx, package, signatures),
        _check_artifact_binding(artifacts),
    ]
    ordered = sorted(checks, key=lambda result: CHECK_ORDER.index(result.check))
    chain_ok, chain_broken_at = workflow.verify_chain(db, ctx)

    return {
        "package_id": package.id,
        "return_code": package.return_code,
        "reporting_date": package.reporting_date,
        "verified_at": datetime.now(UTC),
        # A skipped check is not a failure; a broken chain always is.
        "overall_passed": not any(result.failed for result in ordered) and chain_ok,
        "checks": [result.as_dict() for result in ordered],
        "signatures": [signature_read(signature) for signature in signatures],
        "chain_ok": chain_ok,
        "chain_broken_at": chain_broken_at,
    }


# --- offline export -------------------------------------------------------


def export_verification_bundle(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
) -> dict[str, Any]:
    """The self-contained record ``scripts/verify_attestation.py`` consumes.

    Independent verifiability is the whole point of §3.5: a signature only we
    can check is not evidence. So this carries everything a third party needs —
    the certificates, the detached signature values, the RFC 3161 tokens, the
    exact canonical payloads, and the package identity fields the certification
    digest is built from — and nothing that requires our database to interpret.

    Binary fields are base64 so the bundle is plain JSON. The recomputable
    identity block is what lets an examiner rebuild ``certification_digest``
    from declared values instead of taking our word for the number.
    """
    signatures = workflow.current_signatures(db, ctx, package)
    binding = workflow.compute_binding(package)
    artifacts = {
        artifact.signature_id: artifact.version
        for artifact in _collect_artifacts(
            db, ctx, package, signatures, storage=None, load_bytes=False
        )
    }
    return {
        "schema": RECORD_SCHEMA,
        "exported_at": datetime.now(UTC).isoformat(),
        "package": {
            "organization_id": package.organization_id,
            "bank_id": package.bank_id,
            "package_id": str(package.id),
            "package_version": package.version,
            "return_code": package.return_code,
            "reporting_date": package.reporting_date.isoformat(),
            "basis": package.basis,
            "binding_class": binding.binding_class,
            "content_digest": binding.content_digest,
            "register_state_digest": binding.register_state_digest,
            "source_runs": binding.source_runs,
            "snapshot_sha256": package.snapshot_sha256,
            "attestation_state": package.attestation_state,
            "attestation_cycle": package.attestation_cycle,
        },
        "signatures": [
            _export_signature(signature, artifacts.get(signature.id)) for signature in signatures
        ],
    }


def _export_signature(
    signature: AttestationSignature, version: RegulatoryArtifactVersion | None
) -> dict[str, Any]:
    declared_at = _as_utc(signature.declared_at)
    tsa_time = _as_utc(signature.tsa_time)
    return {
        "signature_id": str(signature.id),
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
        "attestation_payload": dict(signature.attestation_payload or {}),
        "payload_digest": signature.payload_digest,
        "signature_method": signature.signature_method,
        "signature_value_b64": b64encode(signature.signature_value).decode("ascii"),
        "certificate_pem": signature.certificate_pem,
        "certificate_sha256": signature.certificate_sha256,
        "certificate_chain_pem": signature.certificate_chain_pem,
        "tsa_token_b64": (
            b64encode(bytes(signature.tsa_token)).decode("ascii") if signature.tsa_token else None
        ),
        "tsa_time": None if tsa_time is None else tsa_time.isoformat(),
        "tsa_url": signature.tsa_url,
        "declared_at": None if declared_at is None else declared_at.isoformat(),
        "attestation_cycle": signature.attestation_cycle,
        "prev_hash": signature.prev_hash,
        "entry_hash": signature.entry_hash,
        "artifact": None
        if version is None
        else {
            "artifact_version_id": str(version.id),
            "kind": version.kind,
            "object_path": version.object_path,
            "storage_version_id": version.storage_version_id,
            "checksum_sha256": version.checksum_sha256,
            "size_bytes": version.size_bytes,
        },
    }
