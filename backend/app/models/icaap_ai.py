"""ICAAP AI suggestions and their decisions (M4b).

Two tiers, and the difference matters:

* ``icaap_ai_suggestions`` is **SEALED**. It is inserted ``queued``, moves to
  ``running``, then takes ONE update to a terminal status carrying every result
  column — and after that the database refuses to change it. The row is the
  evidence of what was sent to an external service on a bank's behalf: exactly
  which pseudonymised fact sheet, under which consent version and which
  deployment approval, to which model, and what came back. Evidence that can be
  rewritten afterwards is not evidence.
* ``icaap_ai_suggestion_decisions`` is **UNALTERABLE** — UPDATE blocked, DELETE
  reachable only through the cycle cascade, the same tier as committed section
  versions. A human's decision to insert or discard AI text is corrected by a
  new decision, never by an edit. (Full IMMUTABLE would make a cycle
  undeletable, which is why the tier matches P1's versions rather than the
  audit log.)

``fact_sheet`` holds EXACTLY what was sent: pseudonymised, no names, no
identifiers, no people. ``fact_bindings`` holds the block/fact/seq map that was
deliberately NOT sent, and is what conversion and the staleness check read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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

from app.db.base import Base, utc_now

#: ``cancelled`` is the kill-switch/tenant-toggle/expiry outcome at run: the
#: request was never sent, and saying so is different from saying it failed.
ICAAP_AI_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "validated",
    "rejected_validation",
    "refused",
    "failed",
    "rate_limited",
    "cancelled",
)
#: Statuses at which the governed-row guard freezes the row.
ICAAP_AI_TERMINAL_STATUSES: tuple[str, ...] = (
    "validated",
    "rejected_validation",
    "refused",
    "failed",
    "rate_limited",
    "cancelled",
)
#: Statuses that can never carry model output, whatever a caller passes.
ICAAP_AI_NO_OUTPUT_STATUSES: tuple[str, ...] = ("refused", "rate_limited", "cancelled")
ICAAP_AI_FACT_SHEET_MODES: tuple[str, ...] = ("standard", "descriptor_only")
ICAAP_AI_DECISIONS: tuple[str, ...] = ("accepted", "rejected")

_CYCLE_FK_COLUMNS = ["cycle_id", "organization_id", "bank_id"]
_CYCLE_FK_TARGETS = ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"]


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


class IcaapAiSuggestion(Base):
    """One request to draft one section, and everything that came of it."""

    __tablename__ = "icaap_ai_suggestions"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["section_id", "organization_id"],
            ["icaap_sections.id", "icaap_sections.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "organization_id", name="uq_icaap_ai_suggestions_id_org"),
        CheckConstraint(
            _in_list("status", ICAAP_AI_STATUSES), name="ck_icaap_ai_suggestions_status"
        ),
        CheckConstraint(
            _in_list("fact_sheet_mode", ICAAP_AI_FACT_SHEET_MODES),
            name="ck_icaap_ai_suggestions_mode",
        ),
        CheckConstraint("length(fact_sheet_sha256) = 64", name="ck_icaap_ai_suggestions_sheet_sha"),
        CheckConstraint("cycle_round >= 1", name="ck_icaap_ai_suggestions_round"),
        CheckConstraint(
            "status IN ('queued', 'running') OR completed_at IS NOT NULL",
            name="ck_icaap_ai_suggestions_completed",
        ),
        CheckConstraint(
            "status <> 'validated' OR output IS NOT NULL",
            name="ck_icaap_ai_suggestions_validated_output",
        ),
        # A refusal produced no draft; a cancelled request was never sent. Neither
        # may carry output, whatever a caller passes.
        CheckConstraint(
            f"NOT ({_in_list('status', ICAAP_AI_NO_OUTPUT_STATUSES)}) OR output IS NULL",
            name="ck_icaap_ai_suggestions_no_output",
        ),
        Index(
            "ix_icaap_ai_suggestions_section",
            "organization_id",
            "cycle_id",
            "section_key",
            "created_at",
        ),
        Index(
            "ix_icaap_ai_suggestions_requester",
            "organization_id",
            "requested_by",
            "created_at",
        ),
        Index("ix_icaap_ai_suggestions_org_created", "organization_id", "created_at"),
        # One in-flight request per user per section: the debounce's race guard
        # as well as its policy.
        Index(
            "uq_icaap_ai_suggestions_inflight",
            "section_id",
            "requested_by",
            unique=True,
            sqlite_where=sql_text("status IN ('queued', 'running')"),
            postgresql_where=sql_text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    section_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    section_key: Mapped[str] = mapped_column(String(60), nullable=False)
    cycle_round: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="queued", server_default=sql_text("'queued'"), nullable=False
    )
    #: A human. Machine principals never request drafts.
    requested_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    fact_sheet_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    #: EXACTLY what was sent. Pseudonymised; no names, ids or people.
    fact_sheet: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fact_sheet_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: fid -> {block_id, fact_key, binding_seq}. NEVER sent; drives conversion
    #: to factRef nodes and the "figures changed since this draft" check.
    fact_bindings: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    entity_keys: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )

    framework_code: Mapped[str] = mapped_column(String(40), nullable=False)
    framework_version: Mapped[str] = mapped_column(String(40), nullable=False)
    framework_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    model_requested: Mapped[str] = mapped_column(String(80), nullable=False)
    effort: Mapped[str] = mapped_column(String(8), nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    fallbacks_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Gate evidence AS OF ENQUEUE — what the tenant had consented to and what
    #: the deployment was approved for at the moment the request was made.
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
    #: but SERVED only when validated. A draft that failed grounding is never
    #: shown — the user sees a status, never ungrounded prose.
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``[{code, where, span}]`` — codes and locations, never the text.
    validation_errors: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class IcaapAiSuggestionDecision(Base):
    """A human's decision about one suggestion. Written once, never edited."""

    __tablename__ = "icaap_ai_suggestion_decisions"
    __table_args__ = (
        ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["suggestion_id", "organization_id"],
            ["icaap_ai_suggestions.id", "icaap_ai_suggestions.organization_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("suggestion_id", name="uq_icaap_ai_suggestion_decisions_one"),
        CheckConstraint(
            _in_list("decision", ICAAP_AI_DECISIONS), name="ck_icaap_ai_decisions_decision"
        ),
        Index("ix_icaap_ai_decisions_org_cycle", "organization_id", "cycle_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    suggestion_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Which paragraphs were inserted. Empty for a rejection.
    paragraph_indexes: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    inserted_doc_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resulting_working_rev: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: True when the figures had moved since the draft was written and the user
    #: inserted it anyway. A supervisor can ask; the answer is recorded.
    acknowledged_stale: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


__all__ = [
    "ICAAP_AI_DECISIONS",
    "ICAAP_AI_FACT_SHEET_MODES",
    "ICAAP_AI_NO_OUTPUT_STATUSES",
    "ICAAP_AI_STATUSES",
    "ICAAP_AI_TERMINAL_STATUSES",
    "IcaapAiSuggestion",
    "IcaapAiSuggestionDecision",
]
