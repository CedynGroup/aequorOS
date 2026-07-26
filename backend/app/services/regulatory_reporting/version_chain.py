"""What a superseded version can still offer, and how it differs from another.

The supersession chain was readable as a list of statuses and timestamps, which
answers none of the questions asked about a prior filing: who certified it,
which file was actually sent, and what changed since. Both are recoverable —
signatures are append-only across cycles and artifacts are addressed per
package — so this module gathers them per version rather than leaving the card
inert.

Two things it deliberately does NOT do:

- **Invent a download.** Versions 2 and 3 of a live BSD3 chain were never
  exported; they hold no artifact and no archived revision. They report
  ``has_retrievable_files=False`` so the UI can say "never exported" instead of
  rendering a control that cannot work.
- **Present withdrawn signatures as current.** A void increments
  ``attestation_cycle`` and preserves its signatures (legal register L15), so a
  version can read ``unsigned`` while holding two of them. They are listed —
  a withdrawn attestation has to stay legible — and flagged ``withdrawn``.

The artifact surfaces are resolved through ``artifact_versions.list_versions``
and ``workflow.list_package_artifacts``, the same two the current version's
artifacts card uses, so a prior version is described by the resolver that
already decides what THE document is rather than by a parallel one that could
drift from it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import AttestationSignature, RegulatoryPackage
from app.schemas.regulatory_reporting import (
    PackageComparisonRead,
    PackageVersionChainEntryRead,
    PackageVersionChainRead,
    PackageVersionSignatureRead,
    RegulatoryArtifactRead,
)
from app.services.regulatory_reporting import artifact_versions, snapshot_diff, workflow
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_package_or_404,
    validation_passed,
)


def _chain_packages(
    db: Session, ctx: TenantContext, anchor: RegulatoryPackage
) -> list[RegulatoryPackage]:
    """Every version filed for this (return, reporting date, basis), newest first.

    Keyed on the natural identity rather than by walking ``supersedes_id``:
    solo and consolidated are independent chains for the same return and date
    (the partial unique index says so), and a chain whose link was never set
    still belongs to its reporting obligation.
    """
    return list(
        db.scalars(
            select(RegulatoryPackage)
            .where(
                RegulatoryPackage.organization_id == ctx.organization_id,
                RegulatoryPackage.bank_id == anchor.bank_id,
                RegulatoryPackage.return_code == anchor.return_code,
                RegulatoryPackage.reporting_date == anchor.reporting_date,
                RegulatoryPackage.basis == anchor.basis,
            )
            .order_by(RegulatoryPackage.version.desc())
        )
    )


def _signatures(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> list[PackageVersionSignatureRead]:
    """Every signature ever made on this version, across all attestation cycles."""
    rows = db.scalars(
        select(AttestationSignature)
        .where(
            AttestationSignature.organization_id == ctx.organization_id,
            AttestationSignature.package_id == package.id,
        )
        .order_by(AttestationSignature.created_at, AttestationSignature.id)
    )
    return [
        PackageVersionSignatureRead(
            signature_id=row.id,
            signing_role=row.signing_role,  # type: ignore[arg-type]
            signer_id=row.signer_id,
            signer_display_name=row.signer_display_name,
            officer_title=row.officer_title,
            # The trusted RFC 3161 time where the deployment has a TSA, matching
            # artifact_versions._signature_read; declared_at is the fallback.
            signed_at=row.tsa_time or row.declared_at,
            attestation_cycle=row.attestation_cycle,
            withdrawn=row.attestation_cycle != package.attestation_cycle,
        )
        for row in rows
    ]


def _entry(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> PackageVersionChainEntryRead:
    artifacts = workflow.list_package_artifacts(db, ctx, package.bank_id, package.id)
    versions = artifact_versions.list_versions(db, ctx, package.bank_id, package.id).versions
    return PackageVersionChainEntryRead(
        package_id=package.id,
        version=package.version,
        status=package.status,  # type: ignore[arg-type]
        is_current=package.status != "superseded",
        attestation_state=package.attestation_state,
        attestation_cycle=package.attestation_cycle,
        voided_at=package.voided_at,
        void_reason=package.void_reason,
        reporting_date=package.reporting_date,
        basis=package.basis,  # type: ignore[arg-type]
        generated_at=package.generated_at,
        generated_by=package.generated_by,
        validation_passed=validation_passed(package),
        submission_revision=package.submission_revision,
        snapshot_sha256=package.snapshot_sha256,
        signatures=_signatures(db, ctx, package),
        artifacts=[RegulatoryArtifactRead.model_validate(row) for row in artifacts],
        artifact_versions=versions,
        has_retrievable_files=bool(artifacts) or bool(versions),
    )


def get_version_chain(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> PackageVersionChainRead:
    """The whole supersession chain the given package belongs to, enriched."""
    get_bank_or_404(db, ctx, bank_id)
    anchor = get_package_or_404(db, ctx, bank_id, package_id)
    packages = _chain_packages(db, ctx, anchor)
    current = next((row for row in packages if row.status != "superseded"), None)
    return PackageVersionChainRead(
        bank_id=bank_id,
        return_code=anchor.return_code,
        reporting_date=anchor.reporting_date,
        basis=anchor.basis,  # type: ignore[arg-type]
        current_package_id=current.id if current is not None else None,
        versions=[_entry(db, ctx, row) for row in packages],
    )


def compare_versions(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID, against_id: UUID
) -> PackageComparisonRead:
    """Diff the path package's figures (base) against another version (target).

    Cross-return comparison is refused rather than rendered: two different
    return codes share no row codes, so the diff would report every line of both
    templates as added or removed — noise dressed up as a finding. Everything
    else the same return can vary by is allowed, because each is a comparison a
    supervisor legitimately asks for: a later version of the same filing, the
    same return at an earlier reporting date, or solo against consolidated.
    """
    get_bank_or_404(db, ctx, bank_id)
    base = get_package_or_404(db, ctx, bank_id, package_id)
    target = get_package_or_404(db, ctx, bank_id, against_id)
    if base.return_code != target.return_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "comparison_return_mismatch",
                "message": (
                    f"These packages are different returns ({base.return_code} and "
                    f"{target.return_code}); their line items are not comparable. "
                    "Compare versions of the same return."
                ),
            },
        )
    return snapshot_diff.build_comparison(base, target)


__all__ = ["compare_versions", "get_version_chain"]
