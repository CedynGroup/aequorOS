"""Immutable identities and additive bindings for sealed document bytes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TableArgs, UuidV4PrimaryKeyMixin, utc_now


class SealedDocumentObject(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "sealed_document_objects"
    __table_args__: TableArgs = (
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        ForeignKeyConstraint(
            ["envelope_id", "bank_id", "organization_id"],
            [
                "object_key_envelopes.id",
                "object_key_envelopes.bank_id",
                "object_key_envelopes.organization_id",
            ],
        ),
        UniqueConstraint("id", "organization_id", "bank_id", name="uq_sealed_object_scope"),
        UniqueConstraint(
            "storage_slug", "storage_tier", "physical_key", name="uq_sealed_object_location"
        ),
        CheckConstraint("storage_tier = 'outputs'", name="ck_sealed_object_tier"),
        CheckConstraint("byte_length >= 0", name="ck_sealed_object_length"),
        CheckConstraint(
            "length(sha256) = 64 AND sha256 = lower(sha256)", name="ck_sealed_object_sha"
        ).ddl_if(dialect="sqlite"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_sealed_object_sha").ddl_if(
            dialect="postgresql"
        ),
        CheckConstraint(
            "physical_key = 'sealed/v1/' || id::text", name="ck_sealed_object_key"
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "length(context_path) > 0 AND length(storage_slug) > 0", name="ck_sealed_object_context"
        ),
        CheckConstraint(
            "(context_format = 'aws-esdk-object-v1' AND envelope_id IS NOT NULL) OR "
            "(context_format = 'plaintext-v1' AND envelope_id IS NULL)",
            name="ck_sealed_object_format",
        ),
        Index("ix_sealed_objects_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_slug: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    physical_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    context_format: Mapped[str] = mapped_column(String(32), nullable=False)
    context_path: Mapped[str] = mapped_column(String(512), nullable=False)
    envelope_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    original_version_id: Mapped[str | None] = mapped_column(String(255))
    published_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class _DocumentBinding:
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    object_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bound_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


def _binding_constraints(source: str, prefix: str) -> TableArgs:
    return (
        ForeignKeyConstraint(
            ["source_id", "organization_id"], [f"{source}.id", f"{source}.organization_id"]
        ),
        ForeignKeyConstraint(
            ["object_id", "organization_id", "bank_id"],
            [
                "sealed_document_objects.id",
                "sealed_document_objects.organization_id",
                "sealed_document_objects.bank_id",
            ],
        ),
        Index(f"ix_{prefix}_org_bank", "organization_id", "bank_id"),
        Index(f"ix_{prefix}_object", "object_id", "organization_id", "bank_id"),
    )


class ArtifactDocumentBinding(_DocumentBinding, Base):
    __tablename__ = "artifact_document_bindings"
    __table_args__ = _binding_constraints("regulatory_artifact_versions", "artifact_binding")


class PackageAttachmentDocumentBinding(_DocumentBinding, Base):
    __tablename__ = "package_attachment_document_bindings"
    __table_args__ = _binding_constraints(
        "regulatory_package_attachments", "package_attachment_binding"
    )


class IcaapAttachmentDocumentBinding(_DocumentBinding, Base):
    __tablename__ = "icaap_attachment_document_bindings"
    __table_args__ = _binding_constraints("icaap_attachments", "icaap_attachment_binding")


class SupervisoryLetterDocumentBinding(_DocumentBinding, Base):
    __tablename__ = "supervisory_letter_document_bindings"
    __table_args__ = _binding_constraints("bank_supervisory_addons", "supervisory_letter_binding")
