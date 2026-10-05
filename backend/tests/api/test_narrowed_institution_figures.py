"""A branch/region Credit sentence never opens an institution figure surface.

Exercise actual HTTP routes with populated figures, including legacy capital
and rating feed rows, and verify the row-attributable Credit control still works.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.authorization import DataScope, SensitivityScope
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import (
    AuthorizationBinding,
    PackageSignatureRecipient,
    RegulatoryArtifactVersion,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    RegulatoryRun,
)
from app.models.regulatory_reporting import RETURN_FAMILIES
from app.services.regulatory_reporting.registry import REGISTRY
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
from tests.api.test_package_authorization import _read_routes


def _seed_return_packages() -> dict[str, tuple[UUID, UUID, UUID, str]]:
    packages_by_family: dict[str, tuple[UUID, UUID, UUID, str]] = {}
    with get_sessionmaker()() as db:
        for family in RETURN_FAMILIES:
            definition = next(row for row in REGISTRY.values() if row.family == family)
            package = RegulatoryPackage(
                organization_id=ORG_1,
                bank_id=BASE.rsplit("/", 1)[-1],
                return_family=family,
                return_code=definition.code,
                reporting_date=date(2026, 6, 30),
                frequency=definition.frequency,
                status="submitted",
                version=1,
                generated_by=USER_1,
                snapshot={"institution_figures": {"total": "9999999"}},
            )
            db.add(package)
            db.flush()
            artifact = RegulatoryPackageArtifact(
                organization_id=ORG_1,
                package_id=package.id,
                kind="pdf",
                object_path="fixture/institution-figures.pdf",
                checksum_sha256="0" * 64,
                size_bytes=100,
            )
            version = RegulatoryArtifactVersion(
                organization_id=ORG_1,
                package_id=package.id,
                kind="pdf",
                object_path="fixture/institution-figures.pdf",
                checksum_sha256="0" * 64,
                size_bytes=100,
            )
            db.add_all(
                [
                    artifact,
                    version,
                    PackageSignatureRecipient(
                        organization_id=ORG_1,
                        package_id=package.id,
                        signing_role="preparer",
                        recipient_user_id=USER_1,
                        recipient_signer_id="fixture-signer",
                    ),
                ]
            )
            db.flush()
            packages_by_family[family] = (package.id, artifact.id, version.id, definition.code)
        db.commit()
    return packages_by_family


def _assert_return_refusals(
    db_client: TestClient,
    auth: dict[str, str],
    packages_by_family: dict[str, tuple[UUID, UUID, UUID, str]],
    period_id: str,
) -> None:
    for family, (package_id, artifact_id, version_id, return_code) in packages_by_family.items():
        base = f"{BASE}/regulatory-packages/{package_id}"
        paths = _read_routes(package_id) + [
            f"{base}/workflow",
            f"{base}/filing-set",
            f"{base}/email-fallback-instructions",
            f"{base}/email-fallback.eml",
            f"{base}/comparison?against={package_id}",
            f"{base}/attestation",
            f"{base}/attestation/preview?signing_role=preparer",
            f"{base}/attestation/placements",
            f"{base}/attestation/verify",
            f"{BASE}/regulatory-artifacts/{artifact_id}/download",
            f"{BASE}/regulatory-artifact-versions/{version_id}/download",
        ]
        for path in paths:
            response = db_client.get(path, headers=auth)
            assert response.status_code == 404, (family, path, response.text)
        for suffix, payload in (
            ("decide-approval", {"action": "approved"}),
            ("workflow/decisions", {"decision": "approved", "round": 1, "review_digest": "0" * 64}),
        ):
            response = db_client.post(f"{base}/{suffix}", json=payload, headers=auth)
            assert response.status_code == 404, (family, response.text)
        generated = db_client.post(
            f"{BASE}/regulatory-packages",
            headers=auth,
            json={"return_code": return_code, "reporting_date": "2026-06-30", "basis": "solo"},
        )
        assert generated.status_code == (409 if family == "icaap" else 403), (
            family,
            generated.text,
        )
        anchors = db_client.get(
            f"{BASE}/return-anchors", headers=auth, params={"return_code": return_code}
        )
        assert anchors.status_code == 200, anchors.text
        assert all(row["package_id"] is None for row in anchors.json()["anchors"])
        assert all(row["rag"] is None for row in anchors.json()["anchors"])
    inbox = db_client.get("/api/v1/attestation/awaiting-my-signature", headers=auth)
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["items"] == []
    obligations = db_client.get(f"{BASE}/reporting-obligations", headers=auth)
    assert obligations.status_code == 200, obligations.text
    assert all(row["package_id"] is None for row in obligations.json()["obligations"])
    assert all(row["rag"] is None for row in obligations.json()["obligations"])
    assert obligations.json()["summary"] == {
        "overdue": 0,
        "due_soon": 0,
        "on_track": 0,
        "pending_reupload": 0,
    }
    for module in ("liquidity", "capital", "credit", "irr", "fx", "ftp", "forecast"):
        comparison = db_client.get(
            f"{BASE}/reports/comparison",
            headers=auth,
            params={"mode": "period", "module": module, "left": period_id, "right": period_id},
        )
        assert comparison.status_code == 404, (module, comparison.text)


@pytest.mark.parametrize("kind", [DataScope.BRANCH, DataScope.REGION])
def test_narrowed_credit_grant_refuses_cross_module_figures(
    db_client: TestClient, kind: DataScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BI_ENABLED", "0")
    get_settings.cache_clear()
    assert get_settings().bi.enabled is False
    with get_sessionmaker()() as db:
        db.info["organization_id"] = ORG_1
        db.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        db.commit()
    _seed_book()
    _seed_live_rows()
    period_id = _period_id()
    packages_by_family = _seed_return_packages()
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
    with get_sessionmaker()() as db:
        capital_runs_before = set(db.scalars(select(RegulatoryRun.id)))
    for path, payload in (
        ("/regulatory-runs", {"module": "capital", "scenario_code": "baseline"}),
        ("/regulatory-runs", {"module": "capital", "scenario_code": "severe"}),
        ("/capital/run-all-scenarios", {}),
    ):
        refused = db_client.post(
            f"{BASE}{path}",
            headers=headers(roles=("analyst",), authorization_version=version),
            json={"reporting_period_id": period_id, **payload},
        )
        assert refused.status_code == 403, refused.text
    with get_sessionmaker()() as db:
        assert set(db.scalars(select(RegulatoryRun.id))) == capital_runs_before
    execution = db_client.post(
        f"{BASE}/enterprise-stress/runs",
        headers=auth,
        json={
            "reporting_period_id": period_id,
            "scenario_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "reason": "Probe whole-institution execution response refusal",
        },
    )
    assert execution.status_code == 403, execution.text
    # Registries filter before pagination/counts, rather than advertising sealed
    # whole-book results to a principal who cannot read their figures.
    runs = db_client.get(f"{BASE}/regulatory-runs", headers=auth)
    assert runs.status_code == 200, runs.text
    assert runs.json()["runs"] == []
    packages = db_client.get(f"{BASE}/regulatory-packages", headers=auth)
    assert packages.status_code == 200, packages.text
    assert packages.json()["packages"] == []
    assert packages.json()["total"] == 0
    _assert_return_refusals(db_client, auth, packages_by_family, period_id)
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
