"""An FTP rule or curve change recomputes the FTP results the API serves.

The dashboard has no screen that edits an FTP rule or the transfer curve (#314),
so the browser journey (`backend/dashboard/e2e/ftp-risk-workflows.spec.ts`)
cannot drive one. These tests cover the backend half of that workflow over the
canonical fixture book:

* a CURVE change, as the FTP what-if workbench applies it: a custom scenario
  shifting the whole transfer curve reprices every product by exactly the shift
  (assets lose it, funding earns it), and the recomputed contribution and
  below-floor count follow from the baseline margins — without persisting a run;
* a RULE change, as the governed parameter register applies it: superseding the
  minimum product margin moves the floor every subsequent FTP run is held to,
  while the pricing and the run already filed stay exactly as they were.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

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
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    AuthorizationBinding,
    BankReportingPeriod,
    ParamCapitalThreshold,
    RegulatoryRun,
    User,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.helpers import ORG_1, USER_1, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp"
WORKBENCH_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/scenario-workbench/ftp"
MONEY = Decimal("0.0001")  # the FTP engine's money quantum
HUNDRED = Decimal("100")
MIN_MARGIN_CODE = "ftp_min_product_margin_pct"


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


def _seed_book() -> tuple[UUID, date]:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        period = session.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        ).first()
        assert period is not None
        session.commit()
        return period.id, period.period_end
    finally:
        session.close()


def _analyst_headers() -> dict[str, str]:
    """An FTP analyst over the sample bank's confidential FTP figures."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ANALYST,
            scope=authorization.BindingScope(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=SAMPLE_BANK_ID,
                module_scope=ModuleScope.FTP,
                sensitivity_scope=SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "ftp-recompute-test"),
            reason="FTP recompute regression",
        )
        session.commit()
        user = session.get(User, USER_1)
        assert user is not None
        version = user.authorization_version
    finally:
        session.close()
    return headers(ORG_1, user_id=USER_1, roles=("viewer",), authorization_version=version)


def _run_all(client: TestClient, auth: dict[str, str], period_id: UUID) -> dict[str, Any]:
    """Run the FTP scenario set and return the persisted runs by scenario code."""
    response = client.post(
        f"{BASE}/run-all-scenarios",
        headers=auth,
        json={"reporting_period_id": str(period_id)},
    )
    assert response.status_code == 201, response.text
    return {run["scenario_code"]: run for run in response.json()["runs"]}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _shifted_margin(product: dict[str, Any], shift_pct: Decimal) -> Decimal:
    """A uniform transfer-curve shift costs an asset the shift and pays funding it."""
    margin = _dec(product["net_margin_pct"])
    return margin - shift_pct if product["category"] == "asset" else margin + shift_pct


def _contribution(product: dict[str, Any], margin_pct: Decimal) -> Decimal:
    return (_dec(product["balance_ghs"]) * margin_pct / HUNDRED).quantize(
        MONEY, rounding=ROUND_HALF_UP
    )


def _run_count() -> int:
    with get_sessionmaker()() as session:
        return session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0


def test_curve_shift_reprices_every_product_without_persisting(db_client: TestClient) -> None:
    period_id, _ = _seed_book()
    auth = _analyst_headers()
    baseline = _run_all(db_client, auth, period_id)["baseline"]["metrics"]
    products = baseline["products"]
    floor = _dec(baseline["min_product_margin_pct"])

    created = db_client.post(
        f"{WORKBENCH_BASE}/scenarios",
        headers=auth,
        json={
            "code": "desk_curve_up_150",
            "name": "Curve +150bp",
            "shocks": {"curve_shift_bp": 150},
        },
    )
    assert created.status_code == 201, created.text
    runs_before = _run_count()

    response = db_client.post(
        f"{WORKBENCH_BASE}/analysis",
        headers=auth,
        json={
            "reporting_period_id": str(period_id),
            "scenarios": [
                {"kind": "system", "code": "baseline"},
                {"kind": "custom", "scenario_id": created.json()["id"]},
            ],
        },
    )

    assert response.status_code == 200, response.text
    unshifted, shifted = response.json()["results"]
    assert unshifted["status"] == shifted["status"] == "succeeded"
    # The what-if baseline is the persisted baseline: nothing drifts between the two paths.
    assert _dec(unshifted["metrics"]["total_contribution_ghs"]) == _dec(
        baseline["total_contribution_ghs"]
    )
    assert (
        int(unshifted["metrics"]["products_below_min_margin"])
        == baseline["products_below_min_margin"]
    )

    shift = Decimal("1.5")
    assert _dec(shifted["metrics"]["curve_shift_pct"]) == shift
    repriced = {p["product"]: _shifted_margin(p, shift) for p in products}
    expected_contribution = sum(
        (_contribution(p, repriced[p["product"]]) for p in products), Decimal(0)
    )
    assert _dec(shifted["metrics"]["total_contribution_ghs"]) == expected_contribution
    expected_below = sum(1 for margin in repriced.values() if margin < floor)
    assert int(shifted["metrics"]["products_below_min_margin"]) == expected_below
    # The shift is a real change in the answer, not a relabelled baseline.
    assert expected_contribution < _dec(baseline["total_contribution_ghs"])
    assert expected_below > baseline["products_below_min_margin"]
    # A what-if is never filed: the analysis persisted no run.
    assert _run_count() == runs_before


def test_governed_min_margin_change_moves_the_floor_of_later_runs(db_client: TestClient) -> None:
    period_id, period_end = _seed_book()
    auth = _analyst_headers()
    before = _run_all(db_client, auth, period_id)["baseline"]
    before_metrics = before["metrics"]
    assert _dec(before_metrics["min_product_margin_pct"]) == 0

    new_floor = Decimal("1")
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        current = session.scalars(
            select(ParamCapitalThreshold).where(
                ParamCapitalThreshold.organization_id == ORG_1,
                ParamCapitalThreshold.threshold_code == MIN_MARGIN_CODE,
                ParamCapitalThreshold.effective_to.is_(None),
            )
        ).one()
        assert current.effective_from < period_end
        # Supersede, never rewrite: the old generation closes the day the new one opens.
        current.effective_to = period_end
        session.add(
            ParamCapitalThreshold(
                organization_id=ORG_1,
                jurisdiction_code=current.jurisdiction_code,
                threshold_code=MIN_MARGIN_CODE,
                value_pct=new_floor,
                effective_from=period_end,
                approved_by="ALCO minimum-margin revision",
                approval_timestamp=utc_now(),
            )
        )
        session.commit()
    finally:
        session.close()

    after = _run_all(db_client, auth, period_id)["baseline"]
    after_metrics = after["metrics"]

    assert after["id"] != before["id"]
    assert _dec(after_metrics["min_product_margin_pct"]) == new_floor
    # The rule moves the floor, not the pricing: every margin is unchanged.
    margins = {p["product"]: _dec(p["net_margin_pct"]) for p in before_metrics["products"]}
    assert {p["product"]: _dec(p["net_margin_pct"]) for p in after_metrics["products"]} == margins
    below = {product for product, margin in margins.items() if margin < new_floor}
    assert {p["product"] for p in after_metrics["products"] if p["below_min_margin"]} == below
    assert after_metrics["products_below_min_margin"] == len(below)
    assert len(below) > before_metrics["products_below_min_margin"]

    # The run filed under the old rule is immutable and still says what it said.
    filed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{before['id']}", headers=auth
    )
    assert filed.status_code == 200, filed.text
    assert (
        filed.json()["metrics"]["products_below_min_margin"]
        == before_metrics["products_below_min_margin"]
    )
    assert _dec(filed.json()["metrics"]["min_product_margin_pct"]) == 0
