"""Verify an AequorOS attestation OFFLINE — no database, no AequorOS access.

**Why this exists.** A signature only we can verify is not evidence. If proving
that a filed return carries a valid signature requires a running AequorOS, then
the institution's ability to defend the filing depends on the vendor's
availability, cooperation, and continued existence — and an examiner, an
external auditor, or a court-appointed expert has no independent way to check
anything. Independent verifiability is the whole point of
``docs/attestation_esignature.md §3.5``, and this script is it: given the filed
PDF, the exported JSON signature record, and the trust roots, it re-derives
every digest and re-checks every signature using only ``cryptography``,
``pyhanko`` and the standard library.

**Deliberate duplication.** This file imports nothing from ``app`` — not even
``app.services.attestation.digests``. That is the point: the canonicalisation
recipe, the payload shape and the signature verification are reimplemented here
from the published specification so that this file is a *self-contained artefact*
an examiner can read, audit, and run against a document. The duplication cannot
drift silently: ``tests/services/test_attestation_verify.py`` asserts that this
script's ``canonical_json``, ``attestation_payload`` and ``certification_digest``
agree byte-for-byte with the platform's.

**What it can and cannot prove.** It proves the record is internally consistent
and cryptographically sound: the certification digest was formed from the
declared package identity, every signer covered that same digest, each detached
signature verifies against its certificate, the timestamp covers the same digest,
and the PDF's bytes and revision history are intact. It CANNOT check
``content_binding`` (§3.5 check 4) — whether the institution's *live* figures
still match the signed ones is inherently a question about the database, and is
answered by the in-app verification report.

Usage:
    uv run python scripts/verify_attestation.py \\
        --record BSD3-2026-03-31.attestation.json \\
        --pdf BSD3-2026-03-31.pdf \\
        --trust-root /etc/aequoros/pki/root.pem

Exit status is 0 only when no check failed; skipped checks are not failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from base64 import b64decode
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

# Mirrors of the platform constants (see the module docstring on duplication).
ATTESTATION_SCHEMA = "aequoros-attestation-v2"
SIGNATURE_PAYLOAD_SCHEMA = "aequoros-signature-v1"
RECORD_SCHEMA = "aequoros-attestation-record-v1"
TSA_TIME_TOLERANCE = timedelta(seconds=1)

type Status = Literal["PASS", "FAIL", "SKIP"]


@dataclass
class Result:
    """One offline check."""

    name: str
    status: Status
    detail: str
    notes: list[str] = field(default_factory=list)


# --- the canonical recipe, reimplemented ------------------------------------


def canonical_json(payload: Any) -> str:
    """Sorted keys, compact separators, ASCII-escaped, NO ``default=`` handler.

    The absence of ``default=`` is load-bearing: a silent ``str()`` coercion
    would let two different values hash to the same digest.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of(payload: Any) -> str:
    return sha256_hex(canonical_json(payload))


def certification_digest(package: dict[str, Any]) -> str:
    """Rebuild the value every signer signed, from the declared package identity."""
    binding_class = str(package["binding_class"])
    source_runs = list(package.get("source_runs") or [])
    payload = {
        "schema": ATTESTATION_SCHEMA,
        "organization_id": package["organization_id"],
        "bank_id": package["bank_id"],
        "package_id": package["package_id"],
        "package_version": package["package_version"],
        "return_code": package["return_code"],
        "reporting_date": package["reporting_date"],
        "basis": package["basis"],
        "content_digest": package["content_digest"],
        "binding_class": binding_class,
        "source_runs": sorted(
            ({str(k): v for k, v in entry.items()} for entry in source_runs),
            key=canonical_json,
        )
        if binding_class == "engine_run"
        else [],
        "register_state_digest": (
            package.get("register_state_digest") if binding_class == "master_data" else None
        ),
    }
    return digest_of(payload)


def attestation_payload(  # noqa: PLR0913 - the payload's fields ARE the arguments
    *,
    certification_digest_value: str,
    signer_id: str,
    signing_role: str,
    officer_title: str | None,
    statement: str,
    declared_at: str,
) -> dict[str, Any]:
    """The per-signature payload: figures digest + who + what wording."""
    return {
        "schema": SIGNATURE_PAYLOAD_SCHEMA,
        "certification_digest": certification_digest_value,
        "signer_id": signer_id,
        "signing_role": signing_role,
        "officer_title": officer_title,
        "statement": statement,
        "declared_at": declared_at,
    }


# --- helpers ----------------------------------------------------------------


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _label(record: dict[str, Any]) -> str:
    return f"{record.get('signing_role', '?')}/{record.get('signer_id', '?')}"


def _load_trust_roots(paths: Sequence[Path]) -> list[x509.Certificate]:
    roots: list[x509.Certificate] = []
    for path in paths:
        blob = path.read_bytes()
        if b"-----BEGIN" in blob:
            roots.extend(x509.load_pem_x509_certificates(blob))
        else:
            roots.append(x509.load_der_x509_certificate(blob))
    return roots


def _load_chain(pem: str | None) -> list[x509.Certificate]:
    if not pem:
        return []
    try:
        return list(x509.load_pem_x509_certificates(pem.encode("utf-8")))
    except ValueError:
        return []


# --- checks -----------------------------------------------------------------


def check_certification_digest(bundle: dict[str, Any]) -> Result:
    """Was the signed digest really formed from the declared package identity?"""
    package = bundle.get("package") or {}
    signatures: list[dict[str, Any]] = list(bundle.get("signatures") or [])
    if not package or not signatures:
        return Result("certification_digest", "SKIP", "the record carries no package identity")
    try:
        recomputed = certification_digest(package)
    except KeyError as exc:
        return Result(
            "certification_digest", "FAIL", f"the package identity block is missing {exc}"
        )
    problems = [
        f"{_label(record)} certified {record.get('certification_digest')} "
        f"which is not the declared identity's digest"
        for record in signatures
        if record.get("certification_digest") != recomputed
    ]
    if problems:
        return Result(
            "certification_digest",
            "FAIL",
            f"recomputed {recomputed}",
            problems,
        )
    return Result(
        "certification_digest",
        "PASS",
        f"the declared package identity reproduces the signed digest {recomputed[:16]}…",
    )


def check_signer_agreement(bundle: dict[str, Any]) -> Result:
    """Did every signer cover the SAME figures? Provable offline, forever."""
    signatures: list[dict[str, Any]] = list(bundle.get("signatures") or [])
    if len(signatures) < 2:
        return Result(
            "signer_agreement",
            "SKIP",
            f"only {len(signatures)} signature(s) in this record — nothing to compare",
        )
    covered = {str(record.get("certification_digest")) for record in signatures}
    if len(covered) != 1:
        return Result(
            "signer_agreement",
            "FAIL",
            "the signers did not cover the same figures",
            sorted(covered),
        )
    return Result(
        "signer_agreement",
        "PASS",
        f"all {len(signatures)} signers covered the identical figures digest "
        f"{covered.pop()[:16]}…",
    )


def check_payloads(bundle: dict[str, Any]) -> Result:
    """The canonical payload must rebuild from the record's own fields."""
    signatures: list[dict[str, Any]] = list(bundle.get("signatures") or [])
    if not signatures:
        return Result("attestation_payload", "SKIP", "the record carries no signatures")
    problems: list[str] = []
    for record in signatures:
        stored = dict(record.get("attestation_payload") or {})
        rebuilt = attestation_payload(
            certification_digest_value=str(record.get("certification_digest")),
            signer_id=str(record.get("signer_id")),
            signing_role=str(record.get("signing_role")),
            officer_title=record.get("officer_title"),
            statement=str(record.get("statement")),
            declared_at=str(record.get("declared_at") or ""),
        )
        for key in sorted(set(stored) | set(rebuilt)):
            left, right = stored.get(key), rebuilt.get(key)
            if key == "declared_at":
                if _instant(left) != _instant(right):
                    problems.append(f"{_label(record)}: {key} {left!r} vs {right!r}")
                continue
            if left != right:
                problems.append(f"{_label(record)}: {key} differs from the record's own field")
        recomputed = digest_of(stored)
        if recomputed != record.get("payload_digest"):
            problems.append(
                f"{_label(record)}: payload_digest {record.get('payload_digest')} but the "
                f"payload canonicalises to {recomputed}"
            )
    if problems:
        return Result(
            "attestation_payload", "FAIL", "the signed payload does not rebuild", problems
        )
    return Result(
        "attestation_payload",
        "PASS",
        f"{len(signatures)} payload(s) rebuild from the record and match their digests",
    )


def _verify_value(
    record: dict[str, Any], certificate: x509.Certificate
) -> tuple[bool, str]:
    digest = bytes.fromhex(str(record["payload_digest"]))
    value = b64decode(str(record["signature_value_b64"]))
    public_key = certificate.public_key()
    method = str(record.get("signature_method"))

    if method == "detached_ecdsa_p256_sha256" and isinstance(
        public_key, ec.EllipticCurvePublicKey
    ):
        for algorithm in (ec.ECDSA(Prehashed(hashes.SHA256())), ec.ECDSA(hashes.SHA256())):
            try:
                public_key.verify(value, digest, algorithm)
            except (InvalidSignature, UnsupportedAlgorithm, ValueError):
                continue
            return True, "ECDSA P-256/SHA-256"
        return False, "the ECDSA signature does not verify"
    if method == "detached_rsa_pss_sha256" and isinstance(public_key, rsa.RSAPublicKey):
        pss = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO)
        for algorithm in (Prehashed(hashes.SHA256()), hashes.SHA256()):
            try:
                public_key.verify(value, digest, pss, algorithm)
            except (InvalidSignature, UnsupportedAlgorithm, ValueError):
                continue
            return True, "RSA-PSS/SHA-256"
        return False, "the RSA-PSS signature does not verify"
    return False, f"unsupported signature_method {method!r} for this certificate"


def check_detached_signatures(bundle: dict[str, Any]) -> Result:
    signatures: list[dict[str, Any]] = list(bundle.get("signatures") or [])
    if not signatures:
        return Result("detached_signature", "SKIP", "the record carries no signatures")
    problems: list[str] = []
    notes: list[str] = []
    for record in signatures:
        try:
            certificate = x509.load_pem_x509_certificate(
                str(record["certificate_pem"]).encode("utf-8")
            )
        except (KeyError, ValueError) as exc:
            problems.append(f"{_label(record)}: certificate unreadable ({exc})")
            continue
        verified, detail = _verify_value(record, certificate)
        if verified:
            notes.append(f"{_label(record)}: {detail}")
        else:
            problems.append(f"{_label(record)}: {detail}")
    if problems:
        return Result("detached_signature", "FAIL", "a signature does not verify", problems)
    return Result(
        "detached_signature",
        "PASS",
        f"{len(signatures)} detached signature(s) verify against their certificates",
        notes,
    )


def check_certificates(bundle: dict[str, Any], roots: Sequence[x509.Certificate]) -> Result:
    """Thumbprint, issuer chain, and (when supplied) the trust anchor."""
    signatures: list[dict[str, Any]] = list(bundle.get("signatures") or [])
    if not signatures:
        return Result("certificate_chain", "SKIP", "the record carries no signatures")
    problems: list[str] = []
    notes: list[str] = []
    for record in signatures:
        try:
            leaf = x509.load_pem_x509_certificate(str(record["certificate_pem"]).encode("utf-8"))
        except (KeyError, ValueError) as exc:
            problems.append(f"{_label(record)}: certificate unreadable ({exc})")
            continue
        fingerprint = leaf.fingerprint(hashes.SHA256()).hex()
        if fingerprint != record.get("certificate_sha256"):
            problems.append(
                f"{_label(record)}: certificate_sha256 {record.get('certificate_sha256')} does "
                f"not match the embedded certificate ({fingerprint})"
            )
        current = leaf
        broken = False
        for issuer in _load_chain(record.get("certificate_chain_pem")):
            try:
                current.verify_directly_issued_by(issuer)
            except (ValueError, TypeError, InvalidSignature) as exc:
                problems.append(f"{_label(record)}: broken chain link ({exc})")
                broken = True
                break
            current = issuer
        if broken:
            continue
        signing_time = _instant(record.get("tsa_time")) or _instant(record.get("declared_at"))
        if signing_time is not None and not (
            leaf.not_valid_before_utc <= signing_time <= leaf.not_valid_after_utc
        ):
            problems.append(
                f"{_label(record)}: signing time {signing_time.isoformat()} is outside the "
                "certificate validity window"
            )
        if not roots:
            notes.append(f"{_label(record)}: no trust root supplied — issuance checked only")
            continue
        anchored = any(
            current.fingerprint(hashes.SHA256()) == root.fingerprint(hashes.SHA256())
            or _issued_by(current, root)
            for root in roots
        )
        if anchored:
            notes.append(f"{_label(record)}: chains to a supplied trust root")
        else:
            problems.append(
                f"{_label(record)}: the chain terminates at "
                f"{current.subject.rfc4514_string()!r}, which is not a supplied trust root"
            )
    if problems:
        return Result("certificate_chain", "FAIL", "certificate validation failed", problems)
    return Result(
        "certificate_chain",
        "PASS",
        f"{len(signatures)} certificate(s) validated"
        + (" against the supplied trust roots" if roots else " (issuance only)"),
        notes,
    )


def _issued_by(child: x509.Certificate, issuer: x509.Certificate) -> bool:
    try:
        child.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def check_timestamps(bundle: dict[str, Any]) -> Result:
    """RFC 3161: does the token cover THIS payload digest, at the recorded time?"""
    signatures: list[dict[str, Any]] = list(bundle.get("signatures") or [])
    tokened = [record for record in signatures if record.get("tsa_token_b64")]
    if not tokened:
        return Result(
            "trusted_timestamp",
            "SKIP",
            "no signature carries an RFC 3161 token, so validity cannot be shown to "
            "predate certificate expiry or revocation",
        )
    try:
        from asn1crypto import cms  # noqa: PLC0415 - only needed when a token exists
    except ImportError:  # pragma: no cover - asn1crypto ships with pyhanko
        return Result("trusted_timestamp", "SKIP", "asn1crypto is not installed")

    problems: list[str] = []
    notes: list[str] = []
    for record in tokened:
        try:
            content: Any = cms.ContentInfo.load(b64decode(str(record["tsa_token_b64"])))
            encap = content["content"]["encap_content_info"]
            tst_info: Any = encap["content"].parsed
            token_digest = bytes(tst_info["message_imprint"]["hashed_message"].native)
            gen_time = tst_info["gen_time"].native
        except Exception as exc:  # asn1crypto raises broadly on malformed DER
            problems.append(f"{_label(record)}: token unparseable ({exc})")
            continue
        if token_digest != bytes.fromhex(str(record["payload_digest"])):
            problems.append(
                f"{_label(record)}: the token covers a different digest — it is evidence "
                "about other data"
            )
        recorded = _instant(record.get("tsa_time"))
        asserted = gen_time if isinstance(gen_time, datetime) else None
        if asserted is not None:
            asserted = (
                asserted.replace(tzinfo=UTC)
                if asserted.tzinfo is None
                else asserted.astimezone(UTC)
            )
        if asserted is None:
            problems.append(f"{_label(record)}: the token carries no readable genTime")
        elif recorded is not None and abs(asserted - recorded) > TSA_TIME_TOLERANCE:
            problems.append(
                f"{_label(record)}: the token asserts {asserted.isoformat()} but the record "
                f"says {recorded.isoformat()}"
            )
        else:
            notes.append(f"{_label(record)}: trusted time {asserted.isoformat()}")
    if problems:
        return Result("trusted_timestamp", "FAIL", "timestamp validation failed", problems)
    return Result(
        "trusted_timestamp", "PASS", f"{len(tokened)} timestamp token(s) cover this record", notes
    )


def check_artifact_checksum(bundle: dict[str, Any], pdf: Path | None) -> Result:
    """Re-hash the file on disk against the checksums the signatures pinned.

    The supplied file is the FILED document, which is the last incremental
    revision. Earlier signers pinned earlier revisions — those bytes are embedded
    inside this file rather than equal to it, and the PDF revision checks are
    what cover them. So the pass condition is that the file matches at least one
    pin, and every other pin is reported as an earlier revision.
    """
    signatures: list[dict[str, Any]] = list(bundle.get("signatures") or [])
    pinned = [(record, record["artifact"]) for record in signatures if record.get("artifact")]
    if pdf is None:
        return Result("artifact_checksum", "SKIP", "no --pdf was supplied")
    if not pinned:
        return Result(
            "artifact_checksum",
            "SKIP",
            "the record pins no artifact, so there is no checksum to compare the file to",
        )
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    matched = [
        _label(record) for record, artifact in pinned if artifact.get("checksum_sha256") == digest
    ]
    if not matched:
        return Result(
            "artifact_checksum",
            "FAIL",
            f"{pdf.name} hashes to {digest}, which is not any signed artifact",
            [
                f"{_label(record)} pinned {artifact.get('checksum_sha256')} "
                f"({artifact.get('object_path')})"
                for record, artifact in pinned
            ],
        )
    notes = [f"matches the artifact pinned by {', '.join(matched)}"]
    notes += [
        f"{_label(record)} pinned a different (earlier) revision "
        f"{str(artifact.get('checksum_sha256'))[:16]}…"
        for record, artifact in pinned
        if artifact.get("checksum_sha256") != digest
    ]
    return Result(
        "artifact_checksum",
        "PASS",
        f"{pdf.name} hashes to a pinned checksum {digest[:16]}…",
        notes,
    )


def _pdf_statuses(pdf: Path, roots: Sequence[x509.Certificate], bundle: dict[str, Any]) -> Any:
    import io  # noqa: PLC0415

    from asn1crypto import x509 as asn1_x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from pyhanko.pdf_utils.reader import PdfFileReader  # noqa: PLC0415
    from pyhanko.sign.validation import validate_pdf_signature  # noqa: PLC0415
    from pyhanko_certvalidator import ValidationContext  # noqa: PLC0415

    def as_asn1(cert: x509.Certificate) -> Any:
        return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))

    anchors = list(roots)
    if not anchors:
        # Anchor on the record's own chain so the byte-integrity and revision
        # checks still run. Reported as such — it does not establish trust.
        for record in bundle.get("signatures") or []:
            chain = _load_chain(record.get("certificate_chain_pem"))
            if chain:
                anchors.append(chain[-1])
            elif record.get("certificate_pem"):
                anchors.append(
                    x509.load_pem_x509_certificate(str(record["certificate_pem"]).encode("utf-8"))
                )
    others: list[Any] = []
    for record in bundle.get("signatures") or []:
        if record.get("certificate_pem"):
            others.append(
                as_asn1(
                    x509.load_pem_x509_certificate(str(record["certificate_pem"]).encode("utf-8"))
                )
            )
        others.extend(as_asn1(cert) for cert in _load_chain(record.get("certificate_chain_pem")))
    context = ValidationContext(
        trust_roots=[as_asn1(cert) for cert in anchors],
        other_certs=others,
        allow_fetching=False,
        revocation_mode="soft-fail",
    )
    reader = PdfFileReader(io.BytesIO(pdf.read_bytes()))
    policy = _diff_policy()
    return [
        (
            embedded.field_name,
            validate_pdf_signature(
                embedded, signer_validation_context=context, diff_policy=policy
            ),
        )
        for embedded in reader.embedded_signatures
    ]


def _diff_policy() -> Any:
    """pyHanko's revision-diff policy, minus one allowance that convicts falsely.

    ``allow_in_place_appearance_stream_changes`` exists for producers that
    overwrite a form field's appearance stream in place. AequorOS never does —
    every filled value gets a freshly allocated stream object. Left on, the rule
    skips a field only when its appearance stream's last change is *equal to* the
    revision being diffed from rather than *not later than* it, so every field the
    preparer filled reads as an in-place update once any revision exists after the
    approver's signature. PAdES B-LTA always appends one (the DSS, then the
    document timestamp), so a clean fully-signed return with placed
    name/designation/date fields would be reported as tampered.

    Switching it off is strictly stricter: a genuine in-place appearance rewrite
    is refused rather than whitelisted. Restated here rather than imported,
    because this file imports nothing from ``app`` — see the module docstring.
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


def check_pdf(
    pdf: Path | None, roots: Sequence[x509.Certificate], bundle: dict[str, Any]
) -> list[Result]:
    """pyHanko: cryptographic validity, then diff analysis across revisions."""
    if pdf is None:
        reason = "no --pdf was supplied"
        return [
            Result("pdf_signature", "SKIP", reason),
            Result("pdf_inter_signature_tamper", "SKIP", reason),
        ]
    try:
        statuses = _pdf_statuses(pdf, roots, bundle)
    except Exception as exc:
        return [
            Result("pdf_signature", "FAIL", f"{pdf.name} could not be validated: {exc}"),
            Result("pdf_inter_signature_tamper", "FAIL", "the PDF could not be analysed"),
        ]
    if not statuses:
        return [
            Result("pdf_signature", "FAIL", f"{pdf.name} carries no embedded signature"),
            Result("pdf_inter_signature_tamper", "SKIP", "no embedded signature to compare"),
        ]

    from pyhanko.sign.diff_analysis import ModificationLevel  # noqa: PLC0415
    from pyhanko.sign.validation import SignatureCoverageLevel  # noqa: PLC0415

    crypto_problems: list[str] = []
    tamper_problems: list[str] = []
    notes: list[str] = []
    last = len(statuses) - 1
    for index, (field_name, status) in enumerate(statuses):
        notes.append(
            f"{field_name}: intact={status.intact} valid={status.valid} "
            f"trusted={status.trusted} coverage={status.coverage} "
            f"modifications={status.modification_level}"
        )
        if not status.intact:
            crypto_problems.append(f"{field_name}: the signed byte range has been altered")
        if not status.valid:
            crypto_problems.append(f"{field_name}: the signature does not validate")
        # Only report trust once the signature is otherwise sound: pyHanko marks
        # an altered signature untrusted as well, and echoing that as a PKI
        # failure buries the real cause.
        if roots and status.intact and status.valid and not status.trusted:
            crypto_problems.append(
                f"{field_name}: the certificate does not chain to a supplied trust root"
            )
        if status.docmdp_ok is False:
            tamper_problems.append(f"{field_name}: a change violated the document's DocMDP")
        level = status.modification_level
        if level is not None and level > ModificationLevel.FORM_FILLING:
            tamper_problems.append(
                f"{field_name}: modifications reach {level} (max permitted "
                f"{ModificationLevel.FORM_FILLING}); offending change: {status.diff_result}"
            )
        expected = (
            SignatureCoverageLevel.ENTIRE_FILE
            if index == last
            else SignatureCoverageLevel.ENTIRE_REVISION
        )
        if status.coverage != expected:
            tamper_problems.append(
                f"{field_name} is signature {index + 1} of {len(statuses)} and must cover "
                f"{expected}, but covers {status.coverage}"
            )
    return [
        Result(
            "pdf_signature",
            "FAIL" if crypto_problems else "PASS",
            f"{len(statuses)} embedded signature(s) examined"
            + ("" if roots else " (trust anchored on the record's own chain)"),
            crypto_problems or notes,
        ),
        Result(
            "pdf_inter_signature_tamper",
            "FAIL" if tamper_problems else "PASS",
            "revision history examined",
            tamper_problems,
        ),
    ]


# --- driver -----------------------------------------------------------------


class InputError(Exception):
    """The inputs could not be read — an outage, never a verification verdict."""


def _read_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise InputError(f"no signature record at {path}")
    try:
        bundle = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"the signature record is not readable JSON: {exc}") from exc
    if not isinstance(bundle, dict):
        raise InputError("the signature record must be a JSON object")
    schema = bundle.get("schema")
    if schema != RECORD_SCHEMA:
        raise InputError(f"unsupported record schema {schema!r} (expected {RECORD_SCHEMA!r})")
    return bundle


def run_checks(
    bundle: dict[str, Any], pdf: Path | None, roots: Sequence[x509.Certificate]
) -> list[Result]:
    return [
        check_certification_digest(bundle),
        check_signer_agreement(bundle),
        check_payloads(bundle),
        check_detached_signatures(bundle),
        check_certificates(bundle, roots),
        check_timestamps(bundle),
        check_artifact_checksum(bundle, pdf),
        *check_pdf(pdf, roots, bundle),
    ]


def render(results: Iterable[Result], stream: Any = sys.stdout) -> None:
    for result in results:
        print(f"[{result.status}] {result.name:28s} {result.detail}", file=stream)
        for note in result.notes:
            print(f"         · {note}", file=stream)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify an AequorOS attestation offline, from the filed document, its exported "
            "signature record, and the trust roots. No database access."
        )
    )
    parser.add_argument(
        "--record",
        required=True,
        type=Path,
        help=(
            "Exported JSON signature record, as produced by "
            "app.services.attestation.verify.export_verification_bundle."
        ),
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="The filed signed PDF. Optional: the detached record stands alone.",
    )
    parser.add_argument(
        "--trust-root",
        action="append",
        default=[],
        type=Path,
        metavar="PEM",
        help="Trust anchor (PEM or DER). Repeatable. Without one, issuance is checked "
        "but institutional trust is NOT established.",
    )
    args = parser.parse_args(argv)

    record_path: Path = args.record
    pdf_path: Path | None = args.pdf
    try:
        bundle = _read_record(record_path)
        if pdf_path is not None and not pdf_path.is_file():
            raise InputError(f"no PDF at {pdf_path}")
        roots = _load_trust_roots(list(args.trust_root))
    except (InputError, OSError, ValueError) as exc:
        # Exit 2 for "could not run", distinct from exit 1 for "a check failed":
        # an operator must never read a missing file as a clean verification.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    package = bundle.get("package") or {}
    print(
        f"AequorOS attestation verification (offline)\n"
        f"  return      {package.get('return_code')} {package.get('reporting_date')} "
        f"({package.get('basis')}) version {package.get('package_version')}\n"
        f"  institution {package.get('bank_id')} / {package.get('organization_id')}\n"
        f"  record      {record_path}\n"
        f"  document    {pdf_path if pdf_path else '(none supplied)'}\n"
        f"  trust roots {len(roots) or 'none — trust NOT established'}\n"
    )
    results = run_checks(bundle, pdf_path, roots)
    render(results)

    failures = [result for result in results if result.status == "FAIL"]
    skipped = sum(1 for result in results if result.status == "SKIP")
    print()
    if failures:
        print(
            f"VERIFICATION FAILED — {len(failures)} of {len(results)} checks failed "
            f"({skipped} skipped)."
        )
        print(
            "Do not rely on this document as evidence until the failure is explained. "
            "Note that content binding (whether the institution's live figures still "
            "match the signed ones) cannot be checked offline."
        )
        return 1
    print(f"VERIFICATION PASSED — {len(results) - skipped} checks passed, {skipped} skipped.")
    print(
        "Scope: this proves the record is internally consistent and cryptographically "
        "sound. It does not prove the institution's live figures still match the signed "
        "ones — that is the in-app content_binding check."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
