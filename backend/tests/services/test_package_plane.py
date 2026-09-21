"""The package plane P3 adds: attachments, family hooks, and the filing set.

Four properties this suite exists to keep true:

1. ``return_signing_policies.required_attachments`` is ENFORCED, for every
   family, and the e-sign kill switch does not exempt it. Before P3 a policy
   could name a Board resolution and nothing could satisfy it, so the
   requirement silently passed on every submission (audit C-6).
2. What a filing carries is decided by an explicit opt-in, kind by kind. The
   recalculable Excel copy IS filed (founder decision 2026-09-20 — supervisors
   prefer the form with its formulas live); the recalculable Word draft is not,
   and a working kind that names itself nowhere is excluded by default.
3. The family hook seam dispatches, and a family with no hook changes nothing —
   which is what makes "ICAAP got its own rules" safe for every other return.
4. The calendar and the anchor picker say honest things about a return that is
   not in force yet (D-058).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    ReturnSigningPolicy,
)
from app.services.attestation import workflow as attestation_workflow
from app.services.regulatory_reporting import (
    attachments as package_attachments,
)
from app.services.regulatory_reporting import (
    calendar,
    eligibility,
    family_hooks,
    generation,
    validation,
)
from app.services.regulatory_reporting import workflow as reporting_workflow
from app.services.regulatory_reporting.registry import REGISTRY, get_definition
from tests.factories.filing_chain import complete_chain
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
REPORTING_DATE = date(2026, 3, 31)


def _bank(db: Session) -> Bank:
    bank = db.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


def _package(db: Session, *, return_code: str = "LCR-NSFR", family: str = "liquidity"):
    """A minimal package row. The suite is about the plane, not the generators."""
    package = RegulatoryPackage(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        return_family=family,
        return_code=return_code,
        reporting_date=REPORTING_DATE,
        frequency="monthly",
        basis="solo",
        status="approved",
        version=1,
        snapshot={"sections": []},
        source_runs=[],
        generated_by=DEMO_USER_ID,
        generated_at=datetime.now(UTC),
    )
    db.add(package)
    db.flush()
    return package


def _require_attachment(db: Session, *, return_code: str, kind: str) -> ReturnSigningPolicy:
    row = ReturnSigningPolicy(
        organization_id=DEMO_ORG_ID,
        return_code=return_code,
        required_signatures=[],
        require_signature=False,
        required_attachments=[kind],
        effective_from=date(2000, 1, 1),
        reason="test: the bank requires this document with the filing",
    )
    db.add(row)
    db.flush()
    return row


# --- 1. the submission gate counts documents, for every family --------------


def test_a_policy_that_names_an_attachment_now_blocks_submission(db_session: Session) -> None:
    """The defect C-6 names: the requirement existed and nothing enforced it."""
    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    _require_attachment(db_session, return_code="LCR-NSFR", kind="board_resolution")

    # A 409 with a stable ``error_code``, the same wire shape every other
    # submission refusal carries, plus the structured list the Filing tab needs
    # to say WHICH document is missing.
    with pytest.raises(HTTPException) as excinfo:
        attestation_workflow.ensure_submittable(db_session, MAKER, package)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error_code"] == "attachments_missing"  # type: ignore[index]
    missing = excinfo.value.detail["missing"]  # type: ignore[index]
    assert missing == [
        {
            "kind": "board_resolution",
            "required": 1,
            "present": 0,
            "origin": "signing_policy",
        }
    ]


def test_the_esign_kill_switch_does_not_exempt_a_required_document(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ATTESTATION_ESIGN_REQUIRED=0`` suspends SIGNATURES, never documents.

    A signing-infrastructure switch must not silently become a regulatory one:
    whether the Board's resolution accompanies the filing is not a question
    about whether the deployment can sign.
    """
    from app.core.config import get_settings  # noqa: PLC0415

    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    _require_attachment(db_session, return_code="LCR-NSFR", kind="board_resolution")

    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as excinfo:
            attestation_workflow.ensure_submittable(db_session, MAKER, package)
        assert excinfo.value.detail["error_code"] == "attachments_missing"  # type: ignore[index]
    finally:
        monkeypatch.delenv("ATTESTATION_ESIGN_REQUIRED", raising=False)
        get_settings.cache_clear()


def test_a_return_with_no_required_documents_is_submittable_as_before(
    db_session: Session,
) -> None:
    """The unchanged path: no policy attachment, no family hook, no new refusal."""
    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    from tests.factories.attestation import relax_signing  # noqa: PLC0415

    relax_signing(db_session, organization_id=DEMO_ORG_ID, return_code="LCR-NSFR")
    attestation_workflow.ensure_submittable(db_session, MAKER, package)  # no raise


def test_a_withdrawn_document_stops_counting(db_session: Session) -> None:
    """Withdrawal says the document should not have been filed, so it blocks again."""
    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    _require_attachment(db_session, return_code="LCR-NSFR", kind="board_resolution")
    row = _attach(db_session, package, kind="board_resolution")

    policy = attestation_workflow.package_policy(db_session, MAKER, package)
    package_attachments.ensure_required_attachments(db_session, MAKER, package, policy)  # no raise

    package_attachments.withdraw_package_attachment(
        db_session, MAKER, package, row.id, reason="filed the wrong minutes"
    )
    with pytest.raises(HTTPException) as excinfo:
        package_attachments.ensure_required_attachments(db_session, MAKER, package, policy)
    assert excinfo.value.detail["error_code"] == "attachments_missing"  # type: ignore[index]


class _CountingUpload:
    """An ``UploadFile`` stand-in that records how much the route asked for.

    The whole point of the bound is that the body is never fully materialised,
    which a test on the response code alone cannot see: the service refuses an
    oversized upload either way. So this records the ``size`` argument and
    returns only that much.
    """

    filename = "resolution.pdf"

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.requested: int | None = None
        self.calls = 0

    async def read(self, size: int = -1) -> bytes:
        self.calls += 1
        self.requested = size
        return self._payload if size < 0 else self._payload[:size]


async def _upload_through_the_route(
    db: Session, package: RegulatoryPackage, upload: _CountingUpload
) -> None:
    from app.api.deps import PackageAccess  # noqa: PLC0415
    from app.features.manage_package_attachments import upload_package_attachment  # noqa: PLC0415

    await upload_package_attachment(
        bank_id=package.bank_id,
        package_id=package.id,
        db=db,
        access=PackageAccess(ctx=MAKER, bank=_bank(db), package=package),
        storage=None,  # type: ignore[arg-type] - never reached: the refusal precedes it
        file=upload,  # type: ignore[arg-type] - the two methods the route uses
        kind="board_resolution",
        title="Board resolution",
        attributes=None,
    )


def test_the_upload_route_never_reads_an_unbounded_body(db_session: Session) -> None:
    """Audit S-5 (MEDIUM): the cap must apply to the READ, not to the leftovers.

    ``await file.read()`` with no argument lets any principal holding package
    EDIT drive the tenant API's memory to the size of the body they send. Both
    ICAAP upload routes already read ``limit + 1`` and refuse at 413 before
    allocating; this is the route that carries the Board resolution onto a
    filing, and it is the one that did not.
    """
    import asyncio  # noqa: PLC0415

    from app.core.config import get_settings  # noqa: PLC0415

    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    limit = get_settings().icaap.max_attachment_bytes
    upload = _CountingUpload(b"%PDF-1.7\n" + b"x" * (limit + 4096))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_upload_through_the_route(db_session, package, upload))

    assert upload.requested == limit + 1, "the route must bound its own read"
    assert excinfo.value.status_code == 413
    assert excinfo.value.detail["error_code"] == "attachment_too_large"  # type: ignore[index]


def test_a_document_inside_the_limit_still_reaches_the_service(db_session: Session) -> None:
    """The bound refuses size, never content: an honest document passes it."""
    import asyncio  # noqa: PLC0415

    from app.core.config import get_settings  # noqa: PLC0415

    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    upload = _CountingUpload(b"%PDF-1.7\nboard resolution\n%%EOF\n")

    # Storage is ``None``, so reaching the service at all raises something that
    # is NOT the 413 — which is the assertion: the bound let this through.
    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - any non-413 outcome proves it
        asyncio.run(_upload_through_the_route(db_session, package, upload))
    assert not (
        isinstance(excinfo.value, HTTPException) and excinfo.value.status_code == 413
    ), "a document inside the limit must not be refused for size"
    assert upload.requested == get_settings().icaap.max_attachment_bytes + 1


def _attach(db: Session, package: RegulatoryPackage, *, kind: str, sha: str | None = None):
    """A document row, written directly — the upload path needs object storage."""
    from app.models import RegulatoryPackageAttachment  # noqa: PLC0415

    row = RegulatoryPackageAttachment(
        organization_id=package.organization_id,
        bank_id=package.bank_id,
        package_id=package.id,
        package_version=package.version,
        kind=kind,
        title="Board resolution",
        original_filename="resolution.pdf",
        media_type="application/pdf",
        byte_size=1024,
        sha256=(sha or "a" * 64),
        storage_tier="outputs",
        object_path=f"bog_returns/x/{package.id}/attachments/{sha or 'a' * 64}.pdf",
        source="package_upload",
        source_attachment_id=None,
        gate="submission",
        attributes={"resolution_date": "2026-03-31", "resolution_reference": "BR-1"},
        attached_by=DEMO_USER_ID,
    )
    db.add(row)
    db.flush()
    return row


# --- 2. what a filing carries, and what it leaves behind -------------------
#
# This section pinned "no working artifact is ever filed" until 2026-09-20, when
# the founder ruled — having been shown the trade-off — that supervisors prefer
# the Excel form with its formulas live, and that BOTH Excel copies are
# therefore filed. The old rule is not relaxed here, it is REPLACED by a
# narrower one that has to be stated exactly, on two axes:
#
#   KIND      — the formula workbook is filed and never signed; the Word ICAAP
#               draft is not filed at all; a future working kind is excluded
#               until someone names it in the opt-in set.
#   GENERATOR — and only BoG's OWN workbook goes. The decision named the Bank
#               of Ghana's forms, which ``bog_form`` builds from BoG's committed
#               templates. An SDI packet's working sheet is an AequorOS
#               calculation aid; filing it would infer a second regulator's
#               preference from a decision that stated one, so it stays
#               internal — and the "not a filing artifact" label it carries
#               stays true.
#
# The BSD/SDI pair below IS the guarantee: neither test means much alone.


def _artifacts(db: Session, package: RegulatoryPackage, kinds: tuple[str, ...]) -> None:
    for artifact_kind in kinds:
        db.add(
            RegulatoryPackageArtifact(
                organization_id=DEMO_ORG_ID,
                package_id=package.id,
                kind=artifact_kind,
                object_path=(
                    f"bog_returns/x/{package.id}/{package.return_code}.{artifact_kind}"
                ),
                checksum_sha256="b" * 64,
                size_bytes=10,
            )
        )
    db.flush()


def _filed_kinds(db: Session, return_code: str, kinds: tuple[str, ...]) -> list[str]:
    definition = get_definition(return_code)
    assert definition is not None, return_code
    package = _package(db, return_code=return_code, family=definition.family)
    _artifacts(db, package, kinds)
    filed, detail = reporting_workflow._filing_set(  # noqa: SLF001 - the unit under test
        db, MAKER, package, mint_missing=False
    )
    listed = [artifact.kind for artifact in filed]
    # the submission record must say exactly what went, in the order it went
    assert [entry["kind"] for entry in detail["filed_artifacts"]] == listed
    # ...and nothing here is certified, so nothing in the record claims to be
    assert not any(entry["signed"] for entry in detail["filed_artifacts"])
    return listed


#: BSD2 is an official BoG form (generator ``bog_form``) — BoG's own workbook,
#: which is what the founder's decision named.
def test_the_formula_copy_of_an_official_form_is_filed(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    kinds = _filed_kinds(
        db_session, "BSD2", ("pdf", "xlsx", "csv", "xlsx_working", "docx_working")
    )
    assert "xlsx_working" in kinds, "BoG's own formula workbook is part of what is filed"
    assert "docx_working" not in kinds, "the Word draft never reaches a regulator"


#: The other half of the guarantee. These returns DO produce a working copy
#: (``supports_working_copy``), so the exclusion is a real decision about them,
#: not an artefact of their never having one. SDI-STRESS-ANNUAL is also the
#: return whose pre-P0 ``xlsx_working`` digest is pinned by D-021 — filing it
#: would have forced a re-baseline of that fixture.
@pytest.mark.parametrize(
    "return_code", ["SDI-LMT-MONTHLY", "SDI-STRESS-ANNUAL", "SDI-LE-MONTHLY", "NPL-MONTHLY"]
)
def test_a_non_official_working_copy_is_not_filed(db_session: Session, return_code: str) -> None:
    """An SDI packet's working sheet is an AequorOS calculation aid.

    It carries a different label from BoG's formula workbook — ``WORKING COPY —
    FOR INTERNAL REVIEW · not a filing artifact`` — and that sentence has to
    stay TRUE. The whole finding that produced the generator axis was a label
    contradicting behaviour, so this asserts the behaviour the label claims.
    """
    materialize_canonical_test_book(db_session)
    definition = get_definition(return_code)
    assert definition is not None, return_code
    assert definition.supports_working_copy, f"{return_code} must really have a working copy"
    assert definition.generator != "bog_form"

    kinds = _filed_kinds(db_session, return_code, ("pdf", "xlsx", "csv", "xlsx_working"))
    assert "xlsx_working" not in kinds, "an AequorOS calculation sheet is not filed"
    assert kinds == ["pdf", "xlsx", "csv"]


@pytest.mark.parametrize("return_code", ["LCR-NSFR", "BSD2", "ICAAP-REPORT"])
def test_the_word_draft_is_never_filed_whatever_the_return(
    db_session: Session, return_code: str
) -> None:
    materialize_canonical_test_book(db_session)
    kinds = _filed_kinds(db_session, return_code, ("pdf", "docx_working"))
    assert "docx_working" not in kinds
    assert kinds == ["pdf"]


def test_the_filing_leads_with_the_submission_document_and_ends_with_the_csv(
    db_session: Session,
) -> None:
    """Both Excel copies ahead of the CSV, and the signed record never buried.

    The formula copy joining the filing doubles the number of spreadsheets in
    the envelope; an order that left it after the CSV — where "append the new
    kind" would naturally put it — would read as an afterthought to the person
    opening the submission.
    """
    materialize_canonical_test_book(db_session)
    # deliberately inserted worst-case-first: CSV before either workbook
    kinds = _filed_kinds(db_session, "BSD2", ("csv", "xlsx_working", "xlsx", "pdf"))
    assert kinds == ["pdf", "xlsx", "xlsx_working", "csv"]


def test_a_working_kind_reaches_a_regulator_only_by_opting_in_on_both_axes() -> None:
    """The safety property the single set used to give by excluding everything.

    ``WORKING_ARTIFACT_KINDS`` still names every recalculable artifact; what a
    filing excludes is DERIVED from it by subtracting the explicit opt-in, so a
    working kind added to the first set and forgotten is excluded, not filed.
    The generator map is the second axis and is deny-by-default in the same
    way. Deleting either derivation and filtering on a literal would silently
    re-admit the next one.
    """
    filable = reporting_workflow.FILABLE_WORKING_ARTIFACT_KINDS
    working = reporting_workflow.WORKING_ARTIFACT_KINDS
    assert filable <= working, "a filable working kind must still be a working kind"
    assert filable == frozenset({"xlsx_working"}), "one kind opted in, and only one"
    unfilable = reporting_workflow.UNFILABLE_WORKING_ARTIFACT_KINDS
    assert unfilable == working - filable, "the exclusion must stay derived, not written out"
    assert "docx_working" in unfilable
    # a hypothetical third kind is excluded by construction, not by memory
    assert "pptx_working" not in filable

    admits = reporting_workflow.filing_admits_artifact
    # ordinary artifacts are unaffected by either axis
    for kind in ("pdf", "xlsx", "csv"):
        assert admits(kind, generator="bog_form")
        assert admits(kind, generator="sdi_lmt")
        assert admits(kind, generator=None)
    # the formula workbook: BoG's own generator only
    assert admits("xlsx_working", generator="bog_form")
    assert not admits("xlsx_working", generator="sdi_lmt")
    assert not admits("xlsx_working", generator="icaap_report")
    assert not admits("xlsx_working", generator=None), "an unregistered return files no copy"
    # the Word draft: no generator can file it, because its KIND never opted in
    for generator in ("bog_form", "icaap_report", None):
        assert not admits("docx_working", generator=generator)
    # a hypothetical future kind: opting in by kind alone is not enough
    generators = reporting_workflow.WORKING_ARTIFACT_FILING_GENERATORS
    assert generators == {"xlsx_working": frozenset({"bog_form"})}
    assert set(generators) <= filable, "only an opted-in kind may name generators"


def test_certification_still_signs_the_values_only_document(db_session: Session) -> None:
    """The record of truth did not move when the formula copy joined the filing.

    ``artifact_signing`` signs one kind. If that ever became a recalculable
    kind, the signature would cover bytes whose figures change on open — which
    is precisely what the filed-but-not-signed split exists to prevent.
    """
    _ = db_session
    from app.services.attestation import artifact_signing  # noqa: PLC0415

    assert artifact_signing.ARTIFACT_KIND == "pdf"
    assert artifact_signing.ARTIFACT_KIND not in reporting_workflow.WORKING_ARTIFACT_KINDS


def test_the_filing_record_lists_the_documents_by_hash(db_session: Session) -> None:
    """"Which Board resolution did we file" must be answerable from the database."""
    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    _attach(db_session, package, kind="board_resolution", sha="c" * 64)

    _filed, detail = reporting_workflow._filing_set(  # noqa: SLF001
        db_session, MAKER, package, mint_missing=False
    )
    assert detail["attachments"] == [
        {
            "attachment_id": str(
                package_attachments.active_attachments(db_session, MAKER, package)[0].id
            ),
            "kind": "board_resolution",
            "title": "Board resolution",
            "sha256": "c" * 64,
            "byte_size": 1024,
            "source": "package_upload",
            "gate": "submission",
        }
    ]


# --- 3. the family hook seam ------------------------------------------------


class _StubHooks(family_hooks.FamilyHooks):
    family = "liquidity"

    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []

    def required_attachments(self, db, ctx, package) -> dict[str, int]:  # noqa: ANN001, ARG002
        return {"senior_management_report": 1}

    def validation_findings(self, db, package) -> list[dict[str, str]]:  # noqa: ANN001, ARG002
        return [{"rule": "stub.rule", "severity": "ERROR", "detail": "a family rule failed"}]

    def on_transition(self, db, ctx, package, *, previous, new_status) -> None:  # noqa: ANN001, ARG002
        self.transitions.append((previous, new_status))


@pytest.fixture
def stub_hooks():
    hooks = _StubHooks()
    family_hooks.register("liquidity", hooks)
    yield hooks
    family_hooks.unregister("liquidity")


def test_a_family_with_no_hook_contributes_nothing(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    assert family_hooks.for_package(package) is None
    assert family_hooks.required_attachments(db_session, MAKER, package) == {}
    assert family_hooks.validation_findings(db_session, package) == []
    # The generic pipeline is byte-identical: no family findings, no stamp.
    report = validation.run_validation_rules(db_session, package)
    assert all(not finding["rule"].startswith("stub.") for finding in report)


def test_a_family_requirement_is_not_relaxable_by_the_signing_policy(
    db_session: Session, stub_hooks: _StubHooks
) -> None:
    """Dropping a signature slot changes signatures, never documents (M11)."""
    _ = stub_hooks
    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    from tests.factories.attestation import relax_signing  # noqa: PLC0415

    relax_signing(db_session, organization_id=DEMO_ORG_ID, return_code="LCR-NSFR")
    policy = attestation_workflow.package_policy(db_session, MAKER, package)
    assert policy.require_signature is False

    required = package_attachments.required_attachments(db_session, MAKER, package, policy)
    assert required["senior_management_report"] == (1, "family")
    with pytest.raises(HTTPException) as excinfo:
        package_attachments.ensure_required_attachments(db_session, MAKER, package, policy)
    assert excinfo.value.detail["error_code"] == "attachments_missing"  # type: ignore[index]


def test_a_family_finding_may_be_an_error_and_blocks_validation(
    db_session: Session, stub_hooks: _StubHooks
) -> None:
    _ = stub_hooks
    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    package.status = "generated"
    db_session.flush()

    read = validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    assert read.status == "generated"  # an ERROR keeps it out of 'validated'
    assert read.validation_report is not None
    assert read.validation_report.family_rule_version == "liquidity-validation-v1"
    assert any(f.rule == "stub.rule" for f in read.validation_report.findings)


def test_the_transition_hook_sees_every_status_move(
    db_session: Session, stub_hooks: _StubHooks
) -> None:
    materialize_canonical_test_book(db_session)
    package = _package(db_session)
    # A hand-set ``approved`` is no longer filable: the transmission gate reads
    # the review chain, not the status. Walk the chain so the move to
    # ``submitted`` is one the platform would actually make.
    complete_chain(db_session, package)
    stub_hooks.transitions.clear()
    reporting_workflow.transition(db_session, MAKER, package, "submitted")
    assert stub_hooks.transitions == [("approved", "submitted")]


# --- 4. the ICAAP filing family, structurally -------------------------------


def test_an_icaap_return_is_never_minted_through_the_generic_endpoint(
    db_session: Session,
) -> None:
    """A filing with no review chain behind it is the thing the freeze prevents."""
    from app.schemas.regulatory_reporting import RegulatoryPackageCreate  # noqa: PLC0415

    materialize_canonical_test_book(db_session)
    for code, definition in REGISTRY.items():
        if definition.family != "icaap":
            continue
        with pytest.raises(HTTPException) as excinfo:
            generation.generate_package(
                db_session,
                MAKER,
                SAMPLE_BANK_ID,
                RegulatoryPackageCreate(return_code=code, reporting_date=REPORTING_DATE),
            )
        assert excinfo.value.status_code == 409, code
        assert excinfo.value.detail["error_code"] == "icaap_generated_by_freeze", code  # type: ignore[index]


def test_every_icaap_entry_pairs_with_its_own_template() -> None:
    """D-051's open gap: ICAAP-UPDATE cannot share ICAAP-REPORT's template id."""
    from app.services.regulatory_reporting.templates import get_template  # noqa: PLC0415

    ids = set()
    for code, definition in REGISTRY.items():
        if definition.family != "icaap":
            continue
        template = get_template(definition.template_id)
        assert template is not None, code
        assert template.return_code == code
        assert definition.template_id not in ids, "two ICAAP returns share a template id"
        ids.add(definition.template_id)
    assert len(ids) == 3


def test_icaap_filing_channels_follow_the_submissions_the_report_actually_makes(
    db_session: Session,
) -> None:
    """The ICAAP REPORT goes through the portal; the disclosure does not.

    The report and its material-change update are submissions to the regulator,
    and the transport is the portal document upload (founder determination
    2026-09-20 — the public record has it as UNKNOWN, see the provenance note
    on the registry entries). They therefore carry the portal channels, plus
    ``email`` so a portal outage on the 31 March obligation still has a path,
    plus ``manual`` for a submission made outside the platform.

    ``ICAAP-DISCLOSURE`` is the paragraph 82 WEBSITE publication — its filing
    reference is the URL the bank published at, so there is nothing to
    transmit — and ``ICAAP-STRESS`` is a companion prepared alongside the
    report rather than a second thing to file. Both stay manual-only, and this
    is the test that stops a later "make ICAAP consistent" sweep from giving
    them a transport they do not have.
    """
    materialize_canonical_test_book(db_session)
    filed = _package(db_session, return_code="ICAAP-REPORT", family="icaap")
    # No refusal: the portal is a supported channel for the report.
    reporting_workflow._ensure_channel_submittable(  # noqa: SLF001
        db_session, filed, "orass_sandbox"
    )

    # The disclosure is constrained to manual: there is no transmission.
    assert REGISTRY["ICAAP-DISCLOSURE"].allowed_channels == ("manual",)
    # The stress companion is not a separate submission at all, so it is not
    # given a transport — it rides with the report (D-011).
    assert REGISTRY["ICAAP-STRESS"].filing_role == "companion"
    assert REGISTRY["ICAAP-STRESS"].default_channel == "manual"


def test_a_disclosure_cannot_be_recorded_without_the_published_reference(
    db_session: Session,
) -> None:
    """The paragraph 82 filing IS the URL; recording it blind records nothing."""
    definition = REGISTRY["ICAAP-DISCLOSURE"]
    assert definition.requires_external_ref
    with pytest.raises(HTTPException) as excinfo:
        reporting_workflow._resolve_external_ref(  # noqa: SLF001
            definition, "manual", external_ref=None
        )
    assert excinfo.value.detail["error_code"] == "external_ref_required"  # type: ignore[index]
    assert (
        reporting_workflow._resolve_external_ref(  # noqa: SLF001
            definition, "manual", external_ref="https://bank.example/icaap"
        )
        == "https://bank.example/icaap"
    )


def test_an_icaap_report_does_not_export_as_a_workbook(db_session: Session) -> None:
    from app.services.regulatory_reporting.exports import (  # noqa: PLC0415
        _ensure_kind_supported,
    )

    _ = db_session
    definition = REGISTRY["ICAAP-REPORT"]
    _ensure_kind_supported(definition, "pdf")  # no raise
    for kind in ("xlsx", "csv"):
        with pytest.raises(HTTPException) as excinfo:
            _ensure_kind_supported(definition, kind)
        assert (
            excinfo.value.detail["error_code"]  # type: ignore[index]
            == "export_kind_not_supported_for_return"
        ), kind


# --- 5. D-058: a return that is not in force yet ----------------------------


def _governed(db: Session, first_as_of: date | None):
    """Pretend the control plane holds (or does not hold) the first as-of date."""
    bank = _bank(db)
    resolved = eligibility.resolve_eligibility(db, MAKER, bank, as_of=date(2026, 9, 19))
    codes: dict[str, date | None] = dict(resolved.governed_effective_dates)
    codes["icaap_report_first_as_of_date"] = first_as_of
    return resolved, codes


def test_the_calendar_never_shows_an_obligation_before_a_return_is_in_force(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-058. The FY2025 ICAAP row had a due date of 31 March 2026 — already past.

    Telling a bank it is late on a filing it never owed is worse than omitting
    the row: the Guideline is an exposure draft effective 1 January 2027, and
    the governed first as-of date says so.
    """
    materialize_canonical_test_book(db_session)
    _resolved, codes = _governed(db_session, date(2026, 12, 31))
    _patch_governed(monkeypatch, codes, months={"icaap_submission_months": 3})

    listing = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 12, lookback_months=6, as_of=date(2026, 9, 19)
    )
    icaap_rows = [row for row in listing.obligations if row.return_family == "icaap"]
    assert icaap_rows, "the ICAAP obligation must appear once the return is in force"
    assert all(row.reporting_date >= date(2026, 12, 31) for row in icaap_rows)
    assert all(row.rag != "overdue" for row in icaap_rows)


def test_an_unconfigured_first_as_of_date_omits_the_obligation_and_says_so(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed. A commencement date is never assumed (D-024)."""
    materialize_canonical_test_book(db_session)
    _resolved, codes = _governed(db_session, None)
    _patch_governed(monkeypatch, codes, months={"icaap_submission_months": 3})

    listing = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 12, lookback_months=6, as_of=date(2026, 9, 19)
    )
    assert not [row for row in listing.obligations if row.return_family == "icaap"]
    assert listing.coverage_note is not None
    assert "icaap_report_first_as_of_date" in listing.coverage_note


def test_the_returns_picker_still_offers_the_rehearsal_date_and_marks_it(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of D-058: a bank must be able to dry-run before day one.

    The registry's own rule is that a commencement date is not a generation
    gate. So the anchor stays selectable, flagged ``in_force=False`` — the
    Returns workspace can say "rehearsal", and nothing about it reads as a
    deadline.
    """
    materialize_canonical_test_book(db_session)
    _resolved, codes = _governed(db_session, date(2026, 12, 31))
    _patch_governed(monkeypatch, codes, months={"icaap_submission_months": 3})

    listing = calendar.list_return_anchors(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        "ICAAP-REPORT",
        12,
        lookback_months=6,
        as_of=date(2026, 9, 19),
    )
    assert listing.ineligible_reason is None
    assert listing.effective_from == date(2026, 12, 31)
    by_date = {anchor.reporting_date: anchor for anchor in listing.anchors}
    assert date(2025, 12, 31) in by_date
    assert by_date[date(2025, 12, 31)].in_force is False
    assert by_date[date(2026, 12, 31)].in_force is True


def test_the_freeze_path_ignores_the_effective_date(db_session: Session) -> None:
    """The rehearsal path P3-C and P3-D prove freeze and exports on."""
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    resolved = eligibility.resolve_eligibility(db_session, MAKER, bank, as_of=date(2025, 12, 31))
    definition = REGISTRY["ICAAP-REPORT"]
    # The generic gate refuses a pre-effective date...
    with pytest.raises(HTTPException):
        resolved.require(definition, reporting_date=date(2025, 12, 31))
    # ...and the freeze path does not, because rehearsing is the point.
    resolved.require(
        definition, reporting_date=date(2025, 12, 31), ignore={"effective_date"}
    )


# --- 6. every registered return can produce a due date ----------------------


def test_every_registered_return_resolves_a_due_date_or_is_honestly_omitted(
    db_session: Session,
) -> None:
    """The Calendar is tenant-wide: one unresolvable definition took down the board.

    A ``deadline_parameter`` entry whose rule was still called raised a
    ``RuntimeError`` inside ``list_obligations`` and 500'd the whole surface for
    every return, which is a fail-open-shaped blast radius on a fail-closed
    mechanism. This walks the registry through the real resolver.
    """
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    governed = calendar._governed_deadlines(  # noqa: SLF001
        db_session, bank, as_of=date(2026, 9, 19)
    )
    for code, definition in sorted(REGISTRY.items()):
        if definition.event_driven:
            continue
        due, missing = calendar._due_date(  # noqa: SLF001
            definition, REPORTING_DATE, {}, governed
        )
        assert (due is not None) or (missing is not None), code
        if missing is not None:
            assert missing == definition.deadline_parameter, code


def test_the_calendar_survives_a_definition_whose_deadline_is_governed(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    listing = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 12, lookback_months=6, as_of=date(2026, 9, 19)
    )
    assert listing.obligations  # the board renders rather than 500ing
    assert all(row.due_date is not None for row in listing.obligations)


@dataclass(frozen=True)
class _StubGovernedValue:
    value: Decimal | None = None
    value_json: dict[str, Any] | None = None


class _StubResolver:
    """The control plane as this scenario needs it.

    Installed at the ONE seam both consumers share — ``eligibility`` builds it
    and the calendar reuses it off the eligibility context, which is what keeps
    the calendar's query budget fixed. Patching that seam therefore drives both
    the governed commencement dates and the governed deadlines.
    """

    def __init__(
        self, effective_dates: dict[str, date | None], months: dict[str, int]
    ) -> None:
        self._effective = effective_dates
        self._months = months

    def try_resolve(self, code: str, *, as_of: date) -> _StubGovernedValue | None:  # noqa: ARG002
        if code in self._effective:
            when = self._effective[code]
            if when is None:
                return None
            return _StubGovernedValue(value_json={"date": when.isoformat()})
        if code in self._months:
            return _StubGovernedValue(value=Decimal(self._months[code]))
        return None


def _patch_governed(
    monkeypatch: pytest.MonkeyPatch,
    effective_dates: dict[str, date | None],
    *,
    months: dict[str, int],
) -> None:
    """Stand in for the control plane, which the hermetic suite seeds partially."""
    stub = _StubResolver(dict(effective_dates), dict(months))
    monkeypatch.setattr(
        eligibility,
        "parameter_resolver",
        lambda db, bank, *, as_of: stub,  # noqa: ARG005
    )


def test_the_annex_rides_under_its_parent_once_the_parent_is_in_force(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-011: Appendix II is part of the ICAAP submission, not a second filing."""
    materialize_canonical_test_book(db_session)
    _resolved, codes = _governed(db_session, date(2026, 12, 31))
    _patch_governed(
        monkeypatch,
        codes,
        months={"icaap_submission_months": 3, "icaap_disclosure_submission_months": 3},
    )
    listing = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 12, lookback_months=6, as_of=date(2026, 9, 19)
    )
    parents = [
        row
        for row in listing.obligations
        if row.return_code == "ICAAP-REPORT" and row.reporting_date == date(2026, 12, 31)
    ]
    assert len(parents) == 1
    annex_codes = {annex.return_code for annex in parents[0].annexes}
    assert "ICAAP-STRESS-APPENDIX2" in annex_codes
    # ...and it does not ALSO appear as its own obligation on that date.
    assert not [
        row
        for row in listing.obligations
        if row.return_code == "ICAAP-STRESS-APPENDIX2"
        and row.reporting_date == date(2026, 12, 31)
    ]


def _unused(*args: Any) -> None:  # pragma: no cover - keeps imports honest
    _ = args


# --- 7. certification authority on a gated family ---------------------------


def test_the_scalar_approver_role_does_not_authorise_an_icaap_certification(
    db_session: Session,
) -> None:
    """The gap P3-A could not close from its own files.

    A Board or approver certification on an ICAAP package was gated on the
    SCALAR ``approver`` role, which is exactly the shape the authorization
    foundation exists to remove: a scalar role must never satisfy a scoped
    surface. ``require_certify_authority`` answers for a gated family and hands
    every other family back to the ladder unchanged.
    """
    from app.services.regulatory_reporting import family_access  # noqa: PLC0415

    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    icaap = _package(db_session, return_code="ICAAP-REPORT", family="icaap")
    ordinary = _package(db_session)

    # An ungated family is not decided here at all — the caller keeps the ladder.
    assert (
        family_access.require_certify_authority(db_session, MAKER, bank, ordinary, "approver")
        is False
    )
    # A gated family IS decided here, and a scalar approver role does not
    # satisfy it: this principal can see the ICAAP (the fixture grants a
    # capital binding) but holds no APPROVE on it, so the Board slot is refused
    # with an honest 403 rather than admitted by the role claim.
    scalar_approver = TenantContext(
        organization_id=DEMO_ORG_ID,
        actor_user_id=DEMO_USER_ID,
        roles=("admin", "approver", "analyst"),
        authorization_version=1,
    )
    with pytest.raises(HTTPException) as excinfo:
        family_access.require_certify_authority(
            db_session, scalar_approver, bank, icaap, "board"
        )
    assert excinfo.value.status_code == 403


def test_each_signing_role_maps_to_the_permission_it_actually_exercises() -> None:
    """A preparer FINISHES the document; a checker APPROVES the filing."""
    from app.core.authorization import Permission  # noqa: PLC0415
    from app.services.regulatory_reporting import family_access  # noqa: PLC0415

    assert family_access.CERTIFY_PERMISSIONS["preparer"] is Permission.EDIT
    assert family_access.CERTIFY_PERMISSIONS["approver"] is Permission.APPROVE
    assert family_access.CERTIFY_PERMISSIONS["board"] is Permission.APPROVE


def test_only_the_icaap_family_is_gated() -> None:
    """Gating an EXISTING family would silently take a return away from its readers.

    ``icaap_stress`` is the same confidential subject and is deliberately NOT
    here: analysts read it today, and removing that is a product decision with
    a migration, not an implementation detail.
    """
    from app.services.regulatory_reporting import family_access  # noqa: PLC0415

    assert set(family_access.GATED) == {"icaap"}
    assert family_access.is_gated("icaap_stress") is False


def test_a_nominee_is_evaluated_on_the_same_authority_as_a_signer(
    db_session: Session,
) -> None:
    """Routing a return to someone who could never sign it is refused at
    NOMINATION time, not discovered at the ceremony.

    The nominee is evaluated on exactly the authority the certification gate
    would apply, family by family — so the two cannot drift into "you may be
    nominated but you may not sign".
    """
    from app.services.regulatory_reporting import family_access  # noqa: PLC0415

    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    icaap = _package(db_session, return_code="ICAAP-REPORT", family="icaap")
    ordinary = _package(db_session)

    # An ungated family declines to answer, so the caller keeps the ladder.
    assert (
        family_access.nominee_may_sign(db_session, MAKER, bank, ordinary, MAKER, "approver")
        is None
    )
    # A gated family answers, and a principal with no binding at all is refused
    # however good their scalar role looks.
    stranger = TenantContext(
        organization_id=DEMO_ORG_ID,
        actor_user_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        roles=("approver",),
        authorization_version=1,
    )
    assert (
        family_access.nominee_may_sign(db_session, MAKER, bank, icaap, stranger, "board")
        is False
    )


# --- 8. rehearsal: the full lifecycle, with the danger blocked (D-029 / D-068)


def test_a_rehearsal_never_satisfies_a_calendar_obligation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry run that turned a calendar row green would be worse than no dry run."""
    materialize_canonical_test_book(db_session)
    _resolved, codes = _governed(db_session, date(2025, 1, 1))
    _patch_governed(monkeypatch, codes, months={"icaap_submission_months": 3})
    rehearsal = _package(db_session, return_code="ICAAP-REPORT", family="icaap")
    rehearsal.reporting_date = date(2025, 12, 31)
    rehearsal.frequency = "annual"
    rehearsal.status = "submitted"
    rehearsal.is_rehearsal = True
    db_session.flush()

    listing = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, 12, lookback_months=6, as_of=date(2026, 9, 19)
    )
    row = next(
        (
            item
            for item in listing.obligations
            if item.return_code == "ICAAP-REPORT"
            and item.reporting_date == date(2025, 12, 31)
        ),
        None,
    )
    assert row is not None, "the obligation itself is real and must still be listed"
    assert row.package_id is None, "a rehearsal must not be offered as the filing"
    assert row.rag == "overdue", "and it must not turn the obligation green"


def test_a_rehearsal_is_never_transmitted_to_a_regulator(db_session: Session) -> None:
    """The half of D-068 a row-level CHECK cannot reach: the channel is another table."""
    materialize_canonical_test_book(db_session)
    package = _package(db_session, return_code="ICAAP-REPORT", family="icaap")
    package.is_rehearsal = True
    package.status = "approved"
    db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        reporting_workflow._ensure_channel_submittable(  # noqa: SLF001
            db_session, package, "orass_sandbox"
        )
    assert (
        excinfo.value.detail["error_code"]  # type: ignore[index]
        == "rehearsal_channel_not_permitted"
    )
    # ...and the manual record, which is the point of the dry run, is allowed
    # through this gate.
    reporting_workflow._ensure_channel_submittable(  # noqa: SLF001
        db_session, package, "manual"
    )


def test_a_rehearsal_and_a_real_filing_are_separate_version_chains(
    db_session: Session,
) -> None:
    """The other half: ``supersedes_id`` points at a row, so no CHECK can read it.

    Either direction would be wrong — a dry run must not change the status of a
    filing, and a filing must not erase the evidence of a dry run — so the
    supersession lookup is keyed on ``is_rehearsal`` and the two chains never
    meet.
    """
    materialize_canonical_test_book(db_session)
    bank = _bank(db_session)
    real = _package(db_session, return_code="ICAAP-REPORT", family="icaap")
    real.frequency = "annual"
    db_session.flush()
    definition = REGISTRY["ICAAP-REPORT"]

    prior, version, _grant = generation._supersede_prior(  # noqa: SLF001
        db_session,
        MAKER,
        bank,
        definition,
        reporting_date=REPORTING_DATE,
        basis="solo",
        rehearsal=True,
    )
    assert prior is None, "a rehearsal must not retire the real filing"
    assert version is None
    assert real.status != "superseded"


def test_the_freeze_path_cannot_lose_its_reconciliation_gate(db_session: Session) -> None:
    """D-069: a filing path must not shed a gate at a seam.

    ``generate_frozen_package`` is a second package-mint site, and the generic
    one runs a reporting-period lookup and a filing-reconciliation gate before
    any generator. Those must not vanish from the ICAAP path simply because it
    enters through a different door. They live with the freeze
    (``services/icaap/freeze.py``) rather than inside the seam, because the seam
    also serves the paragraph 82 disclosure, which mints from an ALREADY SEALED
    snapshot and has no live book to reconcile.

    Read from the source, so moving the gates is a deliberate act that fails
    here rather than a deletion nobody notices.
    """
    import ast  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    _ = db_session
    from app.services.icaap import freeze as icaap_freeze  # noqa: PLC0415

    source = inspect.getsource(icaap_freeze)
    tree = ast.parse(source)
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute | ast.Name)
    }
    assert "assert_filing_reconciled" in called or "_assert_reconciled" in called, (
        "the ICAAP freeze no longer runs the filing-reconciliation gate; a return "
        "built on a book that does not balance must be impossible to freeze"
    )
    assert "get_snapshot_for_reporting_date" in called or "_period_for" in called, (
        "the ICAAP freeze no longer resolves the reporting period exactly as of the "
        "reporting date"
    )
    assert "generate_frozen_package" in called, (
        "the freeze no longer mints through the shared seam, so these gates may now "
        "guard nothing"
    )
