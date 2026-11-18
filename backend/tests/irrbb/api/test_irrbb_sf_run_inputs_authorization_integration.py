"""The IRRBB Standardised Framework embedding obeys the run-input contract.

The Framework read embeds the whole run (``IrrbbSfRead.run``); the embedded
run's input snapshot follows the same rule as the generic run routes: readable
at the route's own authority, with ``inputs`` withheld unless the caller also
holds IRRBB restricted VIEW on that bank — on the read AND on the minting
response alike.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

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
from app.models import AuthorizationBinding, RegulatoryRun, User
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.sf_book import seed_book
from tests.support.helpers import ORG_1, USER_1, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/standardised-framework"


def _drop_bindings() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()


def _grant(bundle: RoleBundle, sensitivity: SensitivityScope) -> int:
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
                institution_id=SAMPLE_BANK_ID,
                module_scope=ModuleScope.IRRBB,
                sensitivity_scope=sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "sf-inputs-test"),
            reason="exercise the embedded run-input contract",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return user.authorization_version
    finally:
        session.close()


def _auth(version: int) -> dict[str, str]:
    return headers(ORG_1, user_id=USER_1, roles=("admin",), authorization_version=version)


def _seed() -> str:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        period_id = seed_book(session)
        session.commit()
        return str(period_id)
    finally:
        session.close()


def _mint_run(db_client: TestClient, version: int, period_id: str) -> dict[str, Any]:
    created = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": period_id},
        headers=_auth(version),
    )
    assert created.status_code == 201, created.text
    return created.json()


def _stored_inputs(run_id: str) -> dict[str, Any]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        run = session.scalar(
            select(RegulatoryRun).where(
                RegulatoryRun.organization_id == ORG_1,
                RegulatoryRun.id == UUID(run_id),
            )
        )
        assert run is not None
        return deepcopy(run.inputs)
    finally:
        session.close()


def test_the_minting_response_withholds_inputs_below_restricted(
    db_client: TestClient,
) -> None:
    _drop_bindings()
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, SensitivityScope.CONFIDENTIAL)

    body = _mint_run(db_client, version, period_id)

    assert body["inputs"] is None
    assert body["inputs_available"] is False
    assert body["inputs_unavailable_reason"]


def test_the_read_route_withholds_the_embedded_inputs_below_restricted(
    db_client: TestClient,
) -> None:
    _drop_bindings()
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, SensitivityScope.CONFIDENTIAL)
    _mint_run(db_client, version, period_id)
    version = _grant(RoleBundle.VIEWER, SensitivityScope.AGGREGATED)

    response = db_client.get(
        BASE, params={"reporting_period_id": period_id}, headers=_auth(version)
    )

    assert response.status_code == 200, response.text
    run = response.json()["run"]
    assert run["inputs"] is None
    assert run["inputs_available"] is False
    assert run["inputs_unavailable_reason"]


def test_a_restricted_irrbb_viewer_sees_the_embedded_inputs(db_client: TestClient) -> None:
    _drop_bindings()
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, SensitivityScope.CONFIDENTIAL)
    minted = _mint_run(db_client, version, period_id)
    version = _grant(RoleBundle.VIEWER, SensitivityScope.ALL)

    response = db_client.get(
        BASE, params={"reporting_period_id": period_id}, headers=_auth(version)
    )

    assert response.status_code == 200, response.text
    run = response.json()["run"]
    assert run["inputs_available"] is True
    assert run["inputs_unavailable_reason"] is None
    assert run["inputs"] == _stored_inputs(minted["id"])
