"""Export engine entry point (docs/regulatory_reporting.md §5, exports).

``export_package`` is the pinned interface the API wave (RR-4) wires to
``exportRegulatoryPackage``: it renders the package's immutable snapshot
through the declarative template registry (``templates.py``), writes the
bytes to the outputs storage tier at
``bog_returns/{reporting_date}/{package_id}/{return_code}.{ext}``, and
upserts the ``regulatory_package_artifacts`` row — re-exporting the same kind
replaces the object and refreshes checksum/size, never duplicating rows.

Renders are deterministic per package version, so a re-export of an unchanged
package is an idempotent storage no-op with a stable checksum.

Two attestation prerequisites live here (docs/attestation_esignature.md §3.6):

* **The snapshot seal is verified, not merely stored** (gap G3). ``snapshot_sha256``
  is recomputed from the snapshot before anything renders, so a drifted snapshot
  can never silently produce an artifact — which is what the model comment always
  claimed and no code did.
* **Artifact versions are appended, never overwritten** (gap G2). The upsert of
  ``regulatory_package_artifacts`` stays for backward compatibility, but every
  export also appends an immutable ``regulatory_artifact_versions`` row pinning
  the object-store version id, so "the artifact as filed" is resolvable from the
  database. Once a signature references a version row, re-export is refused: the
  bytes a signature covers must not be replaceable underneath it.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    AttestationSignature,
    Bank,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
)
from app.services.ingestion import bank_slug
from app.services.regulatory_reporting.bog_forms.catalog import form_spec
from app.services.regulatory_reporting.bog_forms.engine import FormResult
from app.services.regulatory_reporting.bog_forms.registry_entries import is_bog_official_template
from app.services.regulatory_reporting.bog_forms.render import render_form_xlsx
from app.services.regulatory_reporting.bog_forms.render_pdf import render_form_pdf
from app.services.regulatory_reporting.common import get_bank_or_404
from app.services.regulatory_reporting.exports.csv import render_csv
from app.services.regulatory_reporting.exports.pdf import AttestedOfficer, render_pdf
from app.services.regulatory_reporting.exports.xlsx import render_xlsx
from app.services.regulatory_reporting.generation import snapshot_content_hash
from app.services.regulatory_reporting.registry import get_definition
from app.services.regulatory_reporting.templates import (
    build_rendered_return,
    get_template,
)
from app.storage.client import ObjectMetadata, StorageLocation
from app.storage.factory import get_storage_client

type ExportKind = Literal["xlsx", "csv", "pdf", "xlsx_working", "docx_working"]


def render_bog_form_xlsx(
    code: str,
    snapshot: dict,
    bank: Bank,
    generated_at: datetime,
    *,
    mode: str = "official",
) -> bytes:
    """Template-faithful workbook for an official BoG return, from the snapshot.

    ``mode="official"`` → the sealed values-only artifact (kind ``xlsx``), the
    copy officers certify; ``mode="working"`` → the formula copy with the
    template's live formulas (kind ``xlsx_working``), filed alongside it since
    2026-09-20 and never signed.
    """
    spec = form_spec(code)
    result = FormResult.from_snapshot(spec, snapshot)
    period = snapshot.get("reporting_period", {})
    return render_form_xlsx(
        result,
        bank_name=str(snapshot.get("institution", {}).get("name") or bank.name),
        period_label=str(period.get("label", "")),
        reporting_date=str(snapshot.get("reporting_date", "")),
        generated_at=generated_at,
        mode=mode,
    )


def render_bog_form_pdf(  # noqa: PLR0913 - the attestation record is two named parts
    code: str,
    snapshot: dict,
    bank: Bank,
    generated_at: datetime,
    *,
    package_line: str = "",
    signing_required: bool = True,
    officers: Sequence[AttestedOfficer] = (),
) -> bytes:
    """The official BoG return as a PDF — the artifact the institution files.

    The same sealed run as the xlsx exports, rendered onto BoG's own grid. The
    generic tabular renderer must never be used for a BSD form: it emits a
    line/cell/status listing, which is a completion aid, not a return.
    """
    spec = form_spec(code)
    result = FormResult.from_snapshot(spec, snapshot)
    period = snapshot.get("reporting_period", {})
    return render_form_pdf(
        result,
        bank_name=str(snapshot.get("institution", {}).get("name") or bank.name),
        period_label=str(period.get("label", "")),
        reporting_date=str(snapshot.get("reporting_date", "")),
        generated_at=generated_at,
        package_line=package_line,
        signing_required=signing_required,
        officers=officers,
    )


logger = logging.getLogger(__name__)

WRITTEN_BY = "regulatory_reporting"
_CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsx_working": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "zip": "application/zip",
    "csv": "text/csv",
    "pdf": "application/pdf",
}


def _definition_or_404(return_code: str):
    definition = get_definition(return_code)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Return code '{return_code}' is not registered.",
        )
    return definition


def _template_or_404(template_id: str):
    template = get_template(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No export template is defined for template id '{template_id}'.",
        )
    return template


def _snapshot_or_409(package: RegulatoryPackage) -> dict:
    snapshot = package.snapshot
    if not snapshot or not snapshot.get("sections"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "snapshot_empty",
                "message": (
                    "The package snapshot carries no sections and cannot be exported. "
                    "Regenerate the package first."
                ),
            },
        )
    return snapshot


def _verify_snapshot_seal(package: RegulatoryPackage, snapshot: dict) -> None:
    """Recompute the stored snapshot seal before anything renders (gap G3).

    A mismatch means the immutable snapshot has drifted from what was sealed at
    generation, which makes every downstream artifact — and any signature over
    it — untrustworthy. Pre-seal rows (``snapshot_sha256`` NULL) predate the
    column and can only be warned about.
    """
    stored = package.snapshot_sha256
    if stored is None:
        logger.warning(
            "regulatory package %s (%s v%s) carries no snapshot_sha256; "
            "the export cannot verify snapshot integrity",
            package.id,
            package.return_code,
            package.version,
        )
        return
    recomputed = snapshot_content_hash(snapshot)
    if recomputed == stored:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "snapshot_integrity_failed",
            "message": (
                f"The snapshot of {package.return_code} version {package.version} no longer "
                f"matches the content seal recorded when it was generated "
                f"(sealed {stored[:12]}…, computed {recomputed[:12]}…). The package snapshot "
                "is immutable evidence, so this is data corruption, not a stale render: do "
                "not file this artifact. Regenerate the return and escalate the mismatch."
            ),
        },
    )


def _refuse_if_signed(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, kind: str
) -> None:
    """Block re-export once a LIVE signature covers an artifact version (gap G2).

    Signedness is DERIVED, not flagged: ``regulatory_artifact_versions`` is
    append-only (migration 202607250027 blocks UPDATE), so a mutable "signed"
    column would be inert on Postgres and therefore a lie. The authoritative
    statement that these exact bytes were signed is the existence of an
    ``AttestationSignature`` pointing at the version row.

    Scoped to the package's CURRENT attestation cycle, which is what this
    guard's own remedy ("void the attestation") requires: a void increments the
    cycle rather than deleting signatures, so signatures from a withdrawn cycle
    would otherwise keep the return locked forever and make the void a one-way
    door. Their bytes are unaffected — every signed revision is archived at its
    own object path and pinned by version id, so nothing a withdrawn signature
    covers is replaced by a later export.
    """
    signed = db.scalar(
        select(RegulatoryArtifactVersion.id).where(
            RegulatoryArtifactVersion.organization_id == ctx.organization_id,
            RegulatoryArtifactVersion.package_id == package.id,
            RegulatoryArtifactVersion.kind == kind,
            RegulatoryArtifactVersion.id.in_(
                select(AttestationSignature.artifact_version_id).where(
                    AttestationSignature.organization_id == ctx.organization_id,
                    AttestationSignature.package_id == package.id,
                    AttestationSignature.attestation_cycle == package.attestation_cycle,
                    AttestationSignature.artifact_version_id.is_not(None),
                )
            ),
        )
    )
    if signed is None:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "artifact_version_signed",
            "message": (
                f"A signature already covers the {kind} artifact of {package.return_code} "
                f"version {package.version}, so those exact bytes cannot be replaced. "
                "Void the attestation, or regenerate the return as a new version, and "
                "export from there."
            ),
        },
    )


def export_package(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    kind: ExportKind,
) -> RegulatoryPackageArtifact:
    """Render, store, and record one export artifact for the package."""
    artifact, _version = export_package_version(db, ctx, package, kind)
    return artifact


def _attestation_record(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> tuple[bool, list[AttestedOfficer]]:
    """Whether this return is signed, and who handled it if it is not.

    ONE resolution for both PDF writers — the generic return and the official
    BoG form. They print the same attestation block in two layouts, so a second
    copy of this would let the two documents disagree about who prepared a
    return, which is precisely the kind of divergence a filed artifact must
    never carry.

    When signing is required the officers are irrelevant: the ceremony fills
    the signature fields and the block is its own record.
    """
    from app.services.attestation import policy as attestation_policy  # noqa: PLC0415
    from app.services.filing_workflow import chain as filing_chain  # noqa: PLC0415

    signing_policy = attestation_policy.resolve_policy(
        db,
        ctx,
        bank_id=package.bank_id,
        return_code=package.return_code,
        return_family=package.return_family,
        basis=package.basis,
        as_at=package.reporting_date,
    )
    if signing_policy.require_signature:
        return True, []

    # Who actually handled it, from the chain's own decisions — the same record
    # the workspace shows, so the filed document and the screen cannot disagree.
    state = filing_chain.load_state(db, ctx, package)
    titles = {stage.seq: stage.title for stage in state.stages}
    officers = [
        AttestedOfficer(
            stage=titles.get(decision.stage_seq, f"Stage {decision.stage_seq}"),
            name=decision.decided_by_name,
            title=decision.officer_title,
            at=decision.created_at.strftime("%d %b %Y %H:%M UTC"),
        )
        for decision in state.decisions
        # A send-back is a decision, but not an attestation: the officers of
        # record are those who moved the return forward.
        if decision.decision != "returned"
    ]
    return False, officers


def export_package_version(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    kind: ExportKind,
) -> tuple[RegulatoryPackageArtifact, RegulatoryArtifactVersion]:
    """:func:`export_package`, plus the immutable version row it appended.

    The attestation ceremony needs that row: it signs the bytes of ONE export
    and must pin the exact object version it covered, which the upserted
    artifact row cannot express (it forgets every earlier export). Everything
    else wants the artifact and is spared the second return value.
    """
    definition = _definition_or_404(package.return_code)
    template = _template_or_404(definition.template_id)
    snapshot = _snapshot_or_409(package)
    _verify_snapshot_seal(package, snapshot)
    _refuse_if_signed(db, ctx, package, kind)
    bank = get_bank_or_404(db, ctx, package.bank_id)
    _ensure_kind_supported(definition, kind)

    # The family seam, BEFORE the tabular render. A family whose document is
    # not a grid of rows and columns renders it itself and then falls into the
    # SAME storage / version / artifact code below, so an ICAAP PDF is stored,
    # versioned, hashed and signed exactly like every other return's. The seal
    # and the signed-bytes refusal above still run first.
    family_payload = _family_render(db, ctx, package, kind, bank)
    if family_payload is not None:
        payload, extension, file_stem = family_payload
        return _store_export(
            db, ctx, package, bank, kind, payload, extension, file_stem, snapshot
        )

    rendered = build_rendered_return(
        template,
        snapshot,
        package.source_runs,
        package_id=str(package.id),
        package_version=package.version,
    )

    extension = kind
    if kind == "xlsx_working":
        # Official BSD forms preserve the regulator's own workbook formulas —
        # since 2026-09-20 that copy is filed WITH the protected values-only
        # workbook, and is labelled accordingly. SDI packets use a separately
        # labelled working calculation sheet whose formula plan is explicit and
        # derived from the sealed snapshot; it is NOT filed, and keeps the
        # plain working-copy label that says so.
        if is_bog_official_template(definition.template_id):
            payload = render_bog_form_xlsx(
                definition.code, snapshot, bank, package.generated_at, mode="working"
            )
        elif definition.supports_working_copy:
            payload = render_xlsx(
                rendered,
                generated_at=package.generated_at,
                working_copy=True,
                snapshot=snapshot,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_code": "working_copy_unavailable",
                    "message": (
                        f"'{package.return_code}' does not declare a working-formula export. "
                        "Use 'xlsx' for the sealed export."
                    ),
                },
            )
        extension = "xlsx"
    elif kind == "docx_working":
        # The Word working copy of an ICAAP report. The kind is admitted by the
        # model, the DB CHECK and the export route so the wire contract is
        # coherent, but NO registered return renders one yet. Refusing by name
        # is the only honest answer: falling through to the generic branch
        # below would render a PDF and store it under a Word kind, which reads
        # downstream as a successful export of a document that does not exist.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "export_kind_not_supported_for_return",
                "message": (
                    f"'{package.return_code}' does not declare a Word working copy. "
                    "Use 'pdf' for the filing document."
                ),
            },
        )
    elif kind == "xlsx" and is_bog_official_template(definition.template_id):
        # Official BoG BSD form: rebuild the OFFICIAL workbook from the committed
        # layout with the immutable snapshot's cell values (values-only, sealed).
        payload = render_bog_form_xlsx(definition.code, snapshot, bank, package.generated_at)
    elif kind == "xlsx":
        payload = render_xlsx(rendered, generated_at=package.generated_at)
    elif kind == "csv":
        payload, extension = render_csv(rendered)
    elif kind == "pdf" and is_bog_official_template(definition.template_id):
        # Official BoG BSD form: the filing artifact is the FORM, drawn on the
        # official grid — never the generic line/cell/status listing.
        signing_required, officers = _attestation_record(db, ctx, package)
        payload = render_bog_form_pdf(
            definition.code,
            snapshot,
            bank,
            package.generated_at,
            package_line=f"{package.id} (version {package.version})",
            signing_required=signing_required,
            officers=officers,
        )
    else:
        signing_required, officers = _attestation_record(db, ctx, package)
        payload = render_pdf(
            rendered,
            sandbox_watermark=definition.default_channel == "orass_sandbox",
            signing_required=signing_required,
            officers=officers,
        )

    file_stem = (
        f"{package.return_code}.working" if kind == "xlsx_working" else package.return_code
    )
    return _store_export(
        db, ctx, package, bank, kind, payload, extension, file_stem, snapshot
    )


#: Export kinds a family admits, where the answer is narrower than "any kind".
#: An ICAAP filing is a document: the signed PDF is what is filed and the Word
#: copy is the internal review draft. A spreadsheet of an ICAAP does not exist,
#: and rendering the generic tabular exporter over a narrative snapshot would
#: produce an empty workbook that reads as a successful export.
FAMILY_EXPORT_KINDS: dict[str, frozenset[str]] = {
    "icaap": frozenset({"pdf", "docx_working"}),
}


def _ensure_kind_supported(definition, kind: ExportKind) -> None:
    allowed = FAMILY_EXPORT_KINDS.get(definition.family)
    if allowed is None or kind in allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "export_kind_not_supported_for_return",
            "message": (
                f"'{definition.code}' is a document, not a workbook: it exports as "
                f"{', '.join(sorted(allowed))}."
            ),
            "allowed_kinds": sorted(allowed),
        },
    )


def _family_render(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    kind: ExportKind,
    bank: Bank,
) -> tuple[bytes, str, str] | None:
    """``(payload, extension, file_stem)`` from the family's own renderer, or None."""
    from app.services.regulatory_reporting import family_hooks  # noqa: PLC0415 - lazy seam

    hooks = family_hooks.for_package(package)
    if hooks is None:
        return None
    try:
        return hooks.export(db, ctx, package, kind, bank)
    except NotImplementedError:
        return None


def _store_export(  # noqa: PLR0913 - one storage write is its named parts
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    bank: Bank,
    kind: ExportKind,
    payload: bytes,
    extension: str,
    file_stem: str,
    snapshot: dict,
) -> tuple[RegulatoryPackageArtifact, RegulatoryArtifactVersion]:
    """Write the bytes, append the immutable version row, upsert the artifact."""
    slug = bank_slug(db, bank)
    object_path = (
        f"bog_returns/{package.reporting_date.isoformat()}/{package.id}/{file_stem}.{extension}"
    )
    checksum = hashlib.sha256(payload).hexdigest()
    location = StorageLocation(institution_slug=slug, tier="outputs", object_path=object_path)
    storage = get_storage_client()
    storage.ensure_institution(slug)
    stored = storage.write(
        location,
        io.BytesIO(payload),
        ObjectMetadata(
            institution_slug=slug,
            tier="outputs",
            checksum_sha256=checksum,
            written_at=datetime.now(UTC),
            written_by=WRITTEN_BY,
            as_of_date=package.reporting_date.isoformat(),
            schema_version=str(snapshot.get("schema_version", "")) or None,
            source_reference=package.return_code,
        ),
        content_type=_CONTENT_TYPES[extension],
    )

    # The immutable record of this export. The artifact row below is upserted
    # and therefore forgets every earlier export; this row does not, and it
    # carries the object-store version so a signature can pin exact bytes.
    version = RegulatoryArtifactVersion(
        organization_id=ctx.organization_id,
        package_id=package.id,
        kind=kind,
        object_path=object_path,
        storage_version_id=stored.version_id,
        checksum_sha256=checksum,
        size_bytes=len(payload),
        created_by=ctx.actor_user_id,
    )
    db.add(version)

    artifact = db.scalar(
        select(RegulatoryPackageArtifact).where(
            RegulatoryPackageArtifact.organization_id == ctx.organization_id,
            RegulatoryPackageArtifact.package_id == package.id,
            RegulatoryPackageArtifact.kind == kind,
        )
    )
    if artifact is None:
        artifact = RegulatoryPackageArtifact(
            organization_id=ctx.organization_id,
            package_id=package.id,
            kind=kind,
            object_path=object_path,
            checksum_sha256=checksum,
            size_bytes=len(payload),
        )
        db.add(artifact)
    else:
        artifact.object_path = object_path
        artifact.checksum_sha256 = checksum
        artifact.size_bytes = len(payload)
    db.flush()
    return artifact, version


__all__ = ["ExportKind", "WRITTEN_BY", "export_package", "export_package_version"]
