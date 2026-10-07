"""Persistence for the governed forecast assumption register."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
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

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

#: ``draft`` → ``submitted`` → ``approved`` | ``rejected``. Only ``approved`` ever
#: resolves for a run; approved and rejected versions are final.
VERSION_STATUSES: Final = ("draft", "submitted", "approved", "rejected")
OPEN_STATUSES: Final = ("draft", "submitted")

#: Who wrote the values: a person in the bank (``tenant``), provisioning's
#: illustrative starting position (``starting_position``), or the pre-governance
#: board register rows the migration carried over (``register``).
VERSION_ORIGINS: Final = ("tenant", "starting_position", "register")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


class ForecastAssumptionVersion(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One version of a bank's forecast assumption set, through maker-checker.

    A version is bank-scoped: each bank's board sets its own projection drivers,
    and two banks of one organization share an RLS tenant, so every lookup names
    the bank. ``effective_from`` is the first book date (a run's as-of date) the
    version governs; for a book date the latest-effective approved version wins,
    and a later approval on the same effective date supersedes an earlier one.
    """

    __tablename__ = "forecast_assumption_versions"
    __table_args__ = (
        CheckConstraint(f"status IN {_in(VERSION_STATUSES)}", name="ck_fav_status"),
        CheckConstraint(f"origin IN {_in(VERSION_ORIGINS)}", name="ck_fav_origin"),
        CheckConstraint("version_number >= 1", name="ck_fav_version_number"),
        # An approval always records when and by whom: a platform user, or, for a
        # register row carried over by the migration, the approver it named.
        CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR reviewed_at IS NOT NULL",
            name="ck_fav_reviewed_at",
        ),
        CheckConstraint(
            "status <> 'approved' OR reviewed_by IS NOT NULL OR approver_label IS NOT NULL",
            name="ck_fav_approver",
        ),
        CheckConstraint(
            "reviewed_by IS NULL OR submitted_by IS NULL OR reviewed_by <> submitted_by",
            name="ck_fav_checker_not_submitter",
        ),
        CheckConstraint(
            "reviewed_by IS NULL OR created_by IS NULL OR reviewed_by <> created_by",
            name="ck_fav_checker_not_author",
        ),
        UniqueConstraint(
            "organization_id", "bank_id", "version_number", name="uq_fav_version_number"
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_fav_bank",
        ),
        Index(
            "ix_fav_bank_status_effective",
            "organization_id",
            "bank_id",
            "status",
            "effective_from",
        ),
        # At most one version in flight per bank: the pending change is one
        # object everyone reviews, never two competing drafts.
        Index(
            "uq_fav_one_open_per_bank",
            "organization_id",
            "bank_id",
            unique=True,
            postgresql_where=sql_text(f"status IN {_in(OPEN_STATUSES)}"),
            sqlite_where=sql_text(f"status IN {_in(OPEN_STATUSES)}"),
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: ``{scenario_code: {assumption_key: "decimal string"}}``, always complete.
    presets: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    submitted_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approver_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
