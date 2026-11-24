"""Staff console: enrol a bank on the AequorOS Reporting Gateway.

The bank never handles certificates or keys. The channel operator enrols it;
AequorOS creates the bank's client key and filing signature key in the vault,
and the gateway (faked here with the checks the real one makes) issues the
client and registers the key. Enrolment is idempotent and audited twice: in the
operator log and in the bank's own audit trail.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_operator_settings, get_settings
from app.core.key_management.signing import generate_p256_key, private_key_pem, public_jwk
from app.models import AuditEvent, RegulatoryChannelConfig
from app.operator.api.reporting_gateway import get_reporting_gateway_transport
from app.operator.models import OperatorAuditLog
from app.reporting.service.channel_config import decrypt_channel_credentials
from tests.operator.conftest import operator_headers, provision_payload, start_inspection
from tests.support.factories.outbound import stub_public_dns

BASE_URL = "https://gateway.example.test"
#: Certificate validity is not what these tests exercise; a fixed clock keeps them deterministic.
ISSUED = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
OPERATOR_ID = "dev-operator-01"
_ENROL = "/operator/v1/tenants/{org_id}/banks/{bank_id}/reporting-gateway/enrolment"


def _certificate(
    subject_key: ec.EllipticCurvePrivateKey,
    common_name: str,
    issuer_key: ec.EllipticCurvePrivateKey | None = None,
) -> x509.Certificate:
    now = ISSUED
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Gateway CA")]))
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(issuer_key or subject_key, hashes.SHA256())
    )


def _pem(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


@dataclass
class FakeGateway:
    """The operator API and token endpoint, verifying what the real gateway verifies."""

    operator_key: ec.EllipticCurvePrivateKey = field(default_factory=generate_p256_key)
    ca_key: ec.EllipticCurvePrivateKey = field(default_factory=generate_p256_key)
    receipt_key: ec.EllipticCurvePrivateKey = field(default_factory=generate_p256_key)
    refuse_operator: bool = False
    institutions: dict[str, dict[str, object]] = field(default_factory=dict)
    clients: dict[str, ec.EllipticCurvePublicKey] = field(default_factory=dict)
    signing_keys: dict[str, dict[str, object]] = field(default_factory=dict)
    issued_certificates: dict[str, str] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(f"{request.method} {path}")
        if path == "/oauth/token":
            return self._token(request)
        if path == "/v1/.well-known/jwks.json":
            return httpx.Response(
                200, json={"keys": [public_jwk(self.receipt_key.public_key(), "gw-receipt")]}
            )
        assert request.headers["Authorization"] == "Bearer operator-token", path
        return self._operator(path, cast(dict[str, object], json.loads(request.content)))

    def _operator(self, path: str, body: dict[str, object]) -> httpx.Response:
        if path == "/ops/api/institutions":
            code = str(body["code"])
            existing = self.institutions.get(code)
            if existing is not None and existing["name"] != body["name"]:
                return httpx.Response(409, json={"code": "conflict"})
            self.institutions[code] = body
            return httpx.Response(200 if existing else 201, json={"code": code})
        if path.endswith("/clients"):
            return self._issue(path.split("/")[4], body)
        if path.endswith("/signing-keys"):
            return self._register_key(body)
        return httpx.Response(404, json={"code": "not_found"})

    def _register_key(self, body: dict[str, object]) -> httpx.Response:
        kid = str(body["kid"])
        jwk = cast(dict[str, object], body["public_jwk"])
        known = self.signing_keys.get(kid)
        if known is not None and known != jwk:
            return httpx.Response(409, json={"code": "conflict"})
        self.signing_keys[kid] = jwk
        return httpx.Response(200 if known else 201)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        assertion = form["client_assertion"]
        issuer = str(jwt.decode(assertion, options={"verify_signature": False})["iss"])
        if issuer == OPERATOR_ID:
            if self.refuse_operator:
                return httpx.Response(401, json={"code": "invalid_client_assertion"})
            # The operator signs under the kid it is registered with.
            assert jwt.get_unverified_header(assertion)["kid"] == f"{OPERATOR_ID}-key"
            jwt.decode(
                assertion,
                self.operator_key.public_key(),
                algorithms=["ES256"],
                audience=f"{BASE_URL}/oauth/token",
            )
            assert form["scope"] == "ops:enrol"
            return httpx.Response(200, json={"access_token": "operator-token"})
        public_key = self.clients[issuer]
        jwt.decode(assertion, public_key, algorithms=["ES256"], audience=f"{BASE_URL}/oauth/token")
        return httpx.Response(200, json={"access_token": "bank-token"})

    def _issue(self, code: str, body: dict[str, object]) -> httpx.Response:
        csr = x509.load_pem_x509_csr(str(body["csr_pem"]).encode())
        csr_key = csr.public_key()
        assert isinstance(csr_key, ec.EllipticCurvePublicKey)
        for client_id, known in self.clients.items():
            if known.public_numbers() == csr_key.public_numbers():
                return httpx.Response(
                    200,
                    json={
                        "client_id": client_id,
                        "certificate_pem": self.issued_certificates[client_id],
                    },
                )
        client_id = f"cl-{code}-{len(self.clients) + 1:02d}"
        certificate = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Gateway CA")]))
            .public_key(csr_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(ISSUED - timedelta(days=1))
            .not_valid_after(ISSUED + timedelta(days=365))
            .sign(self.ca_key, hashes.SHA256())
        )
        self.clients[client_id] = csr_key
        self.issued_certificates[client_id] = _pem(certificate)
        return httpx.Response(
            201, json={"client_id": client_id, "certificate_pem": _pem(certificate)}
        )


@pytest.fixture
def gateway(
    operator_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[FakeGateway]:
    fake = FakeGateway()
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
    yield fake
    get_settings.cache_clear()


def _provision(client: TestClient, **overrides: object) -> tuple[str, str]:
    response = client.post(
        "/operator/v1/tenants", json=provision_payload(**overrides), headers=operator_headers()
    )
    body = cast(dict[str, object], response.json())
    assert body["succeeded"] is True, body
    org_id = str(body["organization_id"])
    # Enrolment writes into one tenant's configuration: staff need an active
    # inspection session for that organization, as for its encryption key.
    start_inspection(client, org_id)
    return org_id, str(body["bank_id"])


def _enrol(client: TestClient, org_id: str, bank_id: str, code: str = "0142"):
    return client.post(
        _ENROL.format(org_id=org_id, bank_id=bank_id),
        json={
            "institution_code": code,
            "contacts": [
                {"role": "Head of Regulatory Reporting", "name": "Ama Mensah", "email": "a@b.test"}
            ],
        },
        headers=operator_headers(),
    )


def _channel_row(db: Session, bank_id: str) -> RegulatoryChannelConfig:
    row = db.scalar(
        select(RegulatoryChannelConfig).where(
            RegulatoryChannelConfig.bank_id == bank_id,
            RegulatoryChannelConfig.channel == "reporting_gateway",
        )
    )
    assert row is not None
    return row


def test_enrolment_issues_the_client_registers_the_key_and_tests_the_connection(
    operator_client: TestClient, operator_db: Session, gateway: FakeGateway
) -> None:
    org_id, bank_id = _provision(operator_client)

    response = _enrol(operator_client, org_id, bank_id)

    assert response.status_code == 200, response.text
    body = cast(dict[str, object], response.json())
    assert body["enrolled"] is True
    assert body["institution_code"] == "0142"
    assert body["environment"] == "sandbox"
    assert body["client_id"] == "cl-0142-01"
    assert str(body["signing_kid"]).startswith("0142-sign-")
    assert body["last_connection_test_ok"] is True
    assert gateway.institutions["0142"]["type"] == "universal_bank"
    assert list(gateway.signing_keys) == [body["signing_kid"]]

    row = _channel_row(operator_db, bank_id)
    assert "PRIVATE KEY" not in json.dumps(row.config)
    credentials = decrypt_channel_credentials(row) or {}
    assert set(credentials) == {"client_private_key_pem", "signing_private_key_pem"}
    assert row.config["signing_key"]["provider"] == "vault"

    operator_entry = operator_db.scalar(
        select(OperatorAuditLog).where(OperatorAuditLog.action == "reporting_gateway.enrolled")
    )
    assert operator_entry is not None
    assert operator_entry.detail["bank_id"] == bank_id
    tenant_events = operator_db.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == org_id,
            AuditEvent.event_type == "regulatory_channel_config.updated",
        )
    ).all()
    assert [event.details["action"] for event in tenant_events] == ["gateway_enrolled"]
    assert "PRIVATE KEY" not in json.dumps(tenant_events[0].details)


def test_repeating_an_enrolment_reuses_the_banks_keys_and_client(
    operator_client: TestClient, operator_db: Session, gateway: FakeGateway
) -> None:
    org_id, bank_id = _provision(operator_client)
    first = cast(dict[str, object], _enrol(operator_client, org_id, bank_id).json())
    credentials = decrypt_channel_credentials(_channel_row(operator_db, bank_id))

    second = _enrol(operator_client, org_id, bank_id)

    assert second.status_code == 200, second.text
    assert cast(dict[str, object], second.json())["client_id"] == first["client_id"]
    assert len(gateway.clients) == 1
    assert decrypt_channel_credentials(_channel_row(operator_db, bank_id)) == credentials


def test_a_failed_enrolment_keeps_the_keys_for_the_retry_and_is_audited(
    operator_client: TestClient, operator_db: Session, gateway: FakeGateway
) -> None:
    org_id, bank_id = _provision(operator_client)
    gateway.refuse_operator = True

    refused = _enrol(operator_client, org_id, bank_id)

    assert refused.status_code == 502
    assert (
        refused.json()["error"]["message"]
        == "The gateway refused the operator client's credentials."
    )
    keys = decrypt_channel_credentials(_channel_row(operator_db, bank_id))
    assert keys is not None
    failure = operator_db.scalar(
        select(OperatorAuditLog).where(
            OperatorAuditLog.action == "reporting_gateway.enrolment_failed"
        )
    )
    assert failure is not None and failure.detail["outcome"] == "failure"

    gateway.refuse_operator = False
    retried = _enrol(operator_client, org_id, bank_id)

    assert retried.status_code == 200, retried.text
    assert decrypt_channel_credentials(_channel_row(operator_db, bank_id)) == keys


def test_an_enrolled_bank_cannot_be_moved_to_another_institution_code(
    operator_client: TestClient, gateway: FakeGateway
) -> None:
    org_id, bank_id = _provision(operator_client)
    assert _enrol(operator_client, org_id, bank_id).status_code == 200

    moved = _enrol(operator_client, org_id, bank_id, code="0999")

    assert moved.status_code == 409
    assert "0999" not in gateway.institutions


def test_enrolment_needs_an_institution_code(
    operator_client: TestClient, gateway: FakeGateway
) -> None:
    org_id, bank_id = _provision(operator_client)

    response = operator_client.post(
        _ENROL.format(org_id=org_id, bank_id=bank_id), json={}, headers=operator_headers()
    )

    assert response.status_code == 422
    assert gateway.paths == []


def test_enrolment_refuses_a_bank_under_another_organization(
    operator_client: TestClient, gateway: FakeGateway
) -> None:
    _org_id, bank_id = _provision(operator_client)
    other_org, _other_bank = _provision(
        operator_client, organization_name="Other Holdings", bank_name="Other Bank"
    )

    response = _enrol(operator_client, other_org, bank_id)

    assert response.status_code == 404
    assert gateway.paths == []


def test_enrolment_is_unavailable_until_the_gateway_is_configured(
    operator_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, bank_id = _provision(operator_client)
    monkeypatch.setenv("REPORTING_GATEWAY_URL", "")
    get_operator_settings.cache_clear()

    response = _enrol(operator_client, org_id, bank_id)

    assert response.status_code == 503
    assert "REPORTING_GATEWAY_URL" in response.json()["error"]["message"]


def test_enrolment_status_refuses_a_bank_under_another_organization(
    operator_client: TestClient,
) -> None:
    _org_id, bank_id = _provision(operator_client)
    other_org, _other_bank = _provision(
        operator_client, organization_name="Other Holdings", bank_name="Other Bank"
    )

    response = operator_client.get(
        f"/operator/v1/tenants/{other_org}/banks/{bank_id}/reporting-gateway",
        headers=operator_headers(),
    )

    assert response.status_code == 404


def test_an_unenrolled_bank_reads_as_not_enrolled(operator_client: TestClient) -> None:
    org_id, bank_id = _provision(operator_client)

    response = operator_client.get(
        f"/operator/v1/tenants/{org_id}/banks/{bank_id}/reporting-gateway",
        headers=operator_headers(),
    )

    assert response.status_code == 200
    body = cast(dict[str, object], response.json())
    assert body["enrolled"] is False
    assert body["client_id"] is None


def test_enrolment_needs_an_inspection_session_for_the_organization(
    operator_client: TestClient, gateway: FakeGateway
) -> None:
    response = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    body = cast(dict[str, object], response.json())

    refused = _enrol(operator_client, str(body["organization_id"]), str(body["bank_id"]))

    assert refused.status_code == 403
    assert gateway.paths == []
