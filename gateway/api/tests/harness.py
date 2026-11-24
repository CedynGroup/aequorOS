# pyright: standard
"""Test harness: an app on a migrated database, synthetic clients and token helpers."""

from __future__ import annotations

import json
import os
import shutil
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.audit.chain import Actor
from app.core.clock import SettableClock
from app.core.context import AppContext, app_context
from app.core.settings import Settings
from app.db.migrate import upgrade
from app.db.session import Database, make_engine
from app.enrolment.service import (
    EnrolInstitutionRequest,
    IssueClientRequest,
    JwksIn,
    RegisterKeyRequest,
    StatusRequest,
    enrol_institution,
    issue_client,
    register_signing_key,
    set_status,
)
from app.idp.tokens import mint_id_token
from app.main import create_app
from app.seed.client import InstitutionKeys, build_filing, csr_pem, declaration, line_payload
from app.seed.synthetic_staff import DevStaff, find_dev_staff
from tests.synthetic_registry import bootstrap_registry

API_ROOT = Path(__file__).resolve().parents[1]
OPERATOR = Actor("operator", "test-operator", "Channel operator", "Operator client test-operator")
START = datetime(2026, 11, 21, 10, 0, 0, tzinfo=UTC)


def migrate(url: str, schema: str | None = None, app_role: str | None = None) -> None:
    upgrade(url, schema=schema, app_role=app_role)


def cert_header(pem: str) -> dict[str, str]:
    return {"X-Forwarded-Tls-Client-Cert": urllib.parse.quote(pem)}


@dataclass
class Bank:
    code: str
    name: str
    keys: InstitutionKeys
    client_id: str
    certificate_pem: str


@dataclass
class Operator:
    client_id: str
    key: ec.EllipticCurvePrivateKey
    certificate_pem: str
    kid: str


class Harness:
    def __init__(
        self,
        app: Any,
        ctx: AppContext,
        clock: SettableClock,
        engine: Engine,
        client_host: str | None = None,
    ) -> None:
        self.app = app
        self.ctx = ctx
        self.clock = clock
        self.engine = engine
        self.client = (
            TestClient(app, client=(client_host, 50000)) if client_host else TestClient(app)
        )

    # --- staff ---------------------------------------------------------------------
    def staff_token(
        self, subject: str | None = None, *, custom: DevStaff | None = None, **claims: Any
    ) -> str:
        staff = custom or find_dev_staff(subject or "kowusu")
        assert staff is not None
        return mint_id_token(self.ctx, staff, groups=staff.groups, mfa=staff.mfa)

    def staff(self, subject: str = "kowusu", *, custom: DevStaff | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.staff_token(subject, custom=custom)}"}

    # --- assertions and tokens --------------------------------------------------------
    def assertion(
        self,
        client_id: str,
        key: ec.EllipticCurvePrivateKey,
        kid: str | None,
        *,
        aud: str | None = None,
        lifetime: int = 120,
        iat_offset: int = 0,
        jti: str | None = None,
        iss: str | None = None,
    ) -> str:
        now = int(self.clock.now().timestamp()) + iat_offset
        claims = {
            "iss": iss or client_id,
            "sub": client_id,
            "aud": aud or self.ctx.settings.token_endpoint,
            "iat": now,
            "exp": now + lifetime,
            "jti": jti or uuid.uuid4().hex,
        }
        headers = {"kid": kid} if kid else {}
        return jwt.encode(claims, key, algorithm="ES256", headers=headers)

    def token_response(
        self,
        client_id: str,
        key: ec.EllipticCurvePrivateKey,
        kid: str | None,
        cert_pem: str,
        scope: str | None = None,
        **kwargs: Any,
    ) -> Any:
        form = {
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": self.assertion(client_id, key, kid, **kwargs),
        }
        if scope is not None:
            form["scope"] = scope
        return self.client.post("/oauth/token", data=form, headers=cert_header(cert_pem))

    def bank_token(self, bank: Bank, scope: str | None = None) -> str:
        response = self.token_response(
            bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem, scope
        )
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    def bank_headers(self, bank: Bank, scope: str | None = None) -> dict[str, str]:
        return {
            **cert_header(bank.certificate_pem),
            "Authorization": f"Bearer {self.bank_token(bank, scope)}",
        }

    # --- operator -----------------------------------------------------------------------
    def operator(self) -> Operator:
        directory = self.ctx.settings.keys_dir / "operator"
        descriptor = json.loads((directory / "operator.json").read_text())
        key = serialization.load_pem_private_key((directory / "client.key.pem").read_bytes(), None)
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        return Operator(
            descriptor["client_id"],
            key,
            (directory / "client.crt.pem").read_text(),
            descriptor["kid"],
        )

    def operator_headers(self) -> dict[str, str]:
        op = self.operator()
        response = self.token_response(op.client_id, op.key, op.kid, op.certificate_pem)
        assert response.status_code == 200, response.text
        return {
            **cert_header(op.certificate_pem),
            "Authorization": f"Bearer {response.json()['access_token']}",
        }

    # --- enrolment through the service layer --------------------------------------------
    def enrol(
        self,
        code: str = "0142",
        name: str = "Meridian Coast Bank",
        type_: str = "universal_bank",
        *,
        live: bool = True,
        environment: str = "production",
        supervisor: str | None = None,
    ) -> Bank:
        keys = InstitutionKeys(signing_kid=f"{code}-sign-1")
        with self.ctx.db.session() as session:
            enrol_institution(
                session,
                self.ctx,
                OPERATOR,
                EnrolInstitutionRequest.model_validate(
                    {"code": code, "name": name, "type": type_, "contacts": []}
                ),
            )
            _, issued = issue_client(
                session,
                self.ctx,
                OPERATOR,
                code,
                IssueClientRequest.model_validate(
                    {
                        "environment": environment,
                        "csr_pem": csr_pem(keys.client_key, code),
                        "jwks": JwksIn.model_validate(keys.client_jwks()),
                        "software": None,
                    }
                ),
            )
            register_signing_key(
                session,
                self.ctx,
                OPERATOR,
                code,
                RegisterKeyRequest(kid=keys.signing_kid, public_jwk=keys.signing_jwk()),
            )
            if live:
                set_status(
                    session,
                    self.ctx,
                    OPERATOR,
                    code,
                    StatusRequest(status="live", reason="test"),
                    at=self.clock.now() - timedelta(days=400),
                )
            if supervisor:
                from sqlalchemy import select

                from app.db.models import Institution

                row = session.scalars(select(Institution).where(Institution.code == code)).one()
                row.supervisor_subject = supervisor
        return Bank(code, name, keys, issued.client_id, issued.certificate_pem)

    def bootstrap(
        self, codes: tuple[str, ...] | None = ("BSD2", "BSD5A", "BSD6", "LMT", "DBK-DAILY")
    ) -> None:
        with self.ctx.db.session() as session:
            bootstrap_registry(session, self.ctx, self.clock.now() - timedelta(days=500), codes)

    # --- filings --------------------------------------------------------------------------
    def filing(
        self,
        bank: Bank,
        lines: dict[str, int],
        *,
        return_code: str = "BSD2",
        reporting_date: date = date(2026, 10, 31),
        version: str = "2026.1",
        supersedes: str | None = None,
        reason: str | None = None,
        basis: str = "solo",
        kid: str | None = None,
        signing_key: ec.EllipticCurvePrivateKey | None = None,
        institution_code: str | None = None,
        documents: list[tuple[str, str, bytes]] | None = None,
    ) -> dict[str, Any]:
        return build_filing(
            signing_key=signing_key or bank.keys.signing_key,
            kid=kid or bank.keys.signing_kid,
            institution_code=institution_code or bank.code,
            return_code=return_code,
            return_version=version,
            reporting_date=reporting_date,
            payload=line_payload(lines),
            declaration_body=declaration(approved_at=self.clock.now(), amendment_reason=reason),
            created_at=self.clock.now(),
            basis=basis,
            supersedes_receipt_id=supersedes,
            documents=documents,
        )

    def post_filing(
        self,
        bank: Bank,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        key: str | None = None,
        amend: str | None = None,
    ) -> Any:
        path = f"/v1/submissions/{amend}/amendments" if amend else "/v1/submissions"
        hdrs = dict(headers or self.bank_headers(bank))
        hdrs["Idempotency-Key"] = key or str(uuid.uuid4())
        return self.client.post(path, json=body, headers=hdrs)


BSD2_LINES = {
    "1.1": 412350,
    "1.2": 286400,
    "1.3": 1184900,
    "1.4": 2031770,
    "1.5": 143210,
    "1": 4058630,
    "2.1": 3112480,
    "2.2": 214900,
    "2.3": 186050,
    "2.4": 545200,
    "2": 4058630,
}


def make_harness(
    tmp: Path,
    keys_dir: Path,
    template: Path | None,
    *,
    env: str = "test",
    database_url: str | None = None,
    schema: str | None = None,
    keys: Any = None,
    trusted_proxies: str = "",
    client_host: str | None = None,
    ca: Any = None,
    oidc_issuer: str = "",
) -> Harness:
    tmp.mkdir(parents=True, exist_ok=True)
    if database_url is None:
        db_file = tmp / "gateway.db"
        if template is not None:
            shutil.copy(template, db_file)
        url = f"sqlite+pysqlite:///{db_file}"
        engine = make_engine(url)
        if template is None:
            migrate(url)
    else:
        url = database_url
        engine = make_engine(url, schema=schema)
    tmp.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        env=cast(Any, env),
        database_url=url,
        keys_dir=keys_dir,
        base_url="http://testserver",
        scheduler_enabled=False,
        trusted_proxies=trusted_proxies,
        oidc_issuer=oidc_issuer,
    )
    clock = SettableClock(START)
    if ca is None and env == "production":
        from app.enrolment.ca import LocalCertificateAuthority

        ca = LocalCertificateAuthority(keys_dir / "ca", clock)
    app = create_app(
        settings, clock=clock, database=Database(engine), run_scheduler=False, keys=keys, ca=ca
    )
    return Harness(app, app_context(app), clock, engine, client_host)


def pg_url() -> str | None:
    return os.environ.get("GATEWAY_TEST_DATABASE_URL") or None


def truncate_all(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        tables = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename <> 'alembic_version'"
                )
            )
        ]
        if tables:
            connection.execute(text("TRUNCATE " + ", ".join(tables)))


def load_cert(pem: str) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pem.encode())
