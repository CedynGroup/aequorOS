"""What a submission WOULD send — answered by the code that sends it.

The transmit confirmation is the last thing an officer reads before a signed
return leaves the bank. It is only worth showing if it is right, and the
tempting way to build it is wrong: reading ``regulatory_package_artifacts`` and
listing what the package holds. That is not what gets filed.
``workflow._filing_set`` substitutes the SIGNED revision for the unsigned
export and mints a missing filing format as it submits, so an artifact-row
dialog would name files that are not the ones sent — the same confusion that
once filed a fully certified return as the document nobody had signed.

So this asks the resolver itself, with ``mint_missing=False`` (the read-only
mode it already documents for the downtime email bundle) and reports the
would-be-minted format as a file that is generated at submission rather than
one that is missing.

The gates are reported the same way: by RUNNING the controls the submission
runs, read-only, and saying which passed. A parallel re-implementation of
"is this ready" would drift from the real gate, and the drift would show up as
a dialog that says a return is fit to file when the server refuses it.

Read-only: no transition, no mint, no event. The route is a GET.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, RegulatoryPackage
from app.services import institution_profile
from app.services.regulatory_reporting import artifact_versions
from app.services.regulatory_reporting.registry import get_definition

type FilingRole = Literal["signed_record", "official_copy", "formula_copy", "data"]

#: What each artifact kind is FOR in a filing. Kept beside the preview because
#: it is the reader's question ("which of these is the record?"), not the
#: storage layer's — ``xlsx`` and ``xlsx_working`` are both Excel and only one
#: of them can be signed.
_ROLE_BY_KIND: dict[str, FilingRole] = {
    "pdf": "signed_record",
    "xlsx": "official_copy",
    "xlsx_working": "formula_copy",
    "docx_working": "formula_copy",
    "csv": "data",
}


@dataclass(frozen=True, slots=True)
class PreviewEntry:
    kind: str
    filename: str
    role: FilingRole
    size_bytes: int | None
    generated_at_submission: bool
    signature_count: int | None


@dataclass(frozen=True, slots=True)
class FilingPreview:
    filing_set: list[PreviewEntry]
    satisfied: list[str]
    institution_code: str | None
    content_digest: str | None
    submission_revision: str
    is_first_filing: bool
    #: Why the set is not larger. A short filing set is often CORRECT — this
    #: return may carry no signed record and no formula copy by rule — but an
    #: officer cannot tell a correct one from a broken one by counting files.
    omissions: list[str]


def workflow_module():  # noqa: ANN201 - a module handle, typed by its use
    """The submission's own module, imported lazily to break the import cycle.

    The preview MUST read the filing rules from the code that files, never a
    copy of them: a second implementation is what lets a confirmation drift
    from the submission it describes.
    """
    from app.services.regulatory_reporting import workflow  # noqa: PLC0415

    return workflow


def _role_for(kind: str) -> FilingRole:
    # An unknown kind is data, never the record: a new artifact type must earn
    # the word "signed", not inherit it from a default.
    return _ROLE_BY_KIND.get(kind, "data")


def _filename(object_path: str) -> str:
    return object_path.rsplit("/", 1)[-1]


def build_preview(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package: RegulatoryPackage,
) -> FilingPreview:
    """The filing set and the gates, as the submission would find them."""
    from app.services.regulatory_reporting.workflow import (  # noqa: PLC0415 - import cycle
        _filing_set,
        _submission_revision,
    )

    filed, detail = _filing_set(db, ctx, package, mint_missing=False)
    signed = artifact_versions.latest_signed_version(db, ctx, package)
    signed_id = signed.version.id if signed is not None else None
    signature_count = (
        len(artifact_versions.signed_revisions(db, ctx, package)) if signed else 0
    )

    entries = [
        PreviewEntry(
            kind=artifact.kind,
            filename=_filename(artifact.object_path),
            role=_role_for(artifact.kind),
            size_bytes=artifact.size_bytes,
            generated_at_submission=False,
            # Only the signed revision carries signatures; a copy beside it is
            # not signed no matter how many officers signed the return.
            signature_count=(
                signature_count
                if signed_id is not None and artifact.id == signed_id
                else (None if _role_for(artifact.kind) != "signed_record" else 0)
            ),
        )
        for artifact in filed
    ]

    # Everything the submission would mint on its way out — the SAME pack, in
    # the same order, decided by the same predicates. Absent from ``filed``
    # here only because this call refused to mint. Computing it any other way
    # is how the confirmation came to promise one file while the submission
    # sent three.
    definition = get_definition(package.return_code)
    generator = definition.generator if definition is not None else None
    required = definition.filing_format if definition is not None else "xlsx"
    workflow = workflow_module()
    wanted: list[str] = [required] if required is not None else []
    for kind in workflow._FULL_FILING_PACK:  # noqa: SLF001
        if kind not in wanted:
            wanted.append(kind)
    for kind in wanted:
        if kind in {entry.kind for entry in entries}:
            continue
        if not workflow.filing_admits_artifact(kind, generator=generator):
            continue
        recalculable = kind in workflow.WORKING_ARTIFACT_KINDS
        if recalculable and not workflow._produces_working_copy(definition):  # noqa: SLF001
            continue
        entries.append(
            PreviewEntry(
                kind=kind,
                filename=f"{package.return_code}.{kind}",
                role=_role_for(kind),
                size_bytes=None,
                generated_at_submission=True,
                signature_count=None,
            )
        )

    # The revision the submission would stamp. The stored value is null until a
    # package has actually been filed, so a confirmation that read the column
    # would show nothing — or, worse, the PREVIOUS filing's revision on a
    # resubmission.
    revision = _submission_revision(db, ctx, bank.id, package)
    return FilingPreview(
        filing_set=entries,
        satisfied=_satisfied(db, ctx, package, detail),
        institution_code=institution_profile.orass_institution_code(db, ctx, bank.id),
        content_digest=_short_digest(package),
        submission_revision=revision,
        is_first_filing=revision == "1.0",
        omissions=_omissions(db, ctx, package, signed is not None),
    )


def _omissions(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    has_signed_revision: bool,
) -> list[str]:
    """Why each absent artifact is absent, in the officer's terms.

    Only reasons that are STRUCTURAL — a rule of this return or this
    deployment. A file that is merely not exported yet is not an omission: it
    is generated at submission and the set already says so.
    """
    from app.services.attestation import policy as attestation_policy  # noqa: PLC0415
    from app.services.regulatory_reporting.workflow import (  # noqa: PLC0415
        WORKING_ARTIFACT_FILING_GENERATORS,
    )

    notes: list[str] = []
    definition = get_definition(package.return_code)
    generator = definition.generator if definition is not None else None

    if not has_signed_revision:
        resolved = attestation_policy.resolve_policy(
            db,
            ctx,
            bank_id=package.bank_id,
            return_code=package.return_code,
            return_family=package.return_family,
            basis=package.basis,
            as_at=package.reporting_date,
        )
        if not resolved.require_signature:
            notes.append(
                "No signed record: signing is switched off for this "
                "installation, so no officer signature is attached to the "
                "filed document."
            )
        else:
            notes.append(
                "No signed record: this return has not been certified yet."
            )

    admitted = WORKING_ARTIFACT_FILING_GENERATORS.get("xlsx_working", frozenset())
    if generator is not None and generator not in admitted:
        notes.append(
            "No formula copy: the workbook with live formulas is filed only "
            "with the official form templates, and this return is not one."
        )
    return notes


def _short_digest(package: RegulatoryPackage) -> str | None:
    if not package.content_digest:
        return None
    text = package.content_digest
    # Enough to compare against the approval record by eye, not so much that it
    # crowds the sentence it sits in.
    return f"{text[:4]}…{text[-4:]}" if len(text) > 12 else text


def _satisfied(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    detail: dict[str, Any],
) -> list[str]:
    """Gates that ALREADY hold, each phrased as the settled fact it is.

    Only facts. A gate that cannot be evaluated is omitted rather than claimed:
    the officer reads this list as "these are done", and a hopeful entry would
    be a lie at the worst possible moment. The gates still run for real at
    submission, and a refusal surfaces there.
    """
    facts: list[str] = []

    for signer in detail.get("signed_by", []):
        role = str(signer.get("signing_role", "")).replace("_", " ").strip()
        if role:
            facts.append(f"Signed by the {role}")

    # RECONCILIATION IS DELIBERATELY NOT CHECKED HERE.
    #
    # It was, and it cost 33 seconds: `assert_package_reconciled` re-resolves
    # the period, re-asks the balance identity and checks whether any sealed
    # run behind the package was withdrawn. Measured against this tenant, the
    # rest of the preview takes 0.21s and that one call takes 32.8s — so
    # opening the confirmation dialog sat on a spinner for half a minute to
    # render a single line of a checklist.
    #
    # Dropping it loses nothing real. The gate runs FOR REAL inside
    # `submit_package_via_channel`, which is the only place it can be
    # authoritative: a book can break between the preview and the press, so a
    # preview that claimed "already satisfied" was making a promise it could
    # not keep. The dialog names it as a gate that runs at submission instead.
    return facts
