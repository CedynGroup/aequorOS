"""SDI simplified s.29 capital adequacy — the LIVE CAR the s.29 view shows.

Computed directly from canonical data (no Basel live-engine dependency, which is
why the bank capital dashboard cannot serve an SDI): Net Own Funds ÷ Risk-Weighted
Assets against the Act 930 s.29 floor. Additive to ``sdi_capital_checks`` (paid-up
+ statutory-reserve); all regulatory numbers come from the control plane
(``car_min`` floor + simplified ``risk_weight_*`` buckets), never hardcoded — a
pending (unconfirmed) risk weight is flagged, never silently assumed correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot, CanonicalReferenceRow
from app.services import regulatory_parameters as rp

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
# Canonical validation statuses that count as usable book data (matches
# fact_derivation / loan_classification; "warning" is accepted-with-warnings).
_INCLUDED = ("accepted", "warning")

# CET1 own-funds deduction tiers (subtract from NOF); everything else adds.
_DEDUCTION_TIERS = frozenset({"cet1_deduction", "at1_deduction", "tier2_deduction"})

# On-balance-sheet ASSET position type → the simplified SDI risk-weight bucket
# (control-plane ``risk_weight_<bucket>``). LOANs are the 100% "other loans" bucket;
# a mortgage sub-split (50%) is a later refinement once the product taxonomy is
# confirmed. Only assets bear risk weight — any position type NOT in this map
# (deposits, borrowings, derivatives, off-balance LCs/commitments, equity) is
# skipped, never swept into a 100% bucket: a liability is never risk-weighted.
_BUCKET_BY_TYPE = {
    "CASH": "cash",
    "SECURITY_HOLDING": "sovereign",
    "INTERBANK_PLACEMENT": "interbank",
    "LOAN": "other_loans",
    "OTHER_ASSET": "other_assets",
}


@dataclass(frozen=True)
class RiskWeightBand:
    bucket: str
    weight_pct: Decimal
    exposure_ghs: Decimal
    rwa_ghs: Decimal
    confirmation_status: str


@dataclass(frozen=True)
class SdiCapitalSummary:
    as_of: date
    net_own_funds_ghs: Decimal
    total_rwa_ghs: Decimal
    car_pct: Decimal | None
    car_min_pct: Decimal
    status: str  # green | red | na
    car_min_confirmation: str
    computable: bool
    bands: list[RiskWeightBand] = field(default_factory=list)
    pending_parameters: list[str] = field(default_factory=list)


def _dec(value: object) -> Decimal:
    if value is None or value == "":
        return _ZERO
    return Decimal(str(value))


def _net_own_funds(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> Decimal:
    """Signed sum of the ingested ``capital_structure`` components — deduction
    tiers subtract, everything else adds — i.e. Net Own Funds. (``sdi_capital_checks
    .capital_components`` abs-es amounts for its per-component floor checks, so it is
    not reusable for a signed total.)"""
    scope = (
        CanonicalReferenceRow.organization_id == ctx.organization_id,
        CanonicalReferenceRow.bank_id == bank.id,
        CanonicalReferenceRow.dataset_kind == "capital_structure",
    )
    latest = db.scalar(
        select(func.max(CanonicalReferenceRow.as_of_date)).where(
            *scope, CanonicalReferenceRow.as_of_date <= as_of
        )
    )
    if latest is None:
        return _ZERO
    batch = db.scalar(
        select(CanonicalReferenceRow.ingestion_batch_id)
        .where(*scope, CanonicalReferenceRow.as_of_date == latest)
        .order_by(CanonicalReferenceRow.created_at.desc(), CanonicalReferenceRow.id.desc())
        .limit(1)
    )
    nof = _ZERO
    for row in db.scalars(
        select(CanonicalReferenceRow).where(
            *scope,
            CanonicalReferenceRow.as_of_date == latest,
            CanonicalReferenceRow.ingestion_batch_id == batch,
        )
    ):
        payload = row.payload or {}
        amount = _dec(payload.get("amount_ghs"))
        tier = str(payload.get("tier", "")).strip().lower()
        # A negative amount already encodes a deduction; a deduction tier flips a
        # positive magnitude. Guard against double-negation.
        signed = -abs(amount) if tier in _DEDUCTION_TIERS else amount
        nof += signed
    return nof


def _exposure_by_bucket(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> dict[str, Decimal]:
    """GHS exposure (``attributes.balance_ghs`` else base ``balance``) of the
    current-generation asset positions at ``as_of``, grouped by risk-weight bucket."""
    rows = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED),
        )
    ).all()
    totals: dict[str, Decimal] = {}
    for snapshot, position in rows:
        bucket = _BUCKET_BY_TYPE.get(position.position_type)
        if bucket is None:
            continue  # not an on-balance-sheet asset — never risk-weighted
        attrs = snapshot.attributes or {}
        exposure = attrs.get("balance_ghs")
        amount = _dec(exposure) if exposure is not None else _dec(snapshot.balance)
        totals[bucket] = totals.get(bucket, _ZERO) + amount
    return totals


def compute_sdi_capital_summary(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> SdiCapitalSummary:
    """The s.29 capital-adequacy summary: NOF ÷ RWA vs the s.29 floor."""
    car_min_param = rp.resolve(db, bank, "car_min", as_of=as_of)
    car_min = car_min_param.normalized_value or _ZERO

    nof = _net_own_funds(db, ctx, bank, as_of)
    exposures = _exposure_by_bucket(db, ctx, bank, as_of)

    bands: list[RiskWeightBand] = []
    pending: list[str] = []
    total_rwa = _ZERO
    for bucket in sorted(exposures):
        code = f"risk_weight_{bucket}"
        resolved = rp.try_resolve(db, bank, code, as_of=as_of)
        if resolved is None or resolved.value is None:
            # No weight configured — conservatively 100% and note the gap.
            weight = _HUNDRED
            status = "pending"
            pending.append(code)
        else:
            weight = resolved.normalized_value or _ZERO
            status = resolved.confirmation_status
            if resolved.is_pending:
                pending.append(code)
        exposure = exposures[bucket]
        rwa = exposure * weight / _HUNDRED
        total_rwa += rwa
        bands.append(RiskWeightBand(bucket, weight, exposure, rwa, status))

    computable = nof != _ZERO and total_rwa > _ZERO
    car = (nof / total_rwa * _HUNDRED) if total_rwa > _ZERO else None
    if car is None:
        status = "na"
    elif car >= car_min:
        status = "green"
    else:
        status = "red"

    return SdiCapitalSummary(
        as_of=as_of,
        net_own_funds_ghs=nof,
        total_rwa_ghs=total_rwa,
        car_pct=(car.quantize(Decimal("0.01")) if car is not None else None),
        car_min_pct=car_min,
        status=status,
        car_min_confirmation=car_min_param.confirmation_status,
        computable=computable,
        bands=bands,
        pending_parameters=sorted(set(pending)),
    )
