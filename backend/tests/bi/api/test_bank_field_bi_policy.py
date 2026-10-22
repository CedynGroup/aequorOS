"""Bank-key refusals must render as capability decisions, preserving numeric BI."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.bi.domain.catalogue import catalogue
from app.bi.models.notifications import BiAlert, BiAlertEvent, BiSubscription
from app.bi.schemas.notifications import (
    BiAlertListRead,
    BiAlertRead,
    BiSubscriptionDeliveryListRead,
)
from app.bi.schemas.query import BiCatalogueRead, BiFilter, BiQuery, BiQueryResult
from app.bi.service import alerts, subscriptions
from app.bi.service.authorization import query_members
from app.bi.service.identity_policy import IDENTITY_MESSAGE, IDENTITY_REASON
from app.core.authorization import ModuleScope, SensitivityScope
from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.types import KeyReference
from app.identity.public import Bank
from app.models import Job
from tests.bi.api import test_bi_routes as routes
from tests.support.helpers import ORG_1, USER_1, headers

mart = routes.mart
bi_on = routes.bi_on


class _Details(TypedDict):
    error_code: str
    message: str


class _Error(TypedDict):
    details: _Details


class _Failure(TypedDict):
    error: _Error


def test_connected_bank_refusal_explains_capability_and_keeps_numeric_concentration(
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authv = routes.grant_only(db_session, (routes.Grant(ModuleScope.ALL, SensitivityScope.ALL),))
    request_headers = headers(authorization_version=authv)
    query = {
        "measures": ["loans.largest_single_name_share_pct"],
        "time": {"as_of": routes.AS_OF.isoformat()},
    }
    before = db_client.post(routes.BASE + "/query", json=query, headers=request_headers)
    assert before.status_code == 200, before.text
    expected = BiQueryResult.model_validate(cast(object, before.json()))
    assert TypeAdapter(Decimal).validate_python(expected.rows[0][0]) == 50
    old_catalogue = db_client.get(routes.BASE + "/catalogue", headers=request_headers)
    assert old_catalogue.status_code == 200
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-bi-route", "us-east-1", "111122223333")
    provider.add_key(key)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    registry.register(
        db_session,
        organization_id=ORG_1,
        bank_id=mart.id,
        storage_slug="synthetic-bi-route",
        key=key,
        providers=providers,
    )
    db_session.commit()
    numeric = db_client.post(routes.BASE + "/query", json=query, headers=request_headers)
    assert numeric.status_code == 200, numeric.text
    assert BiQueryResult.model_validate(cast(object, numeric.json())).rows == expected.rows
    refused = db_client.post(
        routes.BASE + "/query", json=routes.OBLIGOR_QUERY, headers=request_headers
    )
    assert refused.status_code == 403, refused.text
    details = TypeAdapter(_Failure).validate_python(cast(object, refused.json()))["error"][
        "details"
    ]
    # This distinct code makes the dashboard render the server's own sentence,
    # instead of paraphrasing it as a grant an Org Owner can issue.
    assert details["error_code"] == "bank_key_identity_bi_unavailable"
    assert "bank-held encryption" in details["message"]
    assert "Numeric BI remains available" in details["message"]
    assert "Ada Traders" not in refused.text
    refreshed = db_client.get(
        routes.BASE + "/catalogue",
        headers={**request_headers, "If-None-Match": old_catalogue.headers["etag"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    catalogue = BiCatalogueRead.model_validate(cast(object, refreshed.json()))
    assert "counterparty.name" not in {dimension.id for dimension in catalogue.dimensions}
    assert "counterparty" not in {hierarchy.id for hierarchy in catalogue.hierarchies}
    assert "loans.largest_single_name_share_pct" in {measure.id for measure in catalogue.measures}


def _saved_alerts(db_session: Session, mart: Bank, identity_first: bool) -> dict[bool, UUID]:
    identity_filters: list[dict[str, object]] = [
        {"member": "counterparty.id", "op": "eq", "values": [str(routes.CP_ONE)]}
    ]
    numeric = BiQuery.model_validate(
        {
            "measures": ["loans.largest_single_name_share_pct"],
            "time": {"as_of": routes.AS_OF.isoformat()},
        }
    )
    identity = numeric.model_copy(
        update={"filters": [BiFilter.model_validate(identity_filters[0])]}
    )
    assert tuple(member.id for member in query_members(catalogue(), numeric)) == tuple(
        member.id for member in query_members(catalogue(), identity)
    )
    saved: dict[bool, BiAlert] = {}
    for position, is_identity in enumerate((identity_first, not identity_first)):
        alert = BiAlert(
            organization_id=ORG_1,
            bank_id=mart.id,
            owner_user_id=USER_1,
            name="Identity" if is_identity else "Numeric",
            measure_id="loans.largest_single_name_share_pct",
            filters=identity_filters if is_identity else [],
            direction="above",
            threshold_basis="stated",
            threshold=Decimal(10),
            notify_user_ids=[],
            updated_at=datetime(2026, 9, 1, tzinfo=UTC) - timedelta(days=position),
        )
        db_session.add(alert)
        db_session.flush()
        db_session.add(
            BiAlertEvent(
                organization_id=ORG_1,
                bank_id=mart.id,
                alert_id=alert.id,
                as_of_date=routes.AS_OF,
                build_fingerprint="c" * 64,
                state="breached",
                observed_value=Decimal(50),
                threshold_value=Decimal(10),
                threshold_basis="stated",
                member_ids=[alert.measure_id],
                notified_user_ids=[],
                withheld_user_ids=[],
                builder_version=1,
            )
        )
        saved[is_identity] = alert
    db_session.commit()
    return {identity: alert.id for identity, alert in saved.items()}


@pytest.mark.usefixtures("bi_on")
@pytest.mark.parametrize("identity_first", [False, True])
def test_saved_alert_capability_decisions_are_order_independent(
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    monkeypatch: pytest.MonkeyPatch,
    identity_first: bool,
) -> None:

    authv = routes.grant_only(db_session, (routes.Grant(ModuleScope.ALL, SensitivityScope.ALL),))
    request_headers = headers(authorization_version=authv)
    ids = _saved_alerts(db_session, mart, identity_first)
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-saved-alerts", "us-east-1", "111122223333")
    provider.add_key(key)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    registry.register(
        db_session,
        organization_id=ORG_1,
        bank_id=mart.id,
        storage_slug="synthetic-saved-alerts",
        key=key,
        providers=lambda _name: provider,
    )
    db_session.commit()
    db_session.expunge_all()
    response = db_client.get(routes.BASE + "/alerts", headers=request_headers)
    assert response.status_code == 200, response.text
    listed = BiAlertListRead.model_validate(cast(object, response.json())).alerts
    assert [item.id for item in listed] == [ids[identity_first], ids[not identity_first]]
    by_id = {item.id: item for item in listed}
    assert by_id[ids[False]].latest_state == "breached"
    assert by_id[ids[True]].latest_state is None
    assert by_id[ids[True]].latest_detail == IDENTITY_MESSAGE
    for suffix in ("", "/events"):
        response = db_client.get(
            f"{routes.BASE}/alerts/{ids[True]}{suffix}", headers=request_headers
        )
        assert response.status_code == (403 if suffix else 200), response.text
        if suffix:
            assert IDENTITY_MESSAGE in response.text
        else:
            assert (
                BiAlertRead.model_validate(cast(object, response.json())).latest_detail
                == IDENTITY_MESSAGE
            )
    evaluation = alerts.evaluate_bank(
        db_session, organization_id=ORG_1, bank_id=mart.id, as_of=routes.AS_OF
    )
    outcome = next(item for item in evaluation.outcomes if item.alert_id == ids[True])
    assert outcome.reason == IDENTITY_REASON and outcome.event is not None
    assert outcome.event.observed_value is None and not outcome.notifies
    db_session.commit()
    response = db_client.post(
        f"{routes.BASE}/alerts/{ids[True]}/deactivation",
        json={"reason": "Synthetic stop"},
        headers=request_headers,
    )
    assert response.status_code == 200, response.text
    stopped = BiAlertRead.model_validate(cast(object, response.json()))
    assert not stopped.is_active and stopped.latest_detail == IDENTITY_MESSAGE
    alert = db_session.get(BiAlert, ids[True])
    assert alert is not None
    alert.filters = []
    db_session.commit()
    response = db_client.get(f"{routes.BASE}/alerts/{ids[True]}/events", headers=request_headers)
    assert response.status_code == 200, response.text
    assert IDENTITY_MESSAGE in response.text


def test_saved_subscription_delivery_history_explains_bank_key_capability(
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    authv = routes.grant_only(db_session, (routes.Grant(ModuleScope.ALL, SensitivityScope.ALL),))
    subscription = BiSubscription(
        organization_id=ORG_1,
        bank_id=mart.id,
        owner_user_id=USER_1,
        name="Synthetic saved identity report",
        query={
            "measures": ["loans.balance_rc"],
            "filters": [
                {"member": "counterparty.name", "op": "eq", "values": ["Synthetic borrower"]}
            ],
            "time": {"as_of": routes.AS_OF.isoformat()},
        },
        artifact_format="csv",
        cadence="daily",
        hour=7,
        minute=30,
        recipient_user_ids=[str(USER_1)],
    )
    db_session.add(subscription)
    db_session.commit()
    subscription_id = subscription.id
    provider = LocalKeyProvider()
    key = KeyReference("aws_kms", "synthetic-subscription-history", "us-east-1", "111122223333")
    provider.add_key(key)

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    registry.register(
        db_session,
        organization_id=ORG_1,
        bank_id=mart.id,
        storage_slug="synthetic-subscription-history",
        key=key,
        providers=lambda _name: provider,
    )
    db_session.commit()
    db_session.expunge_all()
    job = Job(
        organization_id=ORG_1,
        bank_id=mart.id,
        job_type=subscriptions.JOB_TYPE_RUN,
        status="running",
        payload={
            "subscription_id": str(subscription_id),
            "scheduled_for": "2026-09-01T07:30:00+00:00",
            "trigger": "new_data",
            "as_of_date": routes.AS_OF.isoformat(),
        },
    )
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(subscriptions.mailer, "relay_configured", lambda: True)
    outcome = subscriptions.run_subscription(db_session, job)
    assert len(outcome.deliveries) == 1
    assert outcome.deliveries[0].reason == IDENTITY_REASON
    assert outcome.deliveries[0].status == "denied"
    response = db_client.get(
        f"{routes.BASE}/subscriptions/{subscription_id}/deliveries",
        headers=headers(authorization_version=authv),
    )
    assert response.status_code == 200, response.text
    history = BiSubscriptionDeliveryListRead.model_validate(cast(object, response.json()))
    assert len(history.deliveries) == 1
    delivery = history.deliveries[0]
    assert delivery.detail == "Not sent. " + IDENTITY_MESSAGE
    assert delivery.reason == IDENTITY_REASON and delivery.row_count is None
