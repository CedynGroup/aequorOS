"""Observable default-deny, scope separation and side-effect tests for stress."""

from __future__ import annotations

from datetime import timedelta
from typing import Never
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.live.public import RegulatoryRun
from app.models import BankReportingPeriod
from app.models.audit_event import AuditEvent
from app.stress.models.stress import (
    EnterpriseStressSignoff,
    MacroScenario,
    ManagementActionItem,
    ManagementActionPlan,
)
from app.stress.schemas.enterprise_stress_signoff import StressSignoffRead
from app.stress.schemas.management_actions import ManagementActionPlanRead
from app.stress.schemas.stress import MacroScenarioListRead, MacroScenarioRead
from app.stress.service import macro_scenarios
from app.stress.service.default_macro_scenarios import DEFAULT_MACRO_SCENARIOS
from tests.data_engine.api.test_ingestion import seed_bank
from tests.stress.authority import grant
from tests.support.helpers import ORG_1, USER_1, headers

URL = "/api/v1/macro-scenarios"


def payload(bank_id: str | None = None) -> dict[str, object]:
    return {
        "code": "authorized_scenario",
        "name": "Authorization boundary scenario",
        "scenario_type": "adverse",
        "severity": "severe",
        "horizon_years": 3,
        "bank_id": bank_id,
        "reason": "Exercise exact scoped authority",
        "paths": [
            {"variable": name, "year_index": 1, "base_value": base, "stress_value": stress}
            for name, base, stress in (
                ("interest_rate", "0.20", "0.25"),
                ("inflation", "0.15", "0.15"),
                ("gdp_growth", "0.05", "0.00"),
                ("fx_usd_ghs", "12.5", "15.0"),
            )
        ],
    }


@pytest.fixture
def unbound(db_client: TestClient, db_session: Session) -> TestClient:
    db_session.execute(
        delete(AuthorizationBinding).where(
            AuthorizationBinding.principal_user_id == USER_1,
            AuthorizationBinding.role_bundle != "member",
        )
    )
    db_session.commit()
    return db_client


@pytest.mark.parametrize("role", ["admin", "approver", "analyst", "viewer"])
def test_scalar_claims_never_authorize_a_write(
    unbound: TestClient, db_session: Session, role: str
) -> None:
    response = unbound.post(URL, headers=headers(roles=(role,)), json=payload())
    assert response.status_code == 403, response.text
    assert db_session.scalar(select(func.count()).select_from(MacroScenario)) == 0


def test_scoped_analyst_can_write_with_a_scalar_viewer_claim(
    unbound: TestClient, db_session: Session
) -> None:
    grant(db_session)
    response = unbound.post(URL, headers=headers(roles=("viewer",)), json=payload())
    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == str(USER_1)


@pytest.mark.parametrize(
    "state", ["expired", "revoked", "future", "wrong_module", "wrong_sensitivity"]
)
def test_incomplete_or_inactive_grants_have_no_effect(
    unbound: TestClient, db_session: Session, state: str
) -> None:
    binding_id = grant(
        db_session,
        module=ModuleScope.CAPITAL if state == "wrong_module" else ModuleScope.RISK,
        sensitivity=(
            SensitivityScope.AGGREGATED
            if state == "wrong_sensitivity"
            else SensitivityScope.CONFIDENTIAL
        ),
    )
    binding = db_session.get(AuthorizationBinding, binding_id)
    assert binding is not None
    if state == "expired":
        binding.valid_from = utc_now() - timedelta(days=2)
        binding.valid_until = utc_now() - timedelta(seconds=1)
    elif state == "future":
        binding.valid_from = utc_now() + timedelta(days=1)
    elif state == "revoked":
        binding.status = "revoked"
        binding.revoked_at = utc_now()
        binding.revoked_by_type = "system"
        binding.revoked_by_id = "stress-authorization-test"
        binding.revoked_reason = "Exercise a revoked binding"
    db_session.commit()
    response = unbound.post(URL, headers=headers(), json=payload())
    assert response.status_code == 403, response.text
    assert db_session.scalar(select(func.count()).select_from(MacroScenario)) == 0


def test_partial_bindings_do_not_compose(unbound: TestClient, db_session: Session) -> None:
    grant(db_session, module=ModuleScope.RISK, sensitivity=SensitivityScope.AGGREGATED)
    grant(db_session, module=ModuleScope.CAPITAL, sensitivity=SensitivityScope.CONFIDENTIAL)
    response = unbound.post(URL, headers=headers(), json=payload())
    assert response.status_code == 403, response.text


def test_exact_bank_grant_never_covers_organization_scenarios(
    unbound: TestClient, db_session: Session
) -> None:
    bank_id = seed_bank(unbound)
    grant(db_session, bank_id=bank_id)
    denied = unbound.post(URL, headers=headers(), json=payload())
    assert denied.status_code == 403
    allowed = unbound.post(URL, headers=headers(roles=("viewer",)), json=payload(bank_id=bank_id))
    assert allowed.status_code == 201, allowed.text
    listing = unbound.get(URL, headers=headers())
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert [row.id for row in MacroScenarioListRead.model_validate(listing.json()).scenarios] == [
        MacroScenarioRead.model_validate(allowed.json()).id
    ]


def test_revoked_resource_disappears_and_cannot_be_edited(
    unbound: TestClient, db_session: Session
) -> None:
    binding_id = grant(db_session)
    created = unbound.post(URL, headers=headers(), json=payload())
    assert created.status_code == 201, created.text
    scenario_id = MacroScenarioRead.model_validate(created.json()).id
    db_session.execute(delete(AuthorizationBinding).where(AuthorizationBinding.id == binding_id))
    db_session.commit()
    response = unbound.patch(
        f"{URL}/{scenario_id}", headers=headers(), json={"name": "forbidden", "reason": "test"}
    )
    assert response.status_code == 404, response.text
    row = db_session.get(MacroScenario, scenario_id)
    assert row is not None and row.name != "forbidden"
    listing = unbound.get(URL, headers=headers())
    assert listing.json() == {"scenarios": [], "total": 0}


def test_translation_requires_both_risk_and_target_module(
    unbound: TestClient, db_session: Session
) -> None:
    grant(db_session)
    created = unbound.post(URL, headers=headers(), json=payload())
    assert created.status_code == 201, created.text
    url = f"{URL}/{created.json()['id']}/translation/liquidity"
    denied = unbound.get(url, headers=headers())
    assert denied.status_code == 404, denied.text
    grant(db_session, RoleBundle.VIEWER, module=ModuleScope.LIQUIDITY)
    allowed = unbound.get(url, headers=headers())
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["module"] == "liquidity"


def test_unknown_bank_refuses_without_creating_a_scenario(
    unbound: TestClient, db_session: Session
) -> None:
    grant(db_session)
    response = unbound.post(URL, headers=headers(), json=payload(bank_id="BK-1S000099"))
    assert response.status_code == 404
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MacroScenario)
            .where(MacroScenario.organization_id == ORG_1)
        )
        == 0
    )


def test_exact_bank_grant_hides_sibling_bank_rows_and_refuses_edits(
    unbound: TestClient, db_session: Session
) -> None:
    bank_id = seed_bank(unbound)
    bank = db_session.get(Bank, bank_id)
    assert bank is not None
    sibling = Bank(
        id="BK-1S000002",
        organization_id=ORG_1,
        name="Sibling institution",
        short_name="Sibling",
        currency=bank.currency,
        jurisdiction_code=bank.jurisdiction_code,
        license_type=bank.license_type,
        institution_type=bank.institution_type,
    )
    db_session.add(sibling)
    db_session.commit()
    broad_binding_id = grant(db_session)
    own = unbound.post(URL, headers=headers(), json=payload(bank_id))
    sibling_created = unbound.post(
        URL,
        headers=headers(),
        json=payload(sibling.id) | {"code": "sibling_scenario"},
    )
    assert own.status_code == sibling_created.status_code == 201
    sibling_id = MacroScenarioRead.model_validate(sibling_created.json()).id
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.id == broad_binding_id)
    )
    grant(db_session, bank_id=bank_id)

    hidden = unbound.get(f"{URL}/{sibling_id}", headers=headers())
    refused = unbound.patch(
        f"{URL}/{sibling_id}",
        headers=headers(),
        json={"name": "Forbidden sibling edit", "reason": "Scope boundary probe"},
    )
    assert hidden.status_code == refused.status_code == 404
    listing = MacroScenarioListRead.model_validate(unbound.get(URL, headers=headers()).json())
    assert listing.total == 1
    assert [row.id for row in listing.scenarios] == [
        MacroScenarioRead.model_validate(own.json()).id
    ]
    sibling_row = db_session.get(MacroScenario, sibling_id)
    assert sibling_row is not None and sibling_row.name != "Forbidden sibling edit"


@pytest.mark.parametrize("bank_scoped", [False, True])
def test_evaluator_failure_refuses_without_side_effects(
    unbound: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    bank_scoped: bool,
) -> None:
    bank_id = seed_bank(unbound) if bank_scoped else None
    grant(db_session, bank_id=bank_id)

    def unavailable(*_args: object, **_kwargs: object) -> Never:
        raise RuntimeError("Evaluator unavailable")

    monkeypatch.setattr("app.identity.public.evaluate_permission", unavailable)
    monkeypatch.setattr("app.identity.service.authorization.evaluate_permission", unavailable)
    response = unbound.post(URL, headers=headers(), json=payload(bank_id))
    assert response.status_code == 403, response.text
    assert db_session.scalar(select(func.count()).select_from(MacroScenario)) == 0


@pytest.mark.parametrize("resource", ["plan", "signoff", "run"])
def test_viewer_cannot_start_other_stress_writes(
    unbound: TestClient, db_session: Session, resource: str
) -> None:
    bank_id = seed_bank(unbound)
    grant(db_session, RoleBundle.VIEWER, bank_id=bank_id)
    requests: dict[str, tuple[str, dict[str, object]]] = {
        "plan": (
            "/api/v1/management-action-plans",
            {
                "bank_id": bank_id,
                "code": "denied_recovery_plan",
                "name": "Denied recovery plan",
                "actions": [
                    {
                        "action_id": "dividend_suspension",
                        "kind": "revise_dividend",
                        "label": "Suspend dividends",
                        "trigger_kind": "on_breach",
                        "effective_year": 1,
                        "dividend_reduction_pct": "100",
                        "rationale": "Retain earnings under stress.",
                    }
                ],
                "reason": "Exercise the preparation authority boundary.",
            },
        ),
        "signoff": (
            f"/api/v1/banks/{bank_id}/enterprise-stress/signoffs",
            {
                "run_id": str(uuid4()),
                "scenario_narrative": "An adverse scenario.",
                "assumptions_rationale": "Documented assumptions.",
                "reason": "Exercise the preparation authority boundary.",
            },
        ),
        "run": (
            f"/api/v1/banks/{bank_id}/enterprise-stress/runs",
            {
                "scenario_id": str(uuid4()),
                "reporting_period_id": str(uuid4()),
                "reason": "Exercise the execution authority boundary.",
            },
        ),
    }
    tracked_models = (
        AuditEvent,
        MacroScenario,
        ManagementActionPlan,
        ManagementActionItem,
        EnterpriseStressSignoff,
        RegulatoryRun,
    )
    before = tuple(
        db_session.scalar(select(func.count()).select_from(model)) for model in tracked_models
    )
    url, body = requests[resource]
    response = unbound.post(url, headers=headers(roles=("admin", "approver", "analyst")), json=body)
    assert response.status_code == 403, response.text
    after = tuple(
        db_session.scalar(select(func.count()).select_from(model)) for model in tracked_models
    )
    assert after == before


def _prepare_hidden_resource(
    client: TestClient, db_session: Session, resource: str, bank_id: str, bank_scoped: bool
) -> tuple[str, UUID]:
    scope_bank = bank_id if bank_scoped else None
    if resource == "system":
        object_id = DEFAULT_MACRO_SCENARIOS[0].id
        collection = URL
    elif resource == "scenario":
        created = client.post(URL, headers=headers(), json=payload(scope_bank))
        assert created.status_code == 201, created.text
        object_id = MacroScenarioRead.model_validate(created.json()).id
        collection = URL
    elif resource == "plan":
        collection = "/api/v1/management-action-plans"
        created = client.post(
            collection,
            headers=headers(),
            json={
                "bank_id": scope_bank,
                "code": "opaque_plan",
                "name": "Opaque plan",
                "actions": [
                    {
                        "action_id": "retain",
                        "kind": "revise_dividend",
                        "label": "Retain earnings",
                        "trigger_kind": "on_breach",
                        "dividend_reduction_pct": "100",
                    }
                ],
                "reason": "Prepare an authorization regression fixture",
            },
        )
        assert created.status_code == 201, created.text
        object_id = ManagementActionPlanRead.model_validate(created.json()).id
    else:
        period = db_session.scalar(
            select(BankReportingPeriod).where(
                BankReportingPeriod.bank_id == bank_id,
                BankReportingPeriod.organization_id == ORG_1,
            )
        )
        assert period is not None
        run = RegulatoryRun(
            organization_id=ORG_1,
            bank_id=bank_id,
            reporting_period_id=period.id,
            module="enterprise_stress",
            scenario_code="opaque_scenario",
            status="succeeded",
            engine_version="test",
            input_schema_version="test",
            output_schema_version="test",
            input_hash="a" * 64,
            created_by=USER_1,
            inputs={"scenario": {"code": "opaque_scenario"}},
            metrics={},
        )
        db_session.add(run)
        db_session.commit()
        collection = f"/api/v1/banks/{bank_id}/enterprise-stress/signoffs"
        created = client.post(
            collection,
            headers=headers(),
            json={
                "run_id": str(run.id),
                "scenario_narrative": "Adverse conditions",
                "assumptions_rationale": "Documented assumptions",
                "reason": "Prepare an authorization regression fixture",
            },
        )
        assert created.status_code == 201, created.text
        object_id = StressSignoffRead.model_validate(created.json()).id
    return collection, object_id


@pytest.mark.parametrize(
    ("resource", "bank_scoped"),
    [
        ("scenario", False),
        ("scenario", True),
        ("system", False),
        ("plan", False),
        ("plan", True),
        ("signoff", True),
    ],
)
def test_hidden_and_missing_stress_objects_have_identical_responses(
    unbound: TestClient, db_session: Session, resource: str, bank_scoped: bool
) -> None:
    bank_id = seed_bank(unbound)
    binding_id = grant(db_session)
    collection, object_id = _prepare_hidden_resource(
        unbound, db_session, resource, bank_id, bank_scoped
    )
    original = unbound.get(f"{collection}/{object_id}", headers=headers())
    assert original.status_code == 200, original.text
    db_session.execute(delete(AuthorizationBinding).where(AuthorizationBinding.id == binding_id))
    db_session.commit()
    tracked_models = (
        AuditEvent,
        MacroScenario,
        ManagementActionPlan,
        ManagementActionItem,
        EnterpriseStressSignoff,
        RegulatoryRun,
    )
    before = tuple(
        db_session.scalar(select(func.count()).select_from(model)) for model in tracked_models
    )
    reason: dict[str, object] = {"reason": "Probe an opaque object boundary"}
    operations: list[tuple[str, str, dict[str, object] | None]] = [
        ("GET", "", None),
        ("PATCH", "", reason),
        ("POST", "/submit", reason),
    ]
    if resource == "signoff":
        operations.extend(
            [
                ("POST", "/withdraw", reason),
                ("POST", "/attest", reason | {"credibility_rationale": "Board review"}),
            ]
        )
    else:
        operations.extend([("POST", "/approve", reason), ("POST", "/archive", reason)])
    if resource in {"scenario", "system"}:
        operations.extend(
            ("GET", f"/translation/{module}", None)
            for module in ("liquidity", "capital", "irr", "fx", "ftp")
        )
        operations.append(("POST", "/clone", reason))
    probe_headers = headers(roles=("admin", "approver", "analyst")) | {
        "X-Request-ID": "opaque-stress-regression",
    }
    for method, suffix, body in operations:
        hidden = unbound.request(
            method, f"{collection}/{object_id}{suffix}", headers=probe_headers, json=body
        )
        missing = unbound.request(
            method, f"{collection}/{uuid4()}{suffix}", headers=probe_headers, json=body
        )
        assert hidden.status_code == missing.status_code == 404, (suffix, hidden.text, missing.text)
        assert hidden.json() == missing.json(), (suffix, hidden.text, missing.text)
    if resource in {"scenario", "system"}:
        ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)
        with pytest.raises(HTTPException) as hidden_run:
            macro_scenarios.resolve_for_official_run(db_session, ctx, object_id)
        with pytest.raises(HTTPException) as missing_run:
            macro_scenarios.resolve_for_official_run(db_session, ctx, uuid4())
        assert hidden_run.value.status_code == missing_run.value.status_code == 404
        assert hidden_run.value.detail == missing_run.value.detail
    after = tuple(
        db_session.scalar(select(func.count()).select_from(model)) for model in tracked_models
    )
    assert after == before
    grant(db_session)
    restored = unbound.get(f"{collection}/{object_id}", headers=headers())
    assert restored.status_code == 200, restored.text
    assert restored.json() == original.json()
