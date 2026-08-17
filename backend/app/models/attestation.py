"""Attestation & e-signature models (docs/attestation_esignature.md).

Evidence tables here are **DB-enforced unalterable** — migration 202607250027
revokes UPDATE and TRUNCATE from every non-superuser role, installs a
BEFORE UPDATE trigger that raises, and adds a restrictive RLS policy that no
future permissive policy can override. Signature evidence the owning role can
rewrite is not evidence (gap G1), so the guarantee lives in the schema rather
than in review discipline.

Precisely what is guaranteed, because the distinction matters:
  * ``attestation_signatures``, ``signer_identities``,
    ``regulatory_artifact_versions`` — never UPDATE-able, never TRUNCATE-able.
    DELETE is reachable only by deleting the owning package (CASCADE), which no
    production path does and which a broken hash chain would expose.
  * ``audit_events`` (in the reporting module) — additionally never DELETE-able.
  * ``signer_keys`` — NOT append-only: key lifecycle genuinely transitions
    (active → rotated → revoked).
  * ``signing_authorizations`` — NOT append-only: single-use tokens are marked
    consumed.
  * ``return_signing_policies`` — configuration, versioned by effective date
    rather than edited in place.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now

# --- vocabularies -----------------------------------------------------------

SIGNING_ROLES: tuple[str, ...] = ("preparer", "approver", "board", "witness")
BINDING_CLASSES: tuple[str, ...] = ("engine_run", "master_data")
ATTESTATION_STATES: tuple[str, ...] = (
    "unsigned",
    "preparer_certified",
    "fully_certified",
    "void",
)
SIGNATURE_METHODS: tuple[str, ...] = (
    "pades_b_lta",
    "pades_b_lt",
    "detached_ecdsa_p256_sha256",
    "detached_rsa_pss_sha256",
)
SIGNER_KEY_STATUSES: tuple[str, ...] = ("active", "rotated", "revoked")
SIGNER_KEY_BACKENDS: tuple[str, ...] = ("pkcs11", "kms", "software", "openbao")
APPEARANCE_KINDS: tuple[str, ...] = ("drawn", "typed")
#: Keys, not font names — the catalogue that resolves each one to a bundled
#: OFL script face or a PDF standard-14 face is
#: ``services/attestation/typed_fonts.py``, and the two must agree exactly.
#: Script faces first: that is the order the adoption panel offers, and the
#: base-14 four are kept only so a mark adopted before the script faces existed
#: still reads back.
TYPED_SIGNATURE_FONTS: tuple[str, ...] = (
    "caveat",
    "dancing_script",
    "great_vibes",
    "allura",
    "times_italic",
    "times_bold_italic",
    "helvetica_oblique",
    "courier_oblique",
)
RECIPIENT_STATUSES: tuple[str, ...] = ("pending", "signed")
#: What a placed box prints. ``signature`` is the PDF signature field; the rest
#: are text form fields whose value is derived server-side from the signature
#: record (``services/attestation/pdf_signing.py``).
PLACEMENT_FIELD_TYPES: tuple[str, ...] = (
    "signature",
    "initials",
    "name",
    "title",
    "date_signed",
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


# --- Phase 1: the permanent signer identity ---------------------------------


class SignerIdentity(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """A permanent, opaque, per-user signer identifier (``SGN-…``).

    Derived once from the platform user id (HMAC + versioned pepper) and then
    PERSISTED as the authority: rotating the pepper never moves an existing
    identity, because derivation runs exactly once. ``user_id`` is RESTRICT —
    a person who has ever been provisioned to sign cannot be deleted out from
    under their signatures (gap G10).
    """

    __tablename__ = "signer_identities"
    __table_args__ = (
        # Global uniqueness: the identifier is opaque, so a platform-wide index
        # leaks nothing across tenants while guaranteeing no ID is ever reused.
        Index("uq_signer_identities_signer_id", "signer_id", unique=True),
        UniqueConstraint("user_id", name="uq_signer_identities_user_id"),
        UniqueConstraint("id", "organization_id", name="uq_signer_identities_id_org"),
        Index("ix_signer_identities_organization_id", "organization_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    signer_id: Mapped[str] = mapped_column(String(24), nullable=False)
    derivation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provisioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


# --- Phase 4: signing keys (custody metadata only — never key material) -----


class SignerKey(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Custody metadata for a signer's key. Contains NO secret material.

    The private key lives in an HSM/KMS as a non-exportable object; only a
    reference (``key_ref``) plus the issued certificate is stored here.
    """

    __tablename__ = "signer_keys"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(SIGNER_KEY_STATUSES)})", name="ck_signer_keys_status"
        ),
        CheckConstraint(
            f"backend IN ({_values(SIGNER_KEY_BACKENDS)})", name="ck_signer_keys_backend"
        ),
        Index("ix_signer_keys_organization_id", "organization_id"),
        Index("ix_signer_keys_signer_id_status", "signer_id", "status"),
        UniqueConstraint("id", "organization_id", name="uq_signer_keys_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    signer_id: Mapped[str] = mapped_column(String(24), nullable=False)
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    #: PKCS#11 label / KMS key id. Not a secret; useless without HSM auth.
    key_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(40), nullable=False)
    certificate_pem: Mapped[str] = mapped_column(Text, nullable=False)
    certificate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    certificate_chain_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


# --- Phase 2: step-up authorisation ----------------------------------------


class SigningAuthorization(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """A single-use permission to use one signer's key, for one exact act.

    Minted only after step-up re-authentication and bound to
    ``(user, package, certification_digest, signing_role)``. Possession of a
    session token is deliberately NOT sufficient to sign (gap G5).
    """

    __tablename__ = "signing_authorizations"
    __table_args__ = (
        CheckConstraint(
            f"signing_role IN ({_values(SIGNING_ROLES)})",
            name="ck_signing_authorizations_role",
        ),
        Index("uq_signing_authorizations_token_hash", "token_hash", unique=True),
        Index("ix_signing_authorizations_org_package", "organization_id", "package_id"),
        UniqueConstraint("id", "organization_id", name="uq_signing_authorizations_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    signer_id: Mapped[str] = mapped_column(String(24), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    signing_role: Mapped[str] = mapped_column(String(16), nullable=False)
    certification_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Only the hash of the authorisation token is stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: acr / amr / auth_time / ip / user_agent from the fresh authentication.
    auth_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


# --- Phase 3: per-return signing policy ------------------------------------


class ReturnSigningPolicy(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Who must sign which return — configuration, never hardcoded.

    Resolution is most-specific-first (see services/attestation/policy.py):
    (bank, return_code, basis) → (bank, return_code) → (bank, family) →
    (org, return_code) → (org, family) → platform default.

    ``require_signed_pdf`` and ``require_signature`` exist because the
    corresponding Bank of Ghana requirements are UNCONFIRMED (confirmation
    register C1/C3/C4); both default conservatively.
    """

    __tablename__ = "return_signing_policies"
    __table_args__ = (
        Index(
            "ix_return_signing_policies_scope",
            "organization_id",
            "bank_id",
            "return_code",
            "return_family",
        ),
        UniqueConstraint("id", "organization_id", name="uq_return_signing_policies_id_org"),
        CheckConstraint(
            "return_code IS NOT NULL OR return_family IS NOT NULL",
            name="ck_return_signing_policies_scope",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: NULL = applies to every bank in the organization.
    bank_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    return_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    return_family: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: NULL = applies to both solo and consolidated.
    basis: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: [{"role": "approver", "officer_titles": ["Chief Financial Officer"], "min_count": 1}]
    required_signatures: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    required_attachments: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    require_signature: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true"), nullable=False
    )
    require_signed_pdf: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    distinct_signers: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)


# --- Phase 0: append-only artifact versions --------------------------------


class RegulatoryArtifactVersion(UuidV4PrimaryKeyMixin, Base):
    """One immutable row per export, pinning the object-store version.

    The existing ``regulatory_package_artifacts`` row is upserted in place by
    the exporter (gap G2), which means "the artifact as filed" cannot be
    resolved from the database. This table appends instead, and records the S3
    ``version_id`` so a signature can pin the exact bytes it covered.
    """

    __tablename__ = "regulatory_artifact_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_regulatory_artifact_versions_org_package_kind",
            "organization_id",
            "package_id",
            "kind",
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_artifact_versions_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # widened for xlsx_working
    object_path: Mapped[str] = mapped_column(String(512), nullable=False)
    #: Object-store version id when the backend supplies one (S3/MinIO
    #: versioning). NULL only where the backend cannot report it.
    storage_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # NOTE: there is deliberately no "signed" flag. This table is append-only,
    # so a mutable marker could never be written on Postgres; whether a version
    # was signed is derived from an AttestationSignature referencing it.
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


# --- Phase 4: the signature record itself ----------------------------------


class AttestationSignature(UuidV4PrimaryKeyMixin, Base):
    """One signature. Append-only, permanently attributable, self-contained.

    Everything verification needs is in the row: the denormalised permanent
    ``signer_id``, the display name as it stood at signing, the certificate
    chain as signed, the trusted timestamp, the digests, and the exact
    canonical payload. Nothing depends on the live user row still existing.
    """

    __tablename__ = "attestation_signatures"
    __table_args__ = (
        CheckConstraint(
            f"signing_role IN ({_values(SIGNING_ROLES)})",
            name="ck_attestation_signatures_role",
        ),
        CheckConstraint(
            f"signature_method IN ({_values(SIGNATURE_METHODS)})",
            name="ck_attestation_signatures_method",
        ),
        CheckConstraint(
            f"binding_class IN ({_values(BINDING_CLASSES)})",
            name="ck_attestation_signatures_binding_class",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_attestation_signatures_org_package",
            "organization_id",
            "package_id",
        ),
        Index("ix_attestation_signatures_signer_id", "signer_id"),
        UniqueConstraint("id", "organization_id", name="uq_attestation_signatures_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    package_version: Mapped[int] = mapped_column(Integer, nullable=False)

    signing_role: Mapped[str] = mapped_column(String(16), nullable=False)
    officer_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: The permanent signee ID, denormalised so attribution outlives the user.
    signer_id: Mapped[str] = mapped_column(String(24), nullable=False)
    signer_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: Name as it stood at signing. Redactable under Act 843 without
    #: impairing attribution, which rests on the opaque signer_id (L10).
    signer_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    binding_class: Mapped[str] = mapped_column(String(16), nullable=False)
    certification_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    register_state_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_source_runs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )

    statement: Mapped[str] = mapped_column(Text, nullable=False)
    attestation_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    signature_method: Mapped[str] = mapped_column(String(40), nullable=False)
    signature_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    certificate_pem: Mapped[str] = mapped_column(Text, nullable=False)
    certificate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    certificate_chain_pem: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: RFC 3161 token + the trusted time it asserts. Authoritative over
    #: declared_at, and what keeps a signature valid after cert revocation.
    tsa_token: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    tsa_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tsa_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Server clock — record-keeping only, never authoritative.
    declared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    auth_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    #: The signed PDF version this signature is embedded in (NULL when the
    #: policy does not require a signed PDF — confirmation register C1).
    artifact_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    #: Per-tenant tamper-evident chain (generalises storage/access_log.py).
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Which attestation cycle this signature belongs to. A void increments the
    #: package's cycle rather than mutating signatures, so the table stays
    #: strictly append-only: signatures from a voided cycle remain readable
    #: forever, and "current" signatures are those matching the package's cycle
    #: (legal register L15 — a withdrawn attestation must stay legible).
    attestation_cycle: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


# --- signature field placement ---------------------------------------------


class ReturnSignaturePlacement(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Where one of a signing role's boxes sits — reusable per return code.

    Coordinates are PDF user space, origin bottom-left, in points, on the page
    named by ``page_index``. Integers rather than floats because sub-point
    precision in a signature box carries no meaning, and a stored float would
    invite two placements that differ only in rounding.

    A role has several rows, one per placed box: a regulator's attestation block
    asks for a name, a designation, a signature and a date per officer.
    ``field_index`` distinguishes two boxes of the same kind for the same role
    and is assigned by the service from list order, never by a client.

    Deliberately NOT effective-dated, unlike ``return_signing_policies``: a
    policy decides whether a filed return was properly attested and therefore
    has to be reconstructible as at the reporting date, whereas a placement only
    decides where the ink landed. What was signed is evidenced by the signed
    bytes themselves, so re-placing a field never reaches history.
    """

    __tablename__ = "return_signature_placements"
    __table_args__ = (
        CheckConstraint(
            f"signing_role IN ({_values(SIGNING_ROLES)})",
            name="ck_return_signature_placements_role",
        ),
        CheckConstraint(
            f"field_type IN ({_values(PLACEMENT_FIELD_TYPES)})",
            name="ck_return_signature_placements_field_type",
        ),
        CheckConstraint("field_index >= 1", name="ck_return_signature_placements_index"),
        CheckConstraint("page_index >= 0", name="ck_return_signature_placements_page"),
        CheckConstraint("x2 > x1 AND y2 > y1", name="ck_return_signature_placements_box"),
        # Two partial indexes rather than one unique constraint over every
        # column: a NULL bank_id means "every bank in the organization", and
        # Postgres treats NULLs as distinct, so a plain constraint would happily
        # admit two conflicting organization-wide rows for one return and box.
        Index(
            "uq_return_signature_placements_bank",
            "organization_id",
            "bank_id",
            "return_code",
            "signing_role",
            "field_type",
            "field_index",
            unique=True,
            postgresql_where=sql_text("bank_id IS NOT NULL"),
            sqlite_where=sql_text("bank_id IS NOT NULL"),
        ),
        Index(
            "uq_return_signature_placements_org",
            "organization_id",
            "return_code",
            "signing_role",
            "field_type",
            "field_index",
            unique=True,
            postgresql_where=sql_text("bank_id IS NULL"),
            sqlite_where=sql_text("bank_id IS NULL"),
        ),
        UniqueConstraint("id", "organization_id", name="uq_return_signature_placements_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: NULL = the organization-wide template for this return code.
    bank_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    return_code: Mapped[str] = mapped_column(String(40), nullable=False)
    signing_role: Mapped[str] = mapped_column(String(16), nullable=False)
    field_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="signature", server_default="signature"
    )
    field_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    x1: Mapped[int] = mapped_column(Integer, nullable=False)
    y1: Mapped[int] = mapped_column(Integer, nullable=False)
    x2: Mapped[int] = mapped_column(Integer, nullable=False)
    y2: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)


class PackageSignaturePlacement(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """A per-package override of the return template.

    This is what a preparer drags onto the page in the signing workspace. It
    beats the template, and EVERY box — both signers', every kind — comes from
    it: the approver cannot move their own fields afterwards, because the
    preparer's DocMDP certification permits only form *filling* (see the
    ``pdf_signing`` module docstring). Placing the approver's fields is therefore
    the preparer's job by construction, not by preference.
    """

    __tablename__ = "package_signature_placements"
    __table_args__ = (
        CheckConstraint(
            f"signing_role IN ({_values(SIGNING_ROLES)})",
            name="ck_package_signature_placements_role",
        ),
        CheckConstraint(
            f"field_type IN ({_values(PLACEMENT_FIELD_TYPES)})",
            name="ck_package_signature_placements_field_type",
        ),
        CheckConstraint("field_index >= 1", name="ck_package_signature_placements_index"),
        CheckConstraint("page_index >= 0", name="ck_package_signature_placements_page"),
        CheckConstraint("x2 > x1 AND y2 > y1", name="ck_package_signature_placements_box"),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "organization_id",
            "package_id",
            "signing_role",
            "field_type",
            "field_index",
            name="uq_package_signature_placements_role",
        ),
        UniqueConstraint("id", "organization_id", name="uq_package_signature_placements_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    signing_role: Mapped[str] = mapped_column(String(16), nullable=False)
    field_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="signature", server_default="signature"
    )
    field_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    x1: Mapped[int] = mapped_column(Integer, nullable=False)
    y1: Mapped[int] = mapped_column(Integer, nullable=False)
    x2: Mapped[int] = mapped_column(Integer, nullable=False)
    y2: Mapped[int] = mapped_column(Integer, nullable=False)
    placed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)


# --- adopted signature appearance ------------------------------------------


class AdoptedSignatureAppearance(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """The mark an officer adopted once and reuses — drawn or typed.

    NOT append-only, and that is the point: this is *presentation*, not
    evidence. Re-adoption is a normal act (a better-drawn mark, a corrected
    spelling) and rewrites the row; what a signature committed to lives in the
    ``attestation_signatures`` row and the signed bytes, neither of which this
    table can reach. Every adoption still raises an audit event, so the history
    of the mark stays reconstructible from the immutable trail.

    ``image_png`` holds ONLY server-normalised bytes — re-rastered to fixed
    dimensions, metadata stripped, format forced to PNG
    (``services/attestation/appearance.py``). The raw upload is never persisted.
    """

    __tablename__ = "signature_appearances"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ({_values(APPEARANCE_KINDS)})", name="ck_signature_appearances_kind"
        ),
        CheckConstraint(
            "(kind = 'drawn' AND image_png IS NOT NULL AND typed_name IS NULL) "
            "OR (kind = 'typed' AND typed_name IS NOT NULL AND image_png IS NULL)",
            name="ck_signature_appearances_payload",
        ),
        UniqueConstraint("organization_id", "signer_id", name="uq_signature_appearances_signer"),
        UniqueConstraint("id", "organization_id", name="uq_signature_appearances_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: The permanent signee ID rather than the user id: the mark belongs to the
    #: signing identity, so it survives everything about the person except them.
    signer_id: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    image_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    typed_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    typed_font: Mapped[str | None] = mapped_column(String(40), nullable=True)
    adopted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    adopted_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


# --- named recipient routing -----------------------------------------------


class PackageSignatureRecipient(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """A named person routed to fill one of the policy's signing slots.

    The policy names ROLES; this names PEOPLE, and it *satisfies* the policy
    rather than replacing it — every guard in ``attestation.workflow`` still runs
    when the nominee actually signs.

    Scoped by ``attestation_cycle`` for the same reason signatures are: a void
    increments the cycle, so the routing of a withdrawn attestation stays
    legible instead of being overwritten (legal register L15).
    """

    __tablename__ = "package_signature_recipients"
    __table_args__ = (
        CheckConstraint(
            f"signing_role IN ({_values(SIGNING_ROLES)})",
            name="ck_package_signature_recipients_role",
        ),
        CheckConstraint(
            f"status IN ({_values(RECIPIENT_STATUSES)})",
            name="ck_package_signature_recipients_status",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_package_signature_recipients_inbox",
            "organization_id",
            "recipient_user_id",
            "status",
        ),
        UniqueConstraint(
            "organization_id",
            "package_id",
            "attestation_cycle",
            "signing_role",
            "recipient_user_id",
            name="uq_package_signature_recipients_nomination",
        ),
        UniqueConstraint("id", "organization_id", name="uq_package_signature_recipients_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attestation_cycle: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=sql_text("1")
    )
    signing_role: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: Denormalised at nomination, so the routing outlives the user row exactly
    #: the way a signature record does.
    recipient_signer_id: Mapped[str] = mapped_column(String(24), nullable=False)
    recipient_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_job_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    routing_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signature_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    nominated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
