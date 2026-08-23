"""Global regulatory-parameter control plane (docs/sdi.md §7 Phase C).

Class/type-keyed regulatory numbers — CAR floors, exposure limits, paid-up
capital floors, LMTD liquidity floors, provisioning rates, reserve ratios — held
as DATA: effective-dated, operator-audited, confirmation-status-aware, resolvable
without a deploy. Engines FETCH these through ``app.services.regulatory_parameters``
instead of embedding a literal (founder directive: no regulatory number is
hardcoded).

GLOBAL, not tenant-scoped, NO RLS — mirrors the ``institution_types`` registry and
``operator_audit_log`` (both global; ``app/models/institution_type.py``,
``app/models/operator.py``). Mutations happen only through the operator API and are
recorded append-only in ``operator_audit_log``. The per-tenant board registers
(``ParamCapitalThreshold``/``ParamLiquidityThreshold`` …) layer ON TOP as tenant
overrides — a tenant may only *tighten* a requirement — so every current engine
read is preserved and the control plane fills the gap where no tenant row exists.

The ``scope`` axis is the tenant-discriminator regime axis:
``scope_type='institution_class'`` keys on {bank, sdi}; ``scope_type='institution_type'``
keys on the specific licence code (e.g. a per-licence paid-up floor). This is the
axis derived from ``banks.institution_type`` and MUST NEVER be conflated with the
counterparty ``institution_class`` fact-attribute token in the BSD line maps — they
share a name only (docs/sdi.md §1.3).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

#: The two scope axes a parameter can key on.
SCOPE_TYPES = ("institution_class", "institution_type")
#: Whether the seeded value is a confirmed regulatory fact or a documented
#: default awaiting BoG/internal confirmation (docs/sdi.md §8). Calculations must
#: surface ``pending`` so a provisional number is never presented as authoritative.
CONFIRMATION_STATUSES = ("confirmed", "pending")
#: Maker-checker lifecycle. ``draft`` rows are invisible to the resolver; only
#: ``approved`` rows (four-eyes) participate in resolution.
PARAMETER_STATUSES = ("draft", "approved")


class RegulatoryParameter(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One effective-dated, operator-approved regulatory number for a scope."""

    __tablename__ = "regulatory_parameter"

    # --- the resolution key -------------------------------------------------
    #: 'institution_class' (bank|sdi) or 'institution_type' (licence code).
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The scope value: 'bank'/'sdi' for a class, or a licence code for a type.
    scope_key: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The parameter identifier, e.g. 'car_min', 'large_exposure_limit_pct',
    #: 'lmtd_narrow_to_volatile', 'prov_loss'.
    param_code: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Jurisdiction — part of the parameter's IDENTITY, since it is in the
    #: resolution key (``app/domain/policy/resolver.py``). No default: a governed
    #: regulatory value proposed without naming its jurisdiction used to be filed
    #: as Ghanaian, which is how a Nigerian scope silently inherited Ghana's floors
    #: (enterprise audit 2026-08-20 §6). Still missing a FK to ``jurisdictions.code``
    #: — that needs a migration; see the WS-E report.
    jurisdiction_code: Mapped[str] = mapped_column(String(8), nullable=False)

    # --- the value + provenance --------------------------------------------
    #: Scalar value (the common case — a percentage, ratio, amount, or count).
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    #: Structured value for non-scalar parameters (e.g. a risk-weight bucket map).
    value_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: The unit the value is expressed in: 'percent' | 'ratio' | 'ghs_millions'
    #: | 'days' | 'bps' | 'multiplier' | 'count'.
    unit: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The legal/directive source (e.g. 'Act 930 s.62(1)') — regulatory defensibility.
    source_citation: Mapped[str] = mapped_column(String(240), nullable=False)
    #: 'confirmed' or 'pending' (docs/sdi.md §8).
    confirmation_status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")

    # --- effective dating (same active-window semantics as params.py) -------
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: Open-ended when null; set to a successor's ``effective_from`` on supersession.
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- maker-checker evidence (four-eyes; approver != proposer) -----------
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    proposed_by: Mapped[str] = mapped_column(String(120), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_rationale: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('institution_class', 'institution_type')",
            name="ck_regulatory_parameter_scope_type",
        ),
        CheckConstraint(
            "confirmation_status IN ('confirmed', 'pending')",
            name="ck_regulatory_parameter_confirmation_status",
        ),
        CheckConstraint(
            "status IN ('draft', 'approved')",
            name="ck_regulatory_parameter_status",
        ),
        CheckConstraint(
            "value_numeric IS NOT NULL OR value_json IS NOT NULL",
            name="ck_regulatory_parameter_value_present",
        ),
        # One generation per (scope, code, jurisdiction, effective_from) — the
        # supersession discipline closes the prior open row before inserting.
        UniqueConstraint(
            "scope_type",
            "scope_key",
            "param_code",
            "jurisdiction_code",
            "effective_from",
            name="uq_regulatory_parameter_generation",
        ),
        # The resolver's hot path: by code + scope + jurisdiction, newest effective.
        Index(
            "ix_regulatory_parameter_resolution",
            "param_code",
            "scope_type",
            "scope_key",
            "jurisdiction_code",
            "effective_from",
        ),
    )
