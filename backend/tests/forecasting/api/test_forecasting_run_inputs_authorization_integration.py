"""Forecasting run inputs are restricted-tier content.

The forecasting run detail and the run POST response stay readable at
Forecasting confidential view, but the stored input snapshot travels only to a
reader who ALSO holds Forecasting restricted view on that bank — the same rule
the shared regulatory-run detail enforces through the same run-module map. All
four Forecasting run modules (forecast, optimizer, what-if, reverse-stress)
answer to one authority, so a confidential-only reader cannot read the stored
snapshot back through this route either.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    AuthorizationBinding,
    Bank,
    BankReportingPeriod,
    RegulatoryRun,
    User,
)
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.helpers import ORG_1, USER_1, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast"
SIBLING_BANK_ID = "BK-FCSTINP1"
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)
STORED_INPUTS = {
    "parameters": {"lcr_threshold": "0.45"},
    "assumptions": {
        "loan_growth_pct": "12",
        "deposit_growth_pct": "10",
        "nim_pct": "5",
        "cost_to_income_pct": "48",
        "credit_loss_rate_pct": "1.0",
        "fx_depreciation_pct": "0",
        "dividend_payout_pct": "30",
        "fee_income_pct_assets": "3",
        "tax_rate_pct": "25",
        "securities_shift_pp": "0",
    },
}


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()


def _grant(
    bundle: RoleBundle,
    *,
    sensitivity: SensitivityScope,
    module: ModuleScope = ModuleScope.FORECASTING,
    institution_id: str = SAMPLE_BANK_ID,
) -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=institution_id,
                module_scope=module,
                sensitivity_scope=sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "run-inputs-test"),
            reason="exercise the forecasting run-input availability contract",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return user.authorization_version
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
                name="Forecasting run-input sibling bank",
                short_name="Run-input sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _add_forecast_run(period_id: UUID) -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        run = RegulatoryRun(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            reporting_period_id=period_id,
            module="forecast",
            scenario_code="base",
            status="succeeded",
            engine_version="test-forecasting-v1",
            input_schema_version="test-input-v1",
            output_schema_version="test-output-v1",
            input_hash=uuid4().hex * 2,
            inputs=deepcopy(STORED_INPUTS),
            metrics={},
            parameter_provenance=[],
            created_by=USER_1,
        )
        session.add(run)
        session.commit()
        return run.id
    finally:
        session.close()


def _stored_run(run_id: UUID) -> dict[str, Any]:
    session = get_sessionmaker()()
    try:
        run = session.scalar(
            select(RegulatoryRun).where(
                RegulatoryRun.organization_id == ORG_1,
                RegulatoryRun.id == run_id,
            )
        )
        assert run is not None
        return {
            "id": str(run.id),
            "bank_id": run.bank_id,
            "module": run.module,
            "scenario_code": run.scenario_code,
            "status": run.status,
            "input_hash": run.input_hash,
            "inputs": deepcopy(run.inputs),
        }
    finally:
        session.close()


def _auth(version: int) -> dict[str, str]:
    return headers(ORG_1, user_id=USER_1, roles=("viewer",), authorization_version=version)


def _assert_withheld(body: dict[str, Any]) -> None:
    assert body["inputs"] is None
    assert body["inputs_available"] is False
    assert body["inputs_unavailable_reason"] == "restricted_view_required"


def _assert_available(body: dict[str, Any], run_id: UUID) -> None:
    assert body["inputs_available"] is True
    assert body["inputs_unavailable_reason"] is None
    assert body["inputs"] == _stored_run(run_id)["inputs"]


def test_confidential_only_reader_gets_redacted_inputs_with_the_flag(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    run_id = _add_forecast_run(period_id)
    version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.CONFIDENTIAL)

    response = db_client.get(f"{BASE}/runs/{run_id}", headers=_auth(version))

    assert response.status_code == 200, response.text
    body = response.json()
    _assert_withheld(body)
    # Everything except the withheld snapshot is the stored run, unchanged.
    stored = _stored_run(run_id)
    for field in ("id", "bank_id", "module", "scenario_code", "status", "input_hash"):
        assert body[field] == stored[field], field


def test_restricted_view_reader_gets_the_stored_inputs(db_client: TestClient) -> None:
    period_id = _seed_book()
    run_id = _add_forecast_run(period_id)
    version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.CONFIDENTIAL)
    version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.RESTRICTED)

    response = db_client.get(f"{BASE}/runs/{run_id}", headers=_auth(version))

    assert response.status_code == 200, response.text
    _assert_available(response.json(), run_id)


def test_all_sensitivity_reader_gets_the_stored_inputs(db_client: TestClient) -> None:
    period_id = _seed_book()
    run_id = _add_forecast_run(period_id)
    version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.ALL)

    response = db_client.get(f"{BASE}/runs/{run_id}", headers=_auth(version))

    assert response.status_code == 200, response.text
    _assert_available(response.json(), run_id)


def test_a_restricted_grant_on_a_sibling_bank_does_not_reveal_inputs(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    _add_sibling_bank()
    run_id = _add_forecast_run(period_id)
    version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.CONFIDENTIAL)
    version = _grant(
        RoleBundle.VIEWER,
        sensitivity=SensitivityScope.RESTRICTED,
        institution_id=SIBLING_BANK_ID,
    )

    response = db_client.get(f"{BASE}/runs/{run_id}", headers=_auth(version))

    assert response.status_code == 200, response.text
    _assert_withheld(response.json())


def test_run_post_response_redacts_inputs_for_a_confidential_only_runner(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    created = db_client.post(
        f"{BASE}/runs",
        headers=_auth(version),
        json={"reporting_period_id": str(period_id), "scenario_code": "base"},
    )

    assert created.status_code == 201, created.text
    body = created.json()
    _assert_withheld(body)
    assert body["module"] == "forecast"

    # The same run served to a reader who then holds restricted view carries
    # the snapshot; the withheld POST response stays a withheld one.
    restricted_version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.RESTRICTED)
    detail = db_client.get(f"{BASE}/runs/{body['id']}", headers=_auth(restricted_version))
    assert detail.status_code == 200, detail.text
    assert detail.json()["inputs_available"] is True
    assert detail.json()["inputs"] == _stored_run(UUID(body["id"]))["inputs"]


def test_the_forecasting_run_list_serves_summaries_without_inputs(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    _add_forecast_run(period_id)
    version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.ALL)

    response = db_client.get(f"{BASE}/runs", headers=_auth(version))

    assert response.status_code == 200, response.text
    for summary in response.json()["runs"]:
        assert "inputs" not in summary
        assert "inputs_available" not in summary
