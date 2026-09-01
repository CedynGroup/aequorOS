"""Loan classification + provisioning service (docs/sdi.md §2.2, §4, §7 Phase G).

Thin orchestration over the pure ``app.domain.capital.loan_classification``
engine: resolve the tenant's institution class, build the class grid from the
regulatory-parameter control plane (every DPD boundary and provisioning rate
comes from ``regulatory_parameters.resolve`` — never a literal), load the bank's
current-generation LOAN snapshots for the period end, run the engine, and return
the classified book with its parameter provenance (which grid, each rate's/
boundary's confirmation status, and whether any is still pending BoG
confirmation).

The loader mirrors the canonical-position slice ``fact_derivation`` and
``le_generation._load_canonical_rows`` read: the current (non-superseded)
generation with accepted/warning validation status, at the requested as-of. The
``days_past_due`` snapshot attribute — documented in docs/API_INTEGRATION.md §5A
but read by no return cell today — is the delinquency signal; a loan without it
falls back to the IFRS 9 stage in the pure engine, never to "performing".

Fail-loud: an unseeded required parameter raises ``RegulatoryParameterError``
straight out of the resolver — a regulatory number is never invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.capital import loan_classification as engine
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot
from app.services import institution_types, jurisdictions
from app.services import regulatory_parameters as rp

# Mirrors fact_derivation / le_generation: the derivation slice is the current
# (non-superseded) generation with accepted/warning status.
_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")
_LOAN_POSITION_TYPE = "LOAN"
_ZERO = Decimal("0")


@dataclass(frozen=True)
class ParameterProvenance:
    """Provenance for one resolved control-plane parameter."""

    param_code: str
    value: Decimal
    unit: str
    confirmation_status: str
    source_citation: str
    is_pending: bool


@dataclass(frozen=True)
class DelinquencyBucket:
    """An analytical raw-DPD rollup, distinct from a regulatory grade."""

    code: str
    label: str
    count: int
    exposure_ghs: Decimal


@dataclass(frozen=True)
class PortfolioAtRisk:
    """Gross-loan exposure at or beyond an exact DPD threshold."""

    code: str
    label: str
    exposure_ghs: Decimal
    ratio: Decimal


@dataclass(frozen=True)
class ProvisionsHeld:
    """Provisions the bank actually HOLDS, split by the applied classification.

    Built only when at least one loan STATES ``ecl_provision_ghs`` — a book with
    no stated provisions yields ``None`` on the report, and coverage is reported
    unavailable rather than 0% (absent is not zero). ``specific_ghs`` is the
    held provision on loans the grid classified non-performing; splitting by the
    APPLIED grade keeps the coverage ratio's numerator and denominator on the
    same classification, even where an ingested ``bog_classification`` disagrees
    with the DPD-derived grade (the fact plane's ``provision_held`` split uses
    the ingested view — a divergence between them is a data-quality signal).
    """

    specific_ghs: Decimal
    general_ghs: Decimal
    interest_in_suspense_ghs: Decimal
    #: Loans that stated a provision amount (coverage disclosure).
    stated_loan_count: int

    @property
    def total_ghs(self) -> Decimal:
        return self.specific_ghs + self.general_ghs


@dataclass(frozen=True)
class _DpdExposure:
    exposure_ghs: Decimal
    days_past_due: int


# These are analytical portfolio-at-risk bands, not regulatory classification
# boundaries. The latter are always resolved from the control plane.
_DPD_BANDS: tuple[tuple[str, str, int, int | None], ...] = (
    ("current", "Current", 0, 0),
    ("1_29", "1–29 days", 1, 29),
    ("30_59", "30–59 days", 30, 59),
    ("60_89", "60–89 days", 60, 89),
    ("90_179", "90–179 days", 90, 179),
    ("180_359", "180–359 days", 180, 359),
    ("360_plus", "360+ days", 360, None),
)
_PAR_THRESHOLDS: tuple[tuple[str, str, int], ...] = (
    ("par_30", "PAR 30+", 30),
    ("par_60", "PAR 60+", 60),
    ("par_90", "PAR 90+", 90),
    ("par_180", "PAR 180+", 180),
    ("par_360", "PAR 360+", 360),
)


@dataclass(frozen=True)
class LoanClassificationReport:
    """The classified loan book plus its resolution provenance."""

    institution_class: str
    grid: engine.ClassificationGrid
    result: engine.ClassificationResult
    as_of: date
    loan_count: int
    #: Loans whose foreign-currency balance had no ingested GHS conversion and so
    #: contributed zero exposure (mirrors the fact pipeline — nothing converted
    #: at a made-up rate).
    unconverted_count: int
    #: Loans classified via the IFRS 9 stage proxy (no stated days-past-due).
    stage_proxy_count: int
    #: Loans with neither a DPD nor a stage — booked ``unclassified``, never
    #: performing.
    unclassified_count: int
    #: Exact-DPD coverage. Stage-proxy loans are deliberately excluded.
    dpd_covered_count: int
    dpd_covered_exposure_ghs: Decimal
    delinquency_buckets: tuple[DelinquencyBucket, ...]
    portfolio_at_risk: tuple[PortfolioAtRisk, ...]
    parameters: tuple[ParameterProvenance, ...]
    #: Param codes whose value is still pending BoG/internal confirmation.
    pending_parameters: tuple[str, ...]
    #: Provisions HELD (stated on the book), or ``None`` when no loan states one.
    provisions_held: ProvisionsHeld | None = None
    #: Specific provisions held ÷ NPL exposure, as a percentage. ``None`` when
    #: provisions are unstated OR the book has no NPL exposure to cover.
    provision_coverage_pct: Decimal | None = None

    @property
    def has_pending_parameters(self) -> bool:
        return bool(self.pending_parameters)


def _coerce_dpd(value: Any) -> int | None:
    """Read ``days_past_due`` from a snapshot attribute (int or stringified).

    The push path stores it as an int, the spreadsheet path as a string; a
    blank/absent/unparseable value is treated as *not stated* (None) — which the
    pure engine resolves via the stage proxy, never as performing. A negative
    day count is also treated as not stated (a data error, not "current").
    """
    if value is None or value == "":
        return None
    try:
        days = int(Decimal(str(value).strip()))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return days if days >= 0 else None


def _load_loan_exposures(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[list[engine.LoanExposure], int, list[tuple[Decimal | None, Decimal | None]]]:
    """Current-generation LOAN exposures for ``as_of`` (+ unconverted count).

    Exposure is the GHS balance: ``attributes.balance_ghs`` when present, else
    the base-currency ``balance`` for a base-currency loan, else zero for a
    foreign-currency loan without an ingested conversion (counted, flagged).

    The third element is position-aligned ``(ecl_provision_ghs,
    interest_in_suspense_ghs)`` — each ``None`` when the loan does not state it,
    so the caller can tell an unstated provision from a stated zero.
    """
    records = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type == _LOAN_POSITION_TYPE,
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).all()

    base_currency = jurisdictions.base_currency(bank)
    exposures: list[engine.LoanExposure] = []
    provisions: list[tuple[Decimal | None, Decimal | None]] = []
    unconverted = 0
    for snapshot, position in records:
        attributes = snapshot.attributes or {}
        balance_ghs = _dec_or_none(attributes.get("balance_ghs"))
        if balance_ghs is None:
            if position.currency == base_currency:
                balance_ghs = Decimal(str(snapshot.balance or _ZERO))
            else:
                balance_ghs = _ZERO
                unconverted += 1
        exposures.append(
            engine.LoanExposure(
                exposure_ghs=balance_ghs,
                days_past_due=_coerce_dpd(attributes.get("days_past_due")),
                ifrs9_stage=snapshot.ifrs9_stage,
            )
        )
        provisions.append(
            (
                _dec_or_none(attributes.get("ecl_provision_ghs")),
                _dec_or_none(attributes.get("interest_in_suspense_ghs")),
            )
        )
    return exposures, unconverted, provisions


def _dec_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _load_raw_dpd_exposures(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[_DpdExposure]:
    """Load exact DPD observations without converting stage proxies into DPD."""
    records = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type == _LOAN_POSITION_TYPE,
        )
    ).all()
    base_currency = jurisdictions.base_currency(bank)
    exposures: list[_DpdExposure] = []
    for snapshot, position in records:
        days_past_due = _coerce_dpd((snapshot.attributes or {}).get("days_past_due"))
        if days_past_due is None:
            continue
        balance_ghs = _dec_or_none((snapshot.attributes or {}).get("balance_ghs"))
        if balance_ghs is None:
            balance_ghs = (
                Decimal(str(snapshot.balance or _ZERO))
                if position.currency == base_currency
                else _ZERO
            )
        exposures.append(_DpdExposure(exposure_ghs=balance_ghs, days_past_due=days_past_due))
    return exposures


def _delinquency_buckets(exposures: list[_DpdExposure]) -> tuple[DelinquencyBucket, ...]:
    buckets: list[DelinquencyBucket] = []
    for code, label, minimum, maximum in _DPD_BANDS:
        matching = [
            exposure
            for exposure in exposures
            if exposure.days_past_due >= minimum
            and (maximum is None or exposure.days_past_due <= maximum)
        ]
        buckets.append(
            DelinquencyBucket(
                code=code,
                label=label,
                count=len(matching),
                exposure_ghs=sum((exposure.exposure_ghs for exposure in matching), _ZERO),
            )
        )
    return tuple(buckets)


def _portfolio_at_risk(
    exposures: list[_DpdExposure], total_loan_exposure: Decimal
) -> tuple[PortfolioAtRisk, ...]:
    metrics: list[PortfolioAtRisk] = []
    for code, label, minimum in _PAR_THRESHOLDS:
        exposure_ghs = sum(
            (exposure.exposure_ghs for exposure in exposures if exposure.days_past_due >= minimum),
            _ZERO,
        )
        metrics.append(
            PortfolioAtRisk(
                code=code,
                label=label,
                exposure_ghs=exposure_ghs,
                ratio=exposure_ghs / total_loan_exposure if total_loan_exposure > _ZERO else _ZERO,
            )
        )
    return tuple(metrics)


def _resolve_parameters(
    db: Session, bank: Bank, institution_class: str, as_of: date
) -> tuple[dict[str, Decimal], tuple[ParameterProvenance, ...]]:
    """Resolve every parameter the class grid needs (fail-loud if unseeded)."""
    codes = engine.param_codes_for_class(institution_class)
    values: dict[str, Decimal] = {}
    provenance: list[ParameterProvenance] = []
    for code in codes:
        resolved = rp.resolve(db, bank, code, as_of=as_of)
        values[code] = resolved.decimal
        provenance.append(
            ParameterProvenance(
                param_code=code,
                value=resolved.decimal,
                unit=resolved.unit,
                confirmation_status=resolved.confirmation_status,
                source_citation=resolved.source_citation,
                is_pending=resolved.is_pending,
            )
        )
    return values, tuple(provenance)


def _provisions_held(
    loans: tuple[engine.ClassifiedLoan, ...],
    provisions: list[tuple[Decimal | None, Decimal | None]],
) -> ProvisionsHeld | None:
    """Roll up stated provisions, split by the APPLIED classification."""
    specific = _ZERO
    general = _ZERO
    suspense = _ZERO
    stated = 0
    any_value = False
    for loan, (provision, interest) in zip(loans, provisions, strict=True):
        if provision is not None:
            stated += 1
            any_value = True
            if loan.non_performing:
                specific += provision
            else:
                general += provision
        if interest is not None:
            any_value = True
            suspense += interest
    if not any_value:
        return None
    return ProvisionsHeld(
        specific_ghs=specific,
        general_ghs=general,
        interest_in_suspense_ghs=suspense,
        stated_loan_count=stated,
    )


def classify_loan_book(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> LoanClassificationReport:
    """Classify ``bank``'s LOAN book at ``as_of`` against its class grid.

    Resolves the institution class, builds the class-aware grid from the
    control plane, loads the current-generation LOAN snapshots, runs the pure
    engine, and returns the classified book with full parameter provenance.
    """
    institution_class = institution_types.institution_class(db, bank)
    values, provenance = _resolve_parameters(db, bank, institution_class, as_of)
    grid = engine.grid_from_params(institution_class, values)

    exposures, unconverted, provisions = _load_loan_exposures(db, ctx, bank, as_of)
    result = engine.classify_book(exposures, grid)
    raw_dpd_exposures = _load_raw_dpd_exposures(db, ctx, bank, as_of)
    held = _provisions_held(result.loans, provisions)
    coverage: Decimal | None = None
    if held is not None and result.npl_exposure_ghs > _ZERO:
        coverage = held.specific_ghs / result.npl_exposure_ghs * Decimal("100")

    pending = tuple(item.param_code for item in provenance if item.is_pending)
    return LoanClassificationReport(
        institution_class=institution_class,
        grid=grid,
        result=result,
        as_of=as_of,
        loan_count=len(exposures),
        unconverted_count=unconverted,
        stage_proxy_count=result.stage_proxy_count,
        unclassified_count=result.unclassified_count,
        dpd_covered_count=len(raw_dpd_exposures),
        dpd_covered_exposure_ghs=sum(
            (exposure.exposure_ghs for exposure in raw_dpd_exposures), _ZERO
        ),
        delinquency_buckets=_delinquency_buckets(raw_dpd_exposures),
        portfolio_at_risk=_portfolio_at_risk(raw_dpd_exposures, result.total_exposure_ghs),
        parameters=provenance,
        pending_parameters=pending,
        provisions_held=held,
        provision_coverage_pct=coverage,
    )
