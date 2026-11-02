"""Observable organization-RISK authority and before-side-effect refusals."""

from __future__ import annotations

from datetime import timedelta
from typing import Never, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.case.schemas.assessments import AssessmentRead, AssessmentRunRead, RunResponse
from app.case.schemas.calculations import CalculationRunListRead
from app.case.schemas.cases import CaseBulkActionRead, CaseRead, ScoreRead
from app.case.schemas.findings import FindingRead
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.domain.risk_constants import FindingSource
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.user import User
from app.identity.service import authorization
from app.models import CalculationRun, Document, Job, RiskCase, RiskFinding
from app.models.audit_event import AuditEvent
from app.schemas.common import ErrorResponse, JsonObject
from app.schemas.jobs import JobRead
from tests.conftest import FakeStorage
from tests.support.api_factories import ApiFactories
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers


@pytest.fixture
def unbound(db_session: Session) -> None:
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == USER_1)
    )
    db_session.commit()


def grant(  # noqa: PLR0913 - every dimension is explicit in the negative cases
    db: Session,
    bundle: RoleBundle = RoleBundle.ANALYST,
    sensitivity: SensitivityScope = SensitivityScope.CONFIDENTIAL,
    *,
    module: ModuleScope = ModuleScope.RISK,
    user_id: UUID = USER_1,
    bank_id: str | None = None,
) -> AuthorizationBinding:
    row = authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION if bank_id is None else InstitutionScope.INSTITUTION,
            bank_id,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "case-authorization-test"),
        reason="Exercise explicit authority in a disposable case test",
        commit=False,
    )
    user = db.get(User, user_id)
    assert user is not None
    user.authorization_version = 1
    db.commit()
    return row


def create(client: TestClient, title: str = "Case authority fixture") -> str:
    response = client.post(
        "/api/v1/cases",
        headers=headers(roles=("viewer",)),
        json={"title": title, "case_type": "vendor"},
    )
    assert response.status_code == 201, response.text
    return str(CaseRead.model_validate(response.json()).id)


@pytest.mark.usefixtures("unbound")
@pytest.mark.parametrize("role", ["admin", "approver", "analyst", "viewer", "examiner"])
def test_scalar_roles_never_create_a_case(
    db_client: TestClient, db_session: Session, role: str
) -> None:
    denied = db_client.post(
        "/api/v1/cases",
        headers=headers(roles=(role,)),
        json={"title": "Denied", "case_type": "vendor"},
    )
    assert denied.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(RiskCase)) == 0
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.usefixtures("unbound")
@pytest.mark.parametrize(
    "dimension",
    ["module", "sensitivity", "principal", "expired", "future", "suspended", "revoked"],
)
def test_incomplete_or_inactive_bindings_cannot_write(
    db_client: TestClient, db_session: Session, dimension: str
) -> None:
    other_user = User(
        id=uuid4(), organization_id=ORG_1, email="case.other@example.test", display_name="Other"
    )
    db_session.add(other_user)
    db_session.flush()
    row = grant(
        db_session,
        module=ModuleScope.CAPITAL if dimension == "module" else ModuleScope.RISK,
        sensitivity=(
            SensitivityScope.AGGREGATED
            if dimension == "sensitivity"
            else SensitivityScope.CONFIDENTIAL
        ),
        user_id=other_user.id if dimension == "principal" else USER_1,
    )
    if dimension == "expired":
        row.valid_from = utc_now() - timedelta(days=2)
        row.valid_until = utc_now() - timedelta(seconds=1)
    elif dimension == "future":
        row.valid_from = utc_now() + timedelta(days=1)
    elif dimension in ("suspended", "revoked"):
        row.status = dimension
        if dimension == "revoked":
            row.revoked_at = utc_now()
            row.revoked_by_type = "system"
            row.revoked_by_id = "case-authorization-test"
            row.revoked_reason = "Exercise revocation"
    db_session.commit()
    denied = db_client.post(
        "/api/v1/cases", headers=headers(), json={"title": "Denied", "case_type": "vendor"}
    )
    assert denied.status_code == 403, denied.text
    assert db_session.scalar(select(func.count()).select_from(RiskCase)) == 0


@pytest.mark.usefixtures("unbound")
def test_partial_bindings_never_compose(db_client: TestClient, db_session: Session) -> None:
    grant(db_session, module=ModuleScope.CAPITAL)
    grant(db_session, sensitivity=SensitivityScope.RESTRICTED)
    assert db_client.get("/api/v1/cases", headers=headers()).status_code == 403
    assert (
        db_client.post(
            "/api/v1/cases", headers=headers(), json={"title": "Denied", "case_type": "vendor"}
        ).status_code
        == 403
    )


@pytest.mark.usefixtures("unbound")
def test_authority_filters_before_counts_and_revocation_hides_ids(
    db_client: TestClient, db_session: Session
) -> None:
    row = grant(db_session)
    case_id = create(db_client)
    foreign = db_client.post(
        "/api/v1/cases",
        headers=headers(ORG_2),
        json={"title": "Other tenant", "case_type": "vendor"},
    )
    assert foreign.status_code == 201
    listing = db_client.get("/api/v1/cases?limit=1", headers=headers())
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == case_id
    assert (
        db_client.get(f"/api/v1/cases/{foreign.json()['id']}", headers=headers()).status_code == 404
    )
    db_session.delete(row)
    db_session.commit()
    assert db_client.get("/api/v1/cases?limit=1", headers=headers()).status_code == 403
    assert db_client.get("/api/v1/cases/summary", headers=headers()).status_code == 403
    for resource_id in (case_id, str(uuid4())):
        assert db_client.get(f"/api/v1/cases/{resource_id}", headers=headers()).status_code == 404
        assert (
            db_client.patch(
                f"/api/v1/cases/{resource_id}", headers=headers(), json={"title": "Denied"}
            ).status_code
            == 404
        )
    db_session.expire_all()
    row = db_session.get(RiskCase, UUID(case_id))
    assert row is not None and row.title == "Case authority fixture"


@pytest.mark.usefixtures("unbound")
def test_sensitivity_and_permission_are_exact(db_client: TestClient, db_session: Session) -> None:
    grant(db_session)
    case_id = create(db_client)
    assert db_client.get(f"/api/v1/cases/{case_id}", headers=headers()).status_code == 200
    workspace = f"/api/v1/cases/{case_id}/financial-workspace"
    assert db_client.get(workspace, headers=headers()).status_code == 404
    grant(db_session, RoleBundle.VIEWER, SensitivityScope.RESTRICTED)
    assert db_client.get(workspace, headers=headers()).status_code == 200
    assert (
        db_client.post(
            f"/api/v1/cases/{case_id}/scenarios/initialize",
            headers=headers(),
            json={"reason": "Denied"},
        ).status_code
        == 404
    )
    assert db_client.get(f"/api/v1/cases/{case_id}/report", headers=headers()).status_code == 404
    grant(db_session, RoleBundle.ANALYST, SensitivityScope.RESTRICTED)
    # Export is now authorized; the existing completed-case invariant refuses it.
    assert db_client.get(f"/api/v1/cases/{case_id}/report", headers=headers()).status_code == 409


@pytest.mark.usefixtures("unbound")
def test_review_does_not_follow_analyst_or_scalar_approver(
    db_client: TestClient, db_session: Session
) -> None:
    grant(db_session)
    case_id = create(db_client)
    finding = db_client.post(
        f"/api/v1/cases/{case_id}/findings",
        headers=headers(),
        json={
            "risk_type": "documentation_gap",
            "title": "Review",
            "summary": "Review",
            "severity": "low",
        },
    )
    assert finding.status_code == 201, finding.text
    path = f"/api/v1/findings/{finding.json()['id']}"
    assert (
        db_client.patch(
            path, headers=headers(roles=("approver",)), json={"status": "acknowledged"}
        ).status_code
        == 404
    )
    grant(db_session, RoleBundle.APPROVER)
    allowed = db_client.patch(
        path, headers=headers(roles=("viewer",)), json={"status": "acknowledged"}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "acknowledged"


@pytest.mark.usefixtures("unbound")
def test_every_case_entry_refuses_before_storage_jobs_or_calculations(
    db_client: TestClient,
    db_session: Session,
    fake_storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> Never:
        pytest.fail("A denied request crossed a storage or calculation boundary")

    for method in (
        "create_presigned_upload_url",
        "create_presigned_download_url",
        "head_object",
        "delete_object",
    ):
        monkeypatch.setattr(fake_storage, method, forbidden)
    monkeypatch.setattr("app.case.service.calculations.calculate_forecast", forbidden)
    app = cast(FastAPI, db_client.app)
    count = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.endpoint.__module__.startswith(
            "app.case.api."
        ):
            continue
        if route.path.startswith("/api/v1/taxonomies/") or route.path in (
            "/api/v1/risk-types",
            "/api/v1/assessment-types",
        ):
            continue
        path = route.path
        for parameter in route.param_convertors:
            path = path.replace(
                "{" + parameter + "}",
                "unsupported" if parameter == "unsupported_entity_type" else str(uuid4()),
            )
        for method in route.methods:
            response = db_client.request(method, path, headers=headers(), json={})
            assert response.status_code in (403, 404), (method, path, response.text)
            count += 1
    assert count == 76
    for model in (RiskCase, Document, Job, CalculationRun, AuditEvent):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0, model


def test_unsupported_entities_are_public_noop_404(db_client: TestClient) -> None:
    path = f"/api/v1/cases/{uuid4()}/financial-workspace/unsupported"
    assert db_client.post(path, json={}).status_code == 404
    assert db_client.patch(f"{path}/{uuid4()}", json={}).status_code == 404


def bank(db: Session) -> str:
    from app.identity.models.bank import Bank  # noqa: PLC0415

    row = Bank(
        organization_id=ORG_1,
        name="Authorization fixture bank",
        short_name="Fixture",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(row)
    db.commit()
    return row.id


@pytest.mark.usefixtures("unbound")
def test_exact_bank_risk_never_covers_organization_cases(
    db_client: TestClient, db_session: Session
) -> None:
    grant(db_session, bank_id=bank(db_session))
    assert db_client.get("/api/v1/cases", headers=headers()).status_code == 403
    assert (
        db_client.post(
            "/api/v1/cases", headers=headers(), json={"title": "Denied", "case_type": "vendor"}
        ).status_code
        == 403
    )
    assert db_client.get(f"/api/v1/cases/{uuid4()}", headers=headers()).status_code == 404
    assert db_session.scalar(select(func.count()).select_from(RiskCase)) == 0


@pytest.mark.usefixtures("unbound")
def test_machine_credentials_cannot_use_human_case_grants(
    db_client: TestClient, db_session: Session
) -> None:
    from app.core.tenancy import TenantContext  # noqa: PLC0415
    from app.identity.service.integration_keys import issue_key  # noqa: PLC0415

    grant(db_session)
    issued = issue_key(
        db_session,
        TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
        bank(db_session),
        "Case refusal fixture",
    )
    machine = {"Authorization": f"Bearer {issued.key}"}
    assert db_client.get("/api/v1/cases", headers=machine).status_code == 401
    denied = db_client.post(
        "/api/v1/cases", headers=machine, json={"title": "Denied", "case_type": "vendor"}
    )
    assert denied.status_code == 401
    assert db_session.scalar(select(func.count()).select_from(RiskCase)) == 0


@pytest.mark.usefixtures("unbound")
def test_global_demo_condition_overrides_a_complete_binding(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings  # noqa: PLC0415

    grant(db_session)
    monkeypatch.setattr(get_settings().app, "demo_mode", True)
    denied = db_client.post(
        "/api/v1/cases", headers=headers(), json={"title": "Denied", "case_type": "vendor"}
    )
    assert denied.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(RiskCase)) == 0


@pytest.mark.usefixtures("unbound")
def test_evaluator_failure_is_closed_before_write(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant(db_session)

    def unavailable(*_args: object, **_kwargs: object) -> Never:
        raise RuntimeError("Authorization store unavailable")

    monkeypatch.setattr(
        "app.case.service.authorization.authorization.evaluate_permission", unavailable
    )
    denied = db_client.post(
        "/api/v1/cases", headers=headers(), json={"title": "Denied", "case_type": "vendor"}
    )
    assert denied.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(RiskCase)) == 0


@pytest.mark.usefixtures("unbound")
def test_access_impact_reports_case_authority_separately_from_bank_risk(
    db_client: TestClient, db_session: Session
) -> None:
    from scripts.authorization_access_impact import build_report  # noqa: PLC0415

    grant(db_session, bank_id=bank(db_session))
    denied = next(
        row for row in build_report(db_session, organization_id=ORG_1) if row.user_id == str(USER_1)
    )
    assert denied.case_permissions == []
    grant(db_session, RoleBundle.VIEWER)
    reader = next(
        row for row in build_report(db_session, organization_id=ORG_1) if row.user_id == str(USER_1)
    )
    assert reader.case_permissions == ["confidential/view"]


@pytest.mark.committing_db
def test_existing_opaque_resources_are_hidden_before_side_effects(
    db_client: TestClient,
    db_session: Session,
    fake_storage: FakeStorage,
    api_factories: ApiFactories,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.case.schemas.calculations import CalculationRunRead  # noqa: PLC0415
    from app.case.schemas.scenarios import ScenarioWorkspaceRead  # noqa: PLC0415

    case_id = api_factories.cases.create().id
    document_id = api_factories.documents.create_uploaded(case_id=case_id).document_id
    assessment_id = api_factories.assessments.create(case_id=case_id).id
    initialized = db_client.post(
        f"/api/v1/cases/{case_id}/scenarios/initialize",
        headers=headers(),
        json={"reason": "Prepare existing resources for refusal proof"},
    )
    assert initialized.status_code == 200, initialized.text
    scenario_id = ScenarioWorkspaceRead.model_validate(initialized.json()).scenarios[0].id
    started = db_client.post(
        f"/api/v1/cases/{case_id}/calculation-runs",
        headers=headers(),
        json={"scenario_id": str(scenario_id)},
    )
    assert started.status_code == 201, started.text
    run_id = CalculationRunRead.model_validate(started.json()).id
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == USER_1)
    )
    db_session.commit()
    before = {
        model: db_session.scalar(select(func.count()).select_from(model))
        for model in (RiskCase, Document, Job, CalculationRun, AuditEvent)
    }

    def forbidden(*_args: object, **_kwargs: object) -> Never:
        pytest.fail("A refused existing resource reached storage or calculation")

    for method in (
        "create_presigned_upload_url",
        "create_presigned_download_url",
        "head_object",
        "delete_object",
    ):
        monkeypatch.setattr(fake_storage, method, forbidden)
    monkeypatch.setattr("app.case.service.calculations.calculate_forecast", forbidden)
    attempts: list[tuple[str, str, dict[str, object]]] = [
        ("GET", f"/documents/{document_id}", {}),
        ("GET", f"/documents/{document_id}/download-url", {}),
        ("POST", f"/documents/{document_id}/parse", {}),
        ("POST", f"/documents/{document_id}/complete-upload", {}),
        ("DELETE", f"/documents/{document_id}", {}),
        ("GET", f"/assessments/{assessment_id}", {}),
        ("POST", f"/assessments/{assessment_id}/run", {}),
        ("GET", f"/cases/{case_id}/calculation-runs/{run_id}", {}),
        ("POST", f"/cases/{case_id}/calculation-runs", {"scenario_id": str(scenario_id)}),
        ("POST", f"/cases/{case_id}/calculation-runs/{run_id}/rerun", {}),
        ("GET", f"/cases/{case_id}/scenarios/{scenario_id}", {}),
        (
            "PATCH",
            f"/cases/{case_id}/scenarios/{scenario_id}",
            {"name": "Denied", "reason": "Denied"},
        ),
        (
            "POST",
            "/documents/upload-request",
            {
                "case_id": str(case_id),
                "filename": "denied.pdf",
                "content_type": "application/pdf",
                "byte_size": 1234,
            },
        ),
    ]
    for method, path, payload in attempts:
        denied = db_client.request(method, f"/api/v1{path}", headers=headers(), json=payload)
        assert denied.status_code == 404, (method, path, denied.text)
    db_session.expire_all()
    for model, count in before.items():
        assert db_session.scalar(select(func.count()).select_from(model)) == count
    document = db_session.get(Document, document_id)
    assert document is not None and document.status == "uploaded"


def disclosure_grants(db: Session, *, restricted: bool) -> None:
    db.execute(delete(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == USER_1))
    db.commit()
    grant(db)
    grant(db, RoleBundle.APPROVER)
    grant(db, RoleBundle.VIEWER, SensitivityScope.RESTRICTED, module=ModuleScope.CAPITAL)
    if restricted:
        grant(db, RoleBundle.VIEWER, SensitivityScope.RESTRICTED)


@pytest.mark.parametrize("restricted", [False, True])
def test_case_payload_disclosure_on_every_mutation_return(
    db_client: TestClient, db_session: Session, restricted: bool
) -> None:
    disclosure_grants(db_session, restricted=restricted)
    metadata: JsonObject = {"label": "Confidential label", "structured_data": {"debt_to_ebitda": 7}}
    created = db_client.post(
        "/api/v1/cases",
        headers=headers(),
        json={"title": "Disclosure", "case_type": "vendor", "metadata": metadata},
    )
    assert created.status_code == 201, created.text
    case_id = CaseRead.model_validate(created.json()).id
    results = [created]
    for method, suffix, payload in (
        ("GET", "", None),
        ("PATCH", "", {"title": "Updated"}),
        ("POST", "/assign", {"assigned_to_user_id": str(USER_1)}),
    ):
        response = db_client.request(
            method, f"/api/v1/cases/{case_id}{suffix}", headers=headers(), json=payload
        )
        assert response.status_code == 200, response.text
        results.append(response)
    for action in ("assign", "unassign", "update_status", "archive"):
        bulk_payload: dict[str, object] = {"case_ids": [str(case_id)], "action": action}
        if action == "assign":
            bulk_payload["assigned_to_user_id"] = str(USER_1)
        elif action == "update_status":
            bulk_payload["status"] = "active"
        bulk = db_client.post("/api/v1/cases/bulk-actions", headers=headers(), json=bulk_payload)
        assert bulk.status_code == 200, bulk.text
        result = CaseBulkActionRead.model_validate(bulk.json())
        assert result.failed == []
        returned = result.succeeded[0].case.metadata
        assert returned == (metadata if restricted else {"label": "Confidential label"})
    archived = db_client.post(f"/api/v1/cases/{case_id}/archive", headers=headers())
    assert archived.status_code == 200, archived.text
    results.append(archived)
    for response in results:
        assert CaseRead.model_validate(response.json()).metadata == (
            metadata if restricted else {"label": "Confidential label"}
        )
    db_session.expire_all()
    stored = db_session.get(RiskCase, case_id)
    assert stored is not None and stored.metadata_ == metadata


def _populated_assessment(client: TestClient) -> tuple[UUID, UUID, RunResponse]:
    created = client.post(
        "/api/v1/cases",
        headers=headers(),
        json={
            "title": "Populated assessment",
            "case_type": "vendor",
            "metadata": {"structured_data": {"debt_to_ebitda": 7, "cash_runway_months": 1.25}},
        },
    )
    assert created.status_code == 201, created.text
    case_id = CaseRead.model_validate(created.json()).id
    assessment = client.post(
        "/api/v1/assessments",
        headers=headers(),
        json={
            "case_id": str(case_id),
            "assessment_type": "vendor_risk",
            "name": "Disclosure",
        },
    )
    assert assessment.status_code == 201, assessment.text
    assessment_id = AssessmentRead.model_validate(assessment.json()).id
    started = client.post(f"/api/v1/assessments/{assessment_id}/run", headers=headers())
    assert started.status_code == 200, started.text
    result = RunResponse.model_validate(started.json())
    assert result.status == "completed"
    return case_id, assessment_id, result


@pytest.mark.parametrize("restricted", [False, True])
def test_populated_assessment_scores_findings_and_history_disclosure(
    db_client: TestClient, db_session: Session, restricted: bool
) -> None:
    case_id, assessment_id, result = _populated_assessment(db_client)
    run_id = result.run_id
    disclosure_grants(db_session, restricted=restricted)
    scores = db_client.get(f"/api/v1/cases/{case_id}/scores", headers=headers())
    assert scores.status_code == 200, scores.text
    score = TypeAdapter(list[ScoreRead]).validate_python(scores.json())[0]
    assert score.score > 0
    if restricted:
        assert score.input_snapshot == {
            "structured_data": {"debt_to_ebitda": 7, "cash_runway_months": 1.25}
        }
        assert any(
            rule.get("details")
            == {"debt_to_ebitda": 7, "medium_threshold": 4, "critical_threshold": 6}
            for rule in score.rule_results
        )
    else:
        assert score.input_snapshot == {}
        assert all("details" not in rule for rule in score.rule_results)
    for path in (f"/assessments/{assessment_id}/runs", f"/assessment-runs/{run_id}"):
        response = db_client.get(f"/api/v1{path}", headers=headers())
        assert response.status_code == 200, response.text
        run = (
            TypeAdapter(list[AssessmentRunRead]).validate_python(response.json())[0]
            if path.endswith("/runs")
            else AssessmentRunRead.model_validate(response.json())
        )
        assert run.summary["risk_score"] == score.score
        assert ("input_snapshot" in run.summary) is restricted
    _assert_finding_disclosure(db_client, db_session, case_id, assessment_id, restricted=restricted)


def _assert_finding_disclosure(
    db_client: TestClient,
    db_session: Session,
    case_id: UUID,
    assessment_id: UUID,
    *,
    restricted: bool,
) -> None:
    sentinel = "RAW_FINANCIAL_EVIDENCE_94821"
    imported = RiskFinding(
        organization_id=ORG_1,
        case_id=case_id,
        assessment_id=assessment_id,
        risk_type="documentation_gap",
        title="Missing certificate",
        summary="Counterparty insurance certificate is missing.",
        rationale=sentinel,
        severity="medium",
        source=FindingSource.IMPORTED.value,
        details={"evidence": [{"quote": sentinel}]},
    )
    db_session.add(imported)
    db_session.commit()
    expected_summaries = {
        finding.id: finding.summary
        for finding in db_session.scalars(select(RiskFinding).where(RiskFinding.case_id == case_id))
    }
    manual = db_client.post(
        f"/api/v1/cases/{case_id}/findings",
        headers=headers(),
        json={
            "risk_type": "leverage_risk",
            "title": "Manual concern",
            "severity": "high",
            "summary": sentinel,
            "rationale": sentinel,
            "details": {"evidence": [{"quote": sentinel}], "liquidity": {"metrics": [sentinel]}},
        },
    )
    assert manual.status_code == 201, manual.text
    finding_id = FindingRead.model_validate(manual.json()).id
    expected_summaries[finding_id] = sentinel if restricted else "Leverage risk finding."
    assert FindingRead.model_validate(manual.json()).disposition_reason is None
    stored_manual = db_session.get(RiskFinding, finding_id)
    assert stored_manual is not None
    stored_manual.assessment_id = assessment_id
    db_session.commit()
    reason = "Actual cash shortfall is 94821 GHS"
    reviewed = db_client.patch(
        f"/api/v1/findings/{finding_id}",
        headers=headers(),
        json={"status": "acknowledged", "disposition_reason": reason},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert FindingRead.model_validate(reviewed.json()).disposition_reason == (
        reason if restricted else None
    )
    generated = db_client.get(f"/api/v1/assessments/{assessment_id}/findings", headers=headers())
    assert generated.status_code == 200, generated.text
    for finding in TypeAdapter(list[FindingRead]).validate_python(generated.json()):
        updated = db_client.patch(
            f"/api/v1/findings/{finding.id}",
            headers=headers(),
            json={"status": "acknowledged", "disposition_reason": reason},
        )
        assert updated.status_code == 200, updated.text
        assert FindingRead.model_validate(updated.json()).summary == expected_summaries[finding.id]
        assert FindingRead.model_validate(updated.json()).disposition_reason == (
            reason if restricted else None
        )
    for response in (manual, reviewed):
        assert FindingRead.model_validate(response.json()).summary == expected_summaries[finding_id]
        assert (sentinel in response.text) is restricted
        assert bool(FindingRead.model_validate(response.json()).details) is restricted
    for path in (
        "/findings",
        f"/cases/{case_id}/findings",
        f"/assessments/{assessment_id}/findings",
    ):
        response = db_client.get(f"/api/v1{path}", headers=headers())
        assert response.status_code == 200, response.text
        findings = TypeAdapter(list[FindingRead]).validate_python(response.json())
        assert {finding.id for finding in findings} == set(expected_summaries)
        assert all(
            finding.disposition_reason == (reason if restricted else None) for finding in findings
        )
        assert (reason in response.text) is restricted
        for finding in findings:
            assert finding.summary == expected_summaries[finding.id]
            detail = db_client.get(f"/api/v1/findings/{finding.id}", headers=headers())
            assert detail.status_code == 200, detail.text
            assert FindingRead.model_validate(detail.json()).summary == expected_summaries[finding.id]
            assert FindingRead.model_validate(detail.json()).disposition_reason == (
                reason if restricted else None
            )
            if restricted:
                assert FindingRead.model_validate(detail.json()).details
            else:
                assert FindingRead.model_validate(detail.json()).details == {}
                assert FindingRead.model_validate(detail.json()).rationale is None
                if finding.source == FindingSource.MANUAL.value:
                    assert sentinel not in detail.text
    grant(db_session, RoleBundle.VIEWER, SensitivityScope.RESTRICTED)
    full = db_client.get(f"/api/v1/cases/{case_id}/scores", headers=headers())
    assert TypeAdapter(list[ScoreRead]).validate_python(full.json())[0].input_snapshot == {
        "structured_data": {"debt_to_ebitda": 7, "cash_runway_months": 1.25}
    }
    full_finding = db_client.get(f"/api/v1/findings/{finding_id}", headers=headers())
    assert full_finding.status_code == 200, full_finding.text
    assert FindingRead.model_validate(full_finding.json()).summary == sentinel
    assert FindingRead.model_validate(full_finding.json()).details["evidence"] == [
        {"quote": sentinel}
    ]
    assert FindingRead.model_validate(full_finding.json()).disposition_reason == reason
    db_session.expire_all()
    stored = db_session.get(RiskFinding, finding_id)
    assert stored is not None and stored.disposition_reason == reason
    assert stored.summary == sentinel


@pytest.mark.parametrize("restricted", [False, True])
def test_calculation_history_diagnostics_disclosure(
    db_client: TestClient, db_session: Session, restricted: bool
) -> None:
    from datetime import date  # noqa: PLC0415

    from app.models import RiskScenario  # noqa: PLC0415

    case_id = UUID(create(db_client))
    scenario = RiskScenario(
        organization_id=ORG_1,
        case_id=case_id,
        name="Baseline",
        scenario_type="baseline",
        created_by=USER_1,
    )
    db_session.add(scenario)
    db_session.flush()
    run = CalculationRun(
        organization_id=ORG_1,
        case_id=case_id,
        scenario_id=scenario.id,
        status="failed",
        engine_version="test",
        input_schema_version="test",
        output_schema_version="test",
        input_hash="a" * 64,
        inputs={"assumptions": {"growth": "94821"}},
        forecast_periods=3,
        as_of_date=date(2026, 6, 30),
        created_by=USER_1,
        error_code="invalid_assumptions",
        error_message="Invalid growth 94821",
        error_details={"assumptions": {"growth": "94821"}},
    )
    db_session.add(run)
    db_session.commit()
    disclosure_grants(db_session, restricted=restricted)
    response = db_client.get(f"/api/v1/cases/{case_id}/calculation-runs", headers=headers())
    assert response.status_code == 200, response.text
    error = CalculationRunListRead.model_validate(response.json()).runs[0].error
    assert error is not None
    assert error.code == "invalid_assumptions"
    assert ("94821" in response.text) is restricted
    assert error.details == ({"assumptions": {"growth": "94821"}} if restricted else None)


@pytest.mark.parametrize("removal", ["delete", "revoke"])
def test_case_job_polling_uses_current_restricted_authority(
    db_client: TestClient, db_session: Session, removal: str
) -> None:
    _, _, result = _populated_assessment(db_client)
    job_path = f"/api/v1/jobs/{result.job_id}"
    allowed = db_client.get(job_path, headers=headers())
    assert allowed.status_code == 200, allowed.text
    assert JobRead.model_validate(allowed.json()).progress["input_snapshot"] == {
        "structured_data": {"debt_to_ebitda": 7, "cash_runway_months": 1.25}
    }
    assert db_client.get(job_path, headers=headers(ORG_2)).status_code == 404
    rows = list(
        db_session.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.principal_user_id == USER_1,
            )
        )
    )
    for row in rows:
        if removal == "delete":
            db_session.delete(row)
        else:
            row.status = "revoked"
            row.revoked_at = utc_now()
            row.revoked_by_type = "system"
            row.revoked_by_id = "case-authorization-test"
            row.revoked_reason = "Exercise retained job refusal"
    version = authorization.invalidate_user_authorization(
        db_session,
        organization_id=ORG_1,
        user_id=USER_1,
        reason="Job authority removed",
    )
    fresh = headers(authorization_version=version)
    denied = db_client.get(job_path, headers=fresh)
    unknown = db_client.get(f"/api/v1/jobs/{uuid4()}", headers=fresh)
    assert denied.status_code == unknown.status_code == 404
    assert "input_snapshot" not in denied.text
    assert (
        ErrorResponse.model_validate(denied.json()).error.message
        == ErrorResponse.model_validate(unknown.json()).error.message
    )
    grant(db_session)
    assert db_client.get(job_path, headers=headers()).status_code == 404
    grant(db_session, RoleBundle.VIEWER, SensitivityScope.RESTRICTED, bank_id=bank(db_session))
    assert db_client.get(job_path, headers=headers()).status_code == 404
    grant(db_session, RoleBundle.VIEWER, SensitivityScope.RESTRICTED)
    assert db_client.get(job_path, headers=headers()).status_code == 200
    unrelated = Job(
        organization_id=ORG_1, job_type="unrelated", status="queued", progress={"step": 1}
    )
    document = Job(organization_id=ORG_1, job_type="document_parse", status="queued")
    db_session.add_all([unrelated, document])
    db_session.commit()
    disclosure_grants(db_session, restricted=False)
    assert db_client.get(f"/api/v1/jobs/{unrelated.id}", headers=headers()).status_code == 200
    assert db_client.get(f"/api/v1/jobs/{document.id}", headers=headers()).status_code == 404
    grant(db_session, RoleBundle.VIEWER, SensitivityScope.RESTRICTED)
    assert db_client.get(f"/api/v1/jobs/{document.id}", headers=headers()).status_code == 200
