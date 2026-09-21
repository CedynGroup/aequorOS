"""The filing review chain: the bank's template, a package's pinned chain, its decisions.

Three tables, and each is the package-plane twin of an ICAAP one
(``models/icaap.py``, P3) because they run the same engine
(``domain/workflow/chain.py``):

* ``filing_workflow_templates`` — the bank's own chain, proposed and approved by
  four eyes, SEALED once approved. Changing who must approve a return is itself
  an approval.
* ``package_workflow_stages`` — the chain AS PINNED for one package, at the
  moment it was sent for approval. Pinned rather than read live, because a
  template can be re-approved mid-review and a return must be able to say which
  governance it was approved under.
* ``package_stage_decisions`` — one officer's decision, bound by
  ``review_digest`` to the exact package state it was taken on.

``returned`` here is the INTERNAL send-back and nothing else. The regulator's
own outcomes — ORASS ``rejected`` (returned for correction) and ``declined``
(final refusal) — stay package statuses. Conflating them would make the audit
trail unable to distinguish "BoG rejected this" from "our Approver sent it
back", which is precisely the distinction a supervisor asks about.
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

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin, utc_now

FILING_WORKFLOW_TEMPLATE_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "superseded",
)
FILING_STAGE_DECISION_KINDS: tuple[str, ...] = ("prepare", "review", "approve")
FILING_STAGE_SOURCES: tuple[str, ...] = ("platform_default", "bank_template")
#: Every decision a filing stage records. ``returned`` is the internal
#: send-back; the regulator's ``rejected``/``declined`` are package statuses and
#: are deliberately absent here.
FILING_STAGE_DECISIONS: tuple[str, ...] = ("submitted", "reviewed", "approved", "returned")
#: The decisions that ADVANCE the chain. At most one may exist per
#: (package, round, stage) — the partial unique index is what makes two
#: simultaneous approvals of one stage a database error rather than a race.
FILING_FORWARD_DECISIONS: tuple[str, ...] = ("submitted", "reviewed", "approved")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


_ACTIVE_TEMPLATE = "status = 'approved'"
_FORWARD_DECISION = f"decision IN ({_values(FILING_FORWARD_DECISIONS)})"
_TEMPLATE_FK_TARGETS = [
    "filing_workflow_templates.id",
    "filing_workflow_templates.organization_id",
]
_PACKAGE_FK_TARGETS = ["regulatory_packages.id", "regulatory_packages.organization_id"]
_BANK_FK_TARGETS = ["banks.id", "banks.organization_id"]


class FilingWorkflowTemplate(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """The bank's own filing review chain, proposed and approved by four eyes.

    Bernard's Preparer → Approver → Validator is the platform DEFAULT, not the
    only possibility: a bank with a fourth reviewer, or one that calls the last
    stage "Compliance Sign-off", says so here. Hardcoding the third role would
    repeat the mistake that produced the redesign — "Approver" was hardcoded and
    the next bank's process did not fit it.
    """

    __tablename__ = "filing_workflow_templates"
    __table_args__ = (
        ForeignKeyConstraint(["bank_id", "organization_id"], _BANK_FK_TARGETS),
        ForeignKeyConstraint(["superseded_by_id", "organization_id"], _TEMPLATE_FK_TARGETS),
        UniqueConstraint("id", "organization_id", name="uq_filing_workflow_templates_id_org"),
        UniqueConstraint(
            "organization_id", "bank_id", "version", name="uq_filing_workflow_templates_version"
        ),
        CheckConstraint(
            f"status IN ({_values(FILING_WORKFLOW_TEMPLATE_STATUSES)})",
            name="ck_filing_workflow_templates_status",
        ),
        CheckConstraint("version >= 1", name="ck_filing_workflow_templates_version"),
        # Maker-checker at the DATABASE, not only in the service: whoever
        # proposed a chain may not be the one who approves it.
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> proposed_by",
            name="ck_filing_workflow_templates_four_eyes",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'superseded') OR decided_at IS NOT NULL",
            name="ck_filing_workflow_templates_decided",
        ),
        Index(
            "uq_filing_workflow_templates_active",
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
    #: ``[{"seq": 1, "stage_key": "preparation", "title": "Preparer",
    #:    "decision_kind": "prepare", "officer_titles": [],
    #:    "transmit_on_approve": false}]`` — validated by
    #: ``app/domain/filing/workflow.validate_stages`` before it is stored.
    stages: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class PackageWorkflowStage(UuidV7PrimaryKeyMixin, Base):
    """The filing chain AS PINNED for one package. Never altered afterwards."""

    __tablename__ = "package_workflow_stages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["package_id", "organization_id"], _PACKAGE_FK_TARGETS, ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["bank_id", "organization_id"], _BANK_FK_TARGETS),
        ForeignKeyConstraint(["template_id", "organization_id"], _TEMPLATE_FK_TARGETS),
        UniqueConstraint("package_id", "seq", name="uq_package_workflow_stages_seq"),
        UniqueConstraint("package_id", "stage_key", name="uq_package_workflow_stages_key"),
        UniqueConstraint("id", "organization_id", name="uq_package_workflow_stages_id_org"),
        CheckConstraint(
            f"decision_kind IN ({_values(FILING_STAGE_DECISION_KINDS)})",
            name="ck_package_workflow_stages_decision_kind",
        ),
        CheckConstraint("seq BETWEEN 1 AND 20", name="ck_package_workflow_stages_seq"),
        CheckConstraint(
            f"source IN ({_values(FILING_STAGE_SOURCES)})",
            name="ck_package_workflow_stages_source",
        ),
        # A stage either came from the platform default or from a named bank
        # template; "from a template" with no template is an untraceable chain.
        CheckConstraint(
            "(source = 'bank_template') = (template_id IS NOT NULL)",
            name="ck_package_workflow_stages_template",
        ),
        Index("ix_package_workflow_stages_org_package", "organization_id", "package_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_key: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    decision_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    #: The job titles that may take this stage's decision; empty = any officer
    #: with the authority. Checked against the signer's recorded title.
    officer_titles: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    #: The one stage whose approval releases the return to the regulator. The
    #: authority itself is ``Permission.SUBMIT``; this says WHERE in the chain
    #: that authority may be exercised.
    transmit_on_approve: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    template_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PackageStageDecision(UuidV7PrimaryKeyMixin, Base):
    """One officer's decision on a return, bound to the exact state it was taken on.

    ``review_digest`` is the point of the table: an Approver approves a
    particular version of a particular return with a particular set of check
    results, and if any of them changes the approval no longer applies.
    """

    __tablename__ = "package_stage_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["package_id", "organization_id"], _PACKAGE_FK_TARGETS, ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["bank_id", "organization_id"], _BANK_FK_TARGETS),
        UniqueConstraint("id", "organization_id", name="uq_package_stage_decisions_id_org"),
        CheckConstraint(
            f"decision IN ({_values(FILING_STAGE_DECISIONS)})",
            name="ck_package_stage_decisions_decision",
        ),
        CheckConstraint("round >= 1", name="ck_package_stage_decisions_round"),
        CheckConstraint("stage_seq BETWEEN 1 AND 20", name="ck_package_stage_decisions_stage_seq"),
        CheckConstraint("length(review_digest) = 64", name="ck_package_stage_decisions_digest"),
        # A send-back names an EARLIER stage and says why. Without both it is an
        # unexplained rejection, which the preparer cannot act on.
        CheckConstraint(
            "decision <> 'returned' OR (return_to_seq IS NOT NULL AND return_to_seq >= 1 "
            "AND return_to_seq < stage_seq AND comment IS NOT NULL)",
            name="ck_package_stage_decisions_return",
        ),
        Index(
            "uq_package_stage_decisions_forward",
            "package_id",
            "round",
            "stage_seq",
            unique=True,
            postgresql_where=sql_text(_FORWARD_DECISION),
            sqlite_where=sql_text(_FORWARD_DECISION),
        ),
        Index("ix_package_stage_decisions_org_package", "organization_id", "package_id"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    package_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    stage_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_key: Mapped[str] = mapped_column(String(60), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    return_to_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: The name and title as they stood at the decision. A later rename must not
    #: rewrite who approved a return.
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


__all__ = [
    "FILING_FORWARD_DECISIONS",
    "FILING_STAGE_DECISIONS",
    "FILING_STAGE_DECISION_KINDS",
    "FILING_STAGE_SOURCES",
    "FILING_WORKFLOW_TEMPLATE_STATUSES",
    "FilingWorkflowTemplate",
    "PackageStageDecision",
    "PackageWorkflowStage",
]
