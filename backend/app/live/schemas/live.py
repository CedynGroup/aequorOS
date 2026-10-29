from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type LiveStatus = Literal["green", "amber", "red", "na"]
type LiveModule = Literal[
    "liquidity", "capital", "credit", "irr", "fx", "ftp", "rating", "forecast"
]
type AlertSeverity = Literal["low", "medium", "high", "critical"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiveModuleView(ClosedModel):
    module: LiveModule
    status: LiveStatus
    metrics: dict[str, Any]
    computed_at: datetime
    computed_from_input_hash: str | None
    source_as_of_date: date
    source_fact_period_id: UUID | None
    engine_version: str
    calculation_generation: int
    # "ready" = computed and the book behind it reconciles; "blocked" = computed
    # on a book that FAILS the balance-sheet identity (the metrics are kept so
    # the operator can see what to repair, and ``pipeline_error`` carries the
    # control's message — nothing derived from it may be filed); "failed" = not
    # computed at all. A reader must not treat "blocked" as "ready".
    pipeline_state: Literal["ready", "blocked", "failed"]
    pipeline_error: str | None


class LiveReconciliationRead(ClosedModel):
    """The balance-sheet identity control's verdict on the live book.

    Present only when the control has something to say — a plug was applied, an
    approved exception is carrying the book, or the identity FAILS. Its absence
    means the ingested book balanced exactly, because a plug of any size is
    recorded (``app/services/reconciliation.py``).

    Amounts are strings: they are exact decimals, and JSON floats are not.
    """

    control: str
    status: Literal["within_tolerance", "exception_applied", "blocked"]
    #: True when nothing derived from this book may be filed.
    blocks_filing: bool
    assets: str
    funding: str
    gap: str
    gap_fraction: str
    tolerance_pct: str
    #: 'control_plane' (a governed, four-eyed, effective-dated row) or
    #: 'module_default' (no row in force — the module's own versioned default).
    tolerance_source: str
    exception_id: str | None
    #: The control's own sentence, as the derivation phrased it. ``None`` when
    #: the verdict does not block.
    message: str | None


class LiveSummaryRead(ClosedModel):
    bank_id: str
    # Deprecated provenance for clients that have not yet moved to the explicit
    # source fields below. It never selects or keys live state.
    reporting_period_id: UUID | None
    period_label: str | None
    source_as_of_date: date | None
    modules: list[LiveModuleView]
    # True when data has been ingested since these figures were computed, so the
    # numbers below are behind the book. This read does not report or alter queue
    # state. It is NOT drift versus the last official filing — that is a
    # governance concept and lives in ``BankFreshnessRead``. It was previously
    # hardcoded false, which presented a value computed before the book changed
    # as current.
    is_stale: bool
    computed_at: datetime | None = Field(title="Live Summary Computed At")
    #: The balance-sheet identity verdict for the book these figures rest on.
    reconciliation: LiveReconciliationRead | None = None


class FreshnessModuleRead(ClosedModel):
    module: LiveModule
    live_hash: str | None
    official_run_hash: str | None
    is_stale: bool
    computed_at: datetime | None = Field(title="Live Metric Computed At")
    official_run_at: datetime | None = Field(title="Official Run At")


class BankFreshnessRead(ClosedModel):
    bank_id: str
    reporting_period_id: UUID | None
    period_label: str | None
    modules: list[FreshnessModuleRead]
    is_stale: bool


class AlertItemRead(ClosedModel):
    finding_id: UUID
    module: LiveModule
    severity: AlertSeverity
    rule_id: str
    message: str
    metric: str | None
    created_at: datetime


class BankAlertsRead(ClosedModel):
    bank_id: str
    total: int
    by_severity: dict[str, int]
    by_module: dict[str, int]
    items: list[AlertItemRead]


class RefreshRequest(ClosedModel):
    as_of_date: date
    reason: str = Field(min_length=1)


class OfficialRunRequest(ClosedModel):
    as_of_date: date
    reason: str = Field(min_length=1)


class JobEnqueuedRead(ClosedModel):
    job_id: UUID
    job_type: str
    status: str


class LiveSnapshotRead(ClosedModel):
    """One day's close (or today's live edge) for a module."""

    snapshot_date: date
    module: str
    metrics: dict[str, Any]
    status: str
    computed_at: datetime


class LiveSnapshotListRead(ClosedModel):
    """Daily ladder for one bank+module, oldest first."""

    bank_id: str
    module: str
    days: int
    snapshots: list[LiveSnapshotRead]
