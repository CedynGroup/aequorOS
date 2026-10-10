from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.authorization import ModuleScope, SensitivityScope
from app.models import (
    BankReportingPeriod,
    ParamCapitalThreshold,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.schemas.regulatory_liquidity import (
    Bsd3PreviewRead,
    LiquidityDashboardRead,
    RegulatoryRunRead,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.fixtures.live_plane import materialize_live_plane
from tests.support.authority import grant_organization_analyst
from tests.support.helpers import ORG_1, headers

BANK_URL = f"/api/v1/banks/{SAMPLE_BANK_ID}"
MONITORING_RULES = ("lcr_above_minimum", "nsfr_above_minimum")
DISCLOSURE = "governed monitoring threshold (Basel reference ratio)"


def _create_monitored_run(
    db_client: TestClient, threshold: Decimal, session_factory: sessionmaker[Session]
) -> tuple[LiquidityDashboardRead, RegulatoryRunRead]:
    with session_factory() as db:
        materialize_canonical_test_book(db)
        for module, sensitivity in (
            (ModuleScope.LIQUIDITY, SensitivityScope.CONFIDENTIAL),
            (ModuleScope.REGULATORY, SensitivityScope.RESTRICTED),
        ):
            grant_organization_analyst(
                db,
                module,
                sensitivity,
                grantor="liquidity-disclosure-test",
                reason="Exercise liquidity dashboard and immutable run reads",
            )
        for parameter in db.scalars(
            select(ParamCapitalThreshold).where(
                ParamCapitalThreshold.organization_id == ORG_1,
                ParamCapitalThreshold.threshold_code.in_(("lcr_min", "nsfr_min")),
            )
        ):
            parameter.value_pct = threshold
        materialize_live_plane(db, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        period_id = db.scalar(
            select(BankReportingPeriod.id)
            .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
            .order_by(BankReportingPeriod.period_end.desc())
            .limit(1)
        )
        db.commit()
    assert period_id is not None

    inline_response = db_client.get(f"{BANK_URL}/liquidity/dashboard", headers=headers())
    assert inline_response.status_code == 200, inline_response.text
    inline = LiquidityDashboardRead.model_validate_json(inline_response.text)
    assert not inline.stored
    expected_pass = {
        "lcr_above_minimum": inline.metrics.lcr_pct >= threshold,
        "nsfr_above_minimum": inline.metrics.nsfr_pct >= threshold,
    }
    assert set(MONITORING_RULES) <= {item.rule_code for item in inline.validations}
    for item in inline.validations:
        if item.rule_code in MONITORING_RULES:
            assert item.passed == expected_pass[item.rule_code]
            assert item.severity == "error"
            assert f"{threshold}% {DISCLOSURE}" in item.message

    created_response = db_client.post(
        f"{BANK_URL}/regulatory-runs",
        headers=headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": str(period_id),
            "scenario_code": "baseline",
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = RegulatoryRunRead.model_validate_json(created_response.text)
    assert created.status == "succeeded"
    assert [
        (v.rule_code, v.passed, v.severity, v.message)
        for v in created.validations
        if v.rule_code in MONITORING_RULES
    ] == [
        (v.rule_code, v.passed, v.severity, v.message)
        for v in inline.validations
        if v.rule_code in MONITORING_RULES
    ]
    assert {
        metric.metric_code: metric.threshold_min
        for metric in created.metric_results
        if metric.metric_code in ("lcr_pct", "nsfr_pct")
    } == {"lcr_pct": threshold, "nsfr_pct": threshold}
    return inline, created


@pytest.mark.parametrize("threshold", [Decimal("100"), Decimal("160")])
def test_dashboard_and_run_disclose_governed_thresholds_without_rewriting_history(
    db_client: TestClient, threshold: Decimal, _bound_test_sessionmaker: sessionmaker[Session]
) -> None:
    session_factory = _bound_test_sessionmaker
    inline, created = _create_monitored_run(db_client, threshold, session_factory)

    with session_factory() as db:
        rows = list(
            db.scalars(
                select(RegulatoryValidation)
                .where(RegulatoryValidation.run_id == created.id)
                .order_by(RegulatoryValidation.position)
            )
        )
        for row in rows:
            if row.rule_code in MONITORING_RULES:
                row.message = row.message.replace(DISCLOSURE, "regulatory minimum")
        historical = [(r.id, r.rule_code, r.passed, r.severity, r.message) for r in rows]
        for parameter in db.scalars(
            select(ParamCapitalThreshold).where(
                ParamCapitalThreshold.organization_id == ORG_1,
                ParamCapitalThreshold.threshold_code.in_(("lcr_min", "nsfr_min")),
            )
        ):
            parameter.value_pct = threshold + Decimal("20")
        db.commit()

    params = {"reporting_period_id": str(inline.period.id)}
    dashboard_response = db_client.get(
        f"{BANK_URL}/liquidity/dashboard", params=params, headers=headers()
    )
    assert dashboard_response.status_code == 200, dashboard_response.text
    dashboard = LiquidityDashboardRead.model_validate_json(dashboard_response.text)
    assert dashboard.stored
    assert dashboard.metrics.model_dump(
        exclude={"fx_funding_gap_ghs", "fx_share_of_liabilities_pct", "stressed_fx_funding_gap_ghs"}
    ) == inline.metrics.model_dump(
        exclude={"fx_funding_gap_ghs", "fx_share_of_liabilities_pct", "stressed_fx_funding_gap_ghs"}
    )
    assert [v for v in dashboard.validations if v.rule_code in MONITORING_RULES] == [
        v for v in inline.validations if v.rule_code in MONITORING_RULES
    ]

    preview_response = db_client.get(
        f"{BANK_URL}/submissions/bsd3", params=params, headers=headers()
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = Bsd3PreviewRead.model_validate_json(preview_response.text)
    assert preview.validations == dashboard.validations

    detail_response = db_client.get(f"{BANK_URL}/regulatory-runs/{created.id}", headers=headers())
    assert detail_response.status_code == 200, detail_response.text
    detail = RegulatoryRunRead.model_validate_json(detail_response.text)
    assert detail.validations == created.validations
    assert detail.metric_results == created.metric_results
    assert detail.line_items == created.line_items
    assert detail.inputs == created.inputs
    assert detail.metrics == created.metrics
    assert detail.input_hash == created.input_hash

    with session_factory() as db:
        saved = db.get(RegulatoryRun, created.id)
        assert saved is not None
        assert saved.input_hash == created.input_hash
        assert saved.inputs == created.inputs
        assert saved.metrics == created.metrics
        assert saved.engine_version == created.engine_version
        saved_rows = db.scalars(
            select(RegulatoryValidation)
            .where(RegulatoryValidation.run_id == created.id)
            .order_by(RegulatoryValidation.position)
        )
        assert [
            (r.id, r.rule_code, r.passed, r.severity, r.message) for r in saved_rows
        ] == historical
