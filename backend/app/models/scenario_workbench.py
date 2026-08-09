"""Scenario Workbench: desk-authored stress scenarios + saved analyses.

The treasury sandbox layer. Two RLS-forced tenant tables (migration
202608090040), deliberately OUTSIDE the regulatory spine:

- ``stress_scenarios`` — user-defined scenarios per module. System/regulatory
  scenarios (the ``*_SCENARIO_CODES`` sets priced from ``ParamStressShock``)
  are never rows here; the catalogue merges them at read time. Editing a
  custom scenario is allowed — these are sandbox definitions, not evidence.
  The immutable artifact remains the RegulatoryRun, which never reads this
  table.
- ``scenario_analyses`` — optionally saved what-if results ("Save analysis").
  A snapshot of resolved shocks + computed results for sharing/revisiting.
  Never a ``RegulatoryRun``; never consumed by report generation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

WORKBENCH_MODULE_VALUES = ("liquidity", "capital", "irr", "fx", "ftp")


class StressScenario(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stress_scenarios"
    __table_args__ = (
        CheckConstraint(
            f"module IN {WORKBENCH_MODULE_VALUES!r}", name="ck_stress_scenarios_module"
        ),
        UniqueConstraint(
            "organization_id", "bank_id", "module", "code", name="uq_stress_scenarios_scope"
        ),
        Index("ix_stress_scenarios_bank", "organization_id", "bank_id", "module"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), ForeignKey("banks.id"), nullable=False)
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {shock_key: stringified Decimal} — validated against the module's
    # vocabulary at write time (scenario_catalog.validate_shocks).
    shocks: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SavedScenarioAnalysis(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scenario_analyses"
    __table_args__ = (
        CheckConstraint(
            f"module IN {WORKBENCH_MODULE_VALUES!r}", name="ck_scenario_analyses_module"
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
            "ix_scenario_analyses_bank",
            "organization_id",
            "bank_id",
            "module",
            "reporting_period_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(60), nullable=False)
    # [{kind, code, scenario_id, label, shocks(resolved), overrides}] — the
    # exact shock sets that produced the results, snapshotted at save time.
    scenarios: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    # Per-scenario result snapshots (metrics/statuses/validations/error).
    results: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
