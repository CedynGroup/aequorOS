"""Immutable implied bank-rating and probability-of-default runs."""

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


class ImpliedRatingRun(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One reproducible application of a versioned rating methodology.

    ``input_snapshot`` contains the exact financial facts, regulatory metrics,
    sovereign rating, and market-environment values consumed by the engine.
    Published ratings are never overwritten: a new execution creates a new
    row, preserving the methodology/input lineage required for model review.
    """

    __tablename__ = "implied_rating_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed')", name="ck_implied_rating_runs_status"
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
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
            "id",
            "organization_id",
            "bank_id",
            name="uq_implied_rating_runs_id_org_bank",
        ),
        Index(
            "ix_implied_rating_runs_org_bank_period",
            "organization_id",
            "bank_id",
            "reporting_period_id",
        ),
        Index("ix_implied_rating_runs_org_input_hash", "organization_id", "input_hash"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    methodology_code: Mapped[str] = mapped_column(String(40), nullable=False)
    methodology_version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    results: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)