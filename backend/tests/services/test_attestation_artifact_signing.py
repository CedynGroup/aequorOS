"""The certification ceremony, wired onto the document
(app/services/attestation/artifact_signing.py — docs §3.2).

Nothing below the subject is faked. The software custody backend generates and
seals a real key, ``SignerKeyService`` issues a real certificate, the real
``signing.certify`` runs the real guards, the real export engine renders a real
BSD3 return, and pyHanko validates the file that comes out. The only stand-ins
are the object store (in-memory, the same seam the export suite uses) and the
step-up ceremony (minted directly — it has its own tests).

The load-bearing assertions are the three properties that would be DEFECTS if
they changed:

* both signatures validate independently on the final document;
* the approver's revision leaves the preparer's bytes untouched, as a literal
  prefix — so the preparer's byte range still covers what it covered;
* a certification that cannot produce a signed document records NO signature
  row, because a signature claiming a signed PDF that does not exist is worse
  evidence than no signature at all.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import SignatureCoverageLevel, validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import (
    AttestationSignature,
    Bank,
    BankReportingPeriod,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    ReturnSigningPolicy,
    SignerKey,
    User,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import RegulatoryPackageCreate
from app.services import regulatory_liquidity
from app.services.attestation import pdf_signing, placements, signing, stepup, workflow
from app.services.attestation.identity import ensure_signer_identity
from app.services.attestation.keys import SignerKeyService
from app.services.attestation.signers import get_raw_signer
from app.services.regulatory_reporting import artifact_versions, generation, validation
from app.services.regulatory_reporting import workflow as reporting_workflow
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from app.storage.client import StorageLocation
from tests.storage.inmemory import InMemoryStorageClient

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
CHECKER_USER_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CHECKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=CHECKER_USER_ID)
REPORTING_DATE = date(2026, 3, 31)
VAULT_KEY = "test-vault-master-key-not-for-production-0002"


@pytest.fixture(autouse=True)
def signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deployment that can actually sign: pepper, vault, soft-key store.

    ``SIGNING_BACKEND=software`` is what makes the pyHanko bridge real in this
    suite — ``get_pdf_signer`` builds a ``SimpleSigner`` from the same sealed
    key the detached attestation is made with, exactly as production would from
    a token.
    """
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
    """One object store behind both writers — the exporter and the signer.

    They are separate module-level bindings on purpose (the signer must not
    reach through the export engine for its client), so both are redirected.
    """
    client = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client", lambda: client
    )
    monkeypatch.setattr(
        "app.services.attestation.artifact_signing.get_storage_client", lambda: client
    )
    return client


# --- fixture data -----------------------------------------------------------


def _seed(db: Session, *, require_signed_pdf: bool = True, roles: Any = None) -> RegulatoryPackage:
    """A validated BSD3 package under a policy that demands a signed PDF."""
    materialize_canonical_test_book(db)
    if db.scalar(select(User.id).where(User.id == CHECKER_USER_ID)) is None:
        db.add(
            User(
                id=CHECKER_USER_ID,
                organization_id=DEMO_ORG_ID,
                email="artifact.approver@example.test",
                display_name="Ama Mensah",
                job_title="Chief Financial Officer",
            )
        )
        db.commit()
    db.add(
        ReturnSigningPolicy(
            organization_id=DEMO_ORG_ID,
            bank_id=SAMPLE_BANK_ID,
            return_code="BSD3",
            required_signatures=roles
            or [
                {"role": "preparer", "min_count": 1, "officer_titles": []},
                {"role": "approver", "min_count": 1, "officer_titles": []},
            ],
            required_attachments=[],
            require_signature=True,
            require_signed_pdf=require_signed_pdf,
            distinct_signers=True,
            effective_from=date(2026, 1, 1),
            reason="Test policy: BSD3 is signed on the document itself.",
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
    return package


def _enrol(db: Session, ctx: TenantContext, display_name: str) -> str:
    """Provision the signer identity and enrol a real soft key + certificate."""
    assert ctx.actor_user_id is not None
    identity = ensure_signer_identity(db, ctx, ctx.actor_user_id)
    service = SignerKeyService(db, ctx)
    if service.active_key(identity.signer_id) is None:
        service.issue(
            signer_id=identity.signer_id,
            display_name=display_name,
            organization_name="Sample Bank",
        )
    db.commit()
    return identity.signer_id


def _certify(  # noqa: PLR0913 - mirrors signing.certify's own irreducible inputs
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    role: str,
    display_name: str,
    backends: signing.SigningBackends | None = None,
) -> AttestationSignature:
    """Drive the real ceremony, minting the step-up authorisation directly."""
    assert ctx.actor_user_id is not None
    signer_id = _enrol(db, ctx, display_name)
    binding = workflow.compute_binding(package)
    token, _row = stepup.mint_authorization(
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
        backends=backends or signing.SigningBackends(raw_signer=get_raw_signer()),
    )


def _fully_certify(db: Session, package: RegulatoryPackage) -> list[AttestationSignature]:
    preparer = _certify(db, MAKER, package, role="preparer", display_name="Kwesi Owusu")
    approver = _certify(db, CHECKER, package, role="approver", display_name="Ama Mensah")
    db.refresh(package)
    assert package.attestation_state == "fully_certified"
    return [preparer, approver]


# --- reading the result back ------------------------------------------------


def _version(db: Session, signature: AttestationSignature) -> RegulatoryArtifactVersion:
    assert signature.artifact_version_id is not None, "the signature pinned no document"
    version = db.scalar(
        select(RegulatoryArtifactVersion).where(
            RegulatoryArtifactVersion.id == signature.artifact_version_id
        )
    )
    assert version is not None
    return version


def _bytes(
    db: Session, storage: InMemoryStorageClient, version: RegulatoryArtifactVersion
) -> bytes:
    slug = db.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    _descriptor, stream = storage.read(
        StorageLocation(institution_slug=slug, tier="outputs", object_path=version.object_path),
        version_id=version.storage_version_id,
    )
    return stream.read()


def _trust(db: Session) -> ValidationContext:
    """Anchor on the certificates the platform itself enrolled.

    The dev path self-signs, so each officer's certificate is its own root —
    which is exactly what the design says a self-signed enrolment buys
    (verifiable, not third-party trusted; legal register L4).
    """
    anchors = [
        asn1_x509.Certificate.load(
            x509.load_pem_x509_certificate(pem.encode("ascii")).public_bytes(
                serialization.Encoding.DER
            )
        )
        for pem in db.scalars(select(SignerKey.certificate_pem))
    ]
    return ValidationContext(
        trust_roots=anchors, allow_fetching=False, revocation_mode="soft-fail"
    )


def _signatures_count(db: Session) -> int:
    return len(list(db.scalars(select(AttestationSignature.id))))


# --- (a) both signatures land on the document and verify ---------------------


def test_both_signatures_are_placed_on_the_document_and_validate_independently(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    package = _seed(db_session)
    preparer, approver = _fully_certify(db_session, package)

    final = _bytes(db_session, storage, _version(db_session, approver))
    reader = PdfFileReader(io.BytesIO(final))
    embedded = reader.embedded_regular_signatures
    assert [sig.field_name for sig in embedded] == [
        pdf_signing.PREPARER_FIELD_NAME,
        pdf_signing.APPROVER_FIELD_NAME,
    ]

    trust = _trust(db_session)
    statuses = [
        validate_pdf_signature(sig, signer_validation_context=trust) for sig in embedded
    ]
    for status in statuses:
        assert status.intact, status.summary()
        assert status.valid, status.summary()
        assert status.trusted, status.summary()

    # The preparer certified (DocMDP) revision 1; the approver's covers the
    # whole file. Anything else means the two are not layered as §3.2 requires.
    assert statuses[0].coverage == SignatureCoverageLevel.ENTIRE_REVISION
    assert statuses[1].coverage == SignatureCoverageLevel.ENTIRE_FILE
    assert reader.embedded_signatures[0].field_name == pdf_signing.PREPARER_FIELD_NAME

    # Each database record names the document it is embedded in, and the two
    # are different revisions of the same return.
    assert preparer.artifact_version_id != approver.artifact_version_id


def test_the_signer_appears_on_the_attestation_page_of_the_filed_document(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The whole point of the feature: the officer is visible ON the page.

    Read back the way a PDF reader would — the appearance streams of every
    widget on the page, decoded with the WinAnsi codec the font resource
    declares. Both families count: the stamp draws the role label and the
    permanent signer ID, and the form's own ruled cells carry the officer's name
    and designation, which is where the regulator asked for them.
    """
    package = _seed(db_session)
    _preparer, approver = _fully_certify(db_session, package)
    final = _bytes(db_session, storage, _version(db_session, approver))

    drawn = _appearance_text(final)
    assert "Prepared by" in drawn
    assert "Approved by" in drawn
    assert "Ama Mensah" in drawn
    assert "Chief Financial Officer" in drawn
    signatures = db_session.scalars(select(AttestationSignature)).all()
    for signature in signatures:
        # The permanent signer id must travel with the document: attribution
        # has to survive a printout and a deprovisioned user (§2.5, §2.6).
        assert signature.signer_id in drawn


def _appearance_text(
    pdf: bytes, *, page_index: int = pdf_signing.ATTESTATION_PAGE_INDEX
) -> str:
    """Every string drawn into a signature widget's appearance, concatenated."""
    import re  # noqa: PLC0415 - local to this reader

    from pyhanko.pdf_utils import generic  # noqa: PLC0415 - local to this reader

    reader = PdfFileReader(io.BytesIO(pdf))
    page = reader.root["/Pages"]["/Kids"][page_index].get_object()
    chunks: list[str] = []

    def walk(stream: Any, depth: int = 0) -> None:
        if not isinstance(stream, generic.StreamObject) or depth > 4:
            return
        chunks.extend(
            bytes.fromhex(run.decode("ascii")).decode("cp1252")
            for run in re.findall(rb"<([0-9a-fA-F]+)>\s*Tj", stream.data)
        )
        for child in stream.get("/Resources", {}).get("/XObject", {}).values():
            walk(child.get_object(), depth + 1)

    for annot in page.get("/Annots", []):
        appearance = annot.get_object().get("/AP")
        if appearance is not None:
            walk(appearance["/N"].get_object())
    return "\n".join(chunks)


# --- (b) the approver appends; the preparer's bytes are untouched ------------


def test_the_approver_appends_and_leaves_the_preparers_bytes_a_literal_prefix(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The determinism rule, asserted on real archived objects.

    If the approver's step re-rendered the document — or if the two revisions
    shared one object path on a backend that could not version it — the
    preparer's pinned bytes would stop being a prefix, and the byte range its
    signature covers would no longer describe anything.
    """
    package = _seed(db_session)
    preparer, approver = _fully_certify(db_session, package)

    preparer_version = _version(db_session, preparer)
    approver_version = _version(db_session, approver)
    revision_one = _bytes(db_session, storage, preparer_version)
    revision_two = _bytes(db_session, storage, approver_version)

    assert revision_two.startswith(revision_one)
    assert len(revision_two) > len(revision_one)
    # Distinct objects, so the preparer's exact bytes remain fetchable even
    # where the backend reports no version id.
    assert preparer_version.object_path != approver_version.object_path

    # The preparer's own document still carries exactly one signature, and it
    # still validates on its own — that is what "untouched" has to mean.
    solo = PdfFileReader(io.BytesIO(revision_one)).embedded_regular_signatures
    assert [sig.field_name for sig in solo] == [pdf_signing.PREPARER_FIELD_NAME]
    status = validate_pdf_signature(solo[0], signer_validation_context=_trust(db_session))
    assert status.intact and status.valid

    # And the unsigned export is still archived beneath both of them.
    versions = db_session.scalars(
        select(RegulatoryArtifactVersion)
        .where(RegulatoryArtifactVersion.package_id == package.id)
        .order_by(RegulatoryArtifactVersion.created_at, RegulatoryArtifactVersion.id)
    ).all()
    assert len(versions) == 3
    unsigned = _bytes(db_session, storage, versions[0])
    assert not PdfFileReader(io.BytesIO(unsigned)).embedded_signatures
    assert revision_one.startswith(unsigned)


# --- (c) a document that cannot be signed records nothing --------------------


def test_a_backend_that_cannot_sign_the_document_records_no_signature(
    db_session: Session, storage: InMemoryStorageClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The KMS backend is a documented stub, so it cannot produce a Signer.

    The detached signer is still injected and working: this proves the failure
    is specifically about the DOCUMENT, and that half a ceremony is not allowed
    to persist. A signature row here would assert a signed PDF that does not
    exist anywhere.
    """
    package = _seed(db_session)
    working = get_raw_signer()
    # Enrol while the software backend is still configured: the subject here is
    # a deployment whose keys exist but whose backend cannot drive pyHanko.
    _enrol(db_session, MAKER, "Kwesi Owusu")
    monkeypatch.setenv("SIGNING_BACKEND", "kms")
    get_settings.cache_clear()

    with pytest.raises(Exception) as raised:  # noqa: PT011 - the code is the assertion
        _certify(
            db_session,
            MAKER,
            package,
            role="preparer",
            display_name="Kwesi Owusu",
            backends=signing.SigningBackends(raw_signer=working),
        )
    assert getattr(raised.value, "status_code", None) == 503
    assert raised.value.detail["error_code"] == "signed_pdf_signer_unavailable"  # type: ignore[attr-defined]

    # The request session would be closed (and so rolled back) by the API
    # dependency; do the same before reading, then prove nothing survived.
    db_session.rollback()
    assert _signatures_count(db_session) == 0
    db_session.refresh(package)
    assert package.attestation_state == "unsigned"
    assert package.status == "validated"


def test_a_placement_off_the_page_fails_the_certification(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """Placement is checked against the real document, and a failure is total.

    This box is large enough to pass the authoring checks in
    ``attestation.placements`` — which cannot know the page size, having no PDF —
    and lands off the right edge of A4. That gap is exactly why the media-box
    check lives in ``pdf_signing`` as well, and this asserts the refusal reaches
    all the way out and takes the whole certification with it rather than being
    trimmed to fit.
    """
    package = _seed(db_session)
    placements.set_package_override(
        db_session,
        MAKER,
        package,
        placements=[
            pdf_signing.FieldPlacement("preparer", 1, (400, 470, 700, 550)),
            pdf_signing.FieldPlacement("approver", 1, (51, 300, 291, 380)),
        ],
        reason="test: a box that falls off the page",
    )
    db_session.commit()

    with pytest.raises(Exception) as raised:  # noqa: PT011 - the code is the assertion
        _certify(db_session, MAKER, package, role="preparer", display_name="Kwesi Owusu")
    assert raised.value.detail["error_code"] == "signed_pdf_failed"  # type: ignore[attr-defined]
    assert "falls outside" in raised.value.detail["message"]  # type: ignore[attr-defined]

    db_session.rollback()
    assert _signatures_count(db_session) == 0


def test_a_placed_field_lands_where_the_override_put_it(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The override reaches the filed document — the founder's flow, end to end.

    A preparer places both boxes (their own AND the approver's, because the DocMDP
    certification forbids adding a field later), certifies, and the approver signs
    into the field the preparer positioned.
    """
    package = _seed(db_session)
    boxes = {
        pdf_signing.PREPARER_FIELD_NAME: (60, 260, 300, 345),
        pdf_signing.APPROVER_FIELD_NAME: (310, 260, 550, 345),
    }
    placements.set_package_override(
        db_session,
        MAKER,
        package,
        placements=[
            pdf_signing.FieldPlacement(
                "preparer", 2, boxes[pdf_signing.PREPARER_FIELD_NAME]
            ),
            pdf_signing.FieldPlacement(
                "approver", 2, boxes[pdf_signing.APPROVER_FIELD_NAME]
            ),
        ],
        reason="test: the preparer places both fields in the workspace",
    )
    db_session.commit()

    _preparer, approver = _fully_certify(db_session, package)
    final = _bytes(db_session, storage, _version(db_session, approver))
    reader = PdfFileReader(io.BytesIO(final))
    page = reader.root["/Pages"]["/Kids"][2].get_object()
    placed = {
        str(annot.get_object()["/T"]): tuple(
            int(coord) for coord in annot.get_object()["/Rect"]
        )
        for annot in page["/Annots"]
    }
    assert placed == boxes


def test_a_role_with_no_field_on_the_document_is_refused_not_skipped(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """A board signature cannot be placed on the artifact, so it is refused.

    The tempting alternative — record the row and skip the document for roles
    the artifact has no field for — would make ``require_signed_pdf`` quietly
    untrue for exactly the signature a board attestation exists to provide.
    """
    package = _seed(
        db_session,
        roles=[
            {"role": "preparer", "min_count": 1, "officer_titles": []},
            {"role": "board", "min_count": 1, "officer_titles": []},
        ],
    )
    _certify(db_session, MAKER, package, role="preparer", display_name="Kwesi Owusu")

    with pytest.raises(Exception) as raised:  # noqa: PT011 - the code is the assertion
        _certify(db_session, CHECKER, package, role="board", display_name="Ama Mensah")
    assert raised.value.detail["error_code"] == "signed_pdf_role_unsupported"  # type: ignore[attr-defined]

    db_session.rollback()
    assert _signatures_count(db_session) == 1


def test_an_approver_cannot_sign_a_document_the_preparer_never_signed(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The policy can change between the two signatures; the document cannot.

    A preparer who certified while ``require_signed_pdf`` was off leaves no
    signed base to append to. Appending to the unsigned export instead would
    produce a document whose approver signature covers a revision the preparer
    never saw.
    """
    package = _seed(db_session, require_signed_pdf=False)
    preparer = _certify(db_session, MAKER, package, role="preparer", display_name="Kwesi Owusu")
    assert preparer.artifact_version_id is None

    policy = db_session.scalar(select(ReturnSigningPolicy))
    assert policy is not None
    policy.require_signed_pdf = True
    db_session.commit()

    with pytest.raises(Exception) as raised:  # noqa: PT011 - the code is the assertion
        _certify(db_session, CHECKER, package, role="approver", display_name="Ama Mensah")
    assert raised.value.detail["error_code"] == "signed_pdf_base_missing"  # type: ignore[attr-defined]

    db_session.rollback()
    assert _signatures_count(db_session) == 1


# --- the flag actually drives the behaviour ---------------------------------


def test_a_policy_that_does_not_require_a_signed_pdf_signs_no_document(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The control case: ``require_signed_pdf`` is the switch, and it is real.

    With the flag off the detached attestation is still produced and the
    ceremony still completes — nothing is signed on the artifact, and no
    artifact version is pinned.
    """
    package = _seed(db_session, require_signed_pdf=False)
    signatures = _fully_certify(db_session, package)

    assert [signature.artifact_version_id for signature in signatures] == [None, None]
    assert not db_session.scalars(select(RegulatoryArtifactVersion.id)).all()


def test_a_supplied_artifact_version_must_belong_to_the_package(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The injection seam is validated, not trusted.

    ``artifact_version_id`` is the one claim on the row nothing else can check
    ("these are the bytes I signed"), so a caller cannot pin a version that has
    nothing to do with this return.
    """
    package = _seed(db_session, require_signed_pdf=False)
    with pytest.raises(Exception) as raised:  # noqa: PT011 - the code is the assertion
        signing.certify(
            db_session,
            MAKER,
            package,
            role="preparer",
            authorization_token=_authorization(db_session, MAKER, package, "preparer"),
            backends=signing.SigningBackends(raw_signer=get_raw_signer()),
            artifact_version_id=UUID("11111111-1111-4111-8111-111111111111"),
        )
    assert raised.value.detail["error_code"] == "artifact_version_unknown"  # type: ignore[attr-defined]

    db_session.rollback()
    assert _signatures_count(db_session) == 0


def _authorization(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, role: str
) -> str:
    assert ctx.actor_user_id is not None
    signer_id = _enrol(db, ctx, "Kwesi Owusu")
    token, _row = stepup.mint_authorization(
        db,
        ctx,
        user_id=ctx.actor_user_id,
        signer_id=signer_id,
        package_id=package.id,
        signing_role=role,
        certification_digest=workflow.compute_binding(package).certification_digest,
        auth_evidence={"method": "password_reauth"},
    )
    return token


# --- a voided attestation must be re-certifiable -----------------------------


def test_a_voided_attestation_can_be_certified_again_on_a_fresh_document(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """Withdrawing an attestation must not strand the return.

    A void is a normal, audited act, and the whole point of it is that the
    figures get another pass. If the second preparer certification could not
    obtain a document to sign, the void would be a one-way door — the return
    could never be filed again. The voided cycle's signed bytes must survive
    intact alongside the new ones, because a withdrawn attestation stays
    legible forever (legal register L15).
    """
    package = _seed(db_session)
    first = _certify(db_session, MAKER, package, role="preparer", display_name="Kwesi Owusu")
    voided_bytes = _bytes(db_session, storage, _version(db_session, first))

    workflow.void_attestation(db_session, MAKER, package, reason="e2e: figures restated")
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    db_session.refresh(package)
    assert package.attestation_state == "unsigned"
    assert package.attestation_cycle == 2

    second = _fully_certify(db_session, package)
    assert all(signature.attestation_cycle == 2 for signature in second)

    # The withdrawn cycle's document is still exactly where its signature says.
    assert _bytes(db_session, storage, _version(db_session, first)) == voided_bytes
    assert _version(db_session, first).object_path != _version(
        db_session, second[0]
    ).object_path


# --- the verifier accepts what the ceremony produced -------------------------


def test_the_verifier_still_passes_with_placed_fields_and_adopted_marks(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The three new features must not weaken the five checks.

    Placement moves the widgets, and an adopted mark adds an image XObject inside
    the appearance stream — both of which land in the signed byte ranges and in
    the diff analysis between the two revisions. If either could push the
    inter-signature modification level above ``FORM_FILLING``, a legitimately
    signed return would be reported as tampered.
    """
    from app.services.attestation import appearance  # noqa: PLC0415 - heavy module
    from app.services.attestation import verify as verifier  # noqa: PLC0415

    package = _seed(db_session)
    placements.set_package_override(
        db_session,
        MAKER,
        package,
        placements=[
            pdf_signing.FieldPlacement("preparer", 2, (60, 260, 300, 345)),
            pdf_signing.FieldPlacement("approver", 2, (310, 260, 550, 345)),
        ],
        reason="test: placed in the workspace",
    )
    for ctx, name in ((MAKER, "Kwesi Owusu"), (CHECKER, "Ama Mensah")):
        signer_id = _enrol(db_session, ctx, name)
        appearance.adopt(
            db_session,
            ctx,
            signer_id=signer_id,
            kind="typed",
            typed_name=name,
            typed_font="times_italic",
        )
    db_session.commit()

    _preparer, approver = _fully_certify(db_session, package)
    roots = [pem.encode("ascii") for pem in db_session.scalars(select(SignerKey.certificate_pem))]
    report = verifier.verify_attestation(
        db_session, MAKER, package, storage=storage, trust_roots=roots
    )
    for check_name in verifier.CHECK_ORDER:
        check = next(entry for entry in report["checks"] if entry["check"] == check_name)
        assert check["evidence"]["status"] == "passed", (check_name, check["detail"])
    assert report["overall_passed"] is True

    # The adopted marks really are on the filed document, beside the signer IDs.
    final = _bytes(db_session, storage, _version(db_session, approver))
    drawn = _appearance_text(final, page_index=2)
    assert "Ama Mensah" in drawn
    assert "Kwesi Owusu" in drawn
    for signature in db_session.scalars(select(AttestationSignature)).all():
        assert signature.signer_id in drawn


def test_the_verifier_passes_every_check_on_a_ceremony_signed_document(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The two halves must agree, or neither is worth anything.

    The orchestrator writes the document and the pins; the verifier is what an
    examiner (or the offline CLI) actually runs. If a ceremony-signed return
    could not pass its own five checks, every green badge in the product would
    be describing a document nobody had verified.
    """
    from app.services.attestation import verify as verifier  # noqa: PLC0415 - heavy module

    package = _seed(db_session)
    _fully_certify(db_session, package)

    roots = [pem.encode("ascii") for pem in db_session.scalars(select(SignerKey.certificate_pem))]
    report = verifier.verify_attestation(
        db_session, MAKER, package, storage=storage, trust_roots=roots
    )
    for name in verifier.CHECK_ORDER:
        check = next(entry for entry in report["checks"] if entry["check"] == name)
        assert check["evidence"]["status"] == "passed", (name, check["detail"])
    assert report["overall_passed"] is True
    assert report["chain_ok"] is True

    # Both fields were examined on the filed document — not just the detached
    # attestation the database holds.
    pdf_check = next(
        entry
        for entry in report["checks"]
        if entry["check"] == verifier.CHECK_PDF_SIGNATURE
    )["evidence"]
    assert {entry["field"] for entry in pdf_check["signatures_examined"]} == {
        pdf_signing.PREPARER_FIELD_NAME,
        pdf_signing.APPROVER_FIELD_NAME,
    }


# --- what gets downloaded, and what gets filed -------------------------------
#
# The signature is only half the promise. A bank that certified BSD3 as preparer
# and approver, pressed Download, and received the raw unsigned PDF has a signed
# document nobody can obtain and an unsigned one on its way to the regulator.
# These are the assertions that would fail if that regressed.


def test_the_download_resolves_to_the_signed_document(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """After signing, the served bytes are the signed revision — not the export.

    ``is_filed`` is the single flag every surface reads (the artifacts card, the
    signing workspace, the channel), so it must land on exactly one version and
    it must be the one the last signature pinned.
    """
    package = _seed(db_session)
    _preparer, approver = _fully_certify(db_session, package)

    listing = artifact_versions.list_versions(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    filed = [version for version in listing.versions if version.is_filed]
    assert len(filed) == 1
    assert filed[0].id == approver.artifact_version_id
    assert filed[0].signed_by is not None
    assert filed[0].signed_by.signing_role == "approver"
    assert filed[0].signed_by.signer_id == approver.signer_id

    _version_row, served = artifact_versions.read_version_bytes(
        db_session, MAKER, SAMPLE_BANK_ID, filed[0].id, storage
    )
    assert served == _bytes(db_session, storage, _version(db_session, approver))
    assert PdfFileReader(io.BytesIO(served)).embedded_regular_signatures


def test_the_unsigned_base_survives_and_stays_retrievable(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """Provenance is kept, and it is genuinely the same bytes.

    A PAdES signature appends, so the signed document contains the base export
    as a literal prefix — which is what makes retaining the base a provenance
    decision rather than a functional dependency. If signing ever mutated the
    base in place, the prefix assertion below is what would catch it.
    """
    package = _seed(db_session)
    _preparer, approver = _fully_certify(db_session, package)

    listing = artifact_versions.list_versions(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    base = next(version for version in listing.versions if version.signed_by is None)
    assert base.is_filed is False

    _row, base_bytes = artifact_versions.read_version_bytes(
        db_session, MAKER, SAMPLE_BANK_ID, base.id, storage
    )
    assert hashlib.sha256(base_bytes).hexdigest() == base.checksum_sha256
    assert not PdfFileReader(io.BytesIO(base_bytes)).embedded_signatures

    signed = _bytes(db_session, storage, _version(db_session, approver))
    assert signed.startswith(base_bytes)


def test_a_corrupted_archive_refuses_the_download(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """Serving bytes a signature does not cover is worse than serving nothing.

    The verifier's artifact-binding check would convict the file eventually —
    in front of an examiner. Refusing here turns that into an operational
    incident an operator can escalate before the return leaves the building.
    """
    package = _seed(db_session)
    _preparer, approver = _fully_certify(db_session, package)
    version = _version(db_session, approver)
    _corrupt(storage, db_session, version)

    with pytest.raises(HTTPException) as raised:
        artifact_versions.read_version_bytes(
            db_session, MAKER, SAMPLE_BANK_ID, version.id, storage
        )
    assert raised.value.status_code == 409
    assert raised.value.detail["error_code"] == "artifact_version_checksum_mismatch"  # type: ignore[index]


def test_submission_files_the_signed_document_alongside_the_template(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """SIGN-4: a fully certified return reaches the regulator SIGNED.

    The hedge is asserted with it. Whether ORASS accepts a PDF as the filing is
    unconfirmed (docs §8 C1), so the registry's required template format rides
    along rather than being replaced — dropping it would trade a signature
    improvement for a possibly rejected statutory filing.
    """
    package = _seed(db_session)
    _preparer, approver = _fully_certify(db_session, package)
    db_session.refresh(package)
    assert package.status == "approved"

    reporting_workflow.submit_package_via_channel(
        db_session, CHECKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
    )
    events = reporting_workflow.list_submission_events(
        db_session, MAKER, SAMPLE_BANK_ID, package.id
    )
    detail = events.events[0].detail
    assert detail["signed_artifact_version_id"] == str(approver.artifact_version_id)
    assert [entry["signing_role"] for entry in detail["signed_by"]] == [
        "preparer",
        "approver",
    ]

    signed_version = _version(db_session, approver)
    filed = {entry["kind"]: entry for entry in detail["filed_artifacts"]}
    assert filed["pdf"]["object_path"] == signed_version.object_path
    assert filed["pdf"]["checksum_sha256"] == signed_version.checksum_sha256
    assert filed["pdf"]["signed"] is True
    # The template format is attached, not dropped for the signed document.
    assert filed["xlsx"]["signed"] is False
    assert detail["auto_exported_kinds"] == ["xlsx"]


def test_an_uncertified_package_files_the_canonical_export(
    db_session: Session, storage: InMemoryStorageClient
) -> None:
    """The control case: nothing about the unsigned path moved.

    A return whose policy requires no signature still files the artifacts it
    always did, and still auto-exports the template format when it has none.
    """
    package = _seed(db_session, require_signed_pdf=False)
    policy = db_session.scalar(select(ReturnSigningPolicy))
    assert policy is not None
    policy.require_signature = False
    package.status = "approved"
    db_session.commit()

    reporting_workflow.submit_package_via_channel(
        db_session, CHECKER, SAMPLE_BANK_ID, package.id, channel_override="orass_sandbox"
    )
    events = reporting_workflow.list_submission_events(
        db_session, MAKER, SAMPLE_BANK_ID, package.id
    )
    detail = events.events[0].detail
    assert "signed_artifact_version_id" not in detail
    assert detail["auto_exported_kinds"] == ["xlsx"]
    assert [entry["kind"] for entry in detail["filed_artifacts"]] == ["xlsx"]
    assert detail["filed_artifacts"][0]["signed"] is False


def _corrupt(
    storage: InMemoryStorageClient, db: Session, version: RegulatoryArtifactVersion
) -> None:
    """Rewrite the stored object's bytes in place, as a silent corruption would.

    Reaching into the in-memory client on purpose: going through ``write`` would
    mint a NEW object version, which is the one thing an object store does that
    this failure mode is defined by the absence of.
    """
    slug = db.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    location = StorageLocation(
        institution_slug=slug, tier="outputs", object_path=version.object_path
    )
    stored = storage._objects[storage._key(location)]  # noqa: SLF001 - simulating corruption
    for entry in stored:
        if entry.content is not None:
            entry.content = entry.content + b"%% tampered\n"
