"""Attestation verification — the five independent checks (docs §3.5).

Every signature in this module is a REAL signature: a self-signed root CA and two
leaf certificates are minted with ``cryptography``, a soft-key ``RawSigner`` is
injected into the real ``signing.certify`` path, and the resulting rows are the
same rows production writes. Nothing is stubbed except the HSM (which
``cryptography`` cannot be) and the object store (in-memory, the same seam the
export tests use).

The load-bearing test is
``test_thoroughly_retampered_figures_fail_content_binding_while_cryptography_passes``:
it proves checks 3 and 4 are genuinely independent. An attacker with database
access rewrites the snapshot AND re-seals every derived column so the package is
internally self-consistent — and the cryptographic checks stay green, because
nothing was done to the signature. Only ``content_binding`` catches it, because
only ``content_binding`` compares live state against what the SIGNATURE recorded.

Two deliberate liberties, both noted where they occur:

* signatures are mutated in place to simulate tampering. Production forbids that
  at the database level (migration 202607250027 revokes UPDATE and installs a
  raising trigger); SQLite has no such enforcement, which is what makes the
  tamper cases testable at all.
* step-up is minted directly rather than driven through OIDC/password
  re-authentication. That path has its own tests; here the subject is what a
  verifier can prove about a signature that already exists.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields as pyhanko_fields
from pyhanko.sign import signers as pyhanko_signers
from pyhanko_certvalidator.registry import SimpleCertificateStore
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    AttestationSignature,
    Bank,
    BankReportingPeriod,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryRun,
    ReturnSigningPolicy,
    SignerKey,
    User,
)
from app.schemas.attestation import VerificationReportRead
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import regulatory_liquidity
from app.services.attestation import digests, pdf_signing, signing, stepup, verify, workflow
from app.services.attestation.identity import ensure_signer_identity
from app.services.regulatory_reporting import generation, validation
from app.services.regulatory_reporting.exports import export_package
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from app.storage.client import ObjectMetadata, StorageLocation
from tests.storage.inmemory import InMemoryStorageClient

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
CHECKER_USER_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CHECKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=CHECKER_USER_ID)
REPORTING_DATE = date(2026, 3, 31)

#: The offline CLI is loaded from its file, not imported as a module, because it
#: is deliberately not part of any package — see its module docstring.
_CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify_attestation.py"


def _load_cli() -> Any:
    spec = importlib.util.spec_from_file_location("aequoros_verify_cli", _CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: ``@dataclass`` resolves its own module from
    # ``sys.modules`` and fails on a module that is not there yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


# --- PKI + soft signer ------------------------------------------------------


@dataclass(frozen=True)
class _Pki:
    """A throwaway CA and the two officer certificates it issued."""

    ca_key: ec.EllipticCurvePrivateKey
    ca_cert: x509.Certificate
    keys: dict[str, ec.EllipticCurvePrivateKey]
    certs: dict[str, x509.Certificate]

    @property
    def ca_pem(self) -> bytes:
        return self.ca_cert.public_bytes(serialization.Encoding.PEM)

    def pem(self, key_ref: str) -> str:
        return self.certs[key_ref].public_bytes(serialization.Encoding.PEM).decode("ascii")

    def fingerprint(self, key_ref: str) -> str:
        return self.certs[key_ref].fingerprint(hashes.SHA256()).hex()


class _SoftSigner:
    """A ``RawSignerPort`` backed by in-process keys.

    Permitted in tests only (§3.3 refuses the software backend when
    ``APP_ENV=prod``). It signs the pre-computed digest exactly as a PKCS#11
    ``CKM_ECDSA`` operation would, which is the convention the verifier reports
    as ``prehashed_sha256``.
    """

    def __init__(self, pki: _Pki) -> None:
        self._pki = pki

    def sign_digest(self, digest: bytes, *, key_ref: str) -> bytes:
        return self._pki.keys[key_ref].sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))


def _issue(
    subject: str,
    *,
    issuer_key: ec.EllipticCurvePrivateKey | None = None,
    issuer_cert: x509.Certificate | None = None,
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    now = datetime.now(UTC)
    is_ca = issuer_key is None
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_cert.subject if issuer_cert is not None else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                # nonRepudiation on the officer certificates: the whole point of
                # the signature is that the signer cannot later disown it.
                content_commitment=not is_ca,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=is_ca,
                crl_sign=is_ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    return key, builder.sign(issuer_key or key, hashes.SHA256())


@pytest.fixture
def pki() -> _Pki:
    ca_key, ca_cert = _issue("AequorOS Attestation Test Root")
    keys: dict[str, ec.EllipticCurvePrivateKey] = {}
    certs: dict[str, x509.Certificate] = {}
    for key_ref, common_name in (
        ("softkey:preparer", "Head of Finance, Sample Bank"),
        ("softkey:approver", "Chief Financial Officer, Sample Bank"),
    ):
        key, cert = _issue(common_name, issuer_key=ca_key, issuer_cert=ca_cert)
        keys[key_ref] = key
        certs[key_ref] = cert
    return _Pki(ca_key=ca_key, ca_cert=ca_cert, keys=keys, certs=certs)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client", lambda: client
    )
    return client


@pytest.fixture(autouse=True)
def attestation_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signing is off by default; verification tests need it on."""
    from app.core.config import get_settings  # noqa: PLC0415

    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-pepper-not-for-production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    get_settings.cache_clear()


# --- fixture data -----------------------------------------------------------


def _seed(db: Session) -> RegulatoryPackage:
    """A validated BSD3 package with a real succeeded liquidity run behind it."""
    seed_sample_bank(db)
    if db.scalar(select(User.id).where(User.id == CHECKER_USER_ID)) is None:
        db.add(
            User(
                id=CHECKER_USER_ID,
                organization_id=DEMO_ORG_ID,
                email="attestation.approver@example.test",
                display_name="Attestation Approver",
                job_title="Chief Financial Officer",
            )
        )
        db.commit()
    # The platform default requires NO signature until an administrator
    # configures one (policy.py: every BoG requirement is unconfirmed, so
    # attestation becomes mandatory only by an explicit, audited act). These
    # tests are about verifying signatures, so the institution configures the
    # maker-checker policy first.
    db.add(
        ReturnSigningPolicy(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            return_code="BSD3",
            required_signatures=[
                {"role": "preparer", "min_count": 1, "officer_titles": []},
                {"role": "approver", "min_count": 1, "officer_titles": []},
            ],
            required_attachments=[],
            require_signature=True,
            require_signed_pdf=False,
            distinct_signers=True,
            effective_from=date(2026, 1, 1),
            reason="Test policy: BSD3 requires a preparer and a distinct approver.",
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
    assert package.status == "validated"
    assert package.source_runs, "BSD3 must bind at least one engine run"
    return package


def _enrol(db: Session, ctx: TenantContext, pki: _Pki, key_ref: str) -> str:
    """Provision the signer identity + custody metadata for one officer."""
    assert ctx.actor_user_id is not None
    identity = ensure_signer_identity(db, ctx, ctx.actor_user_id)
    cert = pki.certs[key_ref]
    db.add(
        SignerKey(
            organization_id=ctx.organization_id,
            signer_id=identity.signer_id,
            backend="software",
            key_ref=key_ref,
            algorithm="ecdsa-p256-sha256",
            certificate_pem=pki.pem(key_ref),
            certificate_sha256=pki.fingerprint(key_ref),
            certificate_chain_pem=pki.ca_pem.decode("ascii"),
            not_before=cert.not_valid_before_utc,
            not_after=cert.not_valid_after_utc,
            status="active",
        )
    )
    db.commit()
    return identity.signer_id


def _certify(  # noqa: PLR0913 - mirrors signing.certify's own irreducible inputs
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    pki: _Pki,
    *,
    role: str,
    key_ref: str,
    artifact_version_id: UUID | None = None,
) -> AttestationSignature:
    """Drive the real certification path with an injected soft signer."""
    assert ctx.actor_user_id is not None
    signer_id = _enrol(db, ctx, pki, key_ref)
    binding = workflow.compute_binding(package)
    token, _authorization = stepup.mint_authorization(
        db,
        ctx,
        user_id=ctx.actor_user_id,
        signer_id=signer_id,
        package_id=package.id,
        signing_role=role,
        certification_digest=binding.certification_digest,
        auth_evidence={"method": "password_reauth", "acr": "urn:test:reauth"},
    )
    return signing.certify(
        db,
        ctx,
        package,
        role=role,
        authorization_token=token,
        backends=signing.SigningBackends(raw_signer=_SoftSigner(pki)),
        artifact_version_id=artifact_version_id,
    )


def _fully_certify(db: Session, pki: _Pki, package: RegulatoryPackage) -> None:
    _certify(db, MAKER, package, pki, role="preparer", key_ref="softkey:preparer")
    _certify(db, CHECKER, package, pki, role="approver", key_ref="softkey:approver")
    db.refresh(package)
    assert package.attestation_state == "fully_certified"


def _report(
    db: Session,
    package: RegulatoryPackage,
    *,
    storage_client: InMemoryStorageClient | None = None,
    pki: _Pki | None = None,
) -> dict[str, Any]:
    return verify.verify_attestation(
        db,
        MAKER,
        package,
        storage=storage_client,
        trust_roots=None if pki is None else [pki.ca_pem],
    )


def _check(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(entry for entry in report["checks"] if entry["check"] == name)


def _status(report: dict[str, Any], name: str) -> str:
    return str(_check(report, name)["evidence"]["status"])


# --- (a) a clean signature verifies -----------------------------------------


def test_clean_detached_attestation_verifies_and_reports_every_check(
    db_session: Session, pki: _Pki
) -> None:
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    report = _report(db_session, package, pki=pki)
    assert VerificationReportRead.model_validate(report).overall_passed is True
    assert report["chain_ok"] is True
    assert report["chain_broken_at"] is None
    assert len(report["signatures"]) == 2

    # Five checks, always all five, always in a stable order — a partial
    # failure has to be legible, so none of them may be collapsed or omitted.
    assert [entry["check"] for entry in report["checks"]] == list(verify.CHECK_ORDER)
    assert _status(report, verify.CHECK_DETACHED_ATTESTATION) == "passed"
    assert _status(report, verify.CHECK_CONTENT_BINDING) == "passed"
    # No signed PDF was required by the policy in force (confirmation C1), so the
    # three artifact-dependent checks skip — and skipping is not failing.
    for name in (
        verify.CHECK_PDF_SIGNATURE,
        verify.CHECK_INTER_SIGNATURE_TAMPER,
        verify.CHECK_ARTIFACT_BINDING,
    ):
        assert _status(report, name) == "skipped"
        assert _check(report, name)["passed"] is True
        assert _check(report, name)["detail"].startswith("Skipped: ")

    detached = _check(report, verify.CHECK_DETACHED_ATTESTATION)
    assert [entry["signature_method"] for entry in detached["evidence"]["signatures"]] == [
        "detached_ecdsa_p256_sha256",
        "detached_ecdsa_p256_sha256",
    ]
    for entry in detached["evidence"]["signatures"]:
        assert entry["payload_recomputes"] is True
        assert entry["payload_digest_matches"] is True
        assert entry["certificate_sha256_matches"] is True
        assert entry["signature_verifies"] is True
        assert entry["digest_convention"] == verify.CONVENTION_PREHASHED
        assert entry["certificate"]["trust_anchored"] is True
    # No TSA is configured in the hermetic suite, so the report says so instead
    # of implying long-term validity it cannot demonstrate.
    assert "carries no RFC 3161 token" in detached["detail"]

    binding = _check(report, verify.CHECK_CONTENT_BINDING)["evidence"]
    assert binding["snapshot_seal"] == "verified"
    assert binding["recomputed_certification_digest"] == package.certification_digest
    assert binding["source_runs"]
    for entry in binding["source_runs"]:
        assert entry["present"] is True
        assert entry["pinned_input_hash"] == entry["stored_input_hash"]
        assert entry["recomputed_input_hash"] == entry["stored_input_hash"]


def test_verification_never_writes(db_session: Session, pki: _Pki) -> None:
    """A verifier that can mutate what it certifies is not a verifier."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)
    before = db_session.scalar(select(RegulatoryRun.id).limit(1))

    _report(db_session, package, pki=pki)

    assert len(db_session.new) == 0
    assert len(db_session.dirty) == 0
    assert len(db_session.deleted) == 0
    # The bank's storage slug in particular must not be lazily assigned by a
    # read-only report (``ingestion.bank_slug`` would have done exactly that).
    assert db_session.scalar(select(RegulatoryRun.id).limit(1)) == before


# --- (b) THE test: content binding is independent of cryptography -----------


def test_thoroughly_retampered_figures_fail_content_binding_while_cryptography_passes(
    db_session: Session, pki: _Pki
) -> None:
    """Rewrite the figures AND re-seal every derived column. Only check 4 catches it.

    This is the strongest form of the tamper: the package is left internally
    self-consistent, so nothing that reasons about the package alone can object.
    The signature is untouched, so every cryptographic check stays green. The
    divergence is provable only by comparing live state to what the SIGNATURE
    recorded — which is what makes checks 3 and 4 genuinely independent rather
    than two views of one property.
    """
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)
    signed_digest = package.certification_digest
    assert signed_digest is not None

    snapshot = json.loads(json.dumps(package.snapshot))
    sections = snapshot["sections"]
    target = sections[0]
    original_value = target["rows"][0]["value"]
    target["rows"][0]["value"] = "999999999"
    assert target["rows"][0]["value"] != original_value

    # A thorough attacker with write access rewrites every derived column too, so
    # the package passes its own integrity checks (export's G3 seal included).
    package.snapshot = snapshot
    package.content_digest = digests.content_digest(snapshot)
    package.snapshot_sha256 = generation.snapshot_content_hash(snapshot)
    package.certification_digest = workflow.compute_binding(package).certification_digest
    db_session.commit()
    tampered_digest = package.certification_digest
    assert tampered_digest is not None
    assert tampered_digest != signed_digest

    report = _report(db_session, package, pki=pki)

    # 1. The cryptographic checks still pass. Nothing was done to the signature,
    #    and a signature cannot notice that the world moved around it.
    assert _status(report, verify.CHECK_DETACHED_ATTESTATION) == "passed"
    for entry in _check(report, verify.CHECK_DETACHED_ATTESTATION)["evidence"]["signatures"]:
        assert entry["signature_verifies"] is True
        assert entry["payload_digest_matches"] is True
    # 2. The hash chain is intact — no signature row was touched.
    assert report["chain_ok"] is True
    # 3. The snapshot seal was re-sealed, so THAT sub-check cannot catch it…
    content = _check(report, verify.CHECK_CONTENT_BINDING)
    assert content["evidence"]["snapshot_seal"] == "verified"
    # 4. …and content_binding fails anyway, because the comparison that matters
    #    is against the digest the signers actually covered.
    assert content["passed"] is False
    assert _status(report, verify.CHECK_CONTENT_BINDING) == "failed"
    assert content["evidence"]["recomputed_certification_digest"] == tampered_digest
    assert content["evidence"]["signed_certification_digests"] == [signed_digest]
    assert signed_digest[:12] in content["detail"]
    assert tampered_digest[:12] in content["detail"]
    assert "diverged from the signed ones" in content["detail"]
    # The re-sealed snapshot is caught twice over: the certification digest no
    # longer reconciles, AND each signature remembers the seal value that stood
    # when it was made.
    assert "but the package now carries" in content["detail"]
    # 5. And therefore the report as a whole is red, with the reason localised to
    #    exactly one of the five checks.
    assert report["overall_passed"] is False
    failed = [entry["check"] for entry in report["checks"] if not entry["passed"]]
    assert failed == [verify.CHECK_CONTENT_BINDING]
    # The failing report must still serialise through the closed response model —
    # a verdict an API cannot return is a verdict nobody sees.
    assert VerificationReportRead.model_validate(report).overall_passed is False


def test_snapshot_mutation_alone_is_caught_by_the_generation_seal(
    db_session: Session, pki: _Pki
) -> None:
    """The sloppier tamper: figures moved, ``snapshot_sha256`` left behind (G3)."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    snapshot = json.loads(json.dumps(package.snapshot))
    snapshot["sections"][0]["rows"][0]["value"] = "1"
    package.snapshot = snapshot
    db_session.commit()

    report = _report(db_session, package, pki=pki)
    content = _check(report, verify.CHECK_CONTENT_BINDING)
    assert content["passed"] is False
    assert content["evidence"]["snapshot_seal"] == "mismatch"
    assert "no longer matches its generation seal" in content["detail"]
    # Note the asymmetry this exposes: ``compute_binding`` prefers the PERSISTED
    # content_digest, so the certification digest still reconciles. The snapshot
    # seal is the sub-check that closes that gap.
    assert content["evidence"]["recomputed_certification_digest"] == package.certification_digest
    assert _status(report, verify.CHECK_DETACHED_ATTESTATION) == "passed"


def test_mutated_engine_run_inputs_fail_content_binding(
    db_session: Session, pki: _Pki
) -> None:
    """The run behind the figures is immutable evidence; prove it still is."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    run_id = UUID(str(package.source_runs[0]["run_id"]))
    run = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run_id))
    assert run is not None
    inputs = json.loads(json.dumps(run.inputs))
    inputs["tampered_marker"] = "an input that was never part of the signed calculation"
    run.inputs = inputs
    db_session.commit()

    report = _report(db_session, package, pki=pki)
    content = _check(report, verify.CHECK_CONTENT_BINDING)
    assert content["passed"] is False
    assert "the immutable run inputs changed" in content["detail"]
    finding = next(
        entry for entry in content["evidence"]["source_runs"] if entry["run_id"] == str(run_id)
    )
    assert finding["recomputed_input_hash"] != finding["stored_input_hash"]
    assert finding["pinned_input_hash"] == finding["stored_input_hash"]
    assert _status(report, verify.CHECK_DETACHED_ATTESTATION) == "passed"


def test_a_deleted_source_run_fails_content_binding(db_session: Session, pki: _Pki) -> None:
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    run_id = UUID(str(package.source_runs[0]["run_id"]))
    run = db_session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run_id))
    assert run is not None
    db_session.delete(run)
    db_session.commit()

    content = _check(_report(db_session, package, pki=pki), verify.CHECK_CONTENT_BINDING)
    assert content["passed"] is False
    assert "no longer exists" in content["detail"]


# --- (c) a corrupted signature value ---------------------------------------


def test_corrupted_signature_value_fails_only_the_detached_check(
    db_session: Session, pki: _Pki
) -> None:
    """Flip one byte of the signature; the chain is repaired so check 3 is isolated."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    signatures = workflow.current_signatures(db_session, MAKER, package)
    victim = signatures[-1]
    corrupted = bytearray(victim.signature_value)
    corrupted[-1] ^= 0xFF
    victim.signature_value = bytes(corrupted)
    # Re-link the chain over the corrupted value so the ONLY thing wrong with the
    # record is the cryptography. (Production blocks this UPDATE outright; the
    # point here is to isolate one check, not to claim it is reachable.)
    victim.entry_hash = workflow.chain_entry_hash(
        prev_hash=victim.prev_hash,
        payload_digest=victim.payload_digest,
        signature_value=victim.signature_value,
    )
    db_session.commit()

    report = _report(db_session, package, pki=pki)
    assert report["chain_ok"] is True
    detached = _check(report, verify.CHECK_DETACHED_ATTESTATION)
    assert detached["passed"] is False
    assert "does not verify against this certificate" in detached["detail"]
    failing = next(
        entry
        for entry in detached["evidence"]["signatures"]
        if entry["signature_id"] == str(victim.id)
    )
    assert failing["signature_verifies"] is False
    assert failing["digest_convention"] is None
    # The payload itself is untouched, so its recomputation still succeeds —
    # the failure is localised to the signature bytes.
    assert failing["payload_digest_matches"] is True
    assert _status(report, verify.CHECK_CONTENT_BINDING) == "passed"
    assert report["overall_passed"] is False


def test_a_swapped_certificate_fails_the_detached_check(
    db_session: Session, pki: _Pki
) -> None:
    """Substituting another officer's certificate cannot rescue a signature."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    signatures = workflow.current_signatures(db_session, MAKER, package)
    victim = signatures[0]
    victim.certificate_pem = pki.pem("softkey:approver")
    db_session.commit()

    detached = _check(_report(db_session, package, pki=pki), verify.CHECK_DETACHED_ATTESTATION)
    assert detached["passed"] is False
    assert "certificate_sha256 does not match" in detached["detail"]
    assert "does not verify against this certificate" in detached["detail"]


def test_a_rewritten_statement_fails_the_detached_check(
    db_session: Session, pki: _Pki
) -> None:
    """What-you-see-is-what-you-sign: the wording is inside the signed payload."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    signatures = workflow.current_signatures(db_session, MAKER, package)
    victim = signatures[0]
    victim.statement = "I certify nothing in particular."
    db_session.commit()

    detached = _check(_report(db_session, package, pki=pki), verify.CHECK_DETACHED_ATTESTATION)
    assert detached["passed"] is False
    assert "disagrees with one rebuilt from the" in detached["detail"]
    assert "statement" in detached["detail"]


# --- (d) the hash chain -----------------------------------------------------


def test_broken_hash_chain_is_detected_and_fails_the_report(
    db_session: Session, pki: _Pki
) -> None:
    """A removed or re-ordered signature leaves the next entry's prev_hash dangling."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    signatures = workflow.current_signatures(db_session, MAKER, package)
    victim = signatures[-1]
    victim.prev_hash = workflow.GENESIS_HASH
    db_session.commit()

    report = _report(db_session, package, pki=pki)
    assert report["chain_ok"] is False
    assert report["chain_broken_at"] == str(victim.id)
    # Every individual check still passes: the chain is a SIXTH, independent
    # property, and a green check set with a broken chain must still read red.
    assert all(entry["passed"] for entry in report["checks"])
    assert report["overall_passed"] is False


# --- the PDF path: checks 1, 2 and 5 ---------------------------------------


def _to_pyhanko_signer(pki: _Pki, key_ref: str) -> pyhanko_signers.SimpleSigner:
    key = pki.keys[key_ref]
    return pyhanko_signers.SimpleSigner(
        signing_cert=asn1_x509.Certificate.load(
            pki.certs[key_ref].public_bytes(serialization.Encoding.DER)
        ),
        signing_key=asn1_keys.PrivateKeyInfo.load(
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ),
        cert_registry=SimpleCertificateStore.from_certs(
            [asn1_x509.Certificate.load(pki.ca_cert.public_bytes(serialization.Encoding.DER))]
        ),
    )


def _sign_pdf(
    payload: bytes, pki: _Pki, *, key_ref: str, field_name: str, certify: bool
) -> bytes:
    """One incremental PAdES revision. ``certify=True`` sets DocMDP FILL_FORMS."""
    writer = IncrementalPdfFileWriter(io.BytesIO(payload))
    metadata = (
        pyhanko_signers.PdfSignatureMetadata(
            field_name=field_name,
            certify=True,
            docmdp_permissions=pyhanko_fields.MDPPerm.FILL_FORMS,
            subfilter=pyhanko_fields.SigSeedSubFilter.PADES,
        )
        if certify
        else pyhanko_signers.PdfSignatureMetadata(
            field_name=field_name,
            subfilter=pyhanko_fields.SigSeedSubFilter.PADES,
        )
    )
    out = pyhanko_signers.sign_pdf(
        writer,
        metadata,
        signer=_to_pyhanko_signer(pki, key_ref),
        new_field_spec=pyhanko_fields.SigFieldSpec(sig_field_name=field_name, on_page=0),
    )
    return out.getvalue()


_SIGNED_PDF_PATH = "bog_returns/attestation/BSD3.signed.pdf"


def _store_signed_pdf(
    db: Session,
    storage_client: InMemoryStorageClient,
    package: RegulatoryPackage,
    payload: bytes,
) -> RegulatoryArtifactVersion:
    """Write the signed bytes and append the immutable version row that pins them."""
    slug = db.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    checksum = hashlib.sha256(payload).hexdigest()
    location = StorageLocation(
        institution_slug=slug, tier="outputs", object_path=_SIGNED_PDF_PATH
    )
    stored = storage_client.write(
        location,
        io.BytesIO(payload),
        ObjectMetadata(
            institution_slug=slug,
            tier="outputs",
            checksum_sha256=checksum,
            written_at=datetime.now(UTC),
            written_by="test",
        ),
        content_type="application/pdf",
    )
    version = RegulatoryArtifactVersion(
        organization_id=package.organization_id,
        package_id=package.id,
        kind="pdf",
        object_path=_SIGNED_PDF_PATH,
        storage_version_id=stored.version_id,
        checksum_sha256=checksum,
        size_bytes=len(payload),
    )
    db.add(version)
    db.commit()
    return version


@dataclass(frozen=True)
class _SignedPdf:
    package: RegulatoryPackage
    preparer_version: RegulatoryArtifactVersion
    approver_version: RegulatoryArtifactVersion
    revision_two: bytes


def _build_signed_pdf_package(
    db: Session, storage_client: InMemoryStorageClient, pki: _Pki
) -> _SignedPdf:
    """A real exported PDF, signed twice as incremental revisions, both pinned.

    Faithful to §3.2: the preparer's certification signature covers revision 1,
    the approver's is appended as an incremental update covering the whole file,
    and each signature record pins the exact object version it covered (G2).
    """
    package = _seed(db)
    exported = export_package(db, MAKER, package, "pdf")
    db.commit()
    slug = db.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    _descriptor, stream = storage_client.read(
        StorageLocation(institution_slug=slug, tier="outputs", object_path=exported.object_path)
    )
    base = stream.read()
    assert base.startswith(b"%PDF")

    revision_one = _sign_pdf(
        base, pki, key_ref="softkey:preparer", field_name="Sig_Preparer", certify=True
    )
    preparer_version = _store_signed_pdf(db, storage_client, package, revision_one)
    _certify(
        db,
        MAKER,
        package,
        pki,
        role="preparer",
        key_ref="softkey:preparer",
        artifact_version_id=preparer_version.id,
    )

    revision_two = _sign_pdf(
        revision_one, pki, key_ref="softkey:approver", field_name="Sig_Approver", certify=False
    )
    approver_version = _store_signed_pdf(db, storage_client, package, revision_two)
    _certify(
        db,
        CHECKER,
        package,
        pki,
        role="approver",
        key_ref="softkey:approver",
        artifact_version_id=approver_version.id,
    )
    db.refresh(package)
    assert package.attestation_state == "fully_certified"
    return _SignedPdf(
        package=package,
        preparer_version=preparer_version,
        approver_version=approver_version,
        revision_two=revision_two,
    )


def test_signed_pdf_passes_the_pdf_tamper_and_artifact_checks(
    db_session: Session, storage: InMemoryStorageClient, pki: _Pki
) -> None:
    signed = _build_signed_pdf_package(db_session, storage, pki)

    report = _report(db_session, signed.package, storage_client=storage, pki=pki)
    assert report["overall_passed"] is True
    for name in verify.CHECK_ORDER:
        assert _status(report, name) == "passed", (name, _check(report, name)["detail"])

    pdf_check = _check(report, verify.CHECK_PDF_SIGNATURE)["evidence"]
    assert {entry["field"] for entry in pdf_check["signatures_examined"]} == {
        "Sig_Preparer",
        "Sig_Approver",
    }
    for entry in pdf_check["signatures_examined"]:
        assert entry["intact"] is True
        assert entry["valid"] is True
        assert entry["trusted"] is True
        assert entry["trust_anchor"] == "configured"

    # The two pinned versions live at the SAME object path: resolving the
    # preparer's bytes proves the storage version_id pin (G2) actually works.
    revisions = _check(report, verify.CHECK_INTER_SIGNATURE_TAMPER)["evidence"]["revisions"]
    preparer_only = [
        entry
        for entry in revisions
        if entry["artifact_version_id"] == str(signed.preparer_version.id)
    ]
    assert [entry["field"] for entry in preparer_only] == ["Sig_Preparer"]
    final = [
        entry
        for entry in revisions
        if entry["artifact_version_id"] == str(signed.approver_version.id)
    ]
    assert [entry["field"] for entry in final] == ["Sig_Preparer", "Sig_Approver"]
    assert final[0]["coverage"] == "SignatureCoverageLevel.ENTIRE_REVISION"
    assert final[0]["modification_level"] == "ModificationLevel.FORM_FILLING"
    assert final[1]["coverage"] == "SignatureCoverageLevel.ENTIRE_FILE"


#: The signature boxes ``DEFAULT_PLACEMENTS`` held before the attestation block
#: was ruled into four cells and the stamp was redesigned: two 240×80 boxes in
#: the clear band BELOW the block, no derived text fields. Institutions have
#: templates saved at these coordinates and returns already filed from them.
LEGACY_PLACEMENTS = (
    pdf_signing.FieldPlacement("preparer", 1, (51, 470, 291, 550)),
    pdf_signing.FieldPlacement("approver", 1, (304, 470, 544, 550)),
)


def test_an_artifact_signed_with_the_pre_stamp_layout_still_passes_every_check(
    db_session: Session, storage: InMemoryStorageClient, pki: _Pki
) -> None:
    """Redesigning the stamp and the template must not un-verify what is filed.

    Signing never re-renders (§3.2, gap G12): a signed artifact is the archived
    bytes plus incremental updates, so changing ``exports/pdf.py`` or the stamp
    geometry changes FUTURE exports only. This is that claim made executable —
    a document prepared and signed at the OLD placement geometry, through the
    real ``pdf_signing`` path, still passes all five checks of §3.5 against
    today's code.
    """
    package = _seed(db_session)
    exported = export_package(db_session, MAKER, package, "pdf")
    db_session.commit()
    slug = db_session.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    _descriptor, stream = storage.read(
        StorageLocation(institution_slug=slug, tier="outputs", object_path=exported.object_path)
    )
    prepared = pdf_signing.prepare_signature_fields(stream.read(), placements=LEGACY_PLACEMENTS)

    profile = pdf_signing.PadesProfile(use_pades_lta=False, embed_validation_info=False)
    revision_one = pdf_signing.sign_as_preparer(
        prepared,
        signer=_to_pyhanko_signer(pki, "softkey:preparer"),
        appearance=_legacy_appearance("preparer"),
        profile=profile,
    )
    preparer_version = _store_signed_pdf(db_session, storage, package, revision_one)
    _certify(
        db_session,
        MAKER,
        package,
        pki,
        role="preparer",
        key_ref="softkey:preparer",
        artifact_version_id=preparer_version.id,
    )
    revision_two = pdf_signing.sign_as_approver(
        revision_one,
        signer=_to_pyhanko_signer(pki, "softkey:approver"),
        appearance=_legacy_appearance("approver"),
        profile=profile,
    )
    approver_version = _store_signed_pdf(db_session, storage, package, revision_two)
    _certify(
        db_session,
        CHECKER,
        package,
        pki,
        role="approver",
        key_ref="softkey:approver",
        artifact_version_id=approver_version.id,
    )
    db_session.refresh(package)

    report = _report(db_session, package, storage_client=storage, pki=pki)
    assert report["overall_passed"] is True
    for name in verify.CHECK_ORDER:
        assert _status(report, name) == "passed", (name, _check(report, name)["detail"])
    # The archived export is still a literal prefix of both signed revisions, so
    # nothing was re-rendered underneath the signatures.
    assert revision_two.startswith(revision_one)
    assert revision_one.startswith(prepared)


def _legacy_appearance(role: str) -> pdf_signing.SignatureAppearance:
    return pdf_signing.SignatureAppearance(
        role_label=pdf_signing.label_for_role(role),
        signer_name="Ama Mensah",
        officer_title="Chief Financial Officer",
        signer_id="SGN-7K4M9PQR2VWX3YZ8",
        signed_at=datetime(2026, 7, 31, 14, 2, 11, tzinfo=UTC),
        timestamped=False,
    )


def test_a_page_added_after_signing_fails_the_tamper_check(
    db_session: Session, storage: InMemoryStorageClient, pki: _Pki
) -> None:
    """The question this check exists for: did anything change between signers?"""
    signed = _build_signed_pdf_package(db_session, storage, pki)

    writer = IncrementalPdfFileWriter(io.BytesIO(signed.revision_two))
    writer.insert_page(
        generic.DictionaryObject(
            {
                generic.pdf_name("/Type"): generic.pdf_name("/Page"),
                generic.pdf_name("/MediaBox"): generic.ArrayObject(
                    [
                        generic.NumberObject(0),
                        generic.NumberObject(0),
                        generic.NumberObject(595),
                        generic.NumberObject(842),
                    ]
                ),
            }
        )
    )
    buffer = io.BytesIO()
    writer.write(buffer)
    tampered = buffer.getvalue()

    slug = db_session.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    location = StorageLocation(
        institution_slug=slug, tier="outputs", object_path=_SIGNED_PDF_PATH
    )
    replacement = storage.write(
        location,
        io.BytesIO(tampered),
        ObjectMetadata(
            institution_slug=slug,
            tier="outputs",
            checksum_sha256=hashlib.sha256(tampered).hexdigest(),
            written_at=datetime.now(UTC),
            written_by="test",
        ),
        content_type="application/pdf",
    )

    # Stage 1: the pinned version still resolves the untampered bytes, so the
    # substitution is invisible until the pin itself is repointed. That is the
    # version pin doing its job.
    assert _report(db_session, signed.package, storage_client=storage, pki=pki)["overall_passed"]

    # Stage 2: repoint the pin at the tampered object and re-record its checksum
    # — the most thorough tamper available to someone with database access.
    version = signed.approver_version
    version.storage_version_id = replacement.version_id
    version.checksum_sha256 = hashlib.sha256(tampered).hexdigest()
    version.size_bytes = len(tampered)
    db_session.commit()

    report = _report(db_session, signed.package, storage_client=storage, pki=pki)
    # The bytes now hash to what the record claims, so the artifact check is
    # satisfied — and the tamper is caught anyway, by diff analysis.
    assert _status(report, verify.CHECK_ARTIFACT_BINDING) == "passed"
    tamper = _check(report, verify.CHECK_INTER_SIGNATURE_TAMPER)
    assert tamper["passed"] is False
    assert "ModificationLevel.OTHER" in tamper["detail"]
    # The offending object is named, not merely "something changed".
    assert "page tree" in tamper["detail"] or "Reference(idnum=" in tamper["detail"]
    assert report["overall_passed"] is False


def test_replaced_artifact_bytes_fail_the_artifact_binding_check(
    db_session: Session, storage: InMemoryStorageClient, pki: _Pki
) -> None:
    """Re-hash the filed object against the checksum the signature pinned."""
    signed = _build_signed_pdf_package(db_session, storage, pki)
    version = signed.approver_version
    version.checksum_sha256 = "0" * 64
    db_session.commit()

    report = _report(db_session, signed.package, storage_client=storage, pki=pki)
    artifact = _check(report, verify.CHECK_ARTIFACT_BINDING)
    assert artifact["passed"] is False
    assert "are not the bytes that were signed" in artifact["detail"]
    finding = next(
        entry
        for entry in artifact["evidence"]["artifacts"]
        if entry["artifact_version_id"] == str(version.id)
    )
    assert finding["recomputed_checksum"] != finding["recorded_checksum"]


def test_unreadable_artifact_skips_the_pdf_checks_but_fails_artifact_binding(
    db_session: Session, storage: InMemoryStorageClient, pki: _Pki
) -> None:
    """Unresolvable bytes are an availability problem, not an invalid signature."""
    signed = _build_signed_pdf_package(db_session, storage, pki)
    signed.approver_version.object_path = "bog_returns/attestation/does-not-exist.pdf"
    db_session.commit()

    report = _report(db_session, signed.package, storage_client=storage, pki=pki)
    assert _status(report, verify.CHECK_PDF_SIGNATURE) == "skipped"
    assert _status(report, verify.CHECK_INTER_SIGNATURE_TAMPER) == "skipped"
    artifact = _check(report, verify.CHECK_ARTIFACT_BINDING)
    assert artifact["passed"] is False
    assert "unresolvable" in artifact["detail"]


def test_an_unreadable_artifact_never_masks_a_real_pdf_failure(
    db_session: Session, storage: InMemoryStorageClient, pki: _Pki
) -> None:
    """A skip must never hide a failure found on another pinned version."""
    signed = _build_signed_pdf_package(db_session, storage, pki)
    versions = list(
        db_session.scalars(
            select(RegulatoryArtifactVersion).where(
                RegulatoryArtifactVersion.package_id == signed.package.id,
                RegulatoryArtifactVersion.kind == "pdf",
            )
        )
    )
    signatures = workflow.current_signatures(db_session, MAKER, signed.package)
    evidence = [
        verify._ArtifactEvidence(  # pyright: ignore[reportPrivateUsage]
            signature_id=signatures[0].id,
            version=versions[0],
            payload=None,
            error="the stored object could not be read",
        ),
        verify._ArtifactEvidence(  # pyright: ignore[reportPrivateUsage]
            signature_id=signatures[-1].id,
            version=versions[-1],
            payload=b"%PDF-1.7 this is not a parseable document",
            error=None,
        ),
    ]
    result = verify._check_pdf_signature(  # pyright: ignore[reportPrivateUsage]
        signatures, evidence, []
    )
    assert result.status == "failed"
    assert "could not be read as a PDF" in result.detail
    tamper = verify._check_inter_signature_tamper(  # pyright: ignore[reportPrivateUsage]
        signatures, evidence, []
    )
    assert tamper.status == "failed"


# --- (e) the offline CLI ----------------------------------------------------


def _write_bundle(db: Session, package: RegulatoryPackage, target: Path) -> dict[str, Any]:
    bundle = verify.export_verification_bundle(db, MAKER, package)
    target.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return bundle


def test_offline_cli_verifies_a_clean_record_with_no_database(
    db_session: Session, storage: InMemoryStorageClient, pki: _Pki, tmp_path: Path
) -> None:
    signed = _build_signed_pdf_package(db_session, storage, pki)
    record = tmp_path / "attestation.json"
    _write_bundle(db_session, signed.package, record)
    pdf = tmp_path / "BSD3.pdf"
    pdf.write_bytes(signed.revision_two)
    root = tmp_path / "root.pem"
    root.write_bytes(pki.ca_pem)

    exit_code = cli.main(
        ["--record", str(record), "--pdf", str(pdf), "--trust-root", str(root)]
    )
    assert exit_code == 0


def test_offline_cli_exits_non_zero_on_a_tampered_pdf(
    db_session: Session, storage: InMemoryStorageClient, pki: _Pki, tmp_path: Path
) -> None:
    signed = _build_signed_pdf_package(db_session, storage, pki)
    record = tmp_path / "attestation.json"
    _write_bundle(db_session, signed.package, record)
    root = tmp_path / "root.pem"
    root.write_bytes(pki.ca_pem)

    tampered = bytearray(signed.revision_two)
    # Rewrite a byte inside the document body, well before the xref table: the
    # signed byte range covers it, so the signature must break.
    tampered[len(tampered) // 3] ^= 0x01
    pdf = tmp_path / "BSD3-tampered.pdf"
    pdf.write_bytes(bytes(tampered))

    exit_code = cli.main(
        ["--record", str(record), "--pdf", str(pdf), "--trust-root", str(root)]
    )
    assert exit_code == 1
    results = cli.run_checks(
        json.loads(record.read_text("utf-8")), pdf, cli._load_trust_roots([root])
    )
    failed = {result.name for result in results if result.status == "FAIL"}
    # The checksum pin catches it even if the PDF parser somehow does not.
    assert "artifact_checksum" in failed


def test_offline_cli_exits_non_zero_on_a_tampered_record(
    db_session: Session, pki: _Pki, tmp_path: Path
) -> None:
    """A forged signature value in the exported record is caught with no PDF at all."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)
    record = tmp_path / "attestation.json"
    bundle = _write_bundle(db_session, package, record)
    root = tmp_path / "root.pem"
    root.write_bytes(pki.ca_pem)

    assert cli.main(["--record", str(record), "--trust-root", str(root)]) == 0

    forged = json.loads(record.read_text("utf-8"))
    forged["signatures"][0]["signature_value_b64"] = bundle["signatures"][1][
        "signature_value_b64"
    ]
    record.write_text(json.dumps(forged), encoding="utf-8")
    assert cli.main(["--record", str(record), "--trust-root", str(root)]) == 1


def test_offline_cli_detects_a_rewritten_figures_digest(
    db_session: Session, pki: _Pki, tmp_path: Path
) -> None:
    """The offline recompute: the digest must follow from the declared identity."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)
    record = tmp_path / "attestation.json"
    bundle = _write_bundle(db_session, package, record)

    bundle["package"]["content_digest"] = "f" * 64
    record.write_text(json.dumps(bundle), encoding="utf-8")
    results = cli.run_checks(json.loads(record.read_text("utf-8")), None, [])
    failed = {result.name for result in results if result.status == "FAIL"}
    assert "certification_digest" in failed
    assert cli.main(["--record", str(record)]) == 1


def test_offline_cli_detects_signers_who_covered_different_figures(
    db_session: Session, pki: _Pki, tmp_path: Path
) -> None:
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)
    record = tmp_path / "attestation.json"
    bundle = _write_bundle(db_session, package, record)

    bundle["signatures"][1]["certification_digest"] = "a" * 64
    record.write_text(json.dumps(bundle), encoding="utf-8")
    results = {
        result.name: result.status
        for result in cli.run_checks(json.loads(record.read_text("utf-8")), None, [])
    }
    assert results["signer_agreement"] == "FAIL"
    assert results["certification_digest"] == "FAIL"


def test_offline_cli_refuses_unknown_inputs_with_a_distinct_exit_code(
    tmp_path: Path,
) -> None:
    """Exit 2 is "could not run"; an operator must never read it as a pass."""
    missing = tmp_path / "nope.json"
    assert cli.main(["--record", str(missing)]) == 2

    wrong_schema = tmp_path / "wrong.json"
    wrong_schema.write_text(json.dumps({"schema": "something-else"}), encoding="utf-8")
    assert cli.main(["--record", str(wrong_schema)]) == 2


def test_offline_cli_canonicalisation_matches_the_platform(
    db_session: Session, pki: _Pki
) -> None:
    """The CLI duplicates the recipe on purpose; this is what stops it drifting."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)
    bundle = verify.export_verification_bundle(db_session, MAKER, package)

    payload = {"z": [3, 2, 1], "a": {"nested": "ünïcode", "n": None}, "b": True}
    assert cli.canonical_json(payload) == digests.canonical_json(payload)
    assert cli.digest_of(payload) == digests.digest_of(payload)
    assert cli.sha256_hex("aequoros") == digests.sha256_hex("aequoros")

    signature = bundle["signatures"][0]
    assert cli.attestation_payload(
        certification_digest_value=signature["certification_digest"],
        signer_id=signature["signer_id"],
        signing_role=signature["signing_role"],
        officer_title=signature["officer_title"],
        statement=signature["statement"],
        declared_at=signature["declared_at"],
    ) == digests.attestation_payload(
        certification_digest_value=signature["certification_digest"],
        signer_id=signature["signer_id"],
        signing_role=signature["signing_role"],
        officer_title=signature["officer_title"],
        statement=signature["statement"],
        declared_at=signature["declared_at"],
    )

    # The independently reimplemented certification digest must land on the exact
    # value the platform signed — otherwise the offline tool is worthless.
    assert cli.certification_digest(bundle["package"]) == signature["certification_digest"]
    assert cli.RECORD_SCHEMA == verify.RECORD_SCHEMA
    assert cli.ATTESTATION_SCHEMA == digests.ATTESTATION_SCHEMA
    assert cli.SIGNATURE_PAYLOAD_SCHEMA == digests.SIGNATURE_PAYLOAD_SCHEMA


def test_offline_cli_recomputes_a_master_data_digest(db_session: Session) -> None:
    """The ``LRT-*`` packs bind no engine run; the offline recompute must handle both."""
    package_identity = {
        "organization_id": DEMO_ORG_ID,
        "bank_id": SAMPLE_BANK_ID,
        "package_id": "0198f000-0000-7000-8000-000000000001",
        "package_version": 1,
        "return_code": "LRT-PROFILE",
        "reporting_date": "2026-03-31",
        "basis": "solo",
        "binding_class": "master_data",
        "content_digest": "b" * 64,
        "register_state_digest": "c" * 64,
        "source_runs": [],
    }
    assert cli.certification_digest(package_identity) == digests.certification_digest(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        package_id="0198f000-0000-7000-8000-000000000001",
        package_version=1,
        return_code="LRT-PROFILE",
        reporting_date="2026-03-31",
        basis="solo",
        content_digest_value="b" * 64,
        binding_class="master_data",
        register_state_digest_value="c" * 64,
    )


# --- tenancy ----------------------------------------------------------------


def test_verification_is_tenant_scoped(db_session: Session, pki: _Pki) -> None:
    """Another tenant sees no signatures — and therefore no green verdict to steal."""
    package = _seed(db_session)
    _fully_certify(db_session, pki, package)

    other = TenantContext(organization_id="OR-NOSUCH1", actor_user_id=DEMO_USER_ID)
    report = verify.verify_attestation(db_session, other, package)
    assert report["signatures"] == []
    assert _status(report, verify.CHECK_DETACHED_ATTESTATION) == "skipped"
    # An empty signature set must never read as "verified": the content check
    # still runs, and the chain walk over an empty chain is trivially intact,
    # but nothing here asserts that this package was ever signed.
    assert report["chain_ok"] is True
