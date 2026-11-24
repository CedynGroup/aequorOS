"""Staff console: every institution on the AequorOS Reporting Gateway.

AequorOS client banks are enrolled from their tenant page; these routes manage the
channel itself — the list of every institution, and an institution that files with
its own software and so holds its own keys. The gateway is faked with the checks the
real one makes; every call is an operator audit entry, refused or not.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_operator_settings, get_settings
from app.core.key_management.signing import generate_p256_key, private_key_pem, public_jwk
from app.models import RegulatoryChannelConfig
from app.operator.api.reporting_gateway import get_reporting_gateway_transport
from app.operator.models import OperatorAuditLog
from tests.operator.conftest import operator_headers, provision_payload
from tests.operator.test_reporting_gateway_enrolment_integration import (
    BASE_URL,
    ISSUED,
    OPERATOR_ID,
    _certificate,  # pyright: ignore[reportPrivateUsage]
    _pem,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.factories.outbound import stub_public_dns

_ROOT = "/operator/v1/reporting-gateway/institutions"
_CODE = re.compile(r"^/ops/api/institutions/(\d{4})(/.*)?$")


def _b64(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode()


@dataclass
class OpsGateway:
    """The gateway's operator API, as far as the console uses it."""

    operator_key: ec.EllipticCurvePrivateKey = field(default_factory=generate_p256_key)
    ca_key: ec.EllipticCurvePrivateKey = field(default_factory=generate_p256_key)
    institutions: dict[str, dict[str, object]] = field(default_factory=dict)

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - a router
        path = request.url.path
        if path == "/oauth/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            jwt.decode(
                form["client_assertion"],
                self.operator_key.public_key(),
                algorithms=["ES256"],
                audience=f"{BASE_URL}/oauth/token",
            )
            return httpx.Response(200, json={"access_token": "operator-token"})
        assert request.headers["Authorization"] == "Bearer operator-token"
        body = cast(dict[str, object], json.loads(request.content)) if request.content else {}
        if path == "/ops/api/institutions":
            if request.method == "GET":
                items = [self.institutions[code] for code in sorted(self.institutions)]
                return httpx.Response(200, json={"items": items, "next_cursor": None})
            return self._enrol(body)
        if path.startswith("/ops/api/clients/") and path.endswith("/revoke"):
            client_id = path.split("/")[4]
            for record in self.institutions.values():
                for client in cast(list[dict[str, object]], record["clients"]):
                    if client["client_id"] == client_id:
                        client["revoked"] = True
                        return httpx.Response(204)
            return httpx.Response(404, json={"code": "not_found"})
        match = _CODE.match(path)
        if match is None or match.group(1) not in self.institutions:
            return httpx.Response(404, json={"code": "not_found", "detail": "No institution."})
        record = self.institutions[match.group(1)]
        rest = match.group(2) or ""
        if request.method == "GET" and not rest:
            return httpx.Response(200, json=record)
        if rest == "/clients":
            return self._issue(record, body)
        if rest == "/signing-keys":
            keys = cast(list[dict[str, object]], record["signing_keys"])
            keys.append(
                {"kid": body["kid"], "status": "active", "registered_at": "2026-11-22T10:00:00Z"}
            )
            return httpx.Response(201)
        if rest == "/status":
            record["status"] = body["status"]
            return httpx.Response(204)
        return httpx.Response(404, json={"code": "not_found"})

    def _enrol(self, body: dict[str, object]) -> httpx.Response:
        code = str(body["code"])
        if code in self.institutions:
            return httpx.Response(200, json=self.institutions[code])
        self.institutions[code] = {
            "code": code,
            "name": body["name"],
            "type": body["type"],
            "status": "enrolled",
            "enrolled_at": "2026-11-22T09:00:00Z",
            "clients": [],
            "signing_keys": [],
        }
        return httpx.Response(201, json=self.institutions[code])

    def _issue(self, record: dict[str, object], body: dict[str, object]) -> httpx.Response:
        csr = x509.load_pem_x509_csr(str(body["csr_pem"]).encode())
        key = csr.public_key()
        assert isinstance(key, ec.EllipticCurvePublicKey)
        jwk = cast(list[dict[str, str]], cast(dict[str, object], body["jwks"])["keys"])[0]
        numbers = key.public_numbers()
        if (jwk.get("x"), jwk.get("y")) != (_b64(numbers.x), _b64(numbers.y)):
            return httpx.Response(
                400,
                json={
                    "code": "invalid_request",
                    "detail": (
                        "The CSR public key must equal the JWKS key used for client assertions."
                    ),
                },
            )
        clients = cast(list[dict[str, object]], record["clients"])
        client_id = f"{record['code']}-client-{len(clients) + 1}"
        certificate = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Gateway CA")]))
            .public_key(key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(ISSUED - timedelta(days=1))
            .not_valid_after(ISSUED + timedelta(days=365))
            .sign(self.ca_key, hashes.SHA256())
        )
        clients.append(
            {
                "client_id": client_id,
                "environment": body["environment"],
                "certificate_not_after": "2027-11-21T12:00:00Z",
                "revoked": False,
            }
        )
        return httpx.Response(
            201,
            json={
                "client_id": client_id,
                "certificate_pem": _pem(certificate),
                "certificate_chain_pem": _pem(certificate),
                "not_after": "2027-11-21T12:00:00Z",
                "scopes": ["submissions:write", "submissions:read"],
            },
        )


@pytest.fixture
def ops(
    operator_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[OpsGateway]:
    fake = OpsGateway()
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
    get_operator_settings.cache_clear()
    get_settings.cache_clear()
    stub_public_dns(monkeypatch, "gateway.example.test")
    app = cast(FastAPI, operator_client.app)
    app.dependency_overrides[get_reporting_gateway_transport] = lambda: httpx.MockTransport(fake)
    yield fake
    get_settings.cache_clear()


def _csr(key: ec.EllipticCurvePrivateKey) -> str:
    request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "0301-client")]))
        .sign(key, hashes.SHA256())
    )
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    return request.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(OperatorAuditLog.action).order_by(OperatorAuditLog.created_at)))


def _link_bank(client: TestClient, db: Session, code: str) -> str:
    """An AequorOS tenant bank enrolled on the gateway under ``code``."""
    response = client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    body = cast(dict[str, object], response.json())
    bank_id = str(body["bank_id"])
    db.add(
        RegulatoryChannelConfig(
            organization_id=str(body["organization_id"]),
            bank_id=bank_id,
            channel="reporting_gateway",
            config={"institution_code": code, "enrolled_at": "2026-11-21T10:00:00+00:00"},
        )
    )
    db.commit()
    return bank_id


def test_list_shows_every_institution_and_which_are_aequoros_banks(
    operator_client: TestClient, operator_db: Session, ops: OpsGateway
) -> None:
    bank_id = _link_bank(operator_client, operator_db, "0142")
    ops._enrol({"code": "0142", "name": "Linked Bank", "type": "universal_bank"})  # pyright: ignore[reportPrivateUsage]
    ops._enrol({"code": "0301", "name": "Outside Bank", "type": "rural_bank"})  # pyright: ignore[reportPrivateUsage]

    response = operator_client.get(_ROOT, headers=operator_headers())

    assert response.status_code == 200, response.text
    items = cast(list[dict[str, object]], response.json()["items"])
    assert [item["code"] for item in items] == ["0142", "0301"]
    linked = cast(dict[str, object], items[0]["aequoros_bank"])
    assert linked["bank_id"] == bank_id
    assert items[1]["aequoros_bank"] is None
    assert _actions(operator_db)[-1] == "reporting_gateway.institutions_read"


def test_outside_institution_is_enrolled_issued_a_client_and_given_a_key(
    operator_client: TestClient, operator_db: Session, ops: OpsGateway
) -> None:
    enrolled = operator_client.post(
        _ROOT,
        json={
            "code": "0301",
            "name": "Outside Bank",
            "type": "rural_bank",
            "contacts": [{"role": "Compliance", "name": "Kofi Boateng", "email": "k@o.test"}],
        },
        headers=operator_headers(),
    )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["status"] == "enrolled"

    # The institution makes its own key pair; only public material reaches AequorOS.
    client_key = generate_p256_key()
    issued = operator_client.post(
        f"{_ROOT}/0301/clients",
        json={
            "environment": "sandbox",
            "csr_pem": _csr(client_key),
            "public_jwk": public_jwk(client_key.public_key(), "outside-client-1"),
        },
        headers=operator_headers(),
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["client_id"] == "0301-client-1"
    assert "BEGIN CERTIFICATE" in issued.json()["certificate_pem"]

    signing_key = generate_p256_key()
    keyed = operator_client.post(
        f"{_ROOT}/0301/signing-keys",
        json={
            "kid": "outside-sign-2026",
            "public_jwk": public_jwk(signing_key.public_key(), "outside-sign-2026"),
        },
        headers=operator_headers(),
    )
    assert keyed.status_code == 200, keyed.text
    assert [key["kid"] for key in keyed.json()["signing_keys"]] == ["outside-sign-2026"]

    live = operator_client.post(
        f"{_ROOT}/0301/status",
        json={"status": "suspended", "reason": "Testing the suspension path"},
        headers=operator_headers(),
    )
    assert live.json()["status"] == "suspended"
    revoked = operator_client.post(
        f"{_ROOT}/0301/clients/0301-client-1/revoke",
        json={"reason": "Key rotation"},
        headers=operator_headers(),
    )
    assert revoked.json()["clients"][0]["revoked"] is True
    assert _actions(operator_db) == [
        "reporting_gateway.institution_enrolled",
        "reporting_gateway.client_issued",
        "reporting_gateway.signing_key_registered",
        "reporting_gateway.institution_status_changed",
        "reporting_gateway.client_revoked",
    ]
    issued_row = operator_db.scalar(
        select(OperatorAuditLog).where(OperatorAuditLog.action == "reporting_gateway.client_issued")
    )
    assert issued_row is not None
    assert "BEGIN" not in json.dumps(issued_row.detail)


def test_the_gateways_reason_reaches_staff_and_the_refusal_is_audited(
    operator_client: TestClient, operator_db: Session, ops: OpsGateway
) -> None:
    operator_client.post(
        _ROOT,
        json={"code": "0301", "name": "Outside Bank", "type": "rural_bank"},
        headers=operator_headers(),
    )
    mismatched = operator_client.post(
        f"{_ROOT}/0301/clients",
        json={
            "environment": "sandbox",
            "csr_pem": _csr(generate_p256_key()),
            "public_jwk": public_jwk(generate_p256_key().public_key(), "other"),
        },
        headers=operator_headers(),
    )
    assert mismatched.status_code == 502
    assert "must equal the JWKS key" in mismatched.json()["error"]["message"]
    assert _actions(operator_db)[-1] == "reporting_gateway.client_issued_failed"


def test_an_aequoros_bank_is_managed_from_its_tenant_page(
    operator_client: TestClient, operator_db: Session, ops: OpsGateway
) -> None:
    _link_bank(operator_client, operator_db, "0142")
    refused = operator_client.post(
        _ROOT,
        json={"code": "0142", "name": "Someone Else", "type": "universal_bank"},
        headers=operator_headers(),
    )
    assert refused.status_code == 409
    assert "Enrol it from its tenant page" in refused.json()["error"]["message"]
    assert ops.institutions == {}
    assert _actions(operator_db)[-1] == "reporting_gateway.institution_enrolled_failed"
