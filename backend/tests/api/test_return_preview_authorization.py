from __future__ import annotations

from typing import Literal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_capital, regulatory_liquidity
from tests.api.helpers import ORG_1, ORG_2, headers
from tests.api.test_liquidity_scoped_authorization import BASE, CTX, _auth, _grant, _seed_book
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    with get_sessionmaker()() as db:
        db.execute(delete(AuthorizationBinding))
        db.commit()


@pytest.mark.parametrize("module", ["capital", "liquidity"])
@pytest.mark.parametrize(
    "sensitivity", [SensitivityScope.AGGREGATED, SensitivityScope.CONFIDENTIAL]
)
def test_return_preview_requires_reporting_view_at_http_and_shared_reader(
    db_client: TestClient,
    module: Literal["capital", "liquidity"],
    sensitivity: SensitivityScope,
) -> None:
    period_id = _seed_book()
    version = _grant(
        RoleBundle.ANALYST,
        module=ModuleScope.CAPITAL if module == "capital" else ModuleScope.LIQUIDITY,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    create_run = (
        regulatory_capital.create_capital_run
        if module == "capital"
        else regulatory_liquidity.create_liquidity_run
    )
    preview_reader = (
        regulatory_capital.get_bsd2_preview
        if module == "capital"
        else regulatory_liquidity.get_bsd3_preview
    )
    path = f"{BASE}/submissions/{'bsd2' if module == 'capital' else 'bsd3'}"
    params = {"reporting_period_id": str(period_id)}
    with get_sessionmaker()() as db:
        run = create_run(
            db,
            CTX,
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module=module, reporting_period_id=period_id, scenario_code="baseline"
            ),
        )
        assert run.status == "succeeded"
        with pytest.raises(HTTPException) as denied:
            preview_reader(db, CTX, SAMPLE_BANK_ID, period_id)
        assert denied.value.status_code == 403
    response = db_client.get(path, params=params, headers=_auth(version, "viewer"))
    assert response.status_code == 403, response.text

    version = _grant(RoleBundle.VIEWER, module=ModuleScope.REGULATORY, sensitivity=sensitivity)
    response = db_client.get(path, params=params, headers=_auth(version, "viewer"))
    assert response.status_code == 403, response.text

    with get_sessionmaker()() as db:
        db.execute(delete(AuthorizationBinding))
        db.commit()
    version = _grant(
        RoleBundle.VIEWER,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    response = db_client.get(path, params=params, headers=_auth(version, "viewer"))
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == str(run.id)
    with get_sessionmaker()() as db:
        preview = preview_reader(db, CTX, SAMPLE_BANK_ID, period_id)
        assert preview.run_id == run.id


@pytest.mark.parametrize("preview", ["bsd2", "bsd3"])
def test_reporting_preview_preserves_baseline_and_tenant_refusals(
    db_client: TestClient, preview: str
) -> None:
    period_id = _seed_book()
    version = _grant(
        RoleBundle.VIEWER,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    path = f"{BASE}/submissions/{preview}"
    params = {"reporting_period_id": str(period_id)}
    missing_run = db_client.get(path, params=params, headers=_auth(version, "viewer"))
    assert missing_run.status_code == 409, missing_run.text
    assert missing_run.json()["error"]["details"]["error_code"] == "no_baseline_run"
    cross_tenant = db_client.get(path, params=params, headers=headers(ORG_2))
    assert cross_tenant.status_code == 404, cross_tenant.text
    unknown_bank = db_client.get(
        path.replace(SAMPLE_BANK_ID, "BK-UNKNOWN1"),
        params=params,
        headers=headers(ORG_1, authorization_version=version),
    )
    assert unknown_bank.status_code == 404, unknown_bank.text
