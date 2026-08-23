"""Read models for SDI tenant dashboards.

These views adapt the authoritative LMTD and exposure helpers into compact,
typed dashboard data. They do not recalculate regulatory definitions in the UI.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.services import regulatory_parameters, sdi_capital
from app.services.regulatory_reporting.le_generation import (
    _BUCKET_CODES,
    _LE_POSITION_TYPES,
    _TABLE1_RATIOS,
    _TABLE2_BUCKETS,
    _TABLE2_POSITION_TYPES,
    _aggregate_entities,
    _CanonicalRow,
    _funding_entities,
    _haircut_for,
    _haircut_schedule,
    _is_bog_eligible_security,
    _is_sovereign_security,
    _ladder_section,
    _load_canonical_rows,
    _related_party_names,
    _table1_inputs,
    _table1_thresholds,
    _unencumbered,
)

_ZERO = Decimal("0")


@dataclass(frozen=True)
class LiquidityRatio:
    code: str
    label: str
    value_pct: Decimal | None
    threshold_pct: Decimal
    status: str
    threshold_source: str


@dataclass(frozen=True)
class LiquidityReserve:
    code: str
    label: str
    value_pct: Decimal | None
    threshold_pct: Decimal
    status: str
    source_citation: str
    confirmation_status: str


@dataclass(frozen=True)
class MaturityBucket:
    code: str
    label: str
    net_mismatch_ghs: Decimal
    cumulative_mismatch_ghs: Decimal | None


@dataclass(frozen=True)
class FundingProvider:
    name: str
    deposit_ghs: Decimal
    pct_total_deposits: Decimal | None
    related: bool


@dataclass(frozen=True)
class FundingConcentration:
    total_deposits_ghs: Decimal
    top_five_deposits_ghs: Decimal
    top_five_pct: Decimal | None
    unattributed_deposits_ghs: Decimal
    providers: list[FundingProvider]


@dataclass(frozen=True)
class CounterbalancingCapacity:
    gross_unencumbered_ghs: Decimal
    monetized_value_ghs: Decimal
    bog_eligible_ghs: Decimal
    uncalibrated_asset_count: int


@dataclass(frozen=True)
class LiquidityMonitoring:
    as_of: date
    maturity_ladder: list[MaturityBucket]
    funding_concentration: FundingConcentration
    counterbalancing_capacity: CounterbalancingCapacity


@dataclass(frozen=True)
class SdiLiquidityPosition:
    as_of: date
    ratios: list[LiquidityRatio]
    reserves: list[LiquidityReserve]
    maturity_ladder: list[MaturityBucket]
    funding_concentration: FundingConcentration
    counterbalancing_capacity: CounterbalancingCapacity


@dataclass(frozen=True)
class Exposure:
    counterparty_name: str
    connection: str
    exposure_ghs: Decimal
    pct_net_own_funds: Decimal | None
    single_obligor_limit_pct: Decimal
    large_exposure_limit_pct: Decimal
    status: str
    exempt: bool


@dataclass(frozen=True)
class SdiLargeExposures:
    as_of: date
    net_own_funds_ghs: Decimal
    exposures: list[Exposure]
    findings: list[str]


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= _ZERO:
        return None
    return numerator * Decimal("100") / denominator


def _reserve_rows(
    db: Session,
    bank: Bank,
    as_of: date,
    rows: Sequence[_CanonicalRow],
    total_deposits: Decimal,
) -> list[LiquidityReserve]:
    primary_parameter = regulatory_parameters.resolve(
        db, bank, "primary_liquidity_reserve_pct", as_of=as_of
    )
    secondary_parameter = regulatory_parameters.resolve(
        db, bank, "secondary_liquidity_reserve_pct", as_of=as_of
    )
    primary = sum(
        (
            row.balance_ghs
            for row in rows
            if row.position_type == "CASH"
            and (row.counterparty_type is None or row.counterparty_type == "CENTRAL_BANK")
        ),
        _ZERO,
    )
    secondary = sum(
        (
            row.balance_ghs
            for row in rows
            if row.position_type == "SECURITY_HOLDING"
            and _is_sovereign_security(row)
            and row.encumbered is not True
        ),
        _ZERO,
    )

    def reserve(
        code: str,
        label: str,
        amount: Decimal,
        parameter: regulatory_parameters.ResolvedParameter,
    ) -> LiquidityReserve:
        value = _pct(amount, total_deposits)
        return LiquidityReserve(
            code=code,
            label=label,
            value_pct=value,
            threshold_pct=parameter.decimal,
            status="not_computable"
            if value is None
            else "below_minimum"
            if value < parameter.decimal
            else "ok",
            source_citation=parameter.source_citation,
            confirmation_status=parameter.confirmation_status,
        )

    return [
        reserve(
            "primary_liquidity_reserve",
            "Primary liquidity reserve",
            primary,
            primary_parameter,
        ),
        reserve(
            "secondary_liquidity_reserve",
            "Primary plus secondary liquidity reserve",
            primary + secondary,
            secondary_parameter,
        ),
    ]


def _funding_concentration(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    rows: list[_CanonicalRow],
    total_deposits: Decimal,
) -> FundingConcentration:
    entities, unattributed, _ = _funding_entities(rows, _related_party_names(db, ctx, bank))
    ranked = sorted(
        (entity for entity in entities.values() if entity.deposits > _ZERO),
        key=lambda entity: (-entity.deposits, entity.name),
    )
    providers = ranked[:10]
    top_five = sum((entity.deposits for entity in providers[:5]), _ZERO)
    return FundingConcentration(
        total_deposits_ghs=total_deposits,
        top_five_deposits_ghs=top_five,
        top_five_pct=_pct(top_five, total_deposits),
        unattributed_deposits_ghs=unattributed,
        providers=[
            FundingProvider(
                name=entity.name,
                deposit_ghs=entity.deposits,
                pct_total_deposits=_pct(entity.deposits, total_deposits),
                related=entity.related,
            )
            for entity in providers
        ],
    )


def _counterbalancing_capacity(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    rows: list[_CanonicalRow],
) -> CounterbalancingCapacity:
    schedule = _haircut_schedule(db, ctx, bank, as_of)
    securities = [
        row
        for row in rows
        if row.position_type == "SECURITY_HOLDING" and _unencumbered(row)
    ]
    gross = sum((row.balance_ghs for row in securities), _ZERO)
    monetized = _ZERO
    bog_eligible = _ZERO
    uncalibrated = 0
    for row in securities:
        haircut = _haircut_for(row.regulatory_category, schedule)
        if haircut is None:
            uncalibrated += 1
            haircut = _ZERO
        monetized += row.balance_ghs * (Decimal("100") - haircut) / Decimal("100")
        if _is_bog_eligible_security(row):
            bog_eligible += row.balance_ghs
    return CounterbalancingCapacity(
        gross_unencumbered_ghs=gross,
        monetized_value_ghs=monetized,
        bog_eligible_ghs=bog_eligible,
        uncalibrated_asset_count=uncalibrated,
    )


def _maturity_ladder(rows: list[_CanonicalRow], as_of: date) -> list[MaturityBucket]:
    ladder_section, _ = _ladder_section(rows, as_of)
    ladder_by_code = {row["code"]: row for row in ladder_section["rows"]}
    mismatch = ladder_by_code.get("10", {})
    cumulative = ladder_by_code.get("11", {})
    bucket_labels = {code: label for code, label, _ in _TABLE2_BUCKETS}
    return [
        MaturityBucket(
            code=code,
            label=bucket_labels[code],
            net_mismatch_ghs=Decimal(str(mismatch.get(code, "0"))),
            cumulative_mismatch_ghs=Decimal(str(cumulative[code])) if code in cumulative else None,
        )
        for code in _BUCKET_CODES
    ]


def get_liquidity_monitoring(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> LiquidityMonitoring:
    """Common canonical-book liquidity analytics for every institution class."""
    rows = _load_canonical_rows(db, ctx, bank, as_of, _TABLE2_POSITION_TYPES)
    inputs = _table1_inputs(rows, as_of)
    return LiquidityMonitoring(
        as_of=as_of,
        maturity_ladder=_maturity_ladder(rows, as_of),
        funding_concentration=_funding_concentration(
            db, ctx, bank, rows, inputs["total_deposits"]
        ),
        counterbalancing_capacity=_counterbalancing_capacity(db, ctx, bank, as_of, rows),
    )


def get_sdi_liquidity_position(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> SdiLiquidityPosition:
    rows = _load_canonical_rows(db, ctx, bank, as_of, _TABLE2_POSITION_TYPES)
    inputs = _table1_inputs(rows, as_of)
    thresholds = _table1_thresholds(db, ctx, bank, as_of)
    ratios: list[LiquidityRatio] = []
    for code, label, numerator_key, denominator_key in _TABLE1_RATIOS:
        threshold, source = thresholds[code]
        value = _pct(inputs[numerator_key], inputs[denominator_key])
        ratios.append(
            LiquidityRatio(
                code=code,
                label=label,
                value_pct=value,
                threshold_pct=threshold,
                status="not_computable"
                if value is None
                else "below_minimum"
                if value < threshold
                else "ok",
                threshold_source=source,
            )
        )

    common = get_liquidity_monitoring(db, ctx, bank, as_of)
    return SdiLiquidityPosition(
        as_of=as_of,
        ratios=ratios,
        reserves=_reserve_rows(db, bank, as_of, rows, inputs["total_deposits"]),
        maturity_ladder=common.maturity_ladder,
        funding_concentration=common.funding_concentration,
        counterbalancing_capacity=common.counterbalancing_capacity,
    )


def get_sdi_large_exposures(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> SdiLargeExposures:
    summary = sdi_capital.compute_sdi_capital_summary(db, ctx, bank, as_of)
    net_own_funds = summary.net_own_funds_ghs
    single_obligor = regulatory_parameters.resolve(
        db, bank, "single_obligor_limit_pct", as_of=as_of
    )
    large_exposure = regulatory_parameters.resolve(
        db, bank, "large_exposure_limit_pct", as_of=as_of
    )
    rows = _load_canonical_rows(db, ctx, bank, as_of, _LE_POSITION_TYPES)
    entities, unattributed_ghs, unattributed_count = _aggregate_entities(rows)
    exposures: list[Exposure] = []
    findings: list[str] = []
    for entity in entities:
        pct = _pct(entity.total, net_own_funds)
        status = (
            "not_computable"
            if pct is None
            else "above_limit"
            if not entity.exempt
            and (pct > single_obligor.decimal or pct > large_exposure.decimal)
            else "ok"
        )
        exposures.append(
            Exposure(
                counterparty_name=entity.name,
                connection=entity.connection,
                exposure_ghs=entity.total,
                pct_net_own_funds=pct,
                single_obligor_limit_pct=single_obligor.decimal,
                large_exposure_limit_pct=large_exposure.decimal,
                status=status,
                exempt=entity.exempt,
            )
        )
    if unattributed_count:
        findings.append(
            f"{unattributed_count} position(s) totalling {unattributed_ghs} GHS "
            "have no attributable counterparty."
        )
    # A limit whose regulatory basis is not yet confirmed must not be presented as
    # a settled test (WS-K, 2026-08-21: several SDI parameter seeds are cited to
    # instruments made under a repealed law). Driven by the parameter's own
    # confirmation status, so re-statusing it in the control plane is enough.
    unconfirmed = sorted(
        parameter.param_code
        for parameter in (single_obligor, large_exposure)
        if parameter.confirmation_status != "confirmed"
    )
    if unconfirmed:
        findings.append(
            "The exposure limit(s) "
            + ", ".join(unconfirmed)
            + " are not yet confirmed against a published regulatory instrument, so "
            "any breach shown here is provisional rather than a filing conclusion."
        )
    return SdiLargeExposures(
        as_of=as_of,
        net_own_funds_ghs=net_own_funds,
        exposures=exposures,
        findings=findings,
    )