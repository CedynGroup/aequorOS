"""Operating-Environment governed assessments (GLOBAL desk table).

Spec: ``docs/internal/operating_environment_score.md``. One row is one
maker-checker-governed determination of the jurisdiction operating-environment
strength for one COB date — the same desk doctrine as ``desk_determinations``:
GLOBAL, deliberately NOT tenant-scoped and NOT RLS-forced (the desk is
AequorOS' own golden-copy production line, upstream of every tenant; tenant
visibility happens only at publication, when the approved score fans out as
the ``GHANA_OPERATING_ENVIRONMENT_SCORE`` market index through the
``aequor_desk`` adapter seam).

``input_snapshot`` + ``input_digest`` make the score reproducible (the
regulatory ``input_hash`` idiom — value-based, id-free, canonically sorted).
Maker-checker: the service refuses ``approved_by == proposed_by``. Lifecycle
``draft → pending_review → approved → published``; published rows are never
edited — a correction is a new assessment for the same COB date.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin

DESK_OPERATING_ENVIRONMENT_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_review",
    "approved",
    "published",
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class DeskOperatingEnvironmentAssessment(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One governed operating-environment determination for a jurisdiction/COB."""

    __tablename__ = "desk_operating_environment_assessments"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(DESK_OPERATING_ENVIRONMENT_STATUSES)})",
            name="ck_desk_operating_environment_assessments_status",
        ),
        Index(
            "ix_desk_operating_environment_assessments_jur_cob",
            "jurisdiction_code",
            "cob_date",
        ),
    )

    # ISO country code (e.g. 'GH'); the jurisdiction the score describes. Not
    # FK-constrained — desk tables stay decoupled from the tenant/registry
    # graph (the desk_* precedent).
    jurisdiction_code: Mapped[str] = mapped_column(String(8), nullable=False)
    cob_date: Mapped[date] = mapped_column(Date, nullable=False)
    # The versioned domain parameter-object identity (e.g.
    # 'oe-bicra/2026.08-placeholder').
    methodology_version: Mapped[str] = mapped_column(String(60), nullable=False)
    # Exact resolved inputs used (observations, judgments, sovereign category,
    # auto-pull provenance), value-based and id-free.
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # The full BICRA breakdown: each input → sub-score → sub-factor → pillar →
    # composite → strength → governor → the [0,1] score.
    computed_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # The published [0,1] strength (6 dp). The value that fans out as the index.
    score: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    # Operator emails — desk staff are workforce identities, not tenant users.
    proposed_by: Mapped[str] = mapped_column(String(320), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
