"""Regulatory Reporting & Submission Hub tables (docs/regulatory_reporting.md §3).

Packages are immutable versions of a generated return for one bank, reporting
date, return, and basis. Regeneration supersedes the current version without
changing its history. Solo and consolidated packages have independent version
chains. Artifacts, approvals, and submission events are append-only; channel
credentials remain write-only.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin, utc_now

PACKAGE_STATUSES = (
    "draft",
    "generated",
    "validated",
    "pending_approval",
    "approved",
    "submitted",
    "acknowledged",
    # Regulator outcomes mirror ORASS (LRT guide §5): "rejected" is returned
    # for correction (rework via a superseding version), "declined" is final.
    "rejected",
    "declined",
    "superseded",
)
# "corporate" (plan W5) is the event-driven LRT pack family; the DB CHECK
# constraint ck_regulatory_packages_return_family was widened to include it
# in migration 202607240021. "large_exposures" (plan W6) is the monthly
# Large Exposures Directive family (Templates 1/1a/2/3/4); the constraint
# was widened again in migration 202607240022. "dbk" (W6 remainder) is the
# DBK daily family (Notice BG/FMD/2026/07) — the family and the "daily"
# frequency were admitted in migration 202607240023.
# "stress" (Phase 2 item 6) is the event-driven Stress Test Output Report
# pack — a Board/ALCO artifact, not a BoG return; the constraint was widened
# in migration 202608070035. "bsd" is the family of the official Bank of Ghana
# BSD prudential returns (BSD1 … BSD17, bog_forms/); it and the "weekly"
# frequency (BSD1/1A/1B/14/15A/15B) were admitted in migration 202608150013,
# which also recoded the legacy mis-labelled 'BSD2'/'BSD3' packages to
# 'CAR-RWA'/'LCR-NSFR' (docs/bog_returns/00_full_return_registry.md §3).
# "sdi" is the separate Specialised Deposit-Taking Institution return family;
# it is admitted in migration 202608230040 and never aliases a BSD form.
RETURN_FAMILIES = (
    "liquidity",
    "capital",
    "irrbb",
    "fx",
    "icaap_stress",
    "corporate",
    "large_exposures",
    "dbk",
    "stress",
    "bsd",
    "sdi",
    # The credit / NPL family (Notice BG/GOV/SEC/2025/23; credit PR-6,
    # migration 202609010050).
    "credit",
    # The ICAAP FILING family (ICAAP P3, migration 202609190058): the annual
    # report, its ¶74 updates and the ¶82 disclosure. Distinct from
    # "icaap_stress", which is the Appendix II stress annex that rides inside
    # it — same subject, different filing role, and the family is what the
    # package-route authorization and the frozen-package gate key on.
    "icaap",
)
RETURN_FREQUENCIES = ("weekly", "monthly", "quarterly", "semiannual", "annual", "daily")
RETURN_BASES = ("solo", "consolidated")
# "xlsx" is the OFFICIAL (sealed, values-only) Excel export — the audit twin of
# the submission PDF, and the copy officers certify; "xlsx_working" (2026-08-16,
# migration 202608160015) is the recalculable copy. On an official BoG BSD form
# it is the FORMULA copy carrying the template's own live formulas, and since
# 2026-09-20 it is filed alongside the protected copy (still never signed); on
# an SDI packet it is an AequorOS calculation sheet and is not filed. The rule
# is workflow.filing_admits_artifact. See bog_forms/render.py.
# "docx_working" (2026-09-19, migration 202609190058) is the Word working copy
# of an ICAAP report, which an officer marks up: never signed, and never filed.
ARTIFACT_KINDS = ("xlsx", "csv", "pdf", "xlsx_working", "docx_working")
#: Where an attachment is required, and by whom. ``freeze`` documents must be on
#: the cycle before the report is sealed (they are part of what was approved);
#: ``submission`` documents accompany the filing itself (the Board resolution);
#: ``optional`` is supporting evidence.
PACKAGE_ATTACHMENT_GATES = ("freeze", "submission", "optional")
#: How the attachment reached the package: uploaded against it, or copied by
#: reference from the ICAAP cycle at freeze.
PACKAGE_ATTACHMENT_SOURCES = ("package_upload", "icaap_cycle")
# "orass_api" is the production machine-to-machine channel (Vizor API Service
# wire contract configured per bank once BoG/Regnology onboarding completes);
# "orass_sandbox" remains the labeled simulator for pre-onboarding use.
SUBMISSION_CHANNELS = ("orass_api", "orass_sandbox", "email", "manual")
SUBMISSION_EVENTS = ("submitted", "status_poll", "acknowledged", "rejected", "declined")
APPROVAL_ACTIONS = ("requested", "approved", "rejected")
RESUBMISSION_STATUSES = ("requested", "granted", "denied")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


class RegulatoryPackage(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One immutable generated-return snapshot version for a reporting date."""

    __tablename__ = "regulatory_packages"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(PACKAGE_STATUSES)})",
            name="ck_regulatory_packages_status",
        ),
        CheckConstraint(
            f"return_family IN ({_values(RETURN_FAMILIES)})",
            name="ck_regulatory_packages_return_family",
        ),
        CheckConstraint(
            f"frequency IN ({_values(RETURN_FREQUENCIES)})",
            name="ck_regulatory_packages_frequency",
        ),
        CheckConstraint(
            "attestation_state IN ('unsigned', 'preparer_certified', 'fully_certified', 'void')",
            name="ck_regulatory_packages_attestation_state",
        ),
        CheckConstraint(
            f"basis IN ({_values(RETURN_BASES)})",
            name="ck_regulatory_packages_basis",
        ),
        CheckConstraint("version >= 1", name="ck_regulatory_packages_version"),
        # --- rehearsal (D-029, ruled by D-068) -------------------------------
        # A rehearsal runs the full lifecycle so a bank can dry-run freeze,
        # signature and submission. These are what stop it ever being mistaken
        # for, or becoming, a filing. The two invariants a row-level CHECK
        # cannot express — a real package must not supersede a rehearsal, and a
        # rehearsal must not reach a transmitting channel — are enforced in the
        # services and pinned by tests; migration 202609190062 says so in full.
        CheckConstraint(
            "NOT is_rehearsal OR return_family = 'icaap'",
            name="ck_regulatory_packages_rehearsal_is_icaap",
        ),
        CheckConstraint(
            "NOT is_rehearsal OR status NOT IN ('acknowledged', 'rejected', 'declined')",
            name="ck_regulatory_packages_rehearsal_never_acknowledged",
        ),
        CheckConstraint(
            "NOT is_rehearsal OR supersedes_id IS NULL",
            name="ck_regulatory_packages_rehearsal_supersedes_nothing",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_packages_id_org"),
        Index(
            "ix_regulatory_packages_org_bank_reporting_date",
            "organization_id",
            "bank_id",
            "reporting_date",
        ),
        Index(
            "ix_regulatory_packages_org_bank_status",
            "organization_id",
            "bank_id",
            "status",
        ),
        # Solo and consolidated packages keep independent current versions for
        # the same return and reporting date — and so does a REHEARSAL, which is
        # a third chain (D-029 / D-068). Without ``is_rehearsal`` in this key, a
        # bank that dry-ran a fiscal year could not then file it: the real
        # package would collide with the rehearsal on a duplicate key.
        Index(
            "uq_regulatory_packages_current",
            "organization_id",
            "bank_id",
            "return_code",
            "reporting_date",
            "basis",
            "is_rehearsal",
            unique=True,
            postgresql_where=sql_text("status != 'superseded'"),
            sqlite_where=sql_text("status != 'superseded'"),
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    return_family: Mapped[str] = mapped_column(String(20), nullable=False)
    return_code: Mapped[str] = mapped_column(String(40), nullable=False)
    reporting_date: Mapped[date] = mapped_column(Date, nullable=False)
    frequency: Mapped[str] = mapped_column(String(12), nullable=False)
    basis: Mapped[str] = mapped_column(
        String(12), default="solo", server_default=sql_text("'solo'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: A DRY RUN, not a filing (D-029 / D-068). Stated on the row rather than
    #: derived from the snapshot's nested cycle block, so a surface that forgets
    #: to exclude rehearsals is a visible bug instead of an invisible one.
    is_rehearsal: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    source_runs: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Did the MACHINE checks pass? This is the attribute that replaced the
    #: ``validated`` status as the authority on machine validation
    #: (``docs/filing_workflow_redesign.md`` §3.1). Validation is a rules-engine
    #: result, not a person and not a step somebody takes: it GATES ENTRY to the
    #: review chain. The status ``validated`` survives as a projection of
    #: "checks passed and nobody has been asked to review it yet", so existing
    #: sealed rows keep the value they were sealed with — but every gate reads
    #: this column, and no gate reads that status.
    checks_passed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    #: Where the review chain is. ``NULL`` means no chain has been pinned — a
    #: package still in preparation, or one that reached a terminal state before
    #: the chain existed. ``pending_approval`` / ``approved`` are projections of
    #: this, not the other way round.
    current_stage_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The review round. A send-back increments it, so round 2 is legible as
    #: round 2 rather than as round 1 with different content.
    workflow_round: Mapped[int] = mapped_column(
        Integer, default=1, server_default=sql_text("1"), nullable=False
    )
    generated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SHA-256 over the canonical-JSON snapshot, sealed at generation; exports
    # verify against it so a drifted snapshot can never silently render.
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # --- attestation (docs/attestation_esignature.md §4) --------------------
    # Content fingerprint with volatile metadata excluded — unlike
    # snapshot_sha256 (which embeds metadata.generated_at and therefore seals a
    # VERSION), this is stable across regenerations of identical figures and is
    # what a signature binds to.
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Master-data provenance for packs that bind no engine run (the LRT-*
    # corporate family, source_runs == []).
    register_state_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attestation_state: Mapped[str] = mapped_column(
        String(24), default="unsigned", server_default="unsigned", nullable=False
    )
    # Incremented on void; signatures carry the cycle they belong to so the
    # signature table stays strictly append-only.
    attestation_cycle: Mapped[int] = mapped_column(
        Integer, default=1, server_default=sql_text("1"), nullable=False
    )
    # The digest frozen at preparer certification. Every later signer must
    # match it exactly, and submission is refused if it drifts.
    certification_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fully_certified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # ORASS-style submission revision ("1.0", "1.1", ...) stamped at submit
    # time; the minor number counts granted resubmissions in the version chain.
    submission_revision: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Supervisor comments captured from the regulator's reject/decline response
    # (ORASS "View Comments" parity); None until such a decision is recorded.
    regulator_comments: Mapped[str | None] = mapped_column(Text, nullable=True)


class RegulatoryPackageArtifact(UuidV7PrimaryKeyMixin, Base):
    """One exported file (xlsx/csv/pdf) minted from a package snapshot."""

    __tablename__ = "regulatory_package_artifacts"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ({_values(ARTIFACT_KINDS)})",
            name="ck_regulatory_package_artifacts_kind",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_package_artifacts_id_org"),
        # One artifact per kind per package at the schema level; the exporter
        # upserts in place, so a duplicate row is always a bug.
        UniqueConstraint(
            "organization_id",
            "package_id",
            "kind",
            name="uq_regulatory_package_artifacts_pkg_kind",
        ),
        Index(
            "ix_regulatory_package_artifacts_org_package",
            "organization_id",
            "package_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    object_path: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatoryPackageAttachment(UuidV7PrimaryKeyMixin, Base):
    """A document filed WITH a return — a Board resolution, minutes, a letter.

    Generic across families, not ICAAP-only: a signing policy has always been
    able to name ``required_attachments``, and until now nothing could satisfy
    it, so the requirement silently passed. The row is UNALTERABLE — the same
    tier as ``regulatory_artifact_versions`` — because "which document did we
    file" must survive anybody's later tidying; withdrawing one is a separate
    event (:class:`RegulatoryPackageAttachmentWithdrawal`), never an edit.

    The object itself is not copied when it comes from an ICAAP cycle: the
    cycle's upload table is unalterable too, so the bytes at ``object_path``
    cannot change, and ``sha256`` proves it.
    """

    __tablename__ = "regulatory_package_attachments"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="ck_regulatory_package_attachments_size"),
        CheckConstraint("length(sha256) = 64", name="ck_regulatory_package_attachments_sha"),
        CheckConstraint("storage_tier = 'outputs'", name="ck_regulatory_package_attachments_tier"),
        CheckConstraint(
            f"source IN ({_values(PACKAGE_ATTACHMENT_SOURCES)})",
            name="ck_regulatory_package_attachments_source",
        ),
        CheckConstraint(
            f"gate IN ({_values(PACKAGE_ATTACHMENT_GATES)})",
            name="ck_regulatory_package_attachments_gate",
        ),
        # A cycle-sourced row must name the cycle upload it came from, and an
        # uploaded one must not pretend to.
        CheckConstraint(
            "(source = 'icaap_cycle') = (source_attachment_id IS NOT NULL)",
            name="ck_regulatory_package_attachments_origin",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_package_attachments_id_org"),
        Index(
            "ix_regulatory_package_attachments_org_package_kind",
            "organization_id",
            "package_id",
            "kind",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: The package version this document was attached to, carried so a
    #: superseding regeneration's manifest cannot be confused with this one's.
    package_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Sniffed from the bytes, never taken from the client's Content-Type.
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    object_path: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ``icaap_attachments.id`` for a cycle-sourced document. A VALUE COPY with
    #: deliberately no foreign key: the filing manifest must not become
    #: undeletable-cycle pressure, and the sha256 is the real binding.
    source_attachment_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    gate: Mapped[str] = mapped_column(String(12), nullable=False)
    #: ``metadata`` is reserved by SQLAlchemy's declarative base. Holds the
    #: kind's own required facts — a Board resolution's date and reference.
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    attached_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatoryPackageAttachmentWithdrawal(UuidV7PrimaryKeyMixin, Base):
    """Withdrawing a filed document is an event, not an edit to the upload."""

    __tablename__ = "regulatory_package_attachment_withdrawals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attachment_id", "organization_id"],
            [
                "regulatory_package_attachments.id",
                "regulatory_package_attachments.organization_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "attachment_id", name="uq_regulatory_package_attachment_withdrawals_attachment"
        ),
        UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_package_attachment_withdrawals_id_org"
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attachment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    withdrawn_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatoryPackageApproval(UuidV7PrimaryKeyMixin, Base):
    """Append-only maker-checker trail (checker != maker enforced in service)."""

    __tablename__ = "regulatory_package_approvals"
    __table_args__ = (
        CheckConstraint(
            f"action IN ({_values(APPROVAL_ACTIONS)})",
            name="ck_regulatory_package_approvals_action",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_package_approvals_id_org"),
        Index(
            "ix_regulatory_package_approvals_org_package",
            "organization_id",
            "package_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(12), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatorySubmissionEvent(UuidV7PrimaryKeyMixin, Base):
    """Append-only channel interaction log for a package."""

    __tablename__ = "regulatory_submission_events"
    __table_args__ = (
        CheckConstraint(
            f"channel IN ({_values(SUBMISSION_CHANNELS)})",
            name="ck_regulatory_submission_events_channel",
        ),
        CheckConstraint(
            f"event IN ({_values(SUBMISSION_EVENTS)})",
            name="ck_regulatory_submission_events_event",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_submission_events_id_org"),
        Index(
            "ix_regulatory_submission_events_org_package",
            "organization_id",
            "package_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatoryResubmissionRequest(UuidV7PrimaryKeyMixin, Base):
    """One post-submission correction request (ORASS "Request Resubmission").

    A submitted/acknowledged return is immutable at the regulator; corrections
    require this formal request with a reason, which the regulator grants or
    denies. A granted request authorizes exactly one superseding regeneration
    (``consumed_by_package_id`` links the version it produced), which carries
    the next submission revision (1.0 -> 1.1).
    """

    __tablename__ = "regulatory_resubmission_requests"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(RESUBMISSION_STATUSES)})",
            name="ck_regulatory_resubmission_requests_status",
        ),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_resubmission_requests_id_org"
        ),
        Index(
            "ix_regulatory_resubmission_requests_org_package",
            "organization_id",
            "package_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="requested")
    requested_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    # The superseding package a granted request produced; a granted request
    # with this still NULL is the one-shot authorization the generator checks.
    consumed_by_package_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RegulatoryChannelConfig(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """Per-bank submission-channel settings; credential material is write-only."""

    __tablename__ = "regulatory_channel_configs"
    __table_args__ = (
        CheckConstraint(
            f"channel IN ({_values(SUBMISSION_CHANNELS)})",
            name="ck_regulatory_channel_configs_channel",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_channel_configs_id_org"),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "channel",
            name="uq_regulatory_channel_configs_scope",
        ),
        Index(
            "ix_regulatory_channel_configs_org_bank",
            "organization_id",
            "bank_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RegulatoryReportingSettings(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """Per-bank reporting configuration — currently the deadline-override map.

    ``deadline_overrides`` is a ``{return_code: day_of_month}`` JSON map that
    lets Bank-IT correct the registry's placeholder monthly deadlines (e.g. the
    BSD2 day-14 and FX-NOP day-10 placeholders) once ORASS onboarding confirms
    the real day. One row per (org, bank). RLS-forced like every tenant table.
    """

    __tablename__ = "regulatory_reporting_settings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_regulatory_reporting_settings_id_org"),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            name="uq_regulatory_reporting_settings_scope",
        ),
        Index(
            "ix_regulatory_reporting_settings_org_bank",
            "organization_id",
            "bank_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    deadline_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
