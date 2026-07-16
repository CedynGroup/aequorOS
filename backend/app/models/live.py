from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

# The six live-view surfaces. Mirrors the regulatory modules plus forecast; the
# five cheap modules are recomputed inline on every refresh, forecast reflects
# the latest immutable official forecast run.
LIVE_MODULES = ("liquidity", "capital", "irr", "fx", "ftp", "forecast")
_MODULE_CHECK = "module IN ('liquidity', 'capital', 'irr', 'fx', 'ftp', 'forecast')"


class LiveMetric(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """The always-fresh per-module baseline view for a (bank, period).

    Upserted on every pipeline refresh (unique on org/bank/period/module), so a
    read is a single cheap lookup and never runs the engines. Distinct from the
    immutable ``regulatory_runs`` used for regulatory filing.
    """

    __tablename__ = "live_metrics"
    __table_args__ = (
        CheckConstraint(_MODULE_CHECK, name="ck_live_metrics_module"),
        CheckConstraint(
            "status IN ('green', 'amber', 'red', 'na')",
            name="ck_live_metrics_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "reporting_period_id",
            "module",
            name="uq_live_metrics_org_bank_period_module",
        ),
        Index(
            "ix_live_metrics_org_bank_period", "organization_id", "bank_id", "reporting_period_id"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    computed_from_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LiveFinding(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """A live limit breach for a (bank, period, module) — the alert source.

    Deterministic-rule findings reconciled on every refresh: a continuing breach
    keeps its row (and ``created_at``) while a cleared breach is superseded. Bank
    scoped, unlike the case-scoped ``risk_findings`` of the assessment workflow.
    """

    __tablename__ = "live_findings"
    __table_args__ = (
        CheckConstraint(_MODULE_CHECK, name="ck_live_findings_module"),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_live_findings_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'needs_review', 'superseded')",
            name="ck_live_findings_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        Index(
            "uq_live_findings_open",
            "organization_id",
            "bank_id",
            "reporting_period_id",
            "module",
            "rule_id",
            unique=True,
            postgresql_where=sql_text("status = 'open'"),
            sqlite_where=sql_text("status = 'open'"),
        ),
        Index("ix_live_findings_org_bank_status", "organization_id", "bank_id", "status"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default=sql_text("'open'"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str | None] = mapped_column(String(80), nullable=True)
