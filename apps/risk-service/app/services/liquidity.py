from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.risk_constants import FindingStatus
from app.models import (
    CalculationForecastPeriod,
    CalculationRun,
    RiskFinding,
    RiskFindingEvidence,
)
from app.schemas.findings import FindingUpdate
from app.schemas.liquidity import (
    LiquidityEvidenceRead,
    LiquidityFindingRead,
    LiquidityFindingReview,
    LiquidityMetricRead,
    LiquiditySummaryRead,
)
from app.services import findings as findings_service
from app.services.audit import record_event
from app.services.cases import get_case_or_404

RULE_VERSION = "liquidity-v1.0.0"
RISK_TYPE = "liquidity"
MONEY = Decimal("0.0001")
RATIO = Decimal("0.0001")
type EvidenceSourceType = Literal["forecast_output", "canonical_input", "scenario_assumption"]
type FindingSeverity = Literal["low", "medium", "high", "critical"]
type FindingReadStatus = Literal[
    "open",
    "accepted",
    "acknowledged",
    "dismissed",
    "needs_review",
    "resolved",
    "superseded",
]


@dataclass(frozen=True)
class LiquidityResult:
    metrics: list[LiquidityMetricRead]
    concerns: list[dict[str, Any]]


def calculate_metrics(periods: list[CalculationForecastPeriod]) -> LiquidityResult:
    if not periods:
        raise ValueError("Liquidity analysis requires at least one forecast output period.")
    ordered = sorted(periods, key=lambda item: item.period_number)
    period_numbers = [item.period_number for item in ordered]
    if period_numbers != list(range(1, len(ordered) + 1)):
        raise ValueError(
            "Liquidity analysis requires one sequential forecast output for every period."
        )
    currencies = {item.currency for item in ordered}
    if len(currencies) != 1:
        raise ValueError("Liquidity analysis requires forecast outputs in one currency.")

    minimum = min(ordered, key=lambda item: (item.cash, item.period_number))
    peak_gap = max((-item.cash for item in ordered), default=Decimal(0))
    peak_gap = max(peak_gap, Decimal(0))
    first_negative = next((item for item in ordered if item.cash < 0), None)
    coverage_rows = [
        (
            item,
            _ratio(
                item.projected_inflows + item.credit_draw,
                item.projected_outflows + item.debt_repayment,
            ),
        )
        for item in ordered
    ]
    coverage_period, minimum_coverage = min(
        coverage_rows, key=lambda item: (item[1], item[0].period_number)
    )
    total_uses = sum(
        (item.projected_outflows + item.debt_repayment for item in ordered), Decimal(0)
    )
    total_draw = sum((item.credit_draw for item in ordered), Decimal(0))
    credit_reliance = _ratio(total_draw, total_uses) if total_uses > 0 else Decimal("0.0000")
    runway = Decimal(first_negative.period_number - 1 if first_negative else len(ordered))

    metrics = [
        LiquidityMetricRead(
            key="minimum_cash_balance",
            label="Minimum cash balance",
            value=_money(minimum.cash),
            unit=minimum.currency,
            period_number=minimum.period_number,
            period_end=minimum.period_end,
            description="Lowest projected ending cash balance across the forecast.",
        ),
        LiquidityMetricRead(
            key="peak_liquidity_gap",
            label="Peak liquidity gap",
            value=_money(peak_gap),
            unit=minimum.currency,
            period_number=first_negative.period_number if first_negative else None,
            period_end=first_negative.period_end if first_negative else None,
            description="Largest amount by which projected ending cash falls below zero.",
        ),
        LiquidityMetricRead(
            key="minimum_sources_coverage",
            label="Minimum sources coverage",
            value=minimum_coverage,
            unit="ratio",
            period_number=coverage_period.period_number,
            period_end=coverage_period.period_end,
            description=(
                "Lowest projected inflows plus credit draws divided by outflows plus "
                "debt repayment."
            ),
        ),
        LiquidityMetricRead(
            key="credit_reliance",
            label="Credit reliance",
            value=credit_reliance,
            unit="ratio",
            description="Forecast credit draws divided by total projected liquidity uses.",
        ),
        LiquidityMetricRead(
            key="cash_runway_periods",
            label="Cash runway",
            value=runway,
            unit="forecast_periods",
            period_number=first_negative.period_number if first_negative else None,
            period_end=first_negative.period_end if first_negative else None,
            description="Completed forecast periods before projected ending cash becomes negative.",
        ),
    ]

    concerns: list[dict[str, Any]] = []
    if first_negative is not None:
        concerns.append(
            {
                "rule_id": "liquidity.negative_cash",
                "severity": "critical" if first_negative.period_number == 1 else "high",
                "title": "Projected cash shortfall",
                "summary": (
                    f"Cash is projected to fall below zero in forecast period "
                    f"{first_negative.period_number}."
                ),
                "rationale": (
                    f"Projected ending cash is {_money(first_negative.cash)} "
                    f"{first_negative.currency}, creating a peak liquidity gap of "
                    f"{_money(peak_gap)} {first_negative.currency}."
                ),
                "period": first_negative,
                "metric_keys": [
                    "minimum_cash_balance",
                    "peak_liquidity_gap",
                    "cash_runway_periods",
                ],
            }
        )
    if minimum_coverage < Decimal("1.20"):
        concerns.append(
            {
                "rule_id": "liquidity.sources_coverage",
                "severity": "high" if minimum_coverage < Decimal(1) else "medium",
                "title": "Thin liquidity sources coverage",
                "summary": (
                    f"Liquidity sources cover {minimum_coverage}x of uses in forecast period "
                    f"{coverage_period.period_number}."
                ),
                "rationale": (
                    "Projected inflows and credit draws provide less than 1.20x coverage of "
                    "projected outflows and debt repayment."
                ),
                "period": coverage_period,
                "metric_keys": ["minimum_sources_coverage"],
            }
        )
    if credit_reliance > Decimal("0.25"):
        concerns.append(
            {
                "rule_id": "liquidity.credit_reliance",
                "severity": "high" if credit_reliance > Decimal("0.50") else "medium",
                "title": "Elevated reliance on credit",
                "summary": f"Credit draws fund {credit_reliance} of projected liquidity uses.",
                "rationale": (
                    "Forecast credit draws exceed 25% of total projected outflows and "
                    "debt repayment."
                ),
                "period": next(item for item in ordered if item.credit_draw > 0),
                "metric_keys": ["credit_reliance"],
            }
        )
    return LiquidityResult(metrics=metrics, concerns=concerns)


def generate_findings(
    db: Session,
    ctx: TenantContext,
    run: CalculationRun,
    periods: list[CalculationForecastPeriod],
) -> None:
    result = calculate_metrics(periods)
    prior = list(
        db.scalars(
            select(RiskFinding).where(
                RiskFinding.organization_id == ctx.organization_id,
                RiskFinding.case_id == run.case_id,
                RiskFinding.risk_type == RISK_TYPE,
                RiskFinding.source == "deterministic_rule",
                RiskFinding.status.in_(("open", "needs_review")),
            )
        )
    )
    for finding in prior:
        liquidity = finding.details.get("liquidity", {})
        if liquidity.get("scenario_id") == str(run.scenario_id):
            finding.status = "superseded"
            finding.disposition_reason = "Superseded by the latest liquidity forecast run."
            record_event(
                db,
                ctx,
                event_type="liquidity_finding.superseded",
                entity_type="risk_finding",
                entity_id=finding.id,
                details={"superseded_by_calculation_run_id": str(run.id)},
            )

    metrics_by_key = {item.key: item.model_dump(mode="json") for item in result.metrics}
    for concern in result.concerns:
        period = concern["period"]
        finding = RiskFinding(
            organization_id=ctx.organization_id,
            case_id=run.case_id,
            risk_type=RISK_TYPE,
            title=concern["title"],
            summary=concern["summary"],
            rationale=concern["rationale"],
            severity=concern["severity"],
            status="open",
            source="deterministic_rule",
            rule_id=concern["rule_id"],
            rule_version=RULE_VERSION,
            details={
                "liquidity": {
                    "calculation_run_id": str(run.id),
                    "scenario_id": str(run.scenario_id),
                    "input_hash": run.input_hash,
                    "metrics": [metrics_by_key[key] for key in concern["metric_keys"]],
                }
            },
        )
        db.add(finding)
        db.flush()
        db.add(
            RiskFindingEvidence(
                organization_id=ctx.organization_id,
                finding_id=finding.id,
                quote=concern["rationale"],
                locator={
                    "source_type": "forecast_output",
                    "label": f"Forecast period {period.period_number}",
                    "source_url": (
                        f"/api/v1/cases/{run.case_id}/calculation-runs/{run.id}"
                        f"#forecast-period-{period.period_number}"
                    ),
                    "calculation_run_id": str(run.id),
                    "forecast_period_id": str(period.id),
                    "period_number": period.period_number,
                    "period_end": period.period_end.isoformat(),
                    "input_hash": run.input_hash,
                },
                relevance=Decimal(1),
            )
        )
        for source_type, records in (
            ("canonical_input", run.inputs.get("balances", [])),
            ("canonical_input", run.inputs.get("cash_flows", [])),
            ("scenario_assumption", run.inputs.get("scenario", {}).get("assumptions", [])),
        ):
            for record in records:
                record_id = record.get("id")
                if not record_id:
                    continue
                db.add(
                    RiskFindingEvidence(
                        organization_id=ctx.organization_id,
                        finding_id=finding.id,
                        locator={
                            "source_type": source_type,
                            "label": _source_label(source_type, record),
                            "source_url": _source_url(run.case_id, source_type, record_id),
                            "record_id": record_id,
                            "input_hash": run.input_hash,
                        },
                        relevance=Decimal("0.75"),
                    )
                )
        record_event(
            db,
            ctx,
            event_type="liquidity_finding.generated",
            entity_type="risk_finding",
            entity_id=finding.id,
            details={
                "case_id": str(run.case_id),
                "scenario_id": str(run.scenario_id),
                "calculation_run_id": str(run.id),
                "rule_id": concern["rule_id"],
            },
        )
    record_event(
        db,
        ctx,
        event_type="liquidity_analysis.completed",
        entity_type="calculation_run",
        entity_id=run.id,
        details={"finding_count": len(result.concerns), "rule_version": RULE_VERSION},
    )


def get_summary(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    scenario_id: UUID | None = None,
    run_id: UUID | None = None,
) -> LiquiditySummaryRead:
    get_case_or_404(db, ctx.organization_id, case_id)
    query = select(CalculationRun).where(
        CalculationRun.organization_id == ctx.organization_id,
        CalculationRun.case_id == case_id,
        CalculationRun.status == "succeeded",
    )
    if run_id is not None:
        query = query.where(CalculationRun.id == run_id)
    if scenario_id is not None:
        query = query.where(CalculationRun.scenario_id == scenario_id)
    run = db.scalar(query.order_by(CalculationRun.created_at.desc(), CalculationRun.id.desc()))
    if run is None:
        return LiquiditySummaryRead(
            case_id=case_id,
            scenario_id=scenario_id,
            calculation_run_id=None,
            calculation_input_hash=None,
            status="not_calculated",
            currency=None,
            as_of_date=None,
            metrics=[],
            findings=[],
            generated_at=None,
        )
    periods = list(
        db.scalars(
            select(CalculationForecastPeriod)
            .where(
                CalculationForecastPeriod.organization_id == ctx.organization_id,
                CalculationForecastPeriod.case_id == case_id,
                CalculationForecastPeriod.run_id == run.id,
            )
            .order_by(CalculationForecastPeriod.period_number)
        )
    )
    result = calculate_metrics(periods)
    finding_rows = list(
        db.scalars(
            select(RiskFinding)
            .where(
                RiskFinding.organization_id == ctx.organization_id,
                RiskFinding.case_id == case_id,
                RiskFinding.risk_type == RISK_TYPE,
                RiskFinding.source == "deterministic_rule",
            )
            .order_by(RiskFinding.created_at.desc(), RiskFinding.id.desc())
        )
    )
    findings = [
        _finding_read(db, ctx, item)
        for item in finding_rows
        if item.details.get("liquidity", {}).get("calculation_run_id") == str(run.id)
    ]
    return LiquiditySummaryRead(
        case_id=case_id,
        scenario_id=run.scenario_id,
        calculation_run_id=run.id,
        calculation_input_hash=run.input_hash,
        status="ready",
        currency=periods[0].currency,
        as_of_date=run.as_of_date,
        metrics=result.metrics,
        findings=findings,
        generated_at=run.completed_at,
    )


def review_finding(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    finding_id: UUID,
    payload: LiquidityFindingReview,
) -> LiquidityFindingRead:
    get_case_or_404(db, ctx.organization_id, case_id)
    finding = findings_service.get_finding_or_404(db, ctx.organization_id, finding_id)
    if finding.case_id != case_id or finding.risk_type != RISK_TYPE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Liquidity finding not found."
        )
    updated = findings_service.update_finding(
        db,
        ctx,
        finding_id,
        FindingUpdate(
            status=(
                FindingStatus.ACKNOWLEDGED
                if payload.action == "acknowledge"
                else FindingStatus.DISMISSED
            ),
            disposition_reason=payload.reason.strip() if payload.reason else None,
        ).to_command(),
    )
    record_event(
        db,
        ctx,
        event_type="liquidity_finding.reviewed",
        entity_type="risk_finding",
        entity_id=updated.id,
        details={"action": payload.action, "reason": payload.reason},
    )
    db.commit()
    return _finding_read(db, ctx, updated)


def _finding_read(db: Session, ctx: TenantContext, finding: RiskFinding) -> LiquidityFindingRead:
    details = finding.details.get("liquidity", {})
    evidence_rows = findings_service.list_finding_evidence(db, ctx, finding.id)
    evidence = []
    for item in evidence_rows:
        locator = item.locator
        evidence.append(
            LiquidityEvidenceRead(
                id=item.id,
                source_type=cast(EvidenceSourceType, locator["source_type"]),
                label=cast(str, locator["label"]),
                source_url=cast(str, locator["source_url"]),
                locator=locator,
                quote=item.quote,
            )
        )
    return LiquidityFindingRead(
        id=finding.id,
        calculation_run_id=UUID(details["calculation_run_id"]),
        rule_id=finding.rule_id or "unknown",
        rule_version=finding.rule_version or RULE_VERSION,
        title=finding.title,
        summary=finding.summary,
        rationale=finding.rationale or finding.summary,
        severity=cast(FindingSeverity, finding.severity),
        status=cast(FindingReadStatus, finding.status),
        disposition_reason=finding.disposition_reason,
        evidence=evidence,
        created_at=finding.created_at,
        updated_at=finding.updated_at,
    )


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return Decimal("999.0000")
    return (numerator / denominator).quantize(RATIO, rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _source_label(source_type: str, record: dict[str, Any]) -> str:
    if source_type == "scenario_assumption":
        return f"Scenario assumption: {record.get('key', record['id'])}"
    return (
        f"Canonical record: {record.get('balance_type') or record.get('category') or record['id']}"
    )


def _source_url(case_id: UUID, source_type: str, record_id: str) -> str:
    if source_type == "scenario_assumption":
        return f"/api/v1/cases/{case_id}/scenarios#assumption-{record_id}"
    return f"/api/v1/cases/{case_id}/financial-workspace#record-{record_id}"
