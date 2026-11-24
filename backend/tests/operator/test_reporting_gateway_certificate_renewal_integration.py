"""Renewing a bank's client certificate on the AequorOS Reporting Gateway.

A renewal replaces the bank's client key and certificate without ever leaving the
bank unable to file: the new client is issued and tested as the bank before the
bank is switched over, and only then is the old client revoked. Every failure
before the switch leaves the bank on its working certificate; a renewal resumes
where it stopped, with the same key, and never makes the gateway issue twice.
Staff renew from the console; the staff plane's scheduled check renews
certificates inside the renewal window and finishes what was left unfinished.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
import time_machine
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import OperatorSettings, get_operator_settings, get_settings
from app.core.key_management.signing import load_p256_private_key, private_key_pem
from app.models import AuditEvent, RegulatoryChannelConfig
from app.operator.api.reporting_gateway import get_reporting_gateway_transport
from app.operator.models import OperatorAuditLog
from app.operator.services.gateway_renewal_scheduler import (
    GatewayRenewalScheduler,
    scheduled_check_enabled,
)
from app.reporting.public import GatewayIdentity, gateway_enrolment, gateway_renewal
from app.reporting.service.channel_config import decrypt_channel_credentials
from app.reporting.service.reporting_gateway_connection import (
    _connection,  # pyright: ignore[reportPrivateUsage]
)
from tests.operator.conftest import operator_headers
from tests.operator.test_reporting_gateway_enrolment_integration import (
    BASE_URL,
    OPERATOR_ID,
    FakeGateway,
    _certificate,  # pyright: ignore[reportPrivateUsage]
    _channel_row,  # pyright: ignore[reportPrivateUsage]
    _enrol,  # pyright: ignore[reportPrivateUsage]
    _pem,  # pyright: ignore[reportPrivateUsage]
    _provision,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.factories.outbound import stub_public_dns

# Every renewal decision is read against the clock (leases, the renewal window, how
# fresh the scheduled check is), so the whole module runs at one instant. The
# fixture certificates are valid from 20 Nov 2026 to 21 Nov 2027.
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
pytestmark = pytest.mark.freeze_time("2026-11-22T12:00:00+00:00")

_RENEW = "/operator/v1/tenants/{org_id}/banks/{bank_id}/reporting-gateway/certificate-renewal"
_REASON = "Annual certificate renewal ahead of expiry."


@dataclass
class RenewingGateway(FakeGateway):
    """The fake gateway, able to revoke clients and to fail at each step."""

    revoked: set[str] = field(default_factory=set[str])
    refuse_issue: bool = False
    refuse_revoke: bool = False
    #: Bank clients whose token requests the gateway refuses (a failed connection test).
    refuse_tokens_for: set[str] = field(default_factory=set[str])

    def _operator(self, path: str, body: dict[str, object]) -> httpx.Response:
        if path.startswith("/ops/api/clients/") and path.endswith("/revoke"):
            if self.refuse_revoke:
                return httpx.Response(503)
            client_id = path.split("/")[4]
            if client_id not in self.clients:
                return httpx.Response(404, json={"code": "not_found"})
            assert str(body["reason"]).strip()
            self.revoked.add(client_id)
            return httpx.Response(204)
        if path.endswith("/clients") and self.refuse_issue:
            return httpx.Response(503)
        return super()._operator(path, body)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        issuer = str(
            jwt.decode(form["client_assertion"], options={"verify_signature": False})["iss"]
        )
        if issuer in self.revoked or issuer in self.refuse_tokens_for:
            return httpx.Response(401, json={"code": "invalid_client_assertion"})
        return super()._token(request)


@dataclass
class Gateway:
    fake: RenewingGateway
    operator: GatewayIdentity

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.fake)


@pytest.fixture
def gateway(
    operator_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Gateway]:
    fake = RenewingGateway()
    directory = tmp_path / "operator"
    directory.mkdir()
    (directory / "client.key.pem").write_text(private_key_pem(fake.operator_key))
    (directory / "client.crt.pem").write_text(
        _pem(_certificate(fake.operator_key, OPERATOR_ID, fake.ca_key))
    )
    (directory / "operator.json").write_text(
        json.dumps(
            {
                "client_id": OPERATOR_ID,
                "kid": f"{OPERATOR_ID}-key",
                "key_file": "client.key.pem",
                "certificate_file": "client.crt.pem",
            }
        )
    )
    monkeypatch.setenv("REPORTING_GATEWAY_URL", BASE_URL)
    monkeypatch.setenv("REPORTING_GATEWAY_OPERATOR_DIR", str(directory))
    monkeypatch.setenv("REPORTING_GATEWAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", "test-master-key-material")
    get_operator_settings.cache_clear()
    get_settings.cache_clear()
    stub_public_dns(monkeypatch, "gateway.example.test")
    app = cast(FastAPI, operator_client.app)
    app.dependency_overrides[get_reporting_gateway_transport] = lambda: httpx.MockTransport(fake)
    operator = gateway_enrolment.operator_identity(
        base_url=BASE_URL, directory=str(directory), forward_certificate=False
    )
    yield Gateway(fake=fake, operator=operator)
    get_settings.cache_clear()


def _enrolled(client: TestClient) -> tuple[str, str]:
    org_id, bank_id = _provision(client)
    response = _enrol(client, org_id, bank_id)
    assert response.status_code == 200, response.text
    return org_id, bank_id


def _renew(client: TestClient, org_id: str, bank_id: str, *, cancel: bool = False):
    path = _RENEW.format(org_id=org_id, bank_id=bank_id) + ("/cancel" if cancel else "")
    return client.post(path, json={"reason": _REASON}, headers=operator_headers())


def _body(response: Any) -> dict[str, object]:  # the test client's response
    return cast(dict[str, object], response.json())


def _record(body: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], body["certificate_renewal"])


def _credentials(db: Session, bank_id: str) -> dict[str, object]:
    db.expire_all()
    return dict(decrypt_channel_credentials(_channel_row(db, bank_id)) or {})


def _bank_actions(db: Session, org_id: str) -> list[object]:
    events = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == org_id,
            AuditEvent.event_type == "regulatory_channel_config.updated",
        )
        .order_by(AuditEvent.created_at)
    ).all()
    return [event.details["action"] for event in events]


def _client_public_key(credentials: dict[str, object], key: str) -> object:
    return load_p256_private_key(str(credentials[key])).public_key().public_numbers()


# -- the staff action ---------------------------------------------------------------


def test_a_renewal_issues_tests_switches_and_then_revokes(
    operator_client: TestClient, operator_db: Session, gateway: Gateway
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    before = _credentials(operator_db, bank_id)

    response = _renew(operator_client, org_id, bank_id)

    assert response.status_code == 200, response.text
    body = _body(response)
    assert body["client_id"] == "cl-0142-02"
    assert body["last_connection_test_ok"] is True
    record = _record(body)
    assert record["status"] == "complete"
    assert record["stage"] == "previous_revoked"
    assert record["trigger"] == "staff"
    assert record["reason"] == _REASON
    assert record["previous_client_id"] == "cl-0142-01"
    assert record["new_client_id"] == "cl-0142-02"
    assert record["previous_revoked_at"] is not None
    assert gateway.fake.revoked == {"cl-0142-01"}
    # The bank holds exactly one client key: the new one. Its filing signature key
    # is untouched by a certificate renewal.
    after = _credentials(operator_db, bank_id)
    assert set(after) == {"client_private_key_pem", "signing_private_key_pem"}
    assert after["signing_private_key_pem"] == before["signing_private_key_pem"]
    assert _client_public_key(after, "client_private_key_pem") == (
        gateway.fake.clients["cl-0142-02"].public_numbers()
    )
    row = _channel_row(operator_db, bank_id)
    assert "PRIVATE KEY" not in json.dumps(row.config)
    assert _bank_actions(operator_db, org_id) == [
        "gateway_enrolled",
        "gateway_certificate_renewed",
        "gateway_previous_certificate_revoked",
    ]
    staff = operator_db.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "reporting_gateway.certificate_renewed"
        )
    )
    assert staff is not None
    assert staff.detail["reason"] == _REASON
    assert staff.detail["previous_client_id"] == "cl-0142-01"


def test_a_failed_connection_test_keeps_the_bank_on_its_current_certificate(
    operator_client: TestClient, operator_db: Session, gateway: Gateway
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    gateway.fake.refuse_tokens_for.add("cl-0142-02")

    refused = _renew(operator_client, org_id, bank_id)

    assert refused.status_code == 502
    message = refused.json()["error"]["message"]
    assert "issued the new client cl-0142-02" in message
    assert "keeps filing with its current certificate" in message
    status = _body(
        operator_client.get(
            _RENEW.format(org_id=org_id, bank_id=bank_id).removesuffix("/certificate-renewal"),
            headers=operator_headers(),
        )
    )
    assert status["client_id"] == "cl-0142-01"
    assert _record(status)["status"] == "failed"
    assert _record(status)["stage"] == "client_issued"
    assert gateway.fake.revoked == set()
    assert "pending_client_private_key_pem" in _credentials(operator_db, bank_id)

    # Retried once the gateway accepts the new client: the same key, the same client.
    gateway.fake.refuse_tokens_for.clear()
    retried = _renew(operator_client, org_id, bank_id)

    assert retried.status_code == 200, retried.text
    assert _body(retried)["client_id"] == "cl-0142-02"
    assert list(gateway.fake.clients) == ["cl-0142-01", "cl-0142-02"]
    assert _record(_body(retried))["failures"] == 0
    assert gateway.fake.revoked == {"cl-0142-01"}


def test_a_gateway_outage_before_issue_is_retried_with_the_same_key(
    operator_client: TestClient, operator_db: Session, gateway: Gateway
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    gateway.fake.refuse_issue = True

    refused = _renew(operator_client, org_id, bank_id)

    assert refused.status_code == 502
    pending = _client_public_key(
        _credentials(operator_db, bank_id), "pending_client_private_key_pem"
    )
    assert list(gateway.fake.clients) == ["cl-0142-01"]
    failure = operator_db.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "reporting_gateway.certificate_renewal_failed"
        )
    )
    assert failure is not None and failure.detail["outcome"] == "failure"

    gateway.fake.refuse_issue = False
    retried = _renew(operator_client, org_id, bank_id)

    assert retried.status_code == 200, retried.text
    assert gateway.fake.clients["cl-0142-02"].public_numbers() == pending


def test_a_revocation_that_fails_after_the_switch_stands_and_is_finished_later(
    operator_client: TestClient,
    operator_db: Session,
    gateway: Gateway,
    _operator_bound_sessionmaker: sessionmaker[Session],
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    gateway.fake.refuse_revoke = True

    response = _renew(operator_client, org_id, bank_id)

    assert response.status_code == 200, response.text
    body = _body(response)
    assert body["client_id"] == "cl-0142-02"
    assert _record(body)["status"] == "revocation_pending"
    assert "could not be revoked" in str(body["message"])

    # The scheduled check finishes it, whether or not the certificate is due.
    gateway.fake.refuse_revoke = False
    result = gateway_renewal.run_scheduled_check(
        _operator_bound_sessionmaker,
        operator=gateway.operator,
        transport=gateway.transport,
    )

    assert result.renewed == [bank_id]
    assert gateway.fake.revoked == {"cl-0142-01"}
    operator_db.expire_all()
    record = cast(
        dict[str, object], _channel_row(operator_db, bank_id).config["certificate_renewal"]
    )
    assert record["status"] == "complete"
    assert record["trigger"] == "staff"
    assert list(gateway.fake.clients) == ["cl-0142-01", "cl-0142-02"]


def test_cancelling_revokes_the_issued_client_and_keeps_the_current_certificate(
    operator_client: TestClient, operator_db: Session, gateway: Gateway
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    gateway.fake.refuse_tokens_for.add("cl-0142-02")
    assert _renew(operator_client, org_id, bank_id).status_code == 502

    cancelled = _renew(operator_client, org_id, bank_id, cancel=True)

    assert cancelled.status_code == 200, cancelled.text
    body = _body(cancelled)
    assert body["client_id"] == "cl-0142-01"
    assert _record(body)["status"] == "cancelled"
    assert gateway.fake.revoked == {"cl-0142-02"}
    assert "pending_client_private_key_pem" not in _credentials(operator_db, bank_id)
    assert _bank_actions(operator_db, org_id)[-1] == "gateway_certificate_renewal_cancelled"
    # Nothing is left to cancel.
    assert _renew(operator_client, org_id, bank_id, cancel=True).status_code == 409


def test_a_switched_renewal_cannot_be_cancelled(
    operator_client: TestClient, gateway: Gateway
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    gateway.fake.refuse_revoke = True
    assert _renew(operator_client, org_id, bank_id).status_code == 200

    refused = _renew(operator_client, org_id, bank_id, cancel=True)

    assert refused.status_code == 409


def test_a_renewal_already_running_is_not_started_again(
    operator_client: TestClient, operator_db: Session, gateway: Gateway
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    row = _channel_row(operator_db, bank_id)
    config = dict(row.config)
    config["certificate_renewal"] = {
        "status": "in_progress",
        "stage": "key_prepared",
        "trigger": "scheduled",
        "requested_by": gateway_renewal.SCHEDULED_BY,
        "reason": "Running elsewhere.",
        "started_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "lease_until": (NOW + timedelta(minutes=5)).isoformat(),
        "failures": 0,
    }
    row.config = config
    operator_db.commit()

    refused = _renew(operator_client, org_id, bank_id)

    assert refused.status_code == 409
    assert "already running" in refused.json()["error"]["message"]
    assert list(gateway.fake.clients) == ["cl-0142-01"]


def test_an_unenrolled_bank_has_no_certificate_to_renew(
    operator_client: TestClient, gateway: Gateway
) -> None:
    org_id, bank_id = _provision(operator_client)

    refused = _renew(operator_client, org_id, bank_id)

    assert refused.status_code == 409
    assert "not enrolled" in refused.json()["error"]["message"]
    assert gateway.fake.clients == {}


# -- the scheduled check ------------------------------------------------------------


def test_the_scheduled_check_renews_only_inside_the_renewal_window(
    operator_client: TestClient,
    operator_db: Session,
    gateway: Gateway,
    _operator_bound_sessionmaker: sessionmaker[Session],
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    status_path = _RENEW.format(org_id=org_id, bank_id=bank_id).removesuffix("/certificate-renewal")

    # The fixture certificate has a year to run: nothing is due 30 days out.
    early = gateway_renewal.run_scheduled_check(
        _operator_bound_sessionmaker, operator=gateway.operator, transport=gateway.transport
    )

    assert early.checked == [bank_id]
    assert early.renewed == []
    checked = _body(operator_client.get(status_path, headers=operator_headers()))
    assert checked["client_id"] == "cl-0142-01"
    assert checked["last_renewal_check_at"] is not None
    assert checked["certificate_renewal"] is None

    # Inside the window (here: renew 400 days ahead), the check renews it.
    due = gateway_renewal.run_scheduled_check(
        _operator_bound_sessionmaker,
        operator=gateway.operator,
        renew_before_days=400,
        transport=gateway.transport,
    )

    assert due.renewed == [bank_id]
    renewed = _body(operator_client.get(status_path, headers=operator_headers()))
    assert renewed["client_id"] == "cl-0142-02"
    record = _record(renewed)
    assert record["trigger"] == "scheduled"
    assert record["requested_by"] == gateway_renewal.SCHEDULED_BY
    assert gateway.fake.revoked == {"cl-0142-01"}


def test_the_scheduled_check_leaves_banks_on_another_gateway_alone(
    operator_client: TestClient,
    operator_db: Session,
    gateway: Gateway,
    _operator_bound_sessionmaker: sessionmaker[Session],
) -> None:
    _, bank_id = _enrolled(operator_client)
    row = _channel_row(operator_db, bank_id)
    config = dict(row.config)
    config["api_base_url"] = "https://another-gateway.example.test"
    row.config = config
    operator_db.commit()

    result = gateway_renewal.run_scheduled_check(
        _operator_bound_sessionmaker,
        operator=gateway.operator,
        renew_before_days=400,
        transport=gateway.transport,
    )

    assert result.skipped == {bank_id: "enrolled on another gateway"}
    assert result.renewed == []
    assert list(gateway.fake.clients) == ["cl-0142-01"]


def test_one_scheduler_run_checks_every_enrolled_bank(
    operator_client: TestClient,
    gateway: Gateway,
    _operator_bound_sessionmaker: sessionmaker[Session],
) -> None:
    _, bank_id = _enrolled(operator_client)
    scheduler = GatewayRenewalScheduler(
        session_factory=_operator_bound_sessionmaker,
        operator=lambda: gateway.operator,
        renew_before_days=30,
        interval_seconds=3600,
        transport=gateway.transport,
    )

    result = scheduler.run_once()

    assert result is not None
    assert result.checked == [bank_id]


def test_a_scheduled_renewal_is_in_the_staff_log_as_the_system(
    operator_client: TestClient,
    operator_db: Session,
    gateway: Gateway,
    _operator_bound_sessionmaker: sessionmaker[Session],
    frozen_clock: time_machine.Traveller,
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    scheduler = GatewayRenewalScheduler(
        session_factory=_operator_bound_sessionmaker,
        operator=lambda: gateway.operator,
        renew_before_days=400,
        interval_seconds=3600,
        transport=gateway.transport,
    )

    result = scheduler.run_once()

    assert result is not None
    assert result.renewed == [bank_id]
    rows = operator_db.scalars(
        select(OperatorAuditLog).where(OperatorAuditLog.auth_mode == "system")
    ).all()
    assert [(row.action, row.target_org) for row in rows] == [
        ("reporting_gateway.certificate_renewed", org_id)
    ]
    assert rows[0].operator_email == gateway_renewal.SCHEDULED_BY
    assert rows[0].detail["bank_id"] == bank_id
    assert rows[0].detail["previous_client_id"] == "cl-0142-01"
    assert rows[0].detail["new_client_id"] == "cl-0142-02"
    assert rows[0].detail["outcome"] == "success"
    # The console's staff log reads it back (it lists rows strictly before "now").
    frozen_clock.move_to(NOW + timedelta(seconds=1))
    listed = _body(
        operator_client.get(
            "/operator/v1/audit",
            params={"action": "reporting_gateway.certificate_renewed"},
            headers=operator_headers(),
        )
    )
    items = cast(list[dict[str, object]], listed["items"])
    assert [item["auth_mode"] for item in items] == ["system"]

    # A check that finds nothing due writes nothing to the staff log.
    quiet = GatewayRenewalScheduler(
        session_factory=_operator_bound_sessionmaker,
        operator=lambda: gateway.operator,
        renew_before_days=30,
        interval_seconds=3600,
        transport=gateway.transport,
    ).run_once()
    assert quiet is not None
    assert quiet.renewed == []
    operator_db.expire_all()
    assert (
        len(
            operator_db.scalars(
                select(OperatorAuditLog).where(OperatorAuditLog.auth_mode == "system")
            ).all()
        )
        == 1
    )


def test_the_scheduled_check_runs_only_where_the_gateway_is_configured() -> None:
    configured = OperatorSettings.model_validate(
        {
            "REPORTING_GATEWAY_URL": BASE_URL,
            "REPORTING_GATEWAY_OPERATOR_DIR": "/run/gateway-operator",
        }
    )
    assert scheduled_check_enabled(configured, "production") is True
    assert scheduled_check_enabled(configured, "test") is False
    switched_off = configured.model_copy(update={"reporting_gateway_renewal_enabled": False})
    assert scheduled_check_enabled(switched_off, "production") is False
    unconfigured = configured.model_copy(update={"reporting_gateway_operator_dir": None})
    assert scheduled_check_enabled(unconfigured, "production") is False


# -- what the bank is told -------------------------------------------------------------


def test_the_bank_is_told_of_automatic_renewal_only_while_the_check_is_running(
    operator_client: TestClient,
    operator_db: Session,
    gateway: Gateway,
    _operator_bound_sessionmaker: sessionmaker[Session],
) -> None:
    _, bank_id = _enrolled(operator_client)
    now = NOW
    assert _connection(dict(_channel_row(operator_db, bank_id).config), now) is not None
    before = _connection(dict(_channel_row(operator_db, bank_id).config), now)
    assert before is not None and before.automatic_renewal_from is None

    gateway_renewal.run_scheduled_check(
        _operator_bound_sessionmaker, operator=gateway.operator, transport=gateway.transport
    )
    operator_db.expire_all()
    config = dict(_channel_row(operator_db, bank_id).config)

    checked = _connection(config, now)
    assert checked is not None
    assert checked.automatic_renewal_from == checked.renews_by - timedelta(days=30)
    stale = _connection(config, now + timedelta(days=4))
    assert stale is not None and stale.automatic_renewal_from is None


def test_the_bank_sees_when_its_certificate_was_renewed(
    operator_client: TestClient, operator_db: Session, gateway: Gateway
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    assert _renew(operator_client, org_id, bank_id).status_code == 200
    operator_db.expire_all()

    facts = _connection(dict(_channel_row(operator_db, bank_id).config), NOW)

    assert facts is not None
    assert facts.client_id == "cl-0142-02"
    assert facts.renewed_at is not None


def test_the_renewal_record_never_carries_key_material(
    operator_client: TestClient, operator_db: Session, gateway: Gateway
) -> None:
    org_id, bank_id = _enrolled(operator_client)
    gateway.fake.refuse_tokens_for.add("cl-0142-02")
    _ = _renew(operator_client, org_id, bank_id)

    row = operator_db.scalar(
        select(RegulatoryChannelConfig).where(RegulatoryChannelConfig.bank_id == bank_id)
    )

    assert row is not None
    assert "PRIVATE KEY" not in json.dumps(row.config)
    events = operator_db.scalars(
        select(AuditEvent).where(AuditEvent.organization_id == org_id)
    ).all()
    assert all("PRIVATE KEY" not in json.dumps(event.details) for event in events)
