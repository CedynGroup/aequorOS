"""Independent HTTP oracle: case_enforcement_rollout.md §Operation matrix.

Malformed UUID path tokens and empty mutation bodies intentionally stop permitted
requests at input validation. This distinguishes an authorization success (422)
from an opaque denial (404) without starting workflows or external effects.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.authorization import InstitutionScope, ModuleScope, RoleBundle, SensitivityScope
from tests.integration.case.testing.generators import CaseBinding


@dataclass(frozen=True)
class CaseOperation:
    method: str
    path: str
    permission: str
    sensitivity: str
    opaque: bool = True


def _operations() -> tuple[CaseOperation, ...]:
    rows: list[CaseOperation] = []

    def add(method: str, permission: str, sensitivity: str, *paths: str) -> None:
        for path in paths:
            opaque = (
                "{" in path
                or path in ("/documents/upload-request", "/assessments", "/cases/bulk-actions")
                and method == "POST"
            )
            rows.append(CaseOperation(method, path, permission, sensitivity, opaque))

    add(
        "GET",
        "view",
        "confidential",
        "/cases",
        "/cases/summary",
        "/cases/{case_id}",
        "/cases/{case_id}/scores",
        "/cases/{case_id}/decisions",
        "/findings",
        "/findings/{finding_id}",
        "/cases/{case_id}/findings",
        "/assessments/{assessment_id}/findings",
        "/cases/{case_id}/scenarios/readiness",
        "/assessments/{assessment_id}/runs",
        "/assessment-runs/{run_id}",
        "/cases/{case_id}/calculation-runs",
        "/cases/{case_id}/capital-projections",
    )
    add(
        "POST",
        "create",
        "confidential",
        "/cases",
        "/cases/{case_id}/decisions",
        "/cases/{case_id}/findings",
    )
    add("PATCH", "edit", "confidential", "/cases/{case_id}")
    add(
        "POST",
        "edit",
        "confidential",
        "/cases/{case_id}/assign",
        "/cases/{case_id}/archive",
        "/cases/bulk-actions",
    )
    add("PATCH", "review", "confidential", "/findings/{finding_id}")
    add(
        "GET",
        "view",
        "restricted",
        "/findings/{finding_id}/evidence",
        "/documents/{document_id}",
        "/documents/{document_id}/parse-status",
        "/cases/{case_id}/documents",
        "/assessments",
        "/assessments/{assessment_id}",
        "/cases/{case_id}/financial-workspace",
        "/cases/{case_id}/financial-data/validation-issues",
        "/cases/{case_id}/scenarios",
        "/cases/{case_id}/scenarios/{scenario_id}",
        "/cases/{case_id}/scenarios/{scenario_id}/validation",
        "/cases/{case_id}/calculation-runs/{run_id}",
        "/cases/{case_id}/capital-projections/{projection_id}",
        "/cases/{case_id}/capital-summary",
        "/cases/{case_id}/capital-comparison",
        "/cases/{case_id}/liquidity/summary",
    )
    add(
        "POST",
        "create",
        "restricted",
        "/documents/upload-request",
        "/assessments",
        "/cases/{case_id}/scenarios",
        "/cases/{case_id}/scenarios/initialize",
        "/cases/{case_id}/scenarios/{scenario_id}/copy",
        "/cases/{case_id}/scenarios/{scenario_id}/assumptions",
    )
    add(
        "POST",
        "edit",
        "restricted",
        "/documents/{document_id}/complete-upload",
        "/cases/{case_id}/scenarios/{scenario_id}/archive",
    )
    add("DELETE", "edit", "restricted", "/documents/{document_id}")
    add(
        "PATCH",
        "edit",
        "restricted",
        "/cases/{case_id}/scenarios/{scenario_id}",
        "/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}",
    )
    add(
        "POST",
        "run",
        "restricted",
        "/documents/{document_id}/parse",
        "/assessments/{assessment_id}/run",
        "/cases/{case_id}/financial-workspace/map",
        "/cases/{case_id}/financial-data/validate",
        "/cases/{case_id}/calculation-runs",
        "/cases/{case_id}/calculation-runs/{run_id}/rerun",
        "/cases/{case_id}/capital-projections",
    )
    add(
        "POST",
        "review",
        "restricted",
        "/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}/review",
        "/cases/{case_id}/liquidity/findings/{finding_id}/review",
    )
    add(
        "GET",
        "export",
        "restricted",
        "/documents/{document_id}/download-url",
        "/cases/{case_id}/report",
    )
    for resource, record in (
        ("cash-flows", "cash_flow"),
        ("institutions", "institution"),
        ("accounts", "account"),
        ("reporting-periods", "reporting_period"),
        ("balances", "balance"),
        ("obligations", "obligation"),
        ("covenants", "covenant"),
    ):
        root = f"/cases/{{case_id}}/financial-workspace/{resource}"
        add("POST", "create", "restricted", root)
        add("PATCH", "edit", "restricted", root + "/{" + record + "_id}")
    return tuple(rows)


CASE_OPERATIONS = _operations()


def permits(bindings: list[CaseBinding], operation: CaseOperation) -> bool:
    """Case duty table, independently stated; never call the production evaluator."""
    duties = {
        RoleBundle.VIEWER: {"view"},
        RoleBundle.AUDITOR: {"view"},
        RoleBundle.ANALYST: {"view", "create", "edit", "run", "export"},
        RoleBundle.APPROVER: {"view", "review"},
        RoleBundle.VALIDATOR: {"view"},
    }
    return any(
        row.lifecycle == "active"
        and row.institution is InstitutionScope.ORGANIZATION
        and row.module in (ModuleScope.RISK, ModuleScope.ALL)
        and row.sensitivity.value in (operation.sensitivity, SensitivityScope.ALL.value)
        and operation.permission in duties.get(row.bundle, set())
        for row in bindings
    )
