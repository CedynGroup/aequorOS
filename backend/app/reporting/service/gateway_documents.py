"""The documents a gateway filing carries: the return's filed Excel, CSV and PDF.

The structured payload is what the gateway validates; the documents are the return as
the bank's officers approved it. Both go in one filing, and the manifest the bank signs
lists every document's SHA-256 and size, so the signature covers the files too.

The set is exactly what the submission's filing set chose (a signed revision in place
of its unsigned export, the full pack otherwise), and every byte is read through the
archived revision that wrote it, verified against its recorded digest. A document that
cannot be proven is not sent: the filing stops instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.attestation.public import RegulatoryArtifactVersion
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service import artifact_versions
from app.reporting.service.channels.base import FiledArtifact
from app.reporting.service.channels.errors import ChannelPreconditionError
from app.storage.factory import get_storage_client

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEDIA_TYPES: dict[str, str] = {
    ".xlsx": XLSX,
    ".csv": "text/csv",
    ".zip": "application/zip",
    ".pdf": "application/pdf",
}
#: Artifact kinds that reach the regulator. The Word ICAAP draft (``docx_working``)
#: never goes.
_FILED_KINDS = frozenset({"xlsx", "xlsx_working", "csv", "pdf"})


@dataclass(frozen=True, slots=True)
class GatewayDocument:
    name: str
    media_type: str
    content: bytes
    #: The filing-set artifact these bytes came from (its storage path).
    object_path: str


def _is_signed(artifact: FiledArtifact) -> bool:
    """The filing set carries an officers' signed revision as a version row."""
    return isinstance(artifact, RegulatoryArtifactVersion)


def _name(base: str, artifact: FiledArtifact, suffix: str, *, has_formula_workbook: bool) -> str:
    if artifact.kind == "xlsx" and has_formula_workbook:
        # Only reached for a signed values-only copy: it goes beside the workbook.
        return f"{base}_signed_values{suffix}"
    return f"{base}{suffix}"


def _version_of(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, artifact: FiledArtifact
) -> RegulatoryArtifactVersion:
    if isinstance(artifact, RegulatoryArtifactVersion):
        return artifact
    version = artifact_versions.archived_version(
        db,
        ctx,
        package_id=artifact.package_id,
        kind=artifact.kind,
        object_path=artifact.object_path,
        checksum_sha256=artifact.checksum_sha256,
    )
    if version is None:
        raise ChannelPreconditionError(
            f"The {artifact.kind.upper()} copy of {package.return_code} cannot be proven "
            "against its archived revision, so it cannot be filed. Export the return "
            "again, then file."
        )
    return version


def filed_documents(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    artifacts: Sequence[FiledArtifact],
) -> list[GatewayDocument]:
    """The filing set's documents, read and verified, named for the regulator.

    Where the return has a formula workbook (the regulator's template with its live
    formulas), that workbook IS the Excel the regulator receives, under the return's
    own name; the values-only copy is not sent beside it. The one exception is a
    values-only copy the bank's officers signed: a signed record always goes, so it
    travels as ``…_signed_values.xlsx``.
    """
    storage = get_storage_client()
    base = f"{package.return_code}_{package.reporting_date.isoformat()}"
    has_formula_workbook = any(artifact.kind == "xlsx_working" for artifact in artifacts)
    documents: list[GatewayDocument] = []
    names: set[str] = set()
    for artifact in artifacts:
        suffix = PurePosixPath(artifact.object_path).suffix.lower()
        media_type = MEDIA_TYPES.get(suffix)
        if media_type is None or artifact.kind not in _FILED_KINDS:
            continue
        if artifact.kind == "xlsx" and has_formula_workbook and not _is_signed(artifact):
            continue
        name = _name(base, artifact, suffix, has_formula_workbook=has_formula_workbook)
        if name in names:
            continue
        version = _version_of(db, ctx, package, artifact)
        _, content = artifact_versions.read_version_bytes(
            db, ctx, package.bank_id, version.id, storage
        )
        documents.append(
            GatewayDocument(
                name=name,
                media_type=media_type,
                content=content,
                object_path=artifact.object_path,
            )
        )
        names.add(name)
    return documents
