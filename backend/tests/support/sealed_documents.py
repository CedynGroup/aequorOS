"""Synthetic source rows shared by sealed-reference service and PostgreSQL tests."""

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy.engine import Connection

from app.db.base import Base
from app.storage.sealed_documents import DocumentFamily

SOURCES = {
    DocumentFamily.ARTIFACT: ("regulatory_artifact_versions", "artifact_document_bindings"),
    DocumentFamily.PACKAGE_ATTACHMENT: (
        "regulatory_package_attachments",
        "package_attachment_document_bindings",
    ),
    DocumentFamily.ICAAP_ATTACHMENT: ("icaap_attachments", "icaap_attachment_document_bindings"),
    DocumentFamily.SUPERVISORY_LETTER: (
        "bank_supervisory_addons",
        "supervisory_letter_document_bindings",
    ),
}


def insert_source(  # noqa: PLR0913 - synthetic source evidence and ownership
    connection: Connection,
    family: DocumentFamily,
    organization: str,
    bank: str,
    sha256: str,
    length: int,
    path: str,
    version: str | None = "original-provider-version",
    *,
    package_family: str = "bsd",
) -> UUID:
    identifier, actor = uuid4(), uuid4()
    row: dict[str, object] = {"id": identifier, "organization_id": organization}
    if family in (DocumentFamily.ARTIFACT, DocumentFamily.PACKAGE_ATTACHMENT):
        package = uuid4()
        connection.execute(
            Base.metadata.tables["regulatory_packages"]
            .insert()
            .values(
                id=package,
                organization_id=organization,
                bank_id=bank,
                return_family=package_family,
                return_code=f"SYN-{identifier.hex[:12]}",
                reporting_date=date(2026, 9, 30),
                frequency="monthly",
                basis="solo",
                status="generated",
                version=1,
                snapshot={},
                source_runs=[],
                generated_by=actor,
            )
        )
        row["package_id"] = package
    if family is DocumentFamily.ARTIFACT:
        row.update(
            kind="pdf",
            object_path=path,
            checksum_sha256=sha256,
            size_bytes=length,
            storage_version_id=version,
            created_by=actor,
        )
    else:
        row["bank_id"] = bank
        if family is DocumentFamily.SUPERVISORY_LETTER:
            row.update(
                status="draft",
                letter_reference="Synthetic reference",
                letter_date=date(2026, 9, 1),
                effective_from=date(2026, 9, 1),
                applies_to_basis="solo",
                basis="pct_total_rwa",
                basis_value=2,
                currency="GHS",
                letter_original_filename="letter.pdf",
                letter_media_type="application/pdf",
                letter_byte_size=length,
                letter_sha256=sha256,
                letter_storage_tier="outputs",
                letter_object_path=path,
                letter_storage_version_id=version,
                created_by=actor,
            )
        else:
            row.update(
                kind="synthetic",
                title="Synthetic document",
                original_filename="synthetic.pdf",
                media_type="application/pdf",
                byte_size=length,
                sha256=sha256,
                storage_tier="outputs",
                object_path=path,
                storage_version_id=version,
            )
            if family is DocumentFamily.PACKAGE_ATTACHMENT:
                row.update(
                    package_version=1, source="package_upload", gate="optional", attached_by=actor
                )
            else:
                cycle = uuid4()
                connection.execute(
                    Base.metadata.tables["icaap_cycles"]
                    .insert()
                    .values(
                        id=cycle,
                        organization_id=organization,
                        bank_id=bank,
                        fiscal_year=2026,
                        as_of_date=date(2026, 9, 30),
                        cycle_kind="annual",
                        basis="solo",
                        subsidiaries_declared=False,
                        title="Synthetic cycle",
                        framework_code="bog_icaap",
                        framework_version="2026.02-ed.1",
                        framework_sha256="a" * 64,
                        status="draft",
                        round=1,
                        due_date=date(2027, 3, 31),
                        due_date_basis="framework",
                        created_by=actor,
                    )
                )
                row.update(cycle_id=cycle, uploaded_by=actor)
    connection.execute(Base.metadata.tables[SOURCES[family][0]].insert().values(**row))
    return identifier
