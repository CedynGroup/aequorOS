"""The BI commentary draft row: one AI request about one institution and date.

Named for the FEATURE (BI commentary) and TABLED in the AI plane
(``ai_commentary_drafts``, beside ``ai_commentary_settings``), because those are
two different questions and they have two different answers:

* the surface is BI — a reader on a BI pack asks for commentary about the
  figures the BI query path just served them;
* the row is AI EGRESS EVIDENCE — exactly which minimised, pseudonymised payload
  left the process, under which consent version, which deployment approval,
  which prompt and which model, and what came back. Its sibling is the consent
  row that gates it, not a mart.

That split is enforced, not stylistic. ``tests/architecture/test_bi_plane_boundary.py``
derives the set of tables BI may write from the ``bi_``-prefixed tables of
``Base.metadata`` and separately asserts that set equals what ``app.models.bi``
declares; a ``bi_``-named table declared anywhere else breaks the derivation. So
the table is ``ai_*``, and the consequence is deliberate: **nothing under
``app/services/bi`` may write this row.** The writes live in
``app/jobs/bi_commentary.py``, exactly as ``app/jobs/bi_export.py`` owns the
``audit_events`` write for the same reason.

Lifecycle, mirroring ``icaap_ai_suggestions`` (M4b): inserted ``queued``, moved
to ``running`` by the AI-lane handler, then ONE update to a terminal status
carrying every result column. The terminal statuses are the reclaim guard as
well as the read contract — a reclaimed job that finds a terminal row returns
without sending a second request.

``payload`` holds EXACTLY what was sent. ``fact_bindings`` holds the map from
each ``{{F:...}}`` id to the platform's own figure and never leaves the process;
it is what the read path resolves placeholders from, which is why a
descriptor-only tenant still SEES its numbers — descriptor-only governs egress,
not display. ``fallback_paragraphs`` is the deterministic commentary, composed
from the same fact sheet at request time: it is what the reader is served
whenever the model path produces nothing usable, and it is written before the
model is ever called so that outcome needs no second code path.
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
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now

#: Every status a request can hold. ``cancelled`` is the governance outcome —
#: the kill-switch, the tenant toggle or the queue expiry withdrew permission
#: before the call, so nothing was sent; it reads differently from ``failed``,
#: which is an incident.
AI_COMMENTARY_DRAFT_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "validated",
    "rejected_validation",
    "refused",
    "failed",
    "rate_limited",
    "cancelled",
)

#: Statuses after which the row is finished. A reclaimed job that finds one of
#: these returns immediately rather than spending a second request.
AI_COMMENTARY_DRAFT_TERMINAL_STATUSES: tuple[str, ...] = (
    "validated",
    "rejected_validation",
    "refused",
    "failed",
    "rate_limited",
    "cancelled",
)

#: Statuses that can never carry model output, whatever a caller passes: a
#: refusal produced no prose, a rate limit produced nothing at all, and a
#: cancelled request was never sent.
AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES: tuple[str, ...] = ("refused", "rate_limited", "cancelled")

#: What left the process. ``descriptor_only`` carries no figure at all; it is
#: the DEFAULT for a tenant that has made no choice (``gates.descriptor_only``).
AI_COMMENTARY_PAYLOAD_MODES: tuple[str, ...] = ("standard", "descriptor_only")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AiCommentaryDraft(Base):
    """One request for commentary on one institution's figures at one date."""

    __tablename__ = "ai_commentary_drafts"
    __table_args__ = (
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        UniqueConstraint("id", "organization_id", name="uq_ai_commentary_drafts_id_org"),
        CheckConstraint(
            f"status IN ({_values(AI_COMMENTARY_DRAFT_STATUSES)})",
            name="ck_ai_commentary_drafts_status",
        ),
        CheckConstraint(
            f"payload_mode IN ({_values(AI_COMMENTARY_PAYLOAD_MODES)})",
            name="ck_ai_commentary_drafts_mode",
        ),
        CheckConstraint("length(payload_sha256) = 64", name="ck_ai_commentary_drafts_payload_sha"),
        CheckConstraint("length(fact_sheet_hash) = 64", name="ck_ai_commentary_drafts_sheet_hash"),
        # The comparison is earlier than the reporting date, the same rule the
        # insights surface refuses on (``bi_insights_comparison_not_earlier``).
        CheckConstraint("compare_to < as_of", name="ck_ai_commentary_drafts_comparison"),
        CheckConstraint(
            "status IN ('queued', 'running') OR completed_at IS NOT NULL",
            name="ck_ai_commentary_drafts_completed",
        ),
        CheckConstraint(
            "status <> 'validated' OR output IS NOT NULL",
            name="ck_ai_commentary_drafts_validated_output",
        ),
        CheckConstraint(
            f"NOT (status IN ({_values(AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES)})) "
            "OR output IS NULL",
            name="ck_ai_commentary_drafts_no_output",
        ),
        CheckConstraint("fact_count >= 0", name="ck_ai_commentary_drafts_fact_count"),
        Index(
            "ix_ai_commentary_drafts_bank_as_of",
            "organization_id",
            "bank_id",
            "as_of",
            "created_at",
        ),
        Index(
            "ix_ai_commentary_drafts_requester",
            "organization_id",
            "requested_by",
            "created_at",
        ),
        Index("ix_ai_commentary_drafts_org_created", "organization_id", "created_at"),
        # One in-flight request per reader per institution and date: the
        # debounce's race guard as well as its policy.
        Index(
            "uq_ai_commentary_drafts_inflight",
            "organization_id",
            "bank_id",
            "as_of",
            "requested_by",
            unique=True,
            sqlite_where=sql_text("status IN ('queued', 'running')"),
            postgresql_where=sql_text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The reporting date the commentary is about, and the period it is measured
    #: against — the insights surface's own two dates.
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    compare_to: Mapped[date] = mapped_column(Date, nullable=False)
    catalogue_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="queued", server_default=sql_text("'queued'"), nullable=False
    )
    #: A human. Machine principals never reach a BI read, let alone this.
    requested_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    payload_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    #: EXACTLY what was sent: aggregated, pseudonymised, and figure-free unless
    #: the tenant opted out of descriptor-only.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``insights.digest.fact_sheet_hash`` of the sheet the payload was
    #: minimised from — value-based, so it moves when the FIGURES move and not
    #: when the live plane re-derives them. The read path compares it to a fresh
    #: sheet to report staleness.
    fact_sheet_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``{{F:id}}`` -> the platform's own figure and its measure. NEVER sent;
    #: this is what a placeholder resolves to at read time.
    fact_bindings: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    #: The ``{{E:key}}`` keys offered. Values are resolved server-side from the
    #: CURRENT registers, never stored here.
    entity_keys: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    #: The deterministic commentary, composed at request time from the same fact
    #: sheet. Served whenever the model path produces nothing usable — which is
    #: a product state, not an error state.
    fallback_paragraphs: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    fact_count: Mapped[int] = mapped_column(Integer, nullable=False)

    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_requested: Mapped[str] = mapped_column(String(80), nullable=False)
    effort: Mapped[str] = mapped_column(String(8), nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    fallbacks_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Gate evidence AS OF ENQUEUE: what the tenant had consented to and what
    #: the deployment was approved for when the request was made.
    consent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    deployment_approval_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)

    job_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    model_served: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fallback_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    refusal_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Stored for ``validated`` AND ``rejected_validation`` (audit and evals),
    #: served only when validated: ungrounded prose is never shown.
    #:
    #: ``none_as_null`` is load-bearing on both nullable JSON columns here, and it
    #: is not a style choice. SQLAlchemy's default persists a Python ``None`` on a
    #: JSON column as the JSON value ``null``, which is NOT SQL ``NULL`` — so the
    #: ``output IS NULL`` half of ``ck_ai_commentary_drafts_no_output`` and the
    #: ``output IS NOT NULL`` half of the validated check would both read the
    #: opposite of what the writer meant, and a refused row could carry prose.
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``[{code, where, span}]`` — codes and locations, never the text.
    validation_errors: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


#: The tenant tables this module owns, named explicitly BECAUSE the prefix does
#: not name them (audit A9-08).
#:
#: This table is ``ai_``-prefixed on purpose: the BI plane-boundary guard derives
#: the set of BI-WRITABLE tables from the ``bi_``-prefixed tables of
#: ``Base.metadata``, and this row is AI EGRESS EVIDENCE written by the ``ai``
#: lane, not a mart. Renaming it to ``bi_`` would make it writable by the BI
#: plane, which is the opposite of what the guard is for.
#:
#: The cost of that correct naming was that every structural check keyed on the
#: prefix silently stopped covering it — the Postgres parity suite among them, so
#: its RLS, its composite foreign key and its indexes were unverified against the
#: migrated schema. Any suite whose subject is "the BI plane's tables" must add
#: this tuple to its subject rather than widen its prefix.
BI_COMMENTARY_TABLES: tuple[str, ...] = ("ai_commentary_drafts",)

__all__ = [
    "AI_COMMENTARY_DRAFT_NO_OUTPUT_STATUSES",
    "AI_COMMENTARY_DRAFT_STATUSES",
    "AI_COMMENTARY_DRAFT_TERMINAL_STATUSES",
    "AI_COMMENTARY_PAYLOAD_MODES",
    "BI_COMMENTARY_TABLES",
    "AiCommentaryDraft",
]
