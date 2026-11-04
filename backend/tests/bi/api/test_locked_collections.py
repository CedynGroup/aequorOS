from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bi.jobs.bi_mart_refresh import run_bi_mart_refresh
from app.bi.models.content import BiDashboard
from app.bi.models.marts import BiMartBuild
from app.bi.models.notifications import BiAlert, BiSubscription
from app.bi.schemas.content import (
    BiDashboardListRead,
    BiDashboardRead,
    BiDashboardSpec,
    BiDashboardSummaryRead,
    BiDashboardVersionListRead,
)
from app.bi.schemas.notifications import BiAlertListRead, BiSubscriptionListRead
from app.bi.service import alerts, content, subscriptions
from app.bi.service.identity_policy import IDENTITY_REASON
from app.bi.service.versions import BUILDER_VERSION
from app.core.authorization import ModuleScope, SensitivityScope
from app.core.config import get_settings
from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.presentation import SESSION_STATE
from app.core.key_management.types import KeyReference, KeyStatus, KeyUnavailableError
from app.identity.public import Bank
from app.models import Job
from tests.bi.api import test_bi_routes as routes
from tests.support.helpers import ORG_1, USER_1, headers

mart = routes.mart
bi_on = routes.bi_on
__all__ = ["mart", "bi_on"]


def canvas(filters: list[dict[str, object]]) -> dict[str, object]:
    return {
        "widgets": [
            {
                "id": "book",
                "kind": "kpi",
                "title": "Book",
                "query": {"measures": ["loans.balance_rc"], "window": "as_of", "filters": filters},
            }
        ],
        "layout": [{"i": "book", "x": 0, "y": 0, "w": 6, "h": 4}],
    }


def saved_collections(
    client: TestClient, db: Session, bank: Bank, auth: dict[str, str]
) -> dict[str, UUID]:
    ids: dict[str, UUID] = {}
    for identity in (False, True):
        label = "protected" if identity else "numeric"
        filters: list[dict[str, object]] = (
            [{"member": "counterparty.name", "op": "eq", "values": ["Private identity"]}]
            if identity
            else []
        )
        alert = BiAlert(
            organization_id=ORG_1,
            bank_id=bank.id,
            owner_user_id=USER_1,
            name=label,
            measure_id="loans.balance_rc",
            filters=filters,
            direction="above",
            threshold_basis="stated",
            threshold=Decimal(10),
            notify_user_ids=[],
        )
        subscription = BiSubscription(
            organization_id=ORG_1,
            bank_id=bank.id,
            owner_user_id=USER_1,
            name=label,
            query={
                "measures": ["loans.balance_rc"],
                "time": {"as_of": routes.AS_OF.isoformat()},
                "filters": filters,
            },
            artifact_format="csv",
            cadence="daily",
            hour=7,
            minute=30,
            recipient_user_ids=[],
        )
        db.add_all([alert, subscription])
        db.flush()
        ids[f"{label}_alert"], ids[f"{label}_subscription"] = alert.id, subscription.id
        db.commit()
        response = client.post(
            routes.BASE + "/dashboards",
            headers=auth,
            json={"title": label, "visibility": "private", "spec": canvas([])},
        )
        assert response.status_code == 201, response.text
        ids[f"{label}_dashboard"] = BiDashboardSummaryRead.model_validate_json(response.content).id
        if identity:
            dashboard = db.get(BiDashboard, ids["protected_dashboard"])
            assert dashboard is not None
            content.update_dashboard(
                db,
                dashboard,
                actor_user_id=USER_1,
                title=label,
                description="",
                visibility="private",
                visibility_role=None,
                spec=BiDashboardSpec.model_validate(canvas(filters)),
                change_note="Synthetic identity version",
            )
            db.commit()
    return ids


def assert_lists(client: TestClient, auth: dict[str, str], message: str) -> None:
    alert_response = client.get(routes.BASE + "/alerts", headers=auth)
    subscription_response = client.get(routes.BASE + "/subscriptions", headers=auth)
    dashboard_response = client.get(routes.BASE + "/dashboards", headers=auth)
    for response in (alert_response, subscription_response, dashboard_response):
        assert response.status_code == 200, response.text
        assert "Private identity" not in response.text
    groups = (
        BiAlertListRead.model_validate_json(alert_response.content).alerts,
        BiSubscriptionListRead.model_validate_json(subscription_response.content).subscriptions,
        BiDashboardListRead.model_validate_json(dashboard_response.content).dashboards,
    )
    for group in groups:
        assert len(group) == 2
        assert [item.locked_reason for item in group].count(message) == 1
        assert [item.locked_reason for item in group].count(None) == 1
    subscriptions_read = groups[1]
    assert next(item for item in subscriptions_read if item.name == "protected").query is None
    assert next(item for item in subscriptions_read if item.name == "numeric").query is not None


def assert_dashboards(
    client: TestClient, auth: dict[str, str], ids: dict[str, UUID], message: str
) -> None:
    history = client.get(
        f"{routes.BASE}/dashboards/{ids['protected_dashboard']}/versions", headers=auth
    )
    assert history.status_code == 200, history.text
    versions = {
        row.version: row
        for row in BiDashboardVersionListRead.model_validate_json(history.content).versions
    }
    assert versions[1].locked_reason is None and versions[2].locked_reason == message
    for label in ("numeric", "protected"):
        response = client.get(
            f"{routes.BASE}/dashboards/{ids[label + '_dashboard']}",
            headers=auth,
            params={"as_of": routes.AS_OF.isoformat()},
        )
        assert response.status_code == 200, response.text
        dashboard = BiDashboardRead.model_validate_json(response.content)
        if label == "protected":
            assert dashboard.message == message and dashboard.widgets == []
        else:
            assert dashboard.widgets[0].query is not None


@pytest.mark.parametrize(
    "scenario",
    [
        (status, legacy)
        for status in (KeyStatus.DISABLED, KeyStatus.UNAVAILABLE)
        for legacy in (True, False)
    ],
)
@pytest.mark.usefixtures("bi_on")
def test_identity_items_lock_without_hiding_numeric_siblings(
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    monkeypatch: pytest.MonkeyPatch,
    scenario: tuple[KeyStatus, bool],
) -> None:
    status, legacy = scenario
    version = routes.grant_only(db_session, (routes.Grant(ModuleScope.ALL, SensitivityScope.ALL),))
    auth = headers(authorization_version=version)
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-mixed-lists", "us-east-1", "111122223333")
    provider.add_key(key)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    ids: dict[str, UUID] = {}
    if legacy:
        ids = saved_collections(db_client, db_session, mart, auth)
    registry.register(
        db_session,
        organization_id=ORG_1,
        bank_id=mart.id,
        storage_slug="synthetic-mixed-lists",
        key=key,
        providers=providers,
    )
    db_session.commit()
    if not legacy:
        ids = saved_collections(db_client, db_session, mart, auth)
    provider.set_status(key, status)
    db_session.expunge_all()
    message = KeyUnavailableError("private", revoked=status == KeyStatus.DISABLED).public_message
    assert_lists(db_client, auth, message)
    assert_dashboards(db_client, auth, ids, message)
    db_session.expunge_all()
    db_session.info.pop(SESSION_STATE, None)
    result = alerts.evaluate_bank(
        db_session, organization_id=ORG_1, bank_id=mart.id, as_of=routes.AS_OF
    )
    outcomes = {outcome.alert_id: outcome for outcome in result.outcomes}
    assert outcomes[ids["protected_alert"]].reason == IDENTITY_REASON
    event = outcomes[ids["numeric_alert"]].event
    assert event is not None and event.observed_value is not None
    db_session.commit()
    again = alerts.evaluate_bank(
        db_session, organization_id=ORG_1, bank_id=mart.id, as_of=routes.AS_OF
    )
    assert all(outcome.result == "already_recorded" for outcome in again.outcomes)


@pytest.mark.usefixtures("bi_on")
def test_subscription_scheduling_and_mart_commit_do_not_open_queries(
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = routes.grant_only(db_session, (routes.Grant(ModuleScope.ALL, SensitivityScope.ALL),))
    ids = saved_collections(db_client, db_session, mart, headers(authorization_version=version))
    subscription = db_session.get(BiSubscription, ids["protected_subscription"])
    assert subscription is not None
    subscription.cadence, subscription.hour, subscription.minute = "on_new_data", None, None
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-scheduling", "us-east-1", "111122223333")
    provider.add_key(key)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    registry.register(
        db_session,
        organization_id=ORG_1,
        bank_id=mart.id,
        storage_slug="synthetic-scheduling",
        key=key,
        providers=providers,
    )
    db_session.commit()
    provider.set_status(key, KeyStatus.DISABLED)

    def unused_provider(_name: str) -> LocalKeyProvider:
        raise AssertionError("Metadata scheduling must not contact KMS")

    monkeypatch.setattr(registry, "provider_for", unused_provider)
    monkeypatch.setenv("BI_SUBSCRIPTIONS_ENABLED", "1")
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1")
    get_settings.cache_clear()
    db_session.expunge_all()
    db_session.info.pop(SESSION_STATE, None)
    start = datetime(2026, 8, 31, 7, 0, tzinfo=UTC)
    due = subscriptions.due_runs(
        db_session, organization_id=ORG_1, window_start=start, window_end=start + timedelta(hours=1)
    )
    assert [run.subscription_id for run in due] == [ids["numeric_subscription"]]
    job = Job(
        organization_id=ORG_1,
        bank_id=mart.id,
        job_type="bi_mart_refresh",
        status="running",
        payload={
            "organization_id": ORG_1,
            "bank_id": mart.id,
            "as_of_date": routes.AS_OF.isoformat(),
            "builder_version": BUILDER_VERSION,
            "reason": "synthetic scheduling",
        },
    )
    db_session.add(job)
    db_session.flush()
    run_bi_mart_refresh(db_session, job)
    assert job.progress is not None and job.progress["status"] == "succeeded"
    assert job.progress["on_new_data_runs_enqueued"] == 1
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(BiMartBuild.id).where(
                BiMartBuild.bank_id == mart.id, BiMartBuild.status == "succeeded"
            )
        )
        is not None
    )
