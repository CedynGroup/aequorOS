"""ICAAP workspace (M1): cycles, sections, versions, data blocks, bindings, attachments.

The workspace is mutable working state; the filing is not. That split is in the
schema rather than in a service, so it holds even against a direct SQL write:

* ``icaap_cycles`` is SEALED once frozen — after the freeze, only the lifecycle
  status and its write-once timestamps may change, and the package link can
  never be rewritten;
* section versions, block bindings, attachments and withdrawals are
  UNALTERABLE — UPDATE is blocked by trigger, DELETE stays reachable so a cycle
  cascade still works.

Blocks are held at CYCLE level, not per section, so the executive summary and
the capital-planning section quote one bound capital position rather than two
that can disagree. Bindings are append-only: "what this cycle said in March" is
answerable forever.

Every table carries ``organization_id`` and ``bank_id`` and is ENABLE+FORCE RLS
on the tenant. Columns are ``sa.JSON``, never JSONB (D-014): the hermetic suite
and the Playwright stack build this schema with ``create_all`` on SQLite, and no
JSON-path query is needed.
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

ICAAP_CYCLE_KINDS: tuple[str, ...] = ("annual", "material_change", "regulator_request", "rehearsal")
ICAAP_CYCLE_STATUSES: tuple[str, ...] = (
    "draft",
    "in_review",
    "frozen",
    "board_approved",
    "submitted",
    "acknowledged",
    "returned",
    "superseded",
    "archived",
)
#: Statuses from which the governed-row guard treats the row as sealed.
ICAAP_SEALED_STATUSES: tuple[str, ...] = (
    "frozen",
    "board_approved",
    "submitted",
    "acknowledged",
    "superseded",
)
ICAAP_SEAL_NEXT_STATES: tuple[str, ...] = (
    "board_approved",
    "submitted",
    "acknowledged",
    "superseded",
    "returned",
)
ICAAP_BASES: tuple[str, ...] = ("solo", "consolidated")
ICAAP_DUE_DATE_BASES: tuple[str, ...] = ("framework", "bank_set", "regulator_set", "timely")
ICAAP_BINDING_SOURCE_KINDS: tuple[str, ...] = (
    "run",
    "package",
    "signoff",
    "plan",
    "register",
    "snapshot",
    "computed",
    "manual",
)

EMPTY_DOC: dict[str, Any] = {"type": "doc", "content": []}
_EMPTY_DOC_SQL = '\'{"type":"doc","content":[]}\''
#: One open cycle per bank x kind x as-of x basis. Superseded and archived rows
#: fall out of the index so a retired rehearsal never blocks a real one.
#:
#: ``submitted`` and ``acknowledged`` fall out too (P3, migration 202609190058).
#: A ¶74 revision of a cycle that is already with the regulator has to be
#: creatable while the filed cycle keeps saying, truthfully, that it is filed.
#: The filed cycle becomes ``superseded`` only when the revision's freeze
#: supersedes its package — never in advance, and never by re-labelling a
#: filing as withdrawn so that an index would admit its successor.
_OPEN_CYCLE = "status NOT IN ('submitted', 'acknowledged', 'superseded', 'archived')"


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


_CYCLE_FK_COLUMNS = ["cycle_id", "organization_id", "bank_id"]
_CYCLE_FK_TARGETS = ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"]


class IcaapCycle(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One ICAAP for a bank: a fiscal year, an as-of date and a reporting basis."""

    __tablename__ = "icaap_cycles"
    __table_args__ = (
        CheckConstraint(
            f"cycle_kind IN ({_values(ICAAP_CYCLE_KINDS)})", name="ck_icaap_cycles_kind"
        ),
        CheckConstraint(
            f"status IN ({_values(ICAAP_CYCLE_STATUSES)})", name="ck_icaap_cycles_status"
        ),
        CheckConstraint(f"basis IN ({_values(ICAAP_BASES)})", name="ck_icaap_cycles_basis"),
        CheckConstraint(
            f"due_date_basis IN ({_values(ICAAP_DUE_DATE_BASES)})",
            name="ck_icaap_cycles_due_date_basis",
        ),
        CheckConstraint("round >= 1", name="ck_icaap_cycles_round"),
        CheckConstraint("fiscal_year BETWEEN 1990 AND 2200", name="ck_icaap_cycles_fiscal_year"),
        CheckConstraint(
            "due_date_basis = 'timely' OR due_date IS NOT NULL", name="ck_icaap_cycles_due_date"
        ),
        # A rehearsal DOES freeze, sign and record a submission (D-029, ruled by
        # D-068): that dry run is the only way a bank can exercise the riskiest
        # steps of the regime while the regulator's text is pending, and the two
        # CHECKs that used to forbid it here bought no safety that the package's
        # own rehearsal constraints do not buy. Migration 202609190062 moved the
        # guarantee to where the danger is — see ``RegulatoryPackage``.
        # A frozen cycle without its package would be a seal over nothing; this
        # also forces freeze to set both in one statement, which is what keeps
        # the SEALED guard satisfiable.
        CheckConstraint(
            "status NOT IN ('frozen', 'board_approved', 'submitted', 'acknowledged') "
            "OR package_id IS NOT NULL",
            name="ck_icaap_cycles_sealed_has_package",
        ),
        CheckConstraint(
            "cycle_kind <> 'material_change' OR change_trigger IS NOT NULL",
            name="ck_icaap_cycles_change_trigger",
        ),
        CheckConstraint(
            "cycle_kind <> 'regulator_request' OR regulator_request_ref IS NOT NULL",
            name="ck_icaap_cycles_regulator_request_ref",
        ),
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
        ),
        ForeignKeyConstraint(
            ["supersedes_cycle_id", "organization_id", "bank_id"], _CYCLE_FK_TARGETS
        ),
        ForeignKeyConstraint(
            ["rebased_from_cycle_id", "organization_id", "bank_id"], _CYCLE_FK_TARGETS
        ),
        UniqueConstraint("id", "organization_id", name="uq_icaap_cycles_id_org"),
        UniqueConstraint("id", "organization_id", "bank_id", name="uq_icaap_cycles_id_org_bank"),
        Index(
            "uq_icaap_cycles_open",
            "organization_id",
            "bank_id",
            "cycle_kind",
            "as_of_date",
            "basis",
            unique=True,
            postgresql_where=sql_text(_OPEN_CYCLE),
            sqlite_where=sql_text(_OPEN_CYCLE),
        ),
        Index("ix_icaap_cycles_org_bank_fy", "organization_id", "bank_id", "fiscal_year"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    cycle_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    basis: Mapped[str] = mapped_column(String(12), nullable=False)
    subsidiaries_declared: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    framework_code: Mapped[str] = mapped_column(String(60), nullable=False)
    framework_version: Mapped[str] = mapped_column(String(40), nullable=False)
    framework_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", server_default=sql_text("'draft'"), nullable=False
    )
    current_stage_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    round: Mapped[int] = mapped_column(
        Integer, default=1, server_default=sql_text("1"), nullable=False
    )
    package_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    change_trigger: Mapped[str | None] = mapped_column(String(40), nullable=True)
    change_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    regulator_request_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    supersedes_cycle_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    rebased_from_cycle_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    board_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class IcaapSection(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """The working copy of one framework section, plus its checklist state."""

    __tablename__ = "icaap_sections"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("cycle_id", "section_key", name="uq_icaap_sections_cycle_key"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_sections_id_org"),
        CheckConstraint("working_rev >= 0", name="ck_icaap_sections_working_rev"),
        CheckConstraint("position BETWEEN 1 AND 99", name="ck_icaap_sections_position"),
        CheckConstraint(
            "committed_version_no IS NULL OR committed_version_no >= 1",
            name="ck_icaap_sections_committed_version_no",
        ),
        Index("ix_icaap_sections_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    section_key: Mapped[str] = mapped_column(String(60), nullable=False)
    letter: Mapped[str] = mapped_column(String(2), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    working_doc: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: dict(EMPTY_DOC),
        server_default=sql_text(_EMPTY_DOC_SQL),
        nullable=False,
    )
    #: Optimistic concurrency. A save carries the rev it was based on; two tabs
    #: editing the same section produce a conflict, never a silent overwrite.
    working_rev: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    working_updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    working_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: A number rather than a foreign key to the versions table: the pair
    #: (section_id, version_no) is unique, and a real FK would be circular.
    committed_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    committed_from_rev: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checklist_state: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    carried_from: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class IcaapSectionVersion(UuidV7PrimaryKeyMixin, Base):
    """A committed section: immutable text with the figures it quoted."""

    __tablename__ = "icaap_section_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["section_id", "organization_id"],
            ["icaap_sections.id", "icaap_sections.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("section_id", "version_no", name="uq_icaap_section_versions_no"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_section_versions_id_org"),
        CheckConstraint("version_no >= 1", name="ck_icaap_section_versions_no"),
        CheckConstraint("round >= 1", name="ck_icaap_section_versions_round"),
        CheckConstraint("length(doc_sha256) = 64", name="ck_icaap_section_versions_sha"),
        Index("ix_icaap_section_versions_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    section_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    section_key: Mapped[str] = mapped_column(String(60), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Which review round committed this text (P3 maker-checker).
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    source_rev: Mapped[int] = mapped_column(Integer, nullable=False)
    editor_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    plain_text: Mapped[str] = mapped_column(Text, nullable=False)
    doc_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_refs: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    block_refs: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    commit_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Reserved for P4: which AI suggestions this text descends from.
    ai_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    committed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IcaapDataBlock(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """A figure the cycle cites, held once and quoted from any section."""

    __tablename__ = "icaap_data_blocks"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("cycle_id", "block_key", name="uq_icaap_data_blocks_cycle_key"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_data_blocks_id_org"),
        CheckConstraint(
            "(pin_reason IS NULL) = (pinned_binding_seq IS NULL)", name="ck_icaap_data_blocks_pin"
        ),
        Index("ix_icaap_data_blocks_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: Validated against the domain catalogue, deliberately without a CHECK: the
    #: catalogue grows every phase and a CHECK would need a migration each time.
    block_type: Mapped[str] = mapped_column(String(40), nullable=False)
    block_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    #: Pinning is a recorded judgement: "I have seen the newer figures and this
    #: report keeps the earlier ones, because ...". Never a silent staleness.
    pin_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinned_binding_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pinned_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class IcaapAttachment(UuidV7PrimaryKeyMixin, Base):
    """Evidence uploaded against the cycle: a report, a resolution, minutes."""

    __tablename__ = "icaap_attachments"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_attachments_id_org"),
        CheckConstraint("byte_size > 0", name="ck_icaap_attachments_size"),
        CheckConstraint("length(sha256) = 64", name="ck_icaap_attachments_sha"),
        CheckConstraint("storage_tier = 'outputs'", name="ck_icaap_attachments_tier"),
        Index("ix_icaap_attachments_org_cycle_kind", "organization_id", "cycle_id", "kind"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
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
    section_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    block_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    #: ``metadata`` is reserved by SQLAlchemy's declarative base.
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    uploaded_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IcaapBlockBinding(UuidV7PrimaryKeyMixin, Base):
    """One resolution of a block: what it bound, and the figures it produced."""

    __tablename__ = "icaap_block_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["block_id", "organization_id"],
            ["icaap_data_blocks.id", "icaap_data_blocks.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["evidence_attachment_id", "organization_id"],
            ["icaap_attachments.id", "icaap_attachments.organization_id"],
        ),
        UniqueConstraint("block_id", "seq", name="uq_icaap_block_bindings_seq"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_block_bindings_id_org"),
        CheckConstraint(
            f"source_kind IN ({_values(ICAAP_BINDING_SOURCE_KINDS)})",
            name="ck_icaap_block_bindings_kind",
        ),
        CheckConstraint("seq >= 1", name="ck_icaap_block_bindings_seq"),
        CheckConstraint("length(payload_sha256) = 64", name="ck_icaap_block_bindings_sha"),
        Index("ix_icaap_block_bindings_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    block_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    resolver: Mapped[str] = mapped_column(String(40), nullable=False)
    resolver_version: Mapped[str] = mapped_column(String(20), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The pin: run id + input hash, package digest, sign-off version, plan
    #: version or register digest — enough to prove what the figure came from.
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_key: Mapped[str] = mapped_column(String(300), nullable=False)
    source_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_run_ids: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_attachment_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    bound_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IcaapAttachmentWithdrawal(UuidV7PrimaryKeyMixin, Base):
    """Withdrawing evidence is an event, not an edit to the upload."""

    __tablename__ = "icaap_attachment_withdrawals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attachment_id", "organization_id"],
            ["icaap_attachments.id", "icaap_attachments.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        UniqueConstraint("attachment_id", name="uq_icaap_attachment_withdrawals_attachment"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_attachment_withdrawals_id_org"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attachment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    withdrawn_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


# ---------------------------------------------------------------------------
# P3 (M3): the review chain, its decisions, and the ¶82 disclosure
#
# The workspace tables above answer "what does this ICAAP say". These answer
# "who agreed to it, in what order, and on which exact text" — which is the
# half a supervisor asks about. They are therefore written once and never
# edited: a stage chain is UNALTERABLE from the moment a cycle is submitted for
# review, a decision is UNALTERABLE from the moment it is taken, and a workflow
# template or a disclosure is SEALED the moment it is approved.
# ---------------------------------------------------------------------------

ICAAP_WORKFLOW_TEMPLATE_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "superseded",
)
#: Statuses at which the governed-row guard treats a template as authoritative.
ICAAP_WORKFLOW_TEMPLATE_SEALED_STATUSES: tuple[str, ...] = ("approved", "superseded")
ICAAP_STAGE_DECISION_KINDS: tuple[str, ...] = ("prepare", "review", "approve", "attest")
ICAAP_STAGE_SOURCES: tuple[str, ...] = ("framework_default", "bank_template")
ICAAP_STAGE_DECISIONS: tuple[str, ...] = (
    "submitted",
    "reviewed",
    "approved",
    "returned",
    "frozen",
    "attested",
    # The Board evidenced its approval by resolution rather than by signing the
    # PDF (D-043: the Board slot is built but off by default). The decision is
    # recorded all the same, against the filed resolution attachment.
    "attested_by_resolution",
)
#: The decisions that ADVANCE the chain. At most one may exist per
#: (cycle, round, stage) — the partial unique index is what makes two
#: simultaneous approvals of the same stage a database error rather than a race.
ICAAP_FORWARD_DECISIONS: tuple[str, ...] = ("submitted", "reviewed", "approved")
ICAAP_DISCLOSURE_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_approval",
    "approved",
    "published",
    "rejected",
    "superseded",
)
#: One live disclosure per cycle; a rejected or superseded one steps aside.
_LIVE_DISCLOSURE = "status NOT IN ('rejected', 'superseded')"
_ACTIVE_TEMPLATE = "status = 'approved'"
_FORWARD_DECISION = f"decision IN ({_values(ICAAP_FORWARD_DECISIONS)})"

_TEMPLATE_FK_TARGETS = [
    "icaap_workflow_templates.id",
    "icaap_workflow_templates.organization_id",
]
_PACKAGE_FK_TARGETS = ["regulatory_packages.id", "regulatory_packages.organization_id"]


class IcaapWorkflowTemplate(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """The bank's own review chain, proposed and approved by four eyes.

    A bank whose Board Risk Committee sits between the CRO and the CEO does not
    have the framework's default chain, and a platform that made it pretend
    otherwise would be recording the wrong governance. So the chain is data —
    but it is *governed* data: changing who must approve an ICAAP is itself an
    approval, and an approved template is sealed against edit. In-flight cycles
    are unaffected, because a cycle pins its chain into
    :class:`IcaapCycleStage` at its first submission.
    """

    __tablename__ = "icaap_workflow_templates"
    __table_args__ = (
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        ForeignKeyConstraint(["superseded_by_id", "organization_id"], _TEMPLATE_FK_TARGETS),
        UniqueConstraint("id", "organization_id", name="uq_icaap_workflow_templates_id_org"),
        UniqueConstraint(
            "organization_id", "bank_id", "version", name="uq_icaap_workflow_templates_version"
        ),
        CheckConstraint(
            f"status IN ({_values(ICAAP_WORKFLOW_TEMPLATE_STATUSES)})",
            name="ck_icaap_workflow_templates_status",
        ),
        CheckConstraint("version >= 1", name="ck_icaap_workflow_templates_version"),
        # Maker-checker at the DATABASE, not only in the service: whoever
        # proposed a chain may not be the one who approves it.
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> proposed_by",
            name="ck_icaap_workflow_templates_four_eyes",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'superseded') OR decided_at IS NOT NULL",
            name="ck_icaap_workflow_templates_decided",
        ),
        Index(
            "uq_icaap_workflow_templates_active",
            "organization_id",
            "bank_id",
            unique=True,
            postgresql_where=sql_text(_ACTIVE_TEMPLATE),
            sqlite_where=sql_text(_ACTIVE_TEMPLATE),
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", server_default=sql_text("'draft'"), nullable=False
    )
    #: ``[{"seq": 1, "stage_key": "preparation", "title": …,
    #:    "decision_kind": "prepare", "officer_titles": [...],
    #:    "freeze_on_approve": false}]`` — validated by
    #: ``app/domain/icaap/workflow.validate_stages`` before it is stored.
    stages: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    #: The framework defaults this chain was derived from, for provenance.
    framework_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    framework_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class IcaapCycleStage(UuidV7PrimaryKeyMixin, Base):
    """The review chain AS PINNED for one cycle. Never altered afterwards.

    Pinning matters because a template can be re-approved mid-cycle. If the
    chain were read live, a cycle could be approved under one governance and
    filed under another, and the report would not be able to say which. So the
    chain in force is copied here at the cycle's first submission and is then
    what every later decision, and the frozen snapshot, refer to.
    """

    __tablename__ = "icaap_cycle_stages"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(["template_id", "organization_id"], _TEMPLATE_FK_TARGETS),
        UniqueConstraint("cycle_id", "seq", name="uq_icaap_cycle_stages_seq"),
        UniqueConstraint("cycle_id", "stage_key", name="uq_icaap_cycle_stages_key"),
        UniqueConstraint("id", "organization_id", name="uq_icaap_cycle_stages_id_org"),
        CheckConstraint(
            f"decision_kind IN ({_values(ICAAP_STAGE_DECISION_KINDS)})",
            name="ck_icaap_cycle_stages_decision_kind",
        ),
        CheckConstraint("seq BETWEEN 1 AND 20", name="ck_icaap_cycle_stages_seq"),
        CheckConstraint(
            f"source IN ({_values(ICAAP_STAGE_SOURCES)})", name="ck_icaap_cycle_stages_source"
        ),
        # A stage either came from the framework defaults or from a named bank
        # template; "from a template" with no template is an untraceable chain.
        CheckConstraint(
            "(source = 'bank_template') = (template_id IS NOT NULL)",
            name="ck_icaap_cycle_stages_template",
        ),
        Index("ix_icaap_cycle_stages_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_key: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    decision_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    #: The job titles that may take this stage's decision; empty = any officer
    #: with the authority. Checked against the signer's recorded title.
    officer_titles: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    #: The one stage whose approval makes the cycle ready to freeze.
    freeze_on_approve: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    template_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IcaapStageDecision(UuidV7PrimaryKeyMixin, Base):
    """One officer's decision, bound to the exact text it was taken on.

    ``review_digest`` is the point of the table: a reviewer approves a
    particular set of committed sections, requirement states and bound figures,
    and if any of them changes the approval no longer applies. Storing the
    digest with the decision is what lets the platform say that, instead of
    hoping nobody edited the report after the CRO read it.
    """

    __tablename__ = "icaap_stage_decisions"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(["package_id", "organization_id"], _PACKAGE_FK_TARGETS),
        UniqueConstraint("id", "organization_id", name="uq_icaap_stage_decisions_id_org"),
        CheckConstraint(
            f"decision IN ({_values(ICAAP_STAGE_DECISIONS)})",
            name="ck_icaap_stage_decisions_decision",
        ),
        CheckConstraint("round >= 1", name="ck_icaap_stage_decisions_round"),
        CheckConstraint("stage_seq BETWEEN 1 AND 20", name="ck_icaap_stage_decisions_stage_seq"),
        CheckConstraint("length(review_digest) = 64", name="ck_icaap_stage_decisions_digest"),
        # A return names an EARLIER stage and says why. Without both it is an
        # unexplained rejection, which the preparer cannot act on.
        CheckConstraint(
            "decision <> 'returned' OR (return_to_seq IS NOT NULL AND return_to_seq >= 1 "
            "AND return_to_seq < stage_seq AND comment IS NOT NULL)",
            name="ck_icaap_stage_decisions_return",
        ),
        Index(
            "uq_icaap_stage_decisions_forward",
            "cycle_id",
            "round",
            "stage_seq",
            unique=True,
            postgresql_where=sql_text(_FORWARD_DECISION),
            sqlite_where=sql_text(_FORWARD_DECISION),
        ),
        Index("ix_icaap_stage_decisions_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    stage_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_key: Mapped[str] = mapped_column(String(60), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    return_to_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    package_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    #: The attestation signature that stood for this decision, where one did
    #: (the Board's ``attested``). A value copy, like ``decided_by_name``: the
    #: decision must survive a voided attestation cycle unchanged.
    signature_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    #: The filed Board resolution, for ``attested_by_resolution``.
    package_attachment_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: The name and title as they stood at the decision. A later rename must not
    #: rewrite who approved an ICAAP (P0 fix-round item 16).
    decided_by_name: Mapped[str] = mapped_column(String(200), nullable=False)
    officer_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The authorization evidence: permission, surface, binding ids and the
    #: condition checks (including maker-checker) the evaluator ran.
    authority: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IcaapDisclosure(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """What the bank chose to publish of its ICAAP, and who approved that.

    ¶82 asks for publication; it does not say what. So the bank selects, every
    section defaults to NOT public, supervisory add-ons can never be published
    at all, and the selection is approved by someone other than whoever made
    it. Once approved the row is sealed — the published set is a statement the
    bank made on a date, not a page that can be quietly re-edited.
    """

    __tablename__ = "icaap_disclosures"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(["source_package_id", "organization_id"], _PACKAGE_FK_TARGETS),
        ForeignKeyConstraint(["package_id", "organization_id"], _PACKAGE_FK_TARGETS),
        UniqueConstraint("id", "organization_id", name="uq_icaap_disclosures_id_org"),
        CheckConstraint(
            f"status IN ({_values(ICAAP_DISCLOSURE_STATUSES)})",
            name="ck_icaap_disclosures_status",
        ),
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> proposed_by",
            name="ck_icaap_disclosures_four_eyes",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'published') OR package_id IS NOT NULL",
            name="ck_icaap_disclosures_has_package",
        ),
        Index(
            "uq_icaap_disclosures_live",
            "cycle_id",
            unique=True,
            postgresql_where=sql_text(_LIVE_DISCLOSURE),
            sqlite_where=sql_text(_LIVE_DISCLOSURE),
        ),
        Index("ix_icaap_disclosures_org_cycle", "organization_id", "cycle_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: The sealed ICAAP package the disclosure is drawn from. Never live data:
    #: a disclosure quotes what was filed, or it is not the same ICAAP.
    source_package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", server_default=sql_text("'draft'"), nullable=False
    )
    selected_section_keys: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    #: What was removed from a selected section and why — the audit answer to
    #: "did the published version omit something material?".
    withheld: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    proposed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The ``ICAAP-DISCLOSURE`` package minted at approval.
    package_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    published_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "EMPTY_DOC",
    "ICAAP_BASES",
    "ICAAP_BINDING_SOURCE_KINDS",
    "ICAAP_CYCLE_KINDS",
    "ICAAP_CYCLE_STATUSES",
    "ICAAP_DISCLOSURE_STATUSES",
    "ICAAP_DUE_DATE_BASES",
    "ICAAP_FORWARD_DECISIONS",
    "ICAAP_SEALED_STATUSES",
    "ICAAP_SEAL_NEXT_STATES",
    "ICAAP_STAGE_DECISIONS",
    "ICAAP_STAGE_DECISION_KINDS",
    "ICAAP_STAGE_SOURCES",
    "ICAAP_WORKFLOW_TEMPLATE_SEALED_STATUSES",
    "ICAAP_WORKFLOW_TEMPLATE_STATUSES",
    "IcaapAttachment",
    "IcaapAttachmentWithdrawal",
    "IcaapBlockBinding",
    "IcaapCycle",
    "IcaapCycleStage",
    "IcaapDataBlock",
    "IcaapDisclosure",
    "IcaapSection",
    "IcaapSectionVersion",
    "IcaapStageDecision",
    "IcaapWorkflowTemplate",
]
