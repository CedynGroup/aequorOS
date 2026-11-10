"""Background jobs: the queue the worker drains for every feature."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.authorization import InstitutionScope, Module, Sensitivity
from app.db.base import Base, TableArgs, UuidV4PrimaryKeyMixin, utc_now


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


class Job(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "jobs"
    __table_args__: TableArgs = (
        Index("ix_jobs_status_run_after", "status", "run_after"),
        Index("ix_jobs_organization_id_coalesce_key", "organization_id", "coalesce_key"),
        # Typed source locator (#186): persisted at enqueue time by the
        # producer. Legacy rows carry all-NULL locators and are hidden at
        # every tenant read path — never inferred or backfilled (mission D9).
        CheckConstraint(
            f"institution_scope IN ({_values(tuple(InstitutionScope))})",
            name="ck_jobs_institution_scope",
        ),
        CheckConstraint(
            f"module IN ({_values(tuple(Module))})",
            name="ck_jobs_module",
        ),
        CheckConstraint(
            f"sensitivity IN ({_values(tuple(Sensitivity))})",
            name="ck_jobs_sensitivity",
        ),
        Index(
            "ix_jobs_org_status_run_locator",
            "organization_id",
            "status",
            "run_after",
            "module",
            "sensitivity",
            "bank_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Polymorphic reference: UUID for most entities, platform ID for banks/orgs.
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Live-engine dispatch fields: the target bank, an arbitrary JSON payload,
    # a not-before schedule time (debounce/backoff/scheduler), and a coalesce
    # key so a burst of ingestions collapses into one queued refresh.
    bank_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Remaining locator dimensions (#186); ``bank_id`` above is subsumed by
    # the locator. All-NULL on legacy rows (hidden) and on worker-only jobs
    # without tenant scope, which instead carry explicit non-tenant
    # provenance below.
    institution_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    module: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sensitivity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    coalesce_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Enqueue provenance (#186): the initiating human/machine principal (or an
    # explicit ``worker``/``system`` marker for defined worker/system
    # enqueues), the permission/action that justified the enqueue, and the
    # decision/audit reference where one exists. Set by every producer;
    # retries and coalesced successors inherit the original values.
    enqueued_by_principal: Mapped[str | None] = mapped_column(String(160), nullable=True)
    enqueued_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    enqueue_decision_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
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
