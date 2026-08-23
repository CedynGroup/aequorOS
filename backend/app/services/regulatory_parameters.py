"""Resolver + seed catalogue for the regulatory-parameter control plane.

Engines call :func:`resolve` (never a literal) to obtain a class/type-keyed
regulatory number with full provenance (value, unit, source, confirmation status,
effective date, resolution scope) — the audit trail a regulator expects
("which parameter, which version, what source, was it confirmed?").

Resolution precedence (docs/sdi.md §7 Phase C):

    tenant board override  >  institution_type row  >  institution_class row  >  (fail-loud)

The **tenant board override** layer lives in the existing per-tenant registers
(``ParamCapitalThreshold``/``ParamLiquidityThreshold`` …, resolved by
``app.services.params.get_active_params``); those are read by each engine BEFORE
falling back here, so a tenant register value takes precedence and every current
bank read is preserved. The board override may only ever TIGHTEN: as of
2026-08-21 that is a hard, generalised clamp (:func:`clamp_overrides`) applied
across a whole register in one pass for every code in
``app.domain.policy.PARAMETER_DIRECTION`` — not the per-code, per-call-site clamp
it used to be, which covered 9 of the 25 governed codes and had two modules
disagreeing about ``car_min``. This module owns the **global default** layer: the
licence-specific (``institution_type``) row wins over the coarse
(``institution_class``) row, and an unseeded required code raises — a regulatory
number is never invented.

The chain itself (scope key, precedence, effective dating, tighten-only rules) is
pure and lives in ``app/domain/policy/resolver.py``; this module is its database
adapter. Call ``policy_scope(db, bank, as_of=...)`` when you need the whole chain.

``SEED_PARAMETERS`` is the single authoritative catalogue used by BOTH the seed
migration and the hermetic-test seed, so the two never drift.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, NamedTuple
from weakref import WeakKeyDictionary

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.authority.outcomes import OutcomeDetail
from app.domain.policy import (
    PARAMETER_DIRECTION,
    ClampReport,
    Direction,
    PolicyScope,
    PolicyUnresolvedError,
    direction_for,
    governed_codes,
    policy_unresolved,
    resolution_order,
    tighten,
)
from app.domain.policy import clamp_overrides as _clamp_values
from app.models import Bank, RegulatoryParameter
from app.services import institution_types, jurisdictions

#: Re-exported from ``app/domain/policy`` so the historic
#: ``regulatory_parameters.tighten`` / ``.PARAMETER_DIRECTION`` call sites keep
#: working while the rules themselves live in the pure resolver.
__all__ = [
    "PARAMETER_DIRECTION",
    "ClampReport",
    "Direction",
    "PolicyScope",
    "PolicyUnresolvedError",
    "RegulatoryParameterError",
    "ResolvedParameter",
    "SEED_PARAMETERS",
    "clamp_overrides",
    "consume_parameter_provenance",
    "control_values",
    "direction_for",
    "governed_codes",
    "parameter_row_provenance",
    "policy_scope",
    "resolve",
    "resolve_class_value",
    "resolve_decimal",
    "resolve_many",
    "seed_rows",
    "tighten",
    "try_resolve",
]

#: Observability (docs/sdi.md §19). The control-plane resolver is the single seam
#: through which every class/type-keyed regulatory number reaches a calculation, so
#: it is where the two risk events are recorded as structured logs: an *unconfirmed*
#: (pending) value driving a live regulatory computation, and a mandatory parameter
#: missing (fail-loud). Both are intercepted into the JSON log stream (loguru
#: ``serialize=True``); the persistent who/when/why audit trail lives separately in
#: ``operator_audit_log``.
logger = logging.getLogger(__name__)

#: The anchor effective date for the seeded defaults — well before any reporting
#: as-of, so a seeded parameter is always active. New generations supersede it.
SEED_EFFECTIVE_FROM = date(2020, 1, 1)
#: The system actor recorded as maker+checker on the seeded defaults (the seed is
#: the authoritative platform default; operator changes go through four-eyes).
SEED_ACTOR = "platform_seed"


class ParamSpec(NamedTuple):
    """One row in the seed catalogue."""

    scope_type: str  # 'institution_class' | 'institution_type'
    scope_key: str  # 'bank'/'sdi' or a licence code
    param_code: str
    value: str  # Decimal-as-string (exact, no float drift)
    unit: str
    source_citation: str
    confirmation_status: str  # 'confirmed' | 'pending'


# The 8 LMTD Table-1 prudential-ratio floors keep the register's existing code
# vocabulary (backend/app/services/liquidity_thresholds.py BANK_MINIMUM_PCT keys)
# so Phase D resolves resolve(db, bank, code) directly. Bank column is byte-verified
# against BANK_MINIMUM_PCT; SDI column per docs/sdi.md §4.1.
_LMTD_FLOORS: tuple[tuple[str, str, str], ...] = (
    # (param_code, bank_floor, sdi_floor)
    ("narrow_to_volatile", "80", "90"),
    ("broad_to_volatile", "100", "100"),
    ("narrow_to_short_term", "50", "50"),
    ("broad_to_short_term", "70", "60"),
    ("narrow_to_total_assets", "30", "30"),
    ("broad_to_total_assets", "50", "40"),
    ("narrow_to_total_deposits", "60", "60"),
    ("broad_to_total_deposits", "80", "70"),
)


def _lmtd_specs() -> list[ParamSpec]:
    specs: list[ParamSpec] = []
    for code, bank_floor, sdi_floor in _LMTD_FLOORS:
        specs.append(
            ParamSpec(
                "institution_class",
                "bank",
                code,
                bank_floor,
                "percent",
                "LMTD 2026 ¶9",
                "confirmed",
            )
        )
        specs.append(
            ParamSpec(
                "institution_class", "sdi", code, sdi_floor, "percent", "LMTD 2026 ¶9", "confirmed"
            )
        )
    return specs


#: The authoritative seed catalogue (docs/sdi.md §2.2, §4). Confirmed [C] values
#: ship 'confirmed'; documented defaults awaiting BoG/internal confirmation [U]
#: ship 'pending' and are editable in the operator console.
SEED_PARAMETERS: tuple[ParamSpec, ...] = (
    # --- capital adequacy --------------------------------------------------
    ParamSpec(
        "institution_class",
        "bank",
        "car_min",
        "13",
        "percent",
        "BoG Capital Requirements Directive 2018 ¶71 (10%) + ¶75 CCB1 (3%)",
        "confirmed",
    ),
    ParamSpec("institution_class", "sdi", "car_min", "10", "percent", "Act 930 s.29", "confirmed"),
    ParamSpec(
        "institution_class",
        "bank",
        "statutory_reserve_fund_pct",
        "50",
        "percent",
        "Act 930 s.34; NBFI r.7",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "statutory_reserve_fund_pct",
        "50",
        "percent",
        "NBFI r.7; Act 930 s.34",
        "confirmed",
    ),
    # --- minimum paid-up capital (licence-specific) ------------------------
    ParamSpec(
        "institution_type",
        "universal_bank",
        "paid_up_min",
        "400",
        "ghs_millions",
        "BoG minimum capital (banks)",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "financial_holding_company",
        "paid_up_min",
        "400",
        "ghs_millions",
        "BoG minimum capital (banks)",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "savings_and_loans",
        "paid_up_min",
        "15",
        "ghs_millions",
        "SDI Subsector ToR",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "finance_house",
        "paid_up_min",
        "15",
        "ghs_millions",
        "SDI Subsector ToR",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "microfinance_bank",
        "paid_up_min",
        "2",
        "ghs_millions",
        "MFI Framework 2026",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "rural_community_bank",
        "paid_up_min",
        "1",
        "ghs_millions",
        "SDI Subsector ToR",
        "confirmed",
    ),
    ParamSpec(
        "institution_type",
        "other_rfi",
        "paid_up_min",
        "15",
        "ghs_millions",
        "SDI Subsector ToR (default)",
        "pending",
    ),
    # --- exposures ---------------------------------------------------------
    ParamSpec(
        "institution_class",
        "bank",
        "single_obligor_limit_pct",
        "25",
        "percent",
        "Act 930 s.62(1)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "single_obligor_limit_pct",
        "25",
        "percent",
        "Act 930 s.62(1)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "large_exposure_limit_pct",
        "20",
        "percent",
        "Large Exposures Directive Sept 2025",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "large_exposure_limit_pct",
        "15",
        "percent",
        "Large Exposures Directive Sept 2025",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "large_exposure_id_threshold_pct",
        "10",
        "percent",
        "BoG LE return (BSD) 10% identification",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "large_exposure_id_threshold_pct",
        "10",
        "percent",
        "BoG LE return (BSD) 10% identification",
        "confirmed",
    ),
    # related-party & aggregate caps: mechanism built, value pending confirmation
    ParamSpec(
        "institution_class",
        "sdi",
        "related_party_limit_pct",
        "25",
        "percent",
        "Act 930 related-party (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "related_party_limit_pct",
        "25",
        "percent",
        "Act 930 related-party (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "aggregate_large_exposure_cap",
        "8",
        "multiplier",
        "LED aggregate cap (×NOF; value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "aggregate_large_exposure_cap",
        "8",
        "multiplier",
        "LED aggregate cap (×NOF; value pending BoG)",
        "pending",
    ),
    # --- SDI liquidity reserves (NBFI r.11) --------------------------------
    ParamSpec(
        "institution_class",
        "sdi",
        "primary_liquidity_reserve_pct",
        "10",
        "percent",
        "NBFI Business Rules 2000 r.11",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "secondary_liquidity_reserve_pct",
        "15",
        "percent",
        "NBFI Business Rules 2000 r.11",
        "confirmed",
    ),
    # --- provisioning rates (grid choice [U]; RATES [C]) -------------------
    ParamSpec(
        "institution_class",
        "sdi",
        "prov_standard",
        "0",
        "percent",
        "NBFI Rules 2000 r.19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "prov_substandard",
        "20",
        "percent",
        "NBFI Rules 2000 r.19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "prov_doubtful",
        "50",
        "percent",
        "NBFI Rules 2000 r.19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "prov_loss",
        "100",
        "percent",
        "NBFI Rules 2000 r.19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_standard",
        "1",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_olem",
        "10",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_substandard",
        "25",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_doubtful",
        "50",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "prov_loss",
        "100",
        "percent",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    # --- loan-classification DPD boundaries (days) -------------------------
    ParamSpec(
        "institution_class",
        "sdi",
        "npl_dpd_threshold",
        "90",
        "days",
        "NBFI Rules 2000 rr.17-19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "dpd_substandard_min",
        "90",
        "days",
        "NBFI Rules 2000 rr.17-19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "dpd_doubtful_min",
        "180",
        "days",
        "NBFI Rules 2000 rr.17-19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "dpd_loss_min",
        "360",
        "days",
        "NBFI Rules 2000 rr.17-19",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "npl_dpd_threshold",
        "90",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "dpd_olem_min",
        "30",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "dpd_substandard_min",
        "90",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "dpd_doubtful_min",
        "180",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "dpd_loss_min",
        "360",
        "days",
        "BoG loan classification (5-grade)",
        "confirmed",
    ),
    # --- simplified SDI risk weights (value pending BoG) -------------------
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_sovereign",
        "0",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_cash",
        "0",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_interbank",
        "20",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_mortgage",
        "50",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_other_loans",
        "100",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "risk_weight_other_assets",
        "100",
        "percent",
        "SDI simplified risk weights (value pending BoG)",
        "pending",
    ),
    # --- Basel LCR HQLA haircuts + Level-2 caps (bank class only) ----------
    # The stock of HQLA was an unweighted face-value sum before 2026-08-21
    # (enterprise audit P0-8): no Level-2A haircut, no Level-2B haircut, no 40%
    # Level-2 cap, no 15% Level-2B sub-cap. These are the governed values the
    # pure engine now resolves through ``LiquidityParams``; nothing about a
    # haircut or a cap is written in the engine.
    #
    # LCR is a Basel measure and is bank-only under Act 930 / the SDI regime
    # (docs/sdi.md §4.6), so these are seeded for ``institution_class='bank'``
    # only — an SDI never runs ``compute_lcr``.
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_l1_haircut_pct",
        "0",
        "percent",
        "BCBS 238 (Basel III LCR) ¶50 — Level 1 assets carry no haircut",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_l2a_haircut_pct",
        "15",
        "percent",
        "BCBS 238 ¶52 — 15% haircut on every Level 2A asset",
        "confirmed",
    ),
    # Basel sets the Level 2B haircut BY SUB-CLASS: 25% for qualifying RMBS
    # (¶54(a)) and 50% for qualifying corporate debt and common equity
    # (¶54(b),(c)). The canonical fact model carries only an HQLA *level*, not an
    # L2B sub-class, so the platform applies the most conservative rate in the
    # range. That is a documented modelling choice, not a BoG-confirmed number —
    # it ships 'pending' so it is visible in the operator console and every
    # resolution is logged. Splitting L2B into sub-classes (and confirming the
    # per-sub-class rate) is the follow-on work.
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_l2b_haircut_pct",
        "50",
        "percent",
        "BCBS 238 ¶54(b),(c) — conservative bound of the 25-50% L2B range",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_level2_cap_pct",
        "40",
        "percent",
        "BCBS 238 ¶47 — Level 2 assets may not exceed 40% of the stock of HQLA",
        "confirmed",
    ),
    ParamSpec(
        "institution_class",
        "bank",
        "hqla_level2b_cap_pct",
        "15",
        "percent",
        "BCBS 238 ¶47 — Level 2B assets may not exceed 15% of the stock of HQLA",
        "confirmed",
    ),
    # --- data-integrity controls (enterprise audit 2026-08-20 P0-10) -------
    # The balance-sheet identity tolerance: |assets − (liabilities + equity)| as
    # a percent of total assets, above which the book may not produce a FILED
    # number (app/services/reconciliation.py). It is a supervisory-judgement
    # number, not a BoG-published one, so it ships 'pending' and is editable in
    # the operator console under four eyes. A tenant board override may only
    # TIGHTEN it (PARAMETER_DIRECTION: ceiling).
    ParamSpec(
        "institution_class",
        "bank",
        "balance_identity_tolerance_pct",
        "0.10",
        "percent",
        "AequorOS data-integrity control (value pending internal confirmation)",
        "pending",
    ),
    ParamSpec(
        "institution_class",
        "sdi",
        "balance_identity_tolerance_pct",
        "0.10",
        "percent",
        "AequorOS data-integrity control (value pending internal confirmation)",
        "pending",
    ),
    *_lmtd_specs(),
)

#: The HQLA parameter codes the LCR engine consumes, in the order the loaders
#: resolve them. Exported so the loaders, the seed and the tests name the same
#: set (``app/domain/liquidity/engine.py`` never names a rate).
HQLA_HAIRCUT_CODES: dict[str, str] = {
    "L1": "hqla_l1_haircut_pct",
    "L2A": "hqla_l2a_haircut_pct",
    "L2B": "hqla_l2b_haircut_pct",
}
HQLA_LEVEL2_CAP_CODE = "hqla_level2_cap_pct"
HQLA_LEVEL2B_CAP_CODE = "hqla_level2b_cap_pct"


@dataclass(frozen=True)
class ResolvedParameter:
    """A resolved regulatory number with its full audit provenance."""

    param_code: str
    value: Decimal | None
    value_json: dict | None
    unit: str
    source_citation: str
    confirmation_status: str
    scope_type: str
    scope_key: str
    jurisdiction_code: str
    effective_from: date
    parameter_id: str
    #: Which link of the chain supplied the value ('institution_type' |
    #: 'institution_class'). Defaulted so historic keyword construction still works.
    layer: str = ""
    #: Set when this value was used to clamp a weaker tenant board override.
    clamped_from: Decimal | None = None

    @property
    def is_pending(self) -> bool:
        return self.confirmation_status == "pending"

    @property
    def decimal(self) -> Decimal:
        if self.value is None:
            msg = f"Regulatory parameter {self.param_code!r} has no scalar value."
            raise ValueError(msg)
        return self.value

    @property
    def normalized_value(self) -> Decimal | None:
        """The scalar value with the trailing zeros a ``Numeric(18,6)`` round-trip
        adds stripped (Decimal("80.000000") -> Decimal("80")), so a value sourced
        from the control plane is byte-identical to an in-code constant when it
        lands in generated return content / a content digest. Integral values
        quantize to scale 0; fractional values normalise (12.500000 -> 12.5)
        without scientific notation for the percentage ranges in use."""
        if self.value is None:
            return None
        v = self.value
        return v.quantize(Decimal(1)) if v == v.to_integral_value() else v.normalize()

    def provenance(self) -> dict[str, object]:
        """The audit record for this resolution: which parameter, which version,
        what source, was it confirmed. Stable wire keys — the WS-A provenance
        struct integration point (mirrors
        ``app.domain.policy.PolicyResolution.provenance``)."""
        return {
            "param_code": self.param_code,
            "value": None if self.value is None else str(self.normalized_value),
            "unit": self.unit,
            "layer": self.layer or self.scope_type,
            "scope_type": self.scope_type,
            "scope_key": self.scope_key,
            "jurisdiction_code": self.jurisdiction_code,
            "effective_from": self.effective_from.isoformat(),
            "source_citation": self.source_citation,
            "confirmation_status": self.confirmation_status,
            "parameter_id": self.parameter_id,
            "clamped": self.clamped_from is not None,
            "clamped_from": None if self.clamped_from is None else str(self.clamped_from),
        }


class RegulatoryParameterError(LookupError):
    """A required regulatory parameter is not seeded for the tenant's scope.

    Carries WS-A's ``POLICY_UNRESOLVED`` outcome detail on ``.detail`` so a caller
    that persists fail-closed states against a run can record it, while
    ``str(exc)`` stays the plain message it has always been.
    """

    def __init__(self, message: str, detail: OutcomeDetail | None = None) -> None:
        super().__init__(message)
        self.detail: OutcomeDetail | None = detail


# The tighten-only rules (``PARAMETER_DIRECTION``, ``Direction``, ``tighten``,
# ``clamp_overrides``) moved to ``app/domain/policy/resolver.py`` on 2026-08-21 and
# are re-exported above. They were pure functions living in a database module, and
# keeping them here is what allowed two call sites to disagree about whether
# ``car_min`` was clamped at all. The DB-bound generalisation is
# :func:`clamp_overrides` further down.


def _active_row(  # noqa: PLR0913 - the resolution key is 5 explicit keyword parts
    db: Session,
    *,
    scope_type: str,
    scope_key: str,
    param_code: str,
    jurisdiction_code: str,
    as_of: date,
) -> RegulatoryParameter | None:
    """The newest APPROVED generation active on ``as_of`` for one scope.

    Active window mirrors ``params.get_active_params`` exactly (one date rule in
    the codebase): ``effective_from <= as_of`` and ``effective_to`` null or > as_of.
    """
    stmt = (
        select(RegulatoryParameter)
        .where(
            RegulatoryParameter.scope_type == scope_type,
            RegulatoryParameter.scope_key == scope_key,
            RegulatoryParameter.param_code == param_code,
            RegulatoryParameter.jurisdiction_code == jurisdiction_code,
            RegulatoryParameter.status == "approved",
            RegulatoryParameter.effective_from <= as_of,
            or_(
                RegulatoryParameter.effective_to.is_(None),
                RegulatoryParameter.effective_to > as_of,
            ),
        )
        .order_by(RegulatoryParameter.effective_from.desc(), RegulatoryParameter.id)
        .limit(1)
    )
    row = db.scalar(stmt)
    if row is not None:
        _record_consumption(db, row)
    return row


# --- governed-parameter row provenance (audit 2026-08-22 D-18) -------------
#
# A sealed ``RegulatoryRun`` recorded the parameter VALUES it consumed
# (``inputs["parameters"]``, covered by the value-based ``input_hash``) but never
# WHICH ROW supplied them, so "prove this filed ratio used the approved
# parameter" had no answer on the governance axis. ``_active_row`` above is the
# single place a governed row is read, so it is where consumption is recorded.
#
# The ledger is keyed by ``Session`` and DRAINED when a run is sealed
# (``consume_parameter_provenance``), which is what binds a row to the run that
# used it. It is deliberately over-inclusive rather than silent: it holds every
# governed row this session resolved since the previous run was sealed, so a
# read model resolving a parameter in the same session just before a run is
# minted contributes an entry. It can therefore name a row the engine did not
# arithmetically consume; it can never MISS one it did.
_CONSUMED: WeakKeyDictionary[Session, dict[str, dict[str, Any]]] = WeakKeyDictionary()


def parameter_row_provenance(row: RegulatoryParameter) -> dict[str, Any]:
    """The identity + authority of one control-plane row, JSON-ready.

    ``row_version`` is ``updated_at``: the control plane has no version column,
    and since ``202608230038`` an approved generation cannot be edited in place,
    so the pair (id, updated_at) pins exactly one immutable state of the row.
    """
    entry: dict[str, Any] = {
        "parameter_id": str(row.id),
        "param_code": row.param_code,
        "scope_type": row.scope_type,
        "scope_key": row.scope_key,
        "jurisdiction_code": row.jurisdiction_code,
        "unit": row.unit,
        "value": None if row.value_numeric is None else str(row.value_numeric),
        "source_citation": row.source_citation,
        "confirmation_status": row.confirmation_status,
        "status": row.status,
        "effective_from": row.effective_from.isoformat(),
        "effective_to": None if row.effective_to is None else row.effective_to.isoformat(),
        "proposed_by": row.proposed_by,
        "approved_by": row.approved_by,
        "approved_at": None if row.approved_at is None else row.approved_at.isoformat(),
        "row_version": None if row.updated_at is None else row.updated_at.isoformat(),
    }
    if row.value_json is not None:
        entry["value_json"] = row.value_json
    return entry


def _record_consumption(db: Session, row: RegulatoryParameter) -> None:
    _CONSUMED.setdefault(db, {})[str(row.id)] = parameter_row_provenance(row)


def consume_parameter_provenance(db: Session) -> list[dict[str, Any]]:
    """Take the rows resolved since the last run was sealed, and clear the ledger.

    Returns a deterministically ordered list. An EMPTY list is a positive
    statement — "this run resolved no governed parameter" — and is stored as
    such; ``None`` on a run means the run predates the column and is never
    written by this function.
    """
    recorded = _CONSUMED.pop(db, {})
    return sorted(
        recorded.values(),
        key=lambda entry: (
            entry["param_code"],
            entry["scope_type"],
            entry["scope_key"],
            entry["jurisdiction_code"],
            entry["effective_from"],
            entry["parameter_id"],
        ),
    )


def _to_resolved(row: RegulatoryParameter) -> ResolvedParameter:
    return ResolvedParameter(
        param_code=row.param_code,
        value=row.value_numeric,
        value_json=row.value_json,
        unit=row.unit,
        source_citation=row.source_citation,
        confirmation_status=row.confirmation_status,
        scope_type=row.scope_type,
        scope_key=row.scope_key,
        jurisdiction_code=row.jurisdiction_code,
        effective_from=row.effective_from,
        parameter_id=str(row.id),
        layer=row.scope_type,
    )


def resolve_class_value(
    db: Session,
    institution_class: str,
    param_code: str,
    *,
    jurisdiction: str,
    as_of: date | None = None,
) -> Decimal | None:
    """The class-keyed scalar value, independent of a specific bank. Used to
    surface the ENFORCED value on a display payload so it equals what the engine
    resolves (one source of truth) — e.g. the institution-type detail's exposure
    limits. Returns None when the class row is not seeded.

    ``jurisdiction`` is REQUIRED and has no default. It used to default to "GH",
    which meant a display payload for a Nigerian institution silently showed
    Ghana's enforced limits (enterprise audit 2026-08-20 §6). Pass the bank's own
    ``jurisdiction_code`` — ``jurisdictions.jurisdiction_code(bank)``.
    """
    row = _active_row(
        db,
        scope_type="institution_class",
        scope_key=institution_class,
        param_code=param_code,
        jurisdiction_code=jurisdiction,
        as_of=as_of or date.today(),
    )
    return _to_resolved(row).normalized_value if row is not None else None


def policy_scope(db: Session, bank: Bank, *, as_of: date | None = None) -> PolicyScope:
    """Build the full policy chain key for ``bank`` (FAIL CLOSED at every link).

    ``Jurisdiction -> Regulator -> Institution Type -> Regime -> Return Family
    -> Effective Date``. This is the ONE place the chain is assembled from a
    ``Bank``; engines and services call it (or the resolvers built on it) instead
    of re-deriving ``institution_class``/``jurisdiction_code`` themselves.

    Raises ``PolicyUnresolvedError`` when the jurisdiction is unset or unknown and
    ``InstitutionTypeUnresolved`` when the licence class does not resolve — no
    link is ever substituted.
    """
    jurisdiction_row = jurisdictions.require_jurisdiction(db, bank)
    type_row = institution_types.get_type(db, bank)
    return PolicyScope(
        jurisdiction_code=jurisdiction_row.code,
        currency=jurisdictions.base_currency(bank),
        regulator_short=jurisdiction_row.regulator_short,
        regulator_name=jurisdiction_row.central_bank_name,
        institution_type=type_row.type_code,
        institution_class=type_row.institution_class,
        capital_regime=type_row.capital_regime,
        return_family=type_row.return_family,
        liquidity_binding=bool(type_row.liquidity_binding),
        as_of=as_of or date.today(),
    )


def _observe_resolution(bank: Bank, resolved: ResolvedParameter) -> ResolvedParameter:
    """Record the observability event for a resolution and return it unchanged.

    A *pending* (unconfirmed) value driving a live regulatory calculation is the
    signal an operator must be able to alert on — a documented default is standing
    in for a not-yet-confirmed BoG number. Confirmed resolutions are not logged
    (they are the steady state and would drown the signal)."""
    if resolved.is_pending:
        logger.warning(
            "regulatory_parameter.pending_value_used code=%s scope=%s/%s value=%s unit=%s "
            "bank=%s org=%s citation=%r",
            resolved.param_code,
            resolved.scope_type,
            resolved.scope_key,
            resolved.value,
            resolved.unit,
            bank.id,
            bank.organization_id,
            resolved.source_citation,
        )
    return resolved


def try_resolve(
    db: Session, bank: Bank, param_code: str, *, as_of: date | None = None
) -> ResolvedParameter | None:
    """Resolve ``param_code`` for ``bank`` or return ``None`` if unseeded.

    Precedence is ``app.domain.policy.resolution_order``: the licence-specific
    (institution_type) row, then the coarse (institution_class) row. Use this for
    dormant/optional parameters (e.g. an aggregate-exposure cap that is inactive
    until a value is confirmed); use :func:`resolve` where the number is mandatory.

    The jurisdiction is resolved FAIL-CLOSED from the bank (no ``or "GH"``): an
    institution with no jurisdiction cannot have a parameter set selected for it.
    """
    scope = policy_scope(db, bank, as_of=as_of)
    for scope_type, scope_key in resolution_order(scope):
        row = _active_row(
            db,
            scope_type=scope_type,
            scope_key=scope_key,
            param_code=param_code,
            jurisdiction_code=scope.jurisdiction_code,
            as_of=scope.as_of,
        )
        if row is not None:
            return _observe_resolution(bank, _to_resolved(row))
    return None


def resolve(
    db: Session, bank: Bank, param_code: str, *, as_of: date | None = None
) -> ResolvedParameter:
    """Resolve a MANDATORY regulatory parameter for ``bank`` (fail-loud).

    Raises :class:`RegulatoryParameterError` when no approved row exists for the
    bank's institution_type or institution_class — a regulatory number is never
    substituted out of thin air (mirror of ``institution_types.get_type`` and
    ``jurisdictions.base_currency`` discipline).
    """
    resolved = try_resolve(db, bank, param_code, as_of=as_of)
    if resolved is None:
        scope = policy_scope(db, bank, as_of=as_of)
        logger.error(
            "regulatory_parameter.unseeded code=%s bank=%s org=%s scope=%s",
            param_code,
            bank.id,
            bank.organization_id,
            scope.describe(),
        )
        msg = (
            f"Regulatory parameter {param_code!r} is not seeded for bank {bank.id} "
            f"({scope.describe()}). It must exist in the regulatory-parameter control "
            "plane — configure it in the operator console."
        )
        raise RegulatoryParameterError(
            msg, policy_unresolved(param_code, scope, reason=msg, items=(f"param:{param_code}",))
        )
    return resolved


def resolve_decimal(
    db: Session, bank: Bank, param_code: str, *, as_of: date | None = None
) -> Decimal:
    """Convenience: the scalar value of a mandatory parameter."""
    return resolve(db, bank, param_code, as_of=as_of).decimal


def resolve_many(
    db: Session, bank: Bank, param_codes: list[str], *, as_of: date | None = None
) -> dict[str, ResolvedParameter]:
    """Resolve several mandatory parameters at once (all must exist)."""
    return {code: resolve(db, bank, code, as_of=as_of) for code in param_codes}


class HqlaParameters(NamedTuple):
    """The governed HQLA haircuts + Level-2 caps for one institution.

    Deliberately PARTIAL rather than fail-loud: a code with no approved,
    effective row is simply absent/``None`` here, and the pure engine then fails
    closed only if it actually binds — i.e. only if the bank really holds an
    asset at that level, or really holds a Level-2 asset whose cap is unresolved.
    A Level-1-only book is therefore never blocked on an unseeded Level-2B rate,
    and no missing rate is ever substituted with a zero.
    """

    haircut_pct: dict[str, Decimal]
    level2_cap_pct: Decimal | None
    level2b_cap_pct: Decimal | None

    @property
    def unresolved_codes(self) -> tuple[str, ...]:
        """The HQLA codes that did not resolve — for an operator-facing message."""
        missing = [
            code for level, code in HQLA_HAIRCUT_CODES.items() if level not in self.haircut_pct
        ]
        if self.level2_cap_pct is None:
            missing.append(HQLA_LEVEL2_CAP_CODE)
        if self.level2b_cap_pct is None:
            missing.append(HQLA_LEVEL2B_CAP_CODE)
        return tuple(missing)


def resolve_hqla_parameters(
    db: Session, bank: Bank, *, as_of: date | None = None
) -> HqlaParameters:
    """The Basel HQLA haircuts + Level-2 caps from the control plane.

    THE single seam through which a haircut or a cap reaches ``compute_lcr``
    (enterprise audit P0-8). The pure liquidity engine names no rate; it consumes
    what this returns via ``LiquidityParams`` and refuses to weight an asset whose
    rate is absent.
    """
    haircuts: dict[str, Decimal] = {}
    for level, code in HQLA_HAIRCUT_CODES.items():
        resolved = try_resolve(db, bank, code, as_of=as_of)
        if resolved is not None and resolved.value is not None:
            haircuts[level] = resolved.decimal
    cap2 = try_resolve(db, bank, HQLA_LEVEL2_CAP_CODE, as_of=as_of)
    cap2b = try_resolve(db, bank, HQLA_LEVEL2B_CAP_CODE, as_of=as_of)
    return HqlaParameters(
        haircut_pct=haircuts,
        level2_cap_pct=None if cap2 is None else cap2.value,
        level2b_cap_pct=None if cap2b is None else cap2b.value,
    )


def control_values(
    db: Session,
    bank: Bank,
    param_codes: Iterable[str],
    *,
    as_of: date | None = None,
) -> dict[str, Decimal | None]:
    """The governed value for each requested code, or ``None`` where unseeded.

    Never invents a value: a code with no approved, effective row for the bank's
    scope maps to ``None``, and :func:`clamp_overrides` then leaves the tenant
    value untouched rather than clamping against a fabricated floor.
    """
    resolved: dict[str, Decimal | None] = {}
    for code in param_codes:
        param = try_resolve(db, bank, code, as_of=as_of)
        resolved[code] = param.normalized_value if param is not None else None
    return resolved


def clamp_overrides(
    db: Session,
    bank: Bank,
    tenant_values: Mapping[str, Decimal],
    *,
    as_of: date | None = None,
) -> ClampReport:
    """Apply tighten-only enforcement to an ENTIRE tenant register in one call.

    THE generalised enforcement (QA audit 2026-08-20 P1-5). Every governed code
    present in ``tenant_values`` is clamped against its control-plane value; codes
    with no declared direction, and codes with no seeded governed value, pass
    through unchanged. Returns the effective values plus a record of each override
    that was weaker than the regulatory value.

    Loaders call this once with their whole threshold dict instead of clamping a
    single code by hand — the pattern that left 16 of the 25 governed codes
    unenforced and made ``regulatory_forecasting`` read ``car_min`` raw while
    ``regulatory_capital`` clamped it.
    """
    governed = {code: value for code, value in tenant_values.items() if code in governed_codes()}
    if not governed:
        return ClampReport(values=dict(tenant_values), clamped=())
    controls = control_values(db, bank, governed, as_of=as_of)
    report = _clamp_values(governed, controls)
    if report.clamped:
        logger.warning(
            "regulatory_parameter.tenant_override_clamped bank=%s org=%s codes=%s details=%s",
            bank.id,
            bank.organization_id,
            ",".join(report.codes_clamped()),
            [record.to_dict() for record in report.clamped],
        )
    merged = dict(tenant_values)
    merged.update(report.values)
    return ClampReport(values=merged, clamped=report.clamped)


def seed_rows(actor: str = SEED_ACTOR) -> list[dict[str, object]]:
    """The seed catalogue as insertable row dicts — shared by the migration and
    the hermetic-test seed so the two can never drift."""
    return [
        {
            "scope_type": spec.scope_type,
            "scope_key": spec.scope_key,
            "param_code": spec.param_code,
            "jurisdiction_code": "GH",
            "value_numeric": Decimal(spec.value),
            "value_json": None,
            "unit": spec.unit,
            "source_citation": spec.source_citation,
            "confirmation_status": spec.confirmation_status,
            "effective_from": SEED_EFFECTIVE_FROM,
            "effective_to": None,
            "status": "approved",
            "proposed_by": actor,
            "approved_by": actor,
        }
        for spec in SEED_PARAMETERS
    ]
