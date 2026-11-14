"""Capital scenario-workbench authority: exact CAP bindings, no scalar ladder.

The capital workbench rides the shared scenario-workbench routes. Every action
requires a complete CAPITAL binding at the same sensitivity the other engine
modules use, decided in the service before any side effect; token roles decide
nothing.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.identity.models.bank import Bank
from app.identity.public import DEFAULT_DENIAL_DETAIL
from app.identity.service import authorization
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    BankReportingPeriod,
    SavedScenarioAnalysis,
    StressScenario,
    User,
)
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from app.stress.service import analysis_workbench
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/scenario-workbench/capital"
SIBLING_BANK_ID = "BK-CAPWB0002"
FOREIGN_BANK_ID = "BK-NOSUCH00"
CAPITAL_SHOCKS = {"ecl_pd_multiplier": "1.15", "ecl_lgd_multiplier": "1.10"}


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        if session.get(Bank, SAMPLE_BANK_ID) is None:
            session.add(
                Bank(
                    id=SAMPLE_BANK_ID,
                    organization_id=ORG_1,
                    name="Capital workbench authority bank",
                    short_name="Capital workbench",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
        session.commit()
    finally:
        session.close()


def _seed_book() -> UUID:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        period_id = session.scalar(
            select(BankReportingPeriod.id)
            .where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
        assert period_id is not None
        session.commit()
        return period_id
    finally:
        session.close()


def _add_sibling_bank() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            Bank(
                id=SIBLING_BANK_ID,
                organization_id=ORG_1,
                name="Capital workbench sibling bank",
                short_name="Capital sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _grant(
    *,
    role_bundle: RoleBundle = RoleBundle.ANALYST,
    institution_id: str | None = SAMPLE_BANK_ID,
    module_scope: ModuleScope = ModuleScope.CAPITAL,
    sensitivity_scope: SensitivityScope = SensitivityScope.CONFIDENTIAL,
) -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        user = session.get(User, USER_1)
        assert user is not None
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                institution_id,
                module_scope,
                sensitivity_scope,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "capital-workbench-test"),
            reason="Exercise capital scenario-workbench authority.",
        )
        session.refresh(user)
        return user.authorization_version
    finally:
        session.close()


def _scenario_count(module: str) -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        count = (
            session.scalar(
                select(func.count())
                .select_from(StressScenario)
                .where(
                    StressScenario.organization_id == ORG_1,
                    StressScenario.module == module,
                )
            )
            or 0
        )
        session.commit()
        return int(count)
    finally:
        session.close()


def _analysis_count(module: str) -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        count = (
            session.scalar(
                select(func.count())
                .select_from(SavedScenarioAnalysis)
                .where(
                    SavedScenarioAnalysis.organization_id == ORG_1,
                    SavedScenarioAnalysis.module == module,
                )
            )
            or 0
        )
        session.commit()
        return int(count)
    finally:
        session.close()


def _audit_count() -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        count = (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.organization_id == ORG_1)
            )
            or 0
        )
        session.commit()
        return int(count)
    finally:
        session.close()


def _seed_saved_analysis(period_id: UUID) -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        row = SavedScenarioAnalysis(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            module="capital",
            reporting_period_id=period_id,
            name="Capital workbench authority snapshot",
            engine_version="test-capital-v1",
            scenarios=[],
            results=[],
            created_by=USER_1,
        )
        session.add(row)
        session.commit()
        return row.id
    finally:
        session.close()


def test_exact_capital_binding_allows_the_whole_workbench(db_client: TestClient) -> None:
    period_id = _seed_book()
    version = _grant()
    auth = headers(roles=("analyst",), authorization_version=version)

    catalogue = db_client.get(f"{BASE}/scenarios", headers=auth)
    assert catalogue.status_code == 200, catalogue.text
    assert catalogue.json()["module"] == "capital"
    assert "ecl_pd_multiplier" in catalogue.json()["allowed_keys"]

    created = db_client.post(
        f"{BASE}/scenarios",
        headers=auth,
        json={
            "code": "cap_authority_case",
            "name": "Capital authority case",
            "shocks": CAPITAL_SHOCKS,
        },
    )
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]

    edited = db_client.patch(
        f"{BASE}/scenarios/{scenario_id}",
        headers=auth,
        json={"name": "Capital authority case, renamed"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Capital authority case, renamed"

    archived = db_client.post(
        f"{BASE}/scenarios/{scenario_id}/archive",
        headers=auth,
        json={"is_archived": True},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["is_archived"] is True

    run = db_client.post(
        f"{BASE}/analysis",
        headers=auth,
        json={
            "reporting_period_id": str(period_id),
            "scenarios": [
                {"kind": "system", "code": "baseline"},
                {"kind": "system", "code": "severe"},
            ],
        },
    )
    assert run.status_code == 200, run.text
    assert run.json()["module"] == "capital"
    assert [entry["status"] for entry in run.json()["results"]] == ["succeeded", "succeeded"]

    saved = db_client.post(
        f"{BASE}/analyses",
        headers=auth,
        json={
            "reporting_period_id": str(period_id),
            "name": "Capital authority snapshot",
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    assert saved.status_code == 201, saved.text
    analysis_id = saved.json()["id"]

    listed = db_client.get(f"{BASE}/analyses", headers=auth)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    detail = db_client.get(f"{BASE}/analyses/{analysis_id}", headers=auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "Capital authority snapshot"

    deleted = db_client.delete(f"{BASE}/analyses/{analysis_id}", headers=auth)
    assert deleted.status_code == 204, deleted.text
    assert db_client.get(f"{BASE}/analyses", headers=auth).json()["total"] == 0


def test_capital_workbench_denies_every_action_without_a_scoped_binding(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_id = _seed_book()
    analysis_id = _seed_saved_analysis(period_id)
    scenarios_before = _scenario_count("capital")
    analyses_before = _analysis_count("capital")
    audits_before = _audit_count()

    compute_calls: list[str] = []

    def compute_probe(*_args: object, **_kwargs: object) -> None:
        compute_calls.append("computed")
        raise AssertionError("denied run must not compute")

    monkeypatch.setattr(analysis_workbench, "_compute_one", compute_probe)

    # The token carries the scalar analyst role; with no scoped CAPITAL binding
    # it must not authorize anything on the workbench.
    auth = headers(roles=("analyst",))

    denied_create = db_client.post(
        f"{BASE}/scenarios",
        headers=auth,
        json={"code": "cap_denied_case", "name": "Denied", "shocks": CAPITAL_SHOCKS},
    )
    assert denied_create.status_code == 403
    assert denied_create.json()["error"]["message"] == DEFAULT_DENIAL_DETAIL

    assert (
        db_client.patch(
            f"{BASE}/scenarios/{uuid4()}",
            headers=auth,
            json={"name": "Denied edit"},
        ).status_code
        == 403
    )
    assert (
        db_client.post(
            f"{BASE}/scenarios/{uuid4()}/archive",
            headers=auth,
            json={"is_archived": True},
        ).status_code
        == 403
    )

    denied_run = db_client.post(
        f"{BASE}/analysis",
        headers=auth,
        json={
            "reporting_period_id": str(period_id),
            "scenarios": [{"kind": "system", "code": "baseline"}],
        },
    )
    assert denied_run.status_code == 403
    assert compute_calls == []

    assert db_client.delete(f"{BASE}/analyses/{analysis_id}", headers=auth).status_code == 403
    assert db_client.get(f"{BASE}/scenarios", headers=auth).status_code == 403
    assert db_client.get(f"{BASE}/analyses", headers=auth).status_code == 403

    assert _scenario_count("capital") == scenarios_before
    assert _analysis_count("capital") == analyses_before
    assert _audit_count() == audits_before


def test_capital_workbench_refuses_sibling_and_foreign_banks(db_client: TestClient) -> None:
    _seed_book()
    _add_sibling_bank()
    version = _grant()
    auth = headers(roles=("analyst",), authorization_version=version)
    sibling_base = f"/api/v1/banks/{SIBLING_BANK_ID}/scenario-workbench/capital"

    denied_catalogue = db_client.get(f"{sibling_base}/scenarios", headers=auth)
    assert denied_catalogue.status_code == 403
    assert denied_catalogue.json()["error"]["message"] == DEFAULT_DENIAL_DETAIL

    denied_create = db_client.post(
        f"{sibling_base}/scenarios",
        headers=auth,
        json={"code": "cap_sibling_case", "name": "Sibling", "shocks": CAPITAL_SHOCKS},
    )
    assert denied_create.status_code == 403
    assert _scenario_count("capital") == 0

    # A bank of another organization is indistinguishable from one that does
    # not exist: the same opaque 404 body as the missing id.
    foreign = db_client.get(
        f"{BASE}/scenarios",
        headers=headers(org_id=ORG_2),
    )
    missing = db_client.get(
        f"/api/v1/banks/{FOREIGN_BANK_ID}/scenario-workbench/capital/scenarios",
        headers=headers(org_id=ORG_2),
    )
    assert foreign.status_code == missing.status_code == 404
    foreign_body = foreign.json()["error"]
    missing_body = missing.json()["error"]
    assert (foreign_body["code"], foreign_body["message"]) == (
        missing_body["code"],
        missing_body["message"],
    )
    assert foreign_body["message"] == "Bank not found."


def test_capital_workbench_refuses_a_different_module_grant(db_client: TestClient) -> None:
    _seed_book()
    version = _grant(module_scope=ModuleScope.LIQUIDITY)
    auth = headers(roles=("analyst",), authorization_version=version)

    assert db_client.get(f"{BASE}/scenarios", headers=auth).status_code == 403
    denied_create = db_client.post(
        f"{BASE}/scenarios",
        headers=auth,
        json={"code": "cap_wrong_module", "name": "Wrong module", "shocks": CAPITAL_SHOCKS},
    )
    assert denied_create.status_code == 403
    assert _scenario_count("capital") == 0


def test_capital_reads_follow_the_confidential_sensitivity(db_client: TestClient) -> None:
    period_id = _seed_book()
    analysis_id = _seed_saved_analysis(period_id)

    aggregated_version = _grant(
        role_bundle=RoleBundle.VIEWER,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )
    aggregated_auth = headers(roles=("viewer",), authorization_version=aggregated_version)
    assert db_client.get(f"{BASE}/scenarios", headers=aggregated_auth).status_code == 403
    assert db_client.get(f"{BASE}/analyses", headers=aggregated_auth).status_code == 403
    # The detail route answers its denials with the opaque not-found shape.
    denied_detail = db_client.get(f"{BASE}/analyses/{analysis_id}", headers=aggregated_auth)
    assert denied_detail.status_code == 404
    assert denied_detail.json()["error"]["message"] == "Analysis not found."

    confidential_version = _grant(role_bundle=RoleBundle.VIEWER)
    confidential_auth = headers(roles=("viewer",), authorization_version=confidential_version)
    allowed_catalogue = db_client.get(f"{BASE}/scenarios", headers=confidential_auth)
    assert allowed_catalogue.status_code == 200, allowed_catalogue.text
    allowed_list = db_client.get(f"{BASE}/analyses", headers=confidential_auth)
    assert allowed_list.status_code == 200, allowed_list.text
    assert allowed_list.json()["total"] == 1
    allowed_detail = db_client.get(f"{BASE}/analyses/{analysis_id}", headers=confidential_auth)
    assert allowed_detail.status_code == 200, allowed_detail.text


def test_capital_writes_require_the_bundle_permissions(db_client: TestClient) -> None:
    period_id = _seed_book()
    analysis_id = _seed_saved_analysis(period_id)
    scenarios_before = _scenario_count("capital")

    # A view-only bundle at the right sensitivity still cannot write or run.
    version = _grant(role_bundle=RoleBundle.VIEWER)
    auth = headers(roles=("viewer",), authorization_version=version)

    denied_create = db_client.post(
        f"{BASE}/scenarios",
        headers=auth,
        json={"code": "cap_viewer_case", "name": "Viewer", "shocks": CAPITAL_SHOCKS},
    )
    assert denied_create.status_code == 403
    assert (
        db_client.post(
            f"{BASE}/analysis",
            headers=auth,
            json={
                "reporting_period_id": str(period_id),
                "scenarios": [{"kind": "system", "code": "baseline"}],
            },
        ).status_code
        == 403
    )
    assert db_client.delete(f"{BASE}/analyses/{analysis_id}", headers=auth).status_code == 403
    assert _scenario_count("capital") == scenarios_before
    assert _analysis_count("capital") == 1
