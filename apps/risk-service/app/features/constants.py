from __future__ import annotations

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

RISK_TYPES = [
    "concentration_risk",
    "liquidity_risk",
    "leverage_risk",
    "cash_flow_risk",
    "documentation_gap",
    "compliance_gap",
    "operational_risk",
]

CASE_STATUSES = {"draft", "active", "in_review", "completed", "archived"}
FINDING_STATUSES = {"open", "accepted", "dismissed", "needs_review", "resolved"}
SEVERITIES = {"low", "medium", "high", "critical"}
