"""Governed forward-curve definitions (spec forward_curve_construction.md §2.6, FC-3).

The "Curve 1/2/3" analogue from the Eikon template, made a first-class governed
object: a named, versioned, dual-controlled configuration that fixes what a
forward curve *is* — instrument set, projection index, discount curve,
interpolation, calendar, conventions, curve frequency, extrapolation. Construction
(``app/services/market_desk/curve_construction.py``) reads a definition and applies
it to a cob's quotes; it never invents these knobs.

GLOBAL table — desk master data, deliberately NOT tenant-scoped and NOT
RLS-forced, the ``DeskMethodology`` / ``jurisdictions`` precedent: the desk is
AequorOS' own golden-copy production line, upstream of every tenant, and the row
is never read by a tenant session. Tenant visibility happens only at publication,
when a constructed curve fans out through the market-data adapter seam.

Track-2 governed exactly like ``DeskMethodology`` (spec §2.6): an approved version
is immutable, and any parameter change mints version+1 with a documented rationale
under maker-checker (approver != proposer). Enforcement lives in the service layer
(``curve_construction.py``), the single write path.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin

DESK_CURVE_DEFINITION_STATUSES: tuple[str, ...] = ("draft", "approved", "retired")
DESK_CURVE_KINDS: tuple[str, ...] = ("forward", "zero", "discount")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class DeskCurveDefinition(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One governed, versioned forward-curve definition (spec §2.6, FC-3).

    ``(curve_code, version)`` is unique; ``status`` walks draft -> approved
    (-> retired), approved rows immutable by service convention. ``params`` is
    the schemaless catch-all for extra governed knobs (grid periods, QA
    tolerances, end-of-month rule) that do not warrant a column yet.
    """

    __tablename__ = "desk_curve_definitions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(DESK_CURVE_DEFINITION_STATUSES)})",
            name="ck_desk_curve_definitions_status",
        ),
        CheckConstraint(
            f"curve_kind IN ({_values(DESK_CURVE_KINDS)})",
            name="ck_desk_curve_definitions_curve_kind",
        ),
        UniqueConstraint(
            "curve_code", "version", name="uq_desk_curve_definitions_code_version"
        ),
    )

    # AEQ.* code, e.g. 'AEQ.GHS.SOV.FWD', 'AEQ.USD.SWAP.3ML'.
    curve_code: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    calendar_name: Mapped[str] = mapped_column(String(40), nullable=False)
    curve_kind: Mapped[str] = mapped_column(String(12), default="forward", nullable=False)
    # Projection index (Eikon "Forward Curve"): '3M_LIBOR' | 'SOFR' | 'GHS_SOVEREIGN'.
    projection_index: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Discount (OIS/AGD) curve code, e.g. 'AEQ.GHS.OIS'; null = self-discounting.
    discount_curve_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Instrument-set family / catalog key (the Eikon "Swap style").
    instrument_set_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    interpolation_method: Mapped[str] = mapped_column(String(40), nullable=False)
    # Output ("Convert to") day count for the grid yields, e.g. 'ACT_360'.
    output_daycount: Mapped[str] = mapped_column(String(20), nullable=False)
    payment_frequency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_interval_months: Mapped[int] = mapped_column(Integer, nullable=False)
    # Forward-grid period length label, e.g. '3M' (the Eikon "Curve Frequency").
    curve_frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    spot_lag_days: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    roll_convention: Mapped[str] = mapped_column(
        String(30), default="modified_following", nullable=False
    )
    extrapolation_rule: Mapped[str] = mapped_column(
        String(30), default="flat_forward", nullable=False
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    change_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(320), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
