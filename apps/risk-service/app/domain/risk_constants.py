from __future__ import annotations

from enum import StrEnum

ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

ASSESSMENT_TYPES = [
    "vendor_risk",
    "borrower_risk",
    "financial_statement_review",
]

LIQUIDITY_RISK_TYPE = "liquidity_risk"
LIQUIDITY_WORKFLOW_ID = "liquidity_analysis"
RISK_TYPES = [
    "concentration_risk",
    LIQUIDITY_RISK_TYPE,
    "leverage_risk",
    "cash_flow_risk",
    "documentation_gap",
    "compliance_gap",
    "operational_risk",
]


class CaseStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class FindingStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    ACKNOWLEDGED = "acknowledged"
    DISMISSED = "dismissed"
    NEEDS_REVIEW = "needs_review"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_INFO = "needs_more_info"
    ESCALATED = "escalated"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingSource(StrEnum):
    DETERMINISTIC_RULE = "deterministic_rule"
    MANUAL = "manual"
    IMPORTED = "imported"


class CaseSort(StrEnum):
    CREATED_AT_DESC = "created_at_desc"
    CREATED_AT_ASC = "created_at_asc"
    UPDATED_AT_DESC = "updated_at_desc"
    UPDATED_AT_ASC = "updated_at_asc"
    RISK_SCORE_DESC = "risk_score_desc"
    RISK_SCORE_ASC = "risk_score_asc"
    TITLE_ASC = "title_asc"


CASE_STATUSES = {status.value for status in CaseStatus}
FINDING_STATUSES = {status.value for status in FindingStatus}
SEVERITIES = {severity.value for severity in Severity}
CASE_DECISIONS = {decision.value for decision in CaseDecision}
RISK_LEVELS = {level.value for level in RiskLevel}
OPEN_FINDING_STATUSES = {FindingStatus.OPEN.value, FindingStatus.NEEDS_REVIEW.value}
SUPERSEDED_FINDING_STATUS = FindingStatus.SUPERSEDED.value
FINDING_SOURCES = {source.value for source in FindingSource}
DETERMINISTIC_FINDING_SOURCE = FindingSource.DETERMINISTIC_RULE.value
MANUAL_FINDING_SOURCE = FindingSource.MANUAL.value
CASE_SORT_OPTIONS = {sort.value for sort in CaseSort}
