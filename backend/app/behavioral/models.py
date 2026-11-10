"""Durable maker-checker proposals for applying Behavioral model estimates."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.behavioral.ml.config import MODEL_SLUGS
from app.db.base import Base, TableArgs, TimestampMixin, UuidV7PrimaryKeyMixin


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class BehavioralModelProposal(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """An immutable estimate snapshot awaiting a different officer's review."""

    __tablename__ = "behavioral_model_proposals"
    __table_args__: TableArgs = (
        CheckConstraint(
            f"model IN ({_values(MODEL_SLUGS)})",
            name="ck_behavioral_model_proposals_model",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted')",
            name="ck_behavioral_model_proposals_status",
        ),
        CheckConstraint(
            "length(trim(model_version)) > 0",
            name="ck_behavioral_model_proposals_version_present",
        ),
        CheckConstraint(
            "(status = 'pending' AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL "
            "AND ingestion_batch_id IS NULL) OR "
            "(status = 'accepted' AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_by_user_id <> proposed_by_user_id AND reviewed_at IS NOT NULL "
            "AND ingestion_batch_id IS NOT NULL)",
            name="ck_behavioral_model_proposals_review",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_behavioral_model_proposals_bank",
        ),
        ForeignKeyConstraint(
            ["proposed_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_behavioral_model_proposals_proposer",
        ),
        ForeignKeyConstraint(
            ["reviewed_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_behavioral_model_proposals_reviewer",
        ),
        ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id", "bank_id"],
            [
                "ingestion_batches.id",
                "ingestion_batches.organization_id",
                "ingestion_batches.bank_id",
            ],
            name="fk_behavioral_model_proposals_batch",
        ),
        UniqueConstraint(
            "ingestion_batch_id",
            name="uq_behavioral_model_proposals_ingestion_batch",
        ),
        Index(
            "ix_behavioral_model_proposals_scope_status",
            "organization_id",
            "bank_id",
            "status",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    values: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    proposed_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingestion_batch_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
