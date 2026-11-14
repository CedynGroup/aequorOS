"""Regulatory run inputs are restricted-tier content.

The run detail and the run POST responses stay readable at confidential VIEW,
but the stored input snapshot travels only to a reader who ALSO holds the run
module's restricted VIEW on that bank. The response says so explicitly
(``inputs_available`` with a reason) instead of either failing or serving the
snapshot silently. ``RegulatoryRunRead`` is the one shared run contract every
engine's run responses flow through, so one redaction covers liquidity,
capital, credit, FX, FTP and the IRRBB embedding.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

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
from app.liquidity.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.liquidity.service import regulatory_liquidity
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

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SIBLING_BANK_ID = "BK-RUNINP001"
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


def _drop_bindings() -> None:
    session = get_sessionmaker()()
    try:
        session.execute(delete(AuthorizationBinding))
        session.commit()
    finally:
        session.close()


def _grant(
    bundle: RoleBundle,
    *,
    sensitivity: SensitivityScope,
    module: ModuleScope = ModuleScope.LIQUIDITY,
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
            reason="exercise the run-input availability contract",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return user.authorization_version
    finally:
        session.close()


def _seed_book() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _latest_period_id() -> str:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        period_id = session.scalar(
            select(BankReportingPeriod.id)
            .where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
        assert period_id is not None
        return str(period_id)
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
                name="Run inputs sibling bank",
                short_name="Sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _mint_baseline_run() -> UUID:
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
        run = regulatory_liquidity.create_liquidity_run(
            session,
            CTX,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="liquidity",
                reporting_period_id=period_id,
                scenario_code="baseline",
            ),
        )
        session.commit()
        return run.id
    finally:
        session.close()


def _stored_run(run_id: UUID) -> dict[str, Any]:
    session = get_sessionmaker()()
    try:
        run = session.scalar(select(RegulatoryRun).where(RegulatoryRun.id == run_id))
        assert run is not None
        return {
            "id": str(run.id),
            "bank_id": run.bank_id,
            "module": run.module,
            "scenario_code": run.scenario_code,
            "status": run.status,
            "input_hash": run.input_hash,
            "metrics": run.metrics,
            "error": None if not run.error_code else {"code": run.error_code},
            "created_by": str(run.created_by),
            "inputs": deepcopy(run.inputs),
        }
    finally:
        session.close()


def _auth(version: int) -> dict[str, str]:
    return headers(ORG_1, user_id=USER_1, roles=("admin",), authorization_version=version)


def _assert_withheld(body: dict[str, Any]) -> None:
    assert body["inputs"] is None
    assert body["inputs_available"] is False
    assert isinstance(body["inputs_unavailable_reason"], str)
    assert body["inputs_unavailable_reason"]


def _assert_available(body: dict[str, Any], run_id: UUID) -> None:
    assert body["inputs_available"] is True
    assert body["inputs_unavailable_reason"] is None
    assert body["inputs"] == _stored_run(run_id)["inputs"]


def test_confidential_only_reader_gets_redacted_inputs_with_the_flag(
    db_client: TestClient,
) -> None:
    _drop_bindings()
    run_id = _mint_baseline_run()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    response = db_client.get(
        f"{BASE}/regulatory-runs/{run_id}",
        headers=_auth(version),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    _assert_withheld(body)
    # Everything except the withheld snapshot is the stored run, unchanged.
    stored = _stored_run(run_id)
    for field in (
        "id",
        "bank_id",
        "module",
        "scenario_code",
        "status",
        "input_hash",
        "metrics",
        "error",
        "created_by",
    ):
        assert body[field] == stored[field], field


def test_restricted_view_reader_gets_the_stored_inputs(db_client: TestClient) -> None:
    _drop_bindings()
    run_id = _mint_baseline_run()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.RESTRICTED)

    response = db_client.get(
        f"{BASE}/regulatory-runs/{run_id}",
        headers=_auth(version),
    )

    assert response.status_code == 200, response.text
    _assert_available(response.json(), run_id)


def test_all_sensitivity_reader_gets_the_stored_inputs(db_client: TestClient) -> None:
    _drop_bindings()
    run_id = _mint_baseline_run()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)

    response = db_client.get(
        f"{BASE}/regulatory-runs/{run_id}",
        headers=_auth(version),
    )

    assert response.status_code == 200, response.text
    _assert_available(response.json(), run_id)


def test_a_restricted_grant_on_a_sibling_bank_does_not_reveal_inputs(
    db_client: TestClient,
) -> None:
    _drop_bindings()
    _add_sibling_bank()
    run_id = _mint_baseline_run()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    version = _grant(
        RoleBundle.ANALYST,
        sensitivity=SensitivityScope.RESTRICTED,
        institution_id=SIBLING_BANK_ID,
    )

    response = db_client.get(
        f"{BASE}/regulatory-runs/{run_id}",
        headers=_auth(version),
    )

    assert response.status_code == 200, response.text
    _assert_withheld(response.json())


def test_run_post_response_redacts_inputs_for_a_confidential_only_runner(
    db_client: TestClient,
) -> None:
    _drop_bindings()
    _seed_book()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    created = db_client.post(
        BASE + "/regulatory-runs",
        headers=_auth(version),
        json={
            "module": "liquidity",
            "reporting_period_id": _latest_period_id(),
            "scenario_code": "baseline",
        },
    )

    assert created.status_code == 201, created.text
    _assert_withheld(created.json())


def test_run_all_scenarios_batch_redacts_every_run(db_client: TestClient) -> None:
    _drop_bindings()
    _seed_book()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    created = db_client.post(
        BASE + "/liquidity/run-all-scenarios",
        headers=_auth(version),
        json={"reporting_period_id": _latest_period_id()},
    )

    assert created.status_code == 201, created.text
    batch = created.json()
    assert batch["runs"], "the batch mints every liquidity scenario"
    for run in batch["runs"]:
        _assert_withheld(run)
