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
)
RETURN_FREQUENCIES = ("weekly", "monthly", "quarterly", "semiannual", "annual", "daily")
RETURN_BASES = ("solo", "consolidated")
# "xlsx" is the OFFICIAL (sealed, values-only) Excel export — the audit twin of
# the submission PDF; "xlsx_working" (2026-08-16, migration 202608160015) is the
# ALM/Finance WORKING copy of an official BoG BSD form with the template's live
# formulas — never a filing artifact, never signed. See bog_forms/render.py.
ARTIFACT_KINDS = ("xlsx", "csv", "pdf", "xlsx_working")
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
        # the same return and reporting date.
        Index(
            "uq_regulatory_packages_current",
            "organization_id",
            "bank_id",
            "return_code",
            "reporting_date",
            "basis",
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
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    source_runs: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
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
