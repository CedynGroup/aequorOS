from __future__ import annotations

from datetime import datetime
from html import escape
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.risk_constants import CaseStatus
from app.models import RiskAssessment, RiskFinding, RiskScore
from app.schemas.common import JsonObject
from app.services.cases import get_case_or_404, list_case_decisions


class ReportCase(BaseModel):
    id: UUID
    title: str
    case_type: str
    subject_type: str | None
    subject_name: str | None
    status: str
    assigned_to_user_id: UUID | None
    risk_score: int | None
    risk_level: str | None
    scoring_version: str | None
    decision: str | None
    decided_at: datetime | None


class ReportFinding(BaseModel):
    id: UUID
    risk_type: str
    title: str
    summary: str
    rationale: str | None
    severity: str
    status: str
    source: str
    rule_id: str | None
    score_impact: int | None
    details: JsonObject


class ReportDecision(BaseModel):
    id: UUID
    decision: str
    previous_decision: str | None
    reason: str | None
    decided_by: UUID | None
    created_at: datetime


class ReportAssessment(BaseModel):
    id: UUID
    name: str
    assessment_type: str
    status: str
    created_at: datetime


class ReportScore(BaseModel):
    id: UUID
    assessment_id: UUID | None
    run_id: UUID | None
    score: int
    risk_level: str
    scoring_version: str
    input_hash: str
    rule_results: list[JsonObject]
    created_at: datetime


class RiskReportPayload(BaseModel):
    case: ReportCase
    findings: list[ReportFinding]
    decisions: list[ReportDecision]
    assessments: list[ReportAssessment]
    scores: list[ReportScore]


def report_payload(db: Session, ctx: TenantContext, case_id: UUID) -> RiskReportPayload:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    if case.status != CaseStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reports can only be generated for completed cases.",
        )
    findings = list(
        db.scalars(
            select(RiskFinding)
            .where(
                RiskFinding.organization_id == ctx.organization_id,
                RiskFinding.case_id == case.id,
            )
            .order_by(RiskFinding.severity.desc(), RiskFinding.created_at.asc())
        )
    )
    decisions = list_case_decisions(db, ctx, case.id)
    assessments = list(
        db.scalars(
            select(RiskAssessment)
            .where(
                RiskAssessment.organization_id == ctx.organization_id,
                RiskAssessment.case_id == case.id,
            )
            .order_by(RiskAssessment.created_at.desc())
        )
    )
    scores = list(
        db.scalars(
            select(RiskScore)
            .where(
                RiskScore.organization_id == ctx.organization_id,
                RiskScore.case_id == case.id,
            )
            .order_by(RiskScore.created_at.desc())
        )
    )
    return RiskReportPayload(
        case=ReportCase(
            id=case.id,
            title=case.title,
            case_type=case.case_type,
            subject_type=case.subject_type,
            subject_name=case.subject_name,
            status=case.status,
            assigned_to_user_id=case.assigned_to_user_id,
            risk_score=case.risk_score,
            risk_level=case.risk_level,
            scoring_version=case.scoring_version,
            decision=case.decision,
            decided_at=case.decided_at,
        ),
        findings=[
            ReportFinding(
                id=finding.id,
                risk_type=finding.risk_type,
                title=finding.title,
                summary=finding.summary,
                rationale=finding.rationale,
                severity=finding.severity,
                status=finding.status,
                source=finding.source,
                rule_id=finding.rule_id,
                score_impact=finding.score_impact,
                details=finding.details,
            )
            for finding in findings
        ],
        decisions=[
            ReportDecision(
                id=decision.id,
                decision=decision.decision,
                previous_decision=decision.previous_decision,
                reason=decision.reason,
                decided_by=decision.decided_by,
                created_at=decision.created_at,
            )
            for decision in decisions
        ],
        assessments=[
            ReportAssessment(
                id=assessment.id,
                name=assessment.name,
                assessment_type=assessment.assessment_type,
                status=assessment.status,
                created_at=assessment.created_at,
            )
            for assessment in assessments
        ],
        scores=[
            ReportScore(
                id=score.id,
                assessment_id=score.assessment_id,
                run_id=score.run_id,
                score=score.score,
                risk_level=score.risk_level,
                scoring_version=score.scoring_version,
                input_hash=score.input_hash,
                rule_results=score.rule_results,
                created_at=score.created_at,
            )
            for score in scores
        ],
    )


def report_html(payload: RiskReportPayload) -> str:
    case = payload.case
    decision = case.decision or "No decision"
    risk_score_value = case.risk_score
    risk_level_value = case.risk_level
    risk_score = escape("N/A" if risk_score_value is None else str(risk_score_value))
    risk_level = escape("N/A" if risk_level_value is None else str(risk_level_value))
    finding_rows = "".join(
        "<tr>"
        f"<td>{escape(finding.severity)}</td>"
        f"<td>{escape(finding.risk_type)}</td>"
        f"<td>{escape(finding.title)}</td>"
        f"<td>{escape(finding.status)}</td>"
        f"<td>{escape(format_optional_value(finding.score_impact, blank=''))}</td>"
        "</tr>"
        for finding in payload.findings
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Risk review report - {escape(case.title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; color: #172033; margin: 32px; }}
    h1 {{ margin-bottom: 4px; }}
    .muted {{ color: #64748b; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 24px 0;
    }}
    .metric {{ border: 1px solid #d9e2ec; padding: 12px; border-radius: 6px; }}
    .label {{ color: #64748b; font-size: 11px; text-transform: uppercase; }}
    .value {{ font-size: 20px; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #d9e2ec; padding: 8px; text-align: left; }}
    th {{ font-size: 11px; text-transform: uppercase; color: #64748b; }}
  </style>
</head>
<body>
  <h1>{escape(case.title)}</h1>
  <p class="muted">{escape(str(case.subject_name or "No subject"))}</p>
  <div class="grid">
    <div class="metric">
      <div class="label">Risk score</div><div class="value">{risk_score}</div>
    </div>
    <div class="metric">
      <div class="label">Risk level</div><div class="value">{risk_level}</div>
    </div>
    <div class="metric">
      <div class="label">Decision</div><div class="value">{escape(decision)}</div>
    </div>
    <div class="metric">
      <div class="label">Findings</div><div class="value">{len(payload.findings)}</div>
    </div>
  </div>
  <h2>Findings</h2>
  <table>
    <thead>
      <tr><th>Severity</th><th>Risk type</th><th>Title</th><th>Status</th><th>Impact</th></tr>
    </thead>
    <tbody>{finding_rows}</tbody>
  </table>
</body>
</html>"""


def format_optional_value(value: object, *, blank: str = "N/A") -> str:
    return blank if value is None else str(value)
