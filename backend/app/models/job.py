"""Background jobs: the queue the worker drains for every feature."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidV4PrimaryKeyMixin, utc_now


class Job(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_organization_id_coalesce_key", "organization_id", "coalesce_key"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Polymorphic reference: UUID for most entities, platform ID for banks/orgs.
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Live-engine dispatch fields: the target bank, an arbitrary JSON payload,
    # a not-before schedule time (debounce/backoff/scheduler), and a coalesce
    # key so a burst of ingestions collapses into one queued refresh.
    bank_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coalesce_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default=sql_text("3"), nullable=False
    )
    progress: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    # Runtime identity that most recently transitioned this row to running.
    # Retained across retries so an orphaned job remains attributable until a
    # subsequent worker claims it.
    claimed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
