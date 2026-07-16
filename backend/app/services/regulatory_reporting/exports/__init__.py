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
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryPackage, RegulatoryPackageArtifact
from app.services.ingestion import bank_slug
from app.services.regulatory_reporting.common import get_bank_or_404
from app.services.regulatory_reporting.exports.csv import render_csv
from app.services.regulatory_reporting.exports.pdf import render_pdf
from app.services.regulatory_reporting.exports.xlsx import render_xlsx
from app.services.regulatory_reporting.registry import get_definition
from app.services.regulatory_reporting.templates import (
    build_rendered_return,
    get_template,
)
from app.storage.client import ObjectMetadata, StorageLocation
from app.storage.factory import get_storage_client

type ExportKind = Literal["xlsx", "csv", "pdf"]

WRITTEN_BY = "regulatory_reporting"
_CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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


def export_package(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    kind: Literal["xlsx", "csv", "pdf"],
) -> RegulatoryPackageArtifact:
    """Render, store, and record one export artifact for the package."""
    definition = _definition_or_404(package.return_code)
    template = _template_or_404(definition.template_id)
    snapshot = _snapshot_or_409(package)
    bank = get_bank_or_404(db, ctx, package.bank_id)

    rendered = build_rendered_return(
        template,
        snapshot,
        package.source_runs,
        package_id=str(package.id),
        package_version=package.version,
    )

    extension = kind
    if kind == "xlsx":
        payload = render_xlsx(rendered, generated_at=package.generated_at)
    elif kind == "csv":
        payload, extension = render_csv(rendered)
    else:
        payload = render_pdf(
            rendered, sandbox_watermark=definition.default_channel == "orass_sandbox"
        )

    slug = bank_slug(db, bank)
    object_path = (
        f"bog_returns/{package.reporting_date.isoformat()}/{package.id}/"
        f"{package.return_code}.{extension}"
    )
    checksum = hashlib.sha256(payload).hexdigest()
    location = StorageLocation(institution_slug=slug, tier="outputs", object_path=object_path)
    storage = get_storage_client()
    storage.ensure_institution(slug)
    storage.write(
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
    return artifact


__all__ = ["ExportKind", "WRITTEN_BY", "export_package"]
