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
bank read is preserved. (The board override tightening the regulatory floor is a
governance expectation, not yet a hard clamp enforced here — a future
enhancement.) This module owns the **global default** layer: the licence-specific
(``institution_type``) row wins over the coarse (``institution_class``) row, and an
unseeded required code raises — a regulatory number is never invented.

``SEED_PARAMETERS`` is the single authoritative catalogue used by BOTH the seed
migration and the hermetic-test seed, so the two never drift.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Bank, RegulatoryParameter
from app.services import institution_types

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
        "Basel CRD (10% + 3% CCB)",
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
    *_lmtd_specs(),
)


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


class RegulatoryParameterError(LookupError):
    """A required regulatory parameter is not seeded for the tenant's scope."""


#: The conservative direction of a governed regulatory value, so a tenant board
#: override can only TIGHTEN it, never weaken it (docs/sdi.md §7; QA audit
#: 2026-08-20 P1-5). ``floor`` → a minimum the override must be at least as high as
#: (effective = max); ``ceiling`` → a maximum/limit the override must be at most
#: (effective = min). A code absent here carries no tightening constraint.
PARAMETER_DIRECTION: dict[str, str] = {
    # capital + leverage minima
    "car_min": "floor",
    "cet1_min": "floor",
    "tier1_min": "floor",
    "leverage_min": "floor",
    # liquidity minima
    "lcr_min": "floor",
    "nsfr_min": "floor",
    "primary_liquidity_reserve_pct": "floor",
    "secondary_liquidity_reserve_pct": "floor",
    "statutory_reserve_fund_pct": "floor",
    # exposure limits (a board may only make them stricter, i.e. lower)
    "single_obligor_limit_pct": "ceiling",
    "large_exposure_limit_pct": "ceiling",
    "related_party_limit_pct": "ceiling",
    # provisioning minima (a board may only over-provide)
    "prov_standard": "floor",
    "prov_olem": "floor",
    "prov_substandard": "floor",
    "prov_doubtful": "floor",
    "prov_loss": "floor",
    # LMTD prudential-ratio floors
    "narrow_to_volatile": "floor",
    "broad_to_volatile": "floor",
    "narrow_to_short_term": "floor",
    "broad_to_short_term": "floor",
    "narrow_to_total_assets": "floor",
    "broad_to_total_assets": "floor",
    "narrow_to_total_deposits": "floor",
    "broad_to_total_deposits": "floor",
}


def tighten(param_code: str, tenant_value: Decimal, control_value: Decimal) -> Decimal:
    """The more conservative of a tenant board override and the control-plane
    regulatory value — the guard that a per-tenant register can only tighten a
    regulatory floor/limit, never weaken it (docs/sdi.md §7; QA audit 2026-08-20
    P1-5). A ``floor`` code returns the higher value, a ``ceiling`` code the lower;
    a code with no declared direction returns the tenant value unchanged (the
    override is unconstrained)."""
    direction = PARAMETER_DIRECTION.get(param_code)
    if direction == "floor":
        return max(tenant_value, control_value)
    if direction == "ceiling":
        return min(tenant_value, control_value)
    return tenant_value


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
    return db.scalar(stmt)


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
    )


def resolve_class_value(
    db: Session,
    institution_class: str,
    param_code: str,
    *,
    jurisdiction: str = "GH",
    as_of: date | None = None,
) -> Decimal | None:
    """The class-keyed scalar value, independent of a specific bank. Used to
    surface the ENFORCED value on a display payload so it equals what the engine
    resolves (one source of truth) — e.g. the institution-type detail's exposure
    limits. Returns None when the class row is not seeded."""
    row = _active_row(
        db,
        scope_type="institution_class",
        scope_key=institution_class,
        param_code=param_code,
        jurisdiction_code=jurisdiction,
        as_of=as_of or date.today(),
    )
    return _to_resolved(row).normalized_value if row is not None else None


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

    Precedence: the licence-specific (institution_type) row, then the coarse
    (institution_class) row. Use this for dormant/optional parameters (e.g. an
    aggregate-exposure cap that is inactive until a value is confirmed); use
    :func:`resolve` where the number is mandatory.
    """
    when = as_of or date.today()
    jurisdiction = (bank.jurisdiction_code or "GH").strip() or "GH"

    type_code = (bank.institution_type or "").strip()
    if type_code:
        row = _active_row(
            db,
            scope_type="institution_type",
            scope_key=type_code,
            param_code=param_code,
            jurisdiction_code=jurisdiction,
            as_of=when,
        )
        if row is not None:
            return _observe_resolution(bank, _to_resolved(row))

    klass = institution_types.institution_class(db, bank)
    row = _active_row(
        db,
        scope_type="institution_class",
        scope_key=klass,
        param_code=param_code,
        jurisdiction_code=jurisdiction,
        as_of=when,
    )
    return _observe_resolution(bank, _to_resolved(row)) if row is not None else None


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
        klass = institution_types.institution_class(db, bank)
        logger.error(
            "regulatory_parameter.unseeded code=%s bank=%s org=%s institution_type=%s "
            "institution_class=%s jurisdiction=%s",
            param_code,
            bank.id,
            bank.organization_id,
            bank.institution_type,
            klass,
            bank.jurisdiction_code,
        )
        msg = (
            f"Regulatory parameter {param_code!r} is not seeded for bank {bank.id} "
            f"(institution_type={bank.institution_type!r}, institution_class={klass!r}, "
            f"jurisdiction={bank.jurisdiction_code!r}). It must exist in the regulatory-"
            "parameter control plane — configure it in the operator console."
        )
        raise RegulatoryParameterError(msg)
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
