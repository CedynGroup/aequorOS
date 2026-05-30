from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.risk_constants import (
    DETERMINISTIC_FINDING_SOURCE,
    SUPERSEDED_FINDING_STATUS,
    CaseStatus,
    FindingStatus,
    RiskLevel,
    Severity,
)
from app.models import (
    Document,
    DocumentExtraction,
    RiskAssessment,
    RiskCase,
    RiskFinding,
    RiskScore,
)
from app.services.audit import record_event

SCORING_VERSION = "deterministic_v1"
RULE_VERSION = "1"


class ScoringInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    required_documents: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("required_documents", "requiredDocuments"),
    )
    provided_documents: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("provided_documents", "providedDocuments"),
    )
    vendor_criticality: str | None = Field(
        default=None,
        validation_alias=AliasChoices("vendor_criticality", "vendorCriticality"),
    )
    debt_to_ebitda: float | None = Field(
        default=None,
        validation_alias=AliasChoices("debt_to_ebitda", "debtToEbitda"),
    )
    cash_runway_months: float | None = Field(
        default=None,
        validation_alias=AliasChoices("cash_runway_months", "cashRunwayMonths"),
    )

    @field_validator("vendor_criticality", mode="before")
    @classmethod
    def normalize_vendor_criticality(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return normalized or None

    @field_validator("required_documents", "provided_documents", mode="before")
    @classmethod
    def normalize_document_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            return [str(value)]
        return [str(item) for item in value if item]

    @field_validator("debt_to_ebitda", "cash_runway_months", mode="before")
    @classmethod
    def normalize_number(cls, value: object) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class RuleFinding:
    rule_id: str
    risk_type: str
    title: str
    summary: str
    rationale: str
    score_impact: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoringResult:
    score_id: UUID
    risk_score: int
    risk_level: str
    findings_created: int
    rules_evaluated: int
    scoring_version: str
    input_snapshot: dict[str, Any]
    input_hash: str


def risk_level_for_score(score: int) -> str:
    if score >= 75:
        return RiskLevel.CRITICAL.value
    if score >= 50:
        return RiskLevel.HIGH.value
    if score >= 25:
        return RiskLevel.MEDIUM.value
    return RiskLevel.LOW.value


def severity_for_impact(score_impact: int) -> str:
    if score_impact >= 40:
        return Severity.CRITICAL.value
    if score_impact >= 30:
        return Severity.HIGH.value
    if score_impact >= 15:
        return Severity.MEDIUM.value
    return Severity.LOW.value


def run_scoring(
    db: Session,
    ctx: TenantContext,
    case: RiskCase,
    assessment: RiskAssessment,
    *,
    run_id,
) -> ScoringResult:
    raw_structured_data = collect_structured_data(db, ctx, case, assessment)
    scoring_input = normalize_structured_data(raw_structured_data)
    structured_data = scoring_input.model_dump(exclude_none=True, exclude_defaults=True)
    input_snapshot = {"structured_data": structured_data}
    input_hash = hash_input_snapshot(input_snapshot)
    findings = evaluate_rules(scoring_input)
    rule_results = [
        {
            "rule_id": finding.rule_id,
            "risk_type": finding.risk_type,
            "score_impact": finding.score_impact,
            "details": finding.details,
        }
        for finding in findings
    ]
    raw_score = sum(finding.score_impact for finding in findings)
    risk_score = min(raw_score, 100)
    risk_level = risk_level_for_score(risk_score)

    supersede_prior_generated_findings(db, ctx, case)

    created = 0
    for finding in findings:
        db.add(
            RiskFinding(
                organization_id=ctx.organization_id,
                case_id=case.id,
                assessment_id=assessment.id,
                run_id=run_id,
                risk_type=finding.risk_type,
                title=finding.title,
                summary=finding.summary,
                rationale=finding.rationale,
                severity=severity_for_impact(finding.score_impact),
                likelihood="medium",
                impact=severity_for_impact(finding.score_impact),
                confidence=Decimal("1.00"),
                status=FindingStatus.OPEN.value,
                source=DETERMINISTIC_FINDING_SOURCE,
                rule_id=finding.rule_id,
                rule_version=RULE_VERSION,
                score_impact=finding.score_impact,
                details=finding.details,
            )
        )
        created += 1

    case.risk_score = risk_score
    case.risk_level = risk_level
    case.scored_at = utc_now()
    case.scoring_version = SCORING_VERSION
    if case.status in {CaseStatus.DRAFT.value, CaseStatus.ACTIVE.value}:
        case.status = CaseStatus.IN_REVIEW.value

    score_record = RiskScore(
        organization_id=ctx.organization_id,
        case_id=case.id,
        assessment_id=assessment.id,
        run_id=run_id,
        score=risk_score,
        risk_level=risk_level,
        scoring_version=SCORING_VERSION,
        input_hash=input_hash,
        input_snapshot=input_snapshot,
        rule_results=rule_results,
    )
    db.add(score_record)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="case.scored",
        entity_type="risk_case",
        entity_id=case.id,
        details={
            "score_id": str(score_record.id),
            "assessment_id": str(assessment.id),
            "run_id": str(run_id),
            "risk_score": risk_score,
            "risk_level": risk_level,
            "scoring_version": SCORING_VERSION,
            "input_hash": input_hash,
            "findings_created": created,
        },
    )

    return ScoringResult(
        score_id=score_record.id,
        risk_score=risk_score,
        risk_level=risk_level,
        findings_created=created,
        rules_evaluated=rules_evaluated_count(),
        scoring_version=SCORING_VERSION,
        input_snapshot=input_snapshot,
        input_hash=input_hash,
    )


def hash_input_snapshot(input_snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(input_snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def normalize_structured_data(data: dict[str, Any]) -> ScoringInput:
    return ScoringInput.model_validate(data)


def supersede_prior_generated_findings(db: Session, ctx: TenantContext, case: RiskCase) -> None:
    prior_findings = db.scalars(
        select(RiskFinding).where(
            RiskFinding.organization_id == ctx.organization_id,
            RiskFinding.case_id == case.id,
            RiskFinding.source == DETERMINISTIC_FINDING_SOURCE,
            RiskFinding.rule_version == RULE_VERSION,
            RiskFinding.rule_id.is_not(None),
            RiskFinding.status.in_({FindingStatus.OPEN.value, FindingStatus.NEEDS_REVIEW.value}),
        )
    )
    for finding in prior_findings:
        finding.status = SUPERSEDED_FINDING_STATUS
        finding.disposition_reason = "Superseded by latest deterministic scoring run."


def collect_structured_data(
    db: Session, ctx: TenantContext, case: RiskCase, assessment: RiskAssessment
) -> dict[str, Any]:
    collected: dict[str, Any] = {}
    case_structured = case.metadata_.get("structured_data")
    if isinstance(case_structured, dict):
        collected.update(case_structured)

    assessment_structured = assessment.input_snapshot.get("structured_data")
    if isinstance(assessment_structured, dict):
        collected.update(assessment_structured)

    extraction_rows = db.scalars(
        select(DocumentExtraction)
        .join(Document, Document.id == DocumentExtraction.document_id)
        .where(
            DocumentExtraction.organization_id == ctx.organization_id,
            Document.organization_id == ctx.organization_id,
            Document.case_id == case.id,
            Document.deleted_at.is_(None),
            DocumentExtraction.status == "completed",
        )
        .order_by(DocumentExtraction.created_at.asc())
    )
    for extraction in extraction_rows:
        extracted = extraction.extracted_json
        structured = extracted.get("structured_data") if isinstance(extracted, dict) else None
        if isinstance(structured, dict):
            collected.update(structured)
        elif isinstance(extracted, dict):
            collected.update(extracted)

    return collected


def evaluate_rules(data: ScoringInput) -> list[RuleFinding]:
    if not data.model_dump(exclude_none=True, exclude_defaults=True):
        return [missing_structured_data_finding()]

    findings: list[RuleFinding] = []
    for rule in STRUCTURED_DATA_RULES:
        findings.extend(rule(data))
    return findings


def missing_structured_data_finding() -> RuleFinding:
    return RuleFinding(
        rule_id="missing_structured_data",
        risk_type="documentation_gap",
        title="Reviewed structured data is missing",
        summary="The case does not include reviewed structured data for scoring.",
        rationale=(
            "Deterministic scoring requires reviewed structured fields before a "
            "complete risk review."
        ),
        score_impact=20,
        details={"required_source": "structured_data"},
    )


def evaluate_required_documents(data: ScoringInput) -> list[RuleFinding]:
    if not data.required_documents:
        return []
    provided_set = {item.strip().lower() for item in data.provided_documents if item}
    missing = [item for item in data.required_documents if item.strip().lower() not in provided_set]
    if not missing:
        return []
    impact = min(15 * len(missing), 30)
    return [
        RuleFinding(
            rule_id="missing_required_documents",
            risk_type="documentation_gap",
            title="Required documents are missing",
            summary=f"{len(missing)} required document(s) are not recorded as provided.",
            rationale=(
                "Missing required documents reduce review completeness and increase review risk."
            ),
            score_impact=impact,
            details={"missing_documents": missing, "impact_cap": 30},
        )
    ]


def evaluate_vendor_criticality(data: ScoringInput) -> list[RuleFinding]:
    criticality = (data.vendor_criticality or "").strip().lower()
    impacts = {"high": 30, "critical": 45}
    impact = impacts.get(criticality)
    if impact is None:
        return []
    return [
        RuleFinding(
            rule_id="vendor_criticality",
            risk_type="operational_risk",
            title="Vendor criticality increases review risk",
            summary=f"Vendor criticality is recorded as {criticality}.",
            rationale=(
                "High-criticality vendors can create elevated operational dependency and "
                "continuity risk."
            ),
            score_impact=impact,
            details={"vendor_criticality": criticality, "thresholds": impacts},
        )
    ]


def evaluate_debt_to_ebitda(data: ScoringInput) -> list[RuleFinding]:
    value = data.debt_to_ebitda
    if value is None or value < 4:
        return []
    impact = 40 if value >= 6 else 25
    return [
        RuleFinding(
            rule_id="elevated_debt_to_ebitda",
            risk_type="leverage_risk",
            title="Debt-to-EBITDA exceeds review threshold",
            summary=f"Debt-to-EBITDA is {value:g}, above the 4.0 review threshold.",
            rationale=(
                "Elevated leverage can reduce repayment flexibility and increase downside "
                "sensitivity."
            ),
            score_impact=impact,
            details={"debt_to_ebitda": value, "medium_threshold": 4, "critical_threshold": 6},
        )
    ]


def evaluate_cash_runway(data: ScoringInput) -> list[RuleFinding]:
    value = data.cash_runway_months
    if value is None or value >= 6:
        return []
    impact = 40 if value < 3 else 25
    return [
        RuleFinding(
            rule_id="low_cash_runway",
            risk_type="liquidity_risk",
            title="Cash runway is below review threshold",
            summary=f"Cash runway is {value:g} month(s), below the 6 month threshold.",
            rationale="Short cash runway can indicate near-term liquidity pressure.",
            score_impact=impact,
            details={"cash_runway_months": value, "medium_threshold": 6, "critical_threshold": 3},
        )
    ]


RuleEvaluator = Callable[[ScoringInput], list[RuleFinding]]
STRUCTURED_DATA_RULES: tuple[RuleEvaluator, ...] = (
    evaluate_required_documents,
    evaluate_vendor_criticality,
    evaluate_debt_to_ebitda,
    evaluate_cash_runway,
)


def rules_evaluated_count() -> int:
    return 1 + len(STRUCTURED_DATA_RULES)
