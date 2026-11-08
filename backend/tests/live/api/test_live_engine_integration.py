"""Live-engine API: automatic trigger, live summary, freshness, alerts, enqueues."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
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
from app.identity.models.authorization import AuthorizationBinding
from app.identity.service import authorization
from app.live.service import pipeline
from app.models import Bank, BankReportingPeriod, CurrentFinancialFact, Job, LiveMetric, User
from app.policy.service import module_scope
from app.services import job_queue
from tests.data_engine.adapters.excel_csv import fixtures
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

# Mapping onboarding is a configure duty: the journey officer holds the
# explicit DATA Configurator grant that bank_seeding's seeder adds.
from tests.support.bank_seeding import seed_bank
from tests.support.factories.canonical import (
    FIXTURE_AS_OF,
    seed_canonical_fixture,
    seed_hedge_and_swap_positions,
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers
from tests.support.ingestion_seeding import (
    FULL_MAPPING,
    activate_mapping,
    start_batch,
)

AS_OF = FIXTURE_AS_OF.isoformat()
_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"


def _seed_and_refresh(db_client: TestClient, *, hedged: bool = False) -> None:
    """Seed the sample bank + canonical fixture and run one live refresh.

    Uses a direct session on the same engine as the TestClient so the API reads
    see the committed live rows the worker would have produced.
    """
    _ = db_client  # ensures the app engine/DB is initialized
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        if hedged:
            seed_hedge_and_swap_positions(
                session,
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
            )
        session.commit()
        job = job_queue.enqueue(
            session,
            ORG_1,
            "pipeline_refresh",
            bank_id=SAMPLE_BANK_ID,
            payload={"as_of_date": AS_OF},
        )
        session.commit()
        pipeline.run_refresh(session, job)
    finally:
        session.close()


def test_accepted_ingestion_enqueues_pipeline_refresh(
    db_client: TestClient, tmp_path: Path
) -> None:
    bank_id = seed_bank(db_client)
    activate_mapping(db_client, bank_id, FULL_MAPPING)
    workbook = fixtures.build_well_formed(tmp_path / "bank.xlsx")
    started = start_batch(db_client, bank_id, workbook)
    assert started["batch"]["status"] == "accepted"

    session = get_sessionmaker()()
    try:
        jobs = list(
            session.scalars(
                select(Job).where(Job.job_type == "pipeline_refresh", Job.bank_id == bank_id)
            )
        )
    finally:
        session.close()
    assert len(jobs) == 1
    assert jobs[0].coalesce_key is not None
    assert jobs[0].coalesce_key.startswith(f"refresh:{bank_id}:")
    assert jobs[0].run_after is not None  # debounced, not immediate


def test_get_live_summary_shape(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/live-summary", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == str(SAMPLE_BANK_ID)
    assert body["reporting_period_id"] is not None
    modules = {module["module"] for module in body["modules"]}
    assert {"liquidity", "capital", "irr", "fx", "ftp", "rating"} <= modules
    # This refresh completed after the fixture inputs, so the live cache is not
    # behind the book. Drift versus the last official filing is separately
    # surfaced by GET /freshness.
    assert body["is_stale"] is False


def test_live_summary_is_read_only_with_current_facts_without_reporting_period(
    db_client: TestClient,
) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.execute(
            delete(BankReportingPeriod).where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
        )
        session.add(
            CurrentFinancialFact(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                source_as_of_date=FIXTURE_AS_OF,
                source_generation=1,
                fact_group="balance_sheet",
                category="cash_vault",
                amount=Decimal("1"),
                currency="GHS",
            )
        )
        session.add(
            LiveMetric(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                source_as_of_date=FIXTURE_AS_OF,
                module="rating",
                metrics={"availability": "unavailable"},
                status="na",
                computed_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        session.commit()

        response = db_client.get(f"{_BASE}/live-summary", headers=headers())

        assert response.status_code == 200, response.text
        assert response.json()["is_stale"] is False
        job = session.scalar(
            select(Job).where(
                Job.job_type == "pipeline_refresh",
                Job.bank_id == SAMPLE_BANK_ID,
            )
        )
        assert job is None
    finally:
        session.close()


def test_get_freshness_shape(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/freshness", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_stale"] is True
    liquidity = next(module for module in body["modules"] if module["module"] == "liquidity")
    assert liquidity["live_hash"] is not None
    assert liquidity["official_run_hash"] is None


def test_get_alerts_surfaces_fx_breach(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/alerts", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    fx_breaches = [item for item in body["items"] if item["module"] == "fx"]
    assert any(item["rule_id"] == "nop_within_aggregate_limit" for item in fx_breaches)
    assert all(item["severity"] in ("critical", "high") for item in body["items"])


def test_fx_alert_clears_after_hedges(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    before = db_client.get(f"{_BASE}/alerts", headers=headers()).json()
    assert any(item["rule_id"] == "nop_within_aggregate_limit" for item in before["items"])

    # Add the hedge book and refresh again through the pipeline.
    session = get_sessionmaker()()
    try:
        seed_hedge_and_swap_positions(
            session,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
        )
        session.commit()
        job = job_queue.enqueue(
            session,
            ORG_1,
            "pipeline_refresh",
            bank_id=SAMPLE_BANK_ID,
            payload={"as_of_date": AS_OF},
        )
        session.commit()
        pipeline.run_refresh(session, job)
    finally:
        session.close()

    after = db_client.get(f"{_BASE}/alerts", headers=headers()).json()
    assert not any(item["rule_id"] == "nop_within_aggregate_limit" for item in after["items"])


def test_refresh_endpoint_enqueues_and_is_pollable(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.post(
        f"{_BASE}/refresh", headers=headers(), json={"as_of_date": AS_OF, "reason": "recompute"}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job_type"] == "pipeline_refresh"
    assert body["status"] == "queued"

    polled = db_client.get(f"/api/v1/jobs/{body['job_id']}", headers=headers())
    assert polled.status_code == 200
    assert polled.json()["job_type"] == "pipeline_refresh"


@pytest.mark.usefixtures("credit_run_authority")
def test_refresh_requires_the_view_authority_its_locator_demands(
    db_client: TestClient,
) -> None:
    """A caller without DATA/confidential view cannot enqueue a refresh.

    An accepted operation must remain monitorable — the queue reads admit a
    row only under current view authority — so the refresh demands the view
    its locator carries up front and refuses otherwise. The hermetic baseline
    sentence (viewer/all/all) is cleared first so the refusal is the missing
    DATA/confidential view, not a stale session.
    """
    _seed_and_refresh(db_client)
    with get_sessionmaker()() as session:
        session.execute(delete(AuthorizationBinding))
        session.commit()
        new_version = authorization.invalidate_user_authorization(
            session, organization_id=ORG_1, user_id=USER_1, reason="Clear the baseline sentence"
        )
        session.commit()
    response = db_client.post(
        f"{_BASE}/refresh",
        headers=headers(authorization_version=new_version),
        json={"as_of_date": AS_OF, "reason": "recompute"},
    )
    assert response.status_code == 403, response.text


def _mint_authority(*, view: bool) -> int:
    """Grant USER_1 the RUN authority the sample bank's run plan enables.

    When ``view`` is set, the grant also carries the bank-scoped regulatory
    view authority the official-run locator demands — the authority the queue
    reads need to monitor the accepted mint (#186).
    """
    with get_sessionmaker()() as session:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        bank.institution_type = "savings_and_loans"
        session.commit()
        assert module_scope.runs_module(session, bank, "irr")
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ANALYST,
            scope=authorization.BindingScope(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=SAMPLE_BANK_ID,
                module_scope=ModuleScope.IRRBB,
                sensitivity_scope=SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "live-engine-test"),
            reason="Authorize the IRRBB engine in the official run plan",
        )
        if view:
            authorization.create_role_binding(
                session,
                organization_id=ORG_1,
                principal_user_id=USER_1,
                principal_type=PrincipalType.HUMAN,
                role_bundle=RoleBundle.VIEWER,
                scope=authorization.BindingScope(
                    institution_scope=InstitutionScope.INSTITUTION,
                    institution_id=SAMPLE_BANK_ID,
                    module_scope=ModuleScope.REGULATORY,
                    sensitivity_scope=SensitivityScope.RESTRICTED,
                ),
                grantor=authorization.GrantorRef(GrantorType.SYSTEM, "live-engine-test"),
                reason="Authorize monitoring of the official-run queue row",
            )
        user = session.get(User, USER_1)
        assert user is not None
        authorization_version = user.authorization_version
    return authorization_version


@pytest.mark.usefixtures("credit_run_authority")
def test_mint_official_run_enqueues(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    authorization_version = _mint_authority(view=True)
    response = db_client.post(
        f"{_BASE}/official-runs",
        headers=headers(authorization_version=authorization_version),
        json={"as_of_date": AS_OF, "reason": "filing"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["job_type"] == "official_run"

    # The accepted mint is monitorable: the caller holds exactly the view
    # authority the row's locator demands, so the poll answers 200 (#186).
    job_id = response.json()["job_id"]
    polled = db_client.get(
        f"/api/v1/jobs/{job_id}", headers=headers(authorization_version=authorization_version)
    )
    assert polled.status_code == 200, polled.text
    assert polled.json()["job_type"] == "official_run"

    session = get_sessionmaker()()
    try:
        official = list(
            session.scalars(
                select(Job).where(Job.job_type == "official_run", Job.bank_id == SAMPLE_BANK_ID)
            )
        )
    finally:
        session.close()
    assert len(official) == 1


@pytest.mark.usefixtures("credit_run_authority")
def test_mint_official_run_requires_monitoring_view(db_client: TestClient) -> None:
    """RUN authority alone cannot mint: the queue reads are view-scoped.

    An accepted operation must not return a job its caller cannot monitor —
    the read contract admits a row only under current view authority — so the
    mint demands the regulatory view up front and refuses otherwise. The
    hermetic baseline sentence (viewer/all/all) is cleared first so the
    refusal is the missing regulatory view, not the loop's RUN gates.
    """
    _seed_and_refresh(db_client)
    with get_sessionmaker()() as session:
        session.execute(delete(AuthorizationBinding))
        session.commit()
        authorization.invalidate_user_authorization(
            session, organization_id=ORG_1, user_id=USER_1, reason="Clear the baseline sentence"
        )
        session.commit()
    authorization_version = _mint_authority(view=False)
    response = db_client.post(
        f"{_BASE}/official-runs",
        headers=headers(authorization_version=authorization_version),
        json={"as_of_date": AS_OF, "reason": "filing"},
    )
    assert response.status_code == 403, response.text


def test_tenant_isolation_org2_gets_404(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    org2 = headers(org_id=ORG_2)
    for path in ("live-summary", "freshness", "alerts"):
        response = db_client.get(f"{_BASE}/{path}", headers=org2)
        assert response.status_code == 404, f"{path}: {response.status_code}"
    for path in ("refresh", "official-runs"):
        response = db_client.post(
            f"{_BASE}/{path}", headers=org2, json={"as_of_date": AS_OF, "reason": "x"}
        )
        assert response.status_code == 404, f"{path}: {response.status_code}"
