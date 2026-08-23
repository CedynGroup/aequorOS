"""Market research desk state (AequorOS_Market_Data_and_Curve_Platform.md §4, §5, §10).

Desk-side tables are GLOBAL — deliberately NOT tenant-scoped and NOT
RLS-forced, the same precedent as the ``jurisdictions`` registry: the desk is
AequorOS' own golden-copy production line, upstream of every tenant. Tenant
visibility happens only at publication, when an approved determination fans
out through the market-data adapter seam (vendor ``aequor_desk``) into each
bank's canonical store with full batch/lineage provenance — the desk tables
themselves are never read by tenant sessions.

Bitemporality (spec §10): ``DeskDetermination.cob_date`` is valid time and
``published_at`` is transaction time. Published determinations are immutable;
a correction mints a NEW determination row carrying ``supersedes_id``.
``DeskObservation`` corrections are append-only the same way
(``superseded_by`` + a current-generation partial unique index, the
``canonical.py`` idiom). ``DeskMethodology`` rows are append-only by service
convention: an approved version is never edited — changes mint version+1
under Track-2 governance (spec §5).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin, utc_now

DESK_CAPTURE_STATUSES: tuple[str, ...] = ("captured", "parsed", "failed")
DESK_OBSERVATION_UNITS: tuple[str, ...] = ("pct", "rate", "ghs", "index")
DESK_METHODOLOGY_STATUSES: tuple[str, ...] = ("draft", "approved", "retired")
DESK_DETERMINATION_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_review",
    "approved",
    "rejected",
    "published",
)
DESK_PUBLICATION_STATUSES: tuple[str, ...] = ("complete", "partial", "failed")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class DeskSourceCapture(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """Silver layer (spec §10 layer 1): one source-tagged capture, kept as-is.

    Never discarded — the lineage root every observation points back to.
    Small captures ride inline in ``payload``; large ones live at
    ``storage_path`` with only the SHA-256 here. A capture whose bytes are
    byte-identical to an earlier capture of the SAME source stores no second
    copy: its payload names the row that holds them and readers resolve the
    hop. ``app/services/market_desk/snippets.py`` owns that payload contract.
    """

    __tablename__ = "desk_source_captures"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(DESK_CAPTURE_STATUSES)})",
            name="ck_desk_source_captures_status",
        ),
        Index("ix_desk_source_captures_source_key_as_of", "source_key", "as_of_date"),
        # The nightly de-duplication probe: "has this source already stored
        # these exact bytes?" — one lookup per artifact captured.
        Index("ix_desk_source_captures_source_key_sha256", "source_key", "content_sha256"),
    )

    # 'bog_tbill_rates' | 'bog_interbank_fx' | 'gfim_daily' | 'manual' | ...
    source_key: Mapped[str] = mapped_column(String(60), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="captured", nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Operator email — desk staff are workforce identities, not tenant users.
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)


class DeskObservation(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One cleaned observation of one series on one date (spec §5 step 1).

    Append-only: a correction inserts a new row and stamps the old one's
    ``superseded_by``; the partial unique index keeps exactly one CURRENT
    row per (series_code, as_of_date).
    """

    __tablename__ = "desk_observations"
    __table_args__ = (
        CheckConstraint(
            f"unit IN ({_values(DESK_OBSERVATION_UNITS)})",
            name="ck_desk_observations_unit",
        ),
        Index(
            "uq_desk_observations_current",
            "series_code",
            "as_of_date",
            unique=True,
            postgresql_where=sql_text("superseded_by IS NULL"),
            sqlite_where=sql_text("superseded_by IS NULL"),
        ),
        Index("ix_desk_observations_series_as_of", "series_code", "as_of_date"),
    )

    # NULL for manual entries; those carry ``entered_by`` provenance instead.
    capture_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("desk_source_captures.id"), nullable=True
    )
    # e.g. 'GHS.TBILL.91.DISCOUNT', 'GHS.INTERBANK.ON', 'GHS.USDGHS.MID', 'GHS.MPR'
    series_code: Mapped[str] = mapped_column(String(80), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    unit: Mapped[str] = mapped_column(String(12), nullable=False)
    # Tender number, volume, trade count, ... — source-specific and schemaless.
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    quality_flags: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    entered_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    superseded_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class DeskMethodology(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """The methodology register (spec §5): one row per (code, version).

    ``parameters`` IS the versioned parameter set — the weekly run reads from
    here, never from hard-coded assumptions. Approved rows are immutable by
    service-layer enforcement (``app/services/market_desk/register.py``):
    changing any parameter mints version+1 with a documented rationale
    (Track 2). ``effective_from`` drives effective-dated resolution.
    """

    __tablename__ = "desk_methodologies"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(DESK_METHODOLOGY_STATUSES)})",
            name="ck_desk_methodologies_status",
        ),
        UniqueConstraint(
            "methodology_code", "version", name="uq_desk_methodologies_code_version"
        ),
    )

    methodology_code: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    change_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(320), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)


class DeskDetermination(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One application of an approved methodology to one COB date (spec §5).

    Bitemporal: ``cob_date`` is valid time, ``published_at`` transaction
    time. ``input_snapshot`` + ``input_digest`` make every published value
    exactly reproducible (the regulatory ``input_hash`` idiom — value-based,
    id-free, canonically sorted). Maker-checker: the service refuses
    ``reviewed_by == prepared_by``. Published rows are never edited — a
    correction is a NEW determination with ``supersedes_id`` set.
    """

    __tablename__ = "desk_determinations"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(DESK_DETERMINATION_STATUSES)})",
            name="ck_desk_determinations_status",
        ),
        Index("ix_desk_determinations_cob_date", "cob_date"),
    )

    cob_date: Mapped[date] = mapped_column(Date, nullable=False)
    methodology_code: Mapped[str] = mapped_column(String(40), nullable=False)
    methodology_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Exact observation set used: [{series_code, as_of_date, value}, ...],
    # sorted by canonical JSON. Values are strings (Decimal-safe).
    input_snapshot: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # Curves as node lists keyed by curve code, plus derived rates (incl. the
    # GRR-consistent base). Set by the calculation pipeline.
    derived_values: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    qa_results: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    # Track-1 research judgment (Option B): determination-scoped overrides /
    # spreads / assumption notes. Never written into the methodology register.
    # Writable only while status is draft; folded into derived rates on compute
    # and included in package_digest for reproducibility.
    research_adjustments: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    prepared_by: Mapped[str] = mapped_column(String(320), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("desk_determinations.id"), nullable=True
    )


class DeskPublication(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One fan-out of one determination into every tenant's canonical store.

    ``results`` records the per-bank outcome:
    ``[{bank_id, ingestion_batch_id, status, error?}, ...]`` — error strings
    are bank-facing only (the adapter seam's leak rule applies here too).
    Partial failure never rolls back successful banks; re-publishing the same
    determination supersedes cleanly by natural key (the pull runner's §4.3
    idempotency contract).
    """

    __tablename__ = "desk_publications"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(DESK_PUBLICATION_STATUSES)})",
            name="ck_desk_publications_status",
        ),
        Index("ix_desk_publications_determination_id", "determination_id"),
    )

    determination_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("desk_determinations.id"), nullable=False
    )
    published_by: Mapped[str] = mapped_column(String(320), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    results: Mapped[list[Any]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)
