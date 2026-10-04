"""A branch/region Credit sentence never opens an institution figure surface.

Exercise actual HTTP routes with populated figures, including legacy capital
and rating feed rows, and verify the row-attributable Credit control still works.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.authorization import DataScope, SensitivityScope
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, RegulatoryPackage
from tests.api.helpers import ORG_1, USER_1, headers
from tests.api.test_credit_authorization import _seed_live_rows
from tests.api.test_credit_route_authorization import (
    BASE,
    BR_ONE,
    BR_ONE_LOANS,
    _declare_regions,
    _grant,
    _period_id,
    _seed_book,
)
from tests.api.test_fx_authorization import _add_regulatory_run


@pytest.mark.parametrize("kind", [DataScope.BRANCH, DataScope.REGION])
def test_narrowed_credit_grant_refuses_cross_module_figures(
    db_client: TestClient, kind: DataScope
) -> None:
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        db.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        db.commit()
    _seed_book()
    _seed_live_rows()
    period_id = _period_id()
    with get_sessionmaker()() as db:
        package = RegulatoryPackage(
            organization_id=ORG_1,
            bank_id=BASE.rsplit("/", 1)[-1],
            return_family="bsd",
            return_code="BSD3",
            reporting_date=date(2026, 6, 30),
            frequency="monthly",
            status="generated",
            version=1,
            generated_by=USER_1,
            snapshot={"institution_figures": {"total": "9999999"}},
        )
        db.add(package)
        db.commit()
        package_id = package.id
    for module in ("liquidity", "capital", "credit", "irr", "fx", "ftp", "forecast"):
        _add_regulatory_run(UUID(period_id), module, "baseline")
    _declare_regions({"BR-001": "North", "BR-002": "South"})
    version = _grant(
        sensitivity_scope=SensitivityScope.ALL,
        data_scope=kind,
        data_scope_values=(BR_ONE,) if kind is DataScope.BRANCH else ("North",),
    )
    auth = headers(authorization_version=version)
    summary = db_client.get(f"{BASE}/live-summary", headers=auth)
    assert summary.status_code == 200, summary.text
    assert summary.json()["modules"] == []
    assert summary.json()["reconciliation"] is None
    alerts = db_client.get(f"{BASE}/alerts", headers=auth)
    assert alerts.status_code == 200, alerts.text
    assert alerts.json()["items"] == []
    assert alerts.json()["total"] == 0
    assert alerts.json()["by_module"] == {}
    assert alerts.json()["by_severity"] == {}
    for module in ("credit", "liquidity", "capital", "irr", "fx", "ftp", "forecast", "rating"):
        response = db_client.get(f"{BASE}/live-snapshots", params={"module": module}, headers=auth)
        assert response.status_code == 403, (module, response.text)
    for path in (
        "/liquidity/dashboard",
        "/liquidity/ewis",
        "/liquidity/cfp",
        "/capital/dashboard",
        "/credit/dashboard",
        "/fx/dashboard",
        "/ftp/dashboard",
        "/irr/dashboard",
        "/cashflow-forecast",
    ):
        response = db_client.get(f"{BASE}{path}", headers=auth)
        assert response.status_code == 403, (path, response.text)
    for path in ("/reverse-stress/latest", "/enterprise-stress/latest", "/enterprise-stress/runs"):
        response = db_client.get(
            f"{BASE}{path}",
            params={
                "reporting_period_id": period_id,
                "scenario_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            },
            headers=auth,
        )
        assert response.status_code == 403, (path, response.text)
    # Registries filter before pagination/counts, rather than advertising sealed
    # whole-book results to a principal who cannot read their figures.
    runs = db_client.get(f"{BASE}/regulatory-runs", headers=auth)
    assert runs.status_code == 200, runs.text
    assert runs.json()["runs"] == []
    packages = db_client.get(f"{BASE}/regulatory-packages", headers=auth)
    assert packages.status_code == 200, packages.text
    assert packages.json()["packages"] == []
    assert packages.json()["total"] == 0
    package_read = db_client.get(f"{BASE}/regulatory-packages/{package_id}", headers=auth)
    assert package_read.status_code == 404, package_read.text
    window = db_client.get(
        f"{BASE}/analytics/window",
        params={"start_date": "2026-03-01", "end_date": "2026-10-04"},
        headers=auth,
    )
    assert window.status_code == 200, window.text
    assert window.json()["ratios"] == []
    assert window.json()["daily"] == []
    book = db_client.get(f"{BASE}/credit/loans", headers=auth)
    assert book.status_code == 200, book.text
    assert book.json()["total"] == len(BR_ONE_LOANS)
    assert {row["source_reference"] for row in book.json()["rows"]} == BR_ONE_LOANS
