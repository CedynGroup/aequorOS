"""Enrol a bank on the AequorOS Reporting Gateway — the channel operator's action.

The gateway is AequorOS' own reporting channel, so a bank never requests
credentials or installs certificates: the channel operator enrols it from the
staff console, through the gateway's operator API (``/ops/api``,
``gateway/contract/SPEC.md`` Part 5). Enrolment:

1. creates the bank's two keys if it has none yet: the API client key (mutual
   TLS and token requests) and the filing signature key (ES256). Both are
   P-256 keys stored only in the platform credential vault, encrypted and bound
   to this bank; they are committed *before* the gateway is called, so a retried
   enrolment reuses them and the gateway, which is idempotent on them, never
   issues a second client or key;
2. enrols the institution, has the gateway's certificate authority issue the
   client certificate from a CSR, and registers the signature key's public half;
3. records the connection on the bank's ``reporting_gateway`` channel and audits it in
   the bank's own audit trail; and
4. tests the connection as the bank: a token and the gateway's keys, nothing filed.

The signature key is used only by :class:`ReportingGatewayChannel` for a package the
bank's own preparer and approver have approved. The channel operator cannot
file, and nothing here signs anything.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.key_management.signing import (
    generate_p256_key,
    jwk_thumbprint,
    load_p256_private_key,
    private_key_pem,
    public_jwk,
)
from app.core.tenancy import TenantContext
from app.identity.public import Bank, orass_institution_code
from app.reporting.models.packages import RegulatoryChannelConfig
from app.reporting.schemas.reporting_gateway import (
    GatewayCertificateRenewalRead,
    GatewayEnrolmentRead,
    GatewayEnrolmentRequest,
    GatewayEnvironment,
)
from app.reporting.service.channel_config import (
    decrypt_channel_credentials,
    store_channel_credentials,
)
from app.reporting.service.channels.errors import ChannelError, ChannelPreconditionError
from app.reporting.service.channels.gateway_http import (
    GatewayErrors,
    GatewayIdentity,
    GatewaySession,
    gateway_base_url,
)
from app.reporting.service.channels.reporting_gateway import (
    CHANNEL_CODE,
    SOFTWARE_NAME,
    ReportingGatewayChannel,
)
from app.services.audit import record_event

logger = logging.getLogger(__name__)

_CODE = re.compile(r"^\d{4}$")

#: Where the bank's latest certificate renewal and the staff plane's scheduled
#: check are kept on its channel configuration (``gateway_renewal``).
RENEWAL_RECORD = "certificate_renewal"
RENEWAL_CHECK = "certificate_renewal_check"
#: The new client key a renewal holds in the vault until it switches the bank.
PENDING_CLIENT_KEY = "pending_client_private_key_pem"
#: Days before expiry a certificate is renewed, unless the staff plane says otherwise.
DEFAULT_RENEW_BEFORE_DAYS = 30

#: AequorOS licence classes → the gateway's institution types.
_INSTITUTION_TYPES: dict[str, str] = {
    "universal_bank": "universal_bank",
    "savings_and_loans": "savings_and_loans",
    "rural_community_bank": "rural_bank",
    "microfinance_bank": "microfinance",
}

_OPERATOR_ERRORS = GatewayErrors(
    by_code={
        "conflict": (
            "The gateway already holds this institution code with a different name or type, "
            "or this signature key name with a different key."
        ),
        "not_found": "The gateway has no institution with this code.",
        "invalid_request": "The gateway rejected the enrolment request as malformed.",
        "certificate_required": "The gateway did not receive the operator client certificate.",
        "certificate_mismatch": (
            "The operator client certificate does not match its gateway registration."
        ),
        "invalid_client_assertion": "The gateway refused the operator client's credentials.",
        "insufficient_scope": "The operator client is not allowed to enrol institutions.",
        "forbidden": "The gateway refused the operator client.",
        "rate_limited": "The gateway is busy; try again shortly.",
    },
    refused="The gateway refused the enrolment request.",
    unreachable="The reporting gateway could not be reached.",
    unavailable="The reporting gateway is unavailable.",
    no_token="The reporting gateway issued no access token to the operator client.",
)


class _OperatorDescriptor(BaseModel):
    model_config = ConfigDict(extra="ignore")
    client_id: str
    key_file: str
    certificate_file: str
    kid: str | None = None


def _unavailable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message)


def operator_identity(
    *, base_url: str | None, directory: str | None, forward_certificate: bool
) -> GatewayIdentity:
    """The channel operator's gateway API client, from its configured directory."""
    if not base_url or not directory:
        raise _unavailable(
            "Gateway enrolment is not configured: set REPORTING_GATEWAY_URL and "
            "REPORTING_GATEWAY_OPERATOR_DIR for the staff console."
        )
    root = Path(directory)
    try:
        descriptor = _OperatorDescriptor.model_validate_json(
            (root / "operator.json").read_text(encoding="utf-8")
        )
        key = (root / descriptor.key_file).read_text(encoding="utf-8")
        certificate = (root / descriptor.certificate_file).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise _unavailable("The gateway operator client could not be read.") from exc
    try:
        url = gateway_base_url(base_url)
    except ChannelPreconditionError as exc:
        raise _unavailable(exc.operator_message) from exc
    return GatewayIdentity(
        base_url=url,
        client_id=descriptor.client_id,
        certificate_pem=certificate,
        private_key_pem=key,
        forward_certificate=forward_certificate,
        key_id=descriptor.kid,
    )


_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def software_version() -> str:
    """The AequorOS version declared with each filing: the installed package's, else the
    project's own (a source checkout is not installed as a package)."""
    try:
        return metadata.version("risk-service")
    except metadata.PackageNotFoundError:
        pass
    try:
        project = cast(
            dict[str, object], tomllib.loads(_PYPROJECT.read_text("utf-8")).get("project", {})
        )
    except (OSError, tomllib.TOMLDecodeError):
        return "unversioned"
    version = project.get("version")
    return version if isinstance(version, str) and version else "unversioned"


def bank_in(db: Session, organization_id: str, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=404, detail="Bank not found in this organization.")
    return bank


def _channel_row(db: Session, bank: Bank) -> RegulatoryChannelConfig | None:
    return db.scalar(
        select(RegulatoryChannelConfig).where(
            RegulatoryChannelConfig.organization_id == bank.organization_id,
            RegulatoryChannelConfig.bank_id == bank.id,
            RegulatoryChannelConfig.channel == CHANNEL_CODE,
        )
    )


def _parsed_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value)


def certificate_not_after(config: Mapping[str, object]) -> datetime | None:
    """When the bank's gateway client certificate expires, if it has one."""
    pem = config.get("client_certificate_pem")
    if not isinstance(pem, str) or not pem:
        return None
    try:
        return x509.load_pem_x509_certificate(pem.encode()).not_valid_after_utc
    except ValueError:
        return None


def _renewal_record(config: Mapping[str, object]) -> GatewayCertificateRenewalRead | None:
    raw = config.get(RENEWAL_RECORD)
    if not isinstance(raw, dict):
        return None
    record = cast(dict[str, object], raw)
    fields = GatewayCertificateRenewalRead.model_fields
    try:
        return GatewayCertificateRenewalRead.model_validate(
            {key: value for key, value in record.items() if key in fields}
        )
    except ValidationError:
        logger.warning("gateway_certificate_renewal.unreadable_record")
        return None


def enrolment_read(
    bank: Bank,
    row: RegulatoryChannelConfig | None,
    message: str | None = None,
    *,
    renew_before_days: int = DEFAULT_RENEW_BEFORE_DAYS,
) -> GatewayEnrolmentRead:
    config: dict[str, Any] = dict(row.config) if row is not None else {}
    signing = cast(dict[str, Any], config.get("signing_key") or {})
    test = cast(dict[str, Any], config.get("last_connection_test") or {})
    not_after = certificate_not_after(config)
    raw_check = config.get(RENEWAL_CHECK)
    check = cast(dict[str, object], raw_check) if isinstance(raw_check, dict) else {}
    environment = config.get("environment")
    tested_ok = test.get("ok")
    return GatewayEnrolmentRead(
        bank_id=bank.id,
        enrolled=bool(config.get("enrolled_at")),
        institution_code=cast(str | None, config.get("institution_code")),
        environment=environment if environment in ("sandbox", "production") else None,
        client_id=cast(str | None, config.get("client_id")),
        certificate_not_after=not_after,
        signing_kid=cast(str | None, signing.get("kid")),
        enrolled_at=_parsed_time(config.get("enrolled_at")),
        last_connection_test_at=_parsed_time(test.get("at")),
        last_connection_test_ok=tested_ok if isinstance(tested_ok, bool) else None,
        message=message,
        renewal_due_from=(
            not_after - timedelta(days=renew_before_days) if not_after is not None else None
        ),
        last_renewal_check_at=_parsed_time(check.get("checked_at")),
        certificate_renewal=_renewal_record(config),
    )


def enrolment_status(
    db: Session,
    organization_id: str,
    bank_id: str,
    *,
    renew_before_days: int = DEFAULT_RENEW_BEFORE_DAYS,
) -> GatewayEnrolmentRead:
    bank = bank_in(db, organization_id, bank_id)
    return enrolment_read(bank, _channel_row(db, bank), renew_before_days=renew_before_days)


def _institution_code(db: Session, bank: Bank, requested: str | None, enrolled: object) -> str:
    code = requested or (
        enrolled
        if isinstance(enrolled, str)
        else orass_institution_code(
            db, TenantContext(organization_id=bank.organization_id), bank.id
        )
    )
    if not code or not _CODE.fullmatch(code):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Give the bank's four-digit institution code; its institution profile has none."
            ),
        )
    if isinstance(enrolled, str) and enrolled != code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This bank is already enrolled on the gateway as institution {enrolled}.",
        )
    return code


def _signing_kid(code: str, now: datetime) -> str:
    return f"{code}-sign-{now:%Y%m%d}"


def _prepare_keys(
    db: Session, row: RegulatoryChannelConfig, bank: Bank, code: str, now: datetime
) -> dict[str, Any]:
    """The bank's vaulted keys, created and committed now if it has none."""
    credentials = dict(decrypt_channel_credentials(row) or {}) if row.credential_ciphertext else {}
    config = dict(row.config)
    prepared = (
        credentials.get("client_private_key_pem")
        and credentials.get("signing_private_key_pem")
        and config.get("institution_code") == code
    )
    if prepared:
        return credentials
    credentials.setdefault("client_private_key_pem", private_key_pem(generate_p256_key()))
    credentials.setdefault("signing_private_key_pem", private_key_pem(generate_p256_key()))
    config["institution_code"] = code
    if not config.get("enrolled_at"):
        config["signing_key"] = {
            "provider": "vault",
            "kid": _signing_kid(code, now),
            "created_at": None,
        }
    row.config = config
    store_channel_credentials(row, bank.id, credentials)
    db.commit()
    return credentials


def client_csr(key: ec.EllipticCurvePrivateKey, code: str, bank: Bank) -> str:
    request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, code),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, bank.name),
                ]
            )
        )
        .sign(key, hashes.SHA256())
    )
    return request.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _register(  # noqa: PLR0913 - one enrolment exchange with the gateway
    session: GatewaySession,
    bank: Bank,
    code: str,
    request: GatewayEnrolmentRequest,
    environment: GatewayEnvironment,
    credentials: dict[str, Any],
    kid: str,
) -> tuple[str, str]:
    """Enrol, issue the client and register the signature key: (client id, certificate)."""
    token = session.access_token("ops:enrol")
    session.send(
        "POST",
        "/ops/api/institutions",
        token=token,
        json_body={
            "code": code,
            "name": bank.name,
            "type": _INSTITUTION_TYPES.get(bank.institution_type, "other"),
            "contacts": [contact.model_dump() for contact in request.contacts],
        },
    )
    path = f"/ops/api/institutions/{quote(code, safe='')}"
    client_key = load_p256_private_key(cast(str, credentials["client_private_key_pem"]))
    thumbprint = jwk_thumbprint(client_key.public_key())
    issued = session.send(
        "POST",
        f"{path}/clients",
        token=token,
        json_body={
            "environment": environment,
            "csr_pem": client_csr(client_key, code, bank),
            "jwks": {"keys": [public_jwk(client_key.public_key(), kid=thumbprint)]},
            "software": {"name": SOFTWARE_NAME, "version": software_version()},
        },
    )
    signing_key = load_p256_private_key(cast(str, credentials["signing_private_key_pem"]))
    session.send(
        "POST",
        f"{path}/signing-keys",
        token=token,
        json_body={"kid": kid, "public_jwk": public_jwk(signing_key.public_key(), kid=kid)},
    )
    client_id = issued.get("client_id")
    certificate_pem = issued.get("certificate_pem")
    if not isinstance(client_id, str) or not isinstance(certificate_pem, str):
        raise ChannelError(
            "The gateway did not return the issued client.",
            internal_detail=f"client response keys={sorted(issued)}",
        )
    return client_id, certificate_pem


def _connection_test(
    row: RegulatoryChannelConfig,
    credentials: dict[str, Any],
    *,
    transport: httpx.BaseTransport | None,
    clock: Callable[[], datetime],
) -> tuple[bool, str]:
    try:
        ReportingGatewayChannel(
            config=dict(row.config), credentials=credentials, transport=transport, clock=clock
        ).connection_test()
    except ChannelError as exc:
        return False, exc.operator_message
    return True, "Enrolled. The bank's connection to the gateway works."


def enrol_bank(  # noqa: PLR0913 - one operator enrolment, with injectable transport and clock
    db: Session,
    organization_id: str,
    bank_id: str,
    request: GatewayEnrolmentRequest,
    *,
    operator: GatewayIdentity,
    environment: GatewayEnvironment,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GatewayEnrolmentRead:
    """Enrol ``bank_id`` on the gateway (idempotent). The caller audits and commits."""
    now_fn = clock or (lambda: datetime.now(UTC))
    bank = bank_in(db, organization_id, bank_id)
    row = _channel_row(db, bank)
    if row is None:
        row = RegulatoryChannelConfig(
            organization_id=bank.organization_id, bank_id=bank.id, channel=CHANNEL_CODE, config={}
        )
        db.add(row)
    enrolled_code = row.config.get("institution_code") if row.config.get("enrolled_at") else None
    code = _institution_code(db, bank, request.institution_code, enrolled_code)
    credentials = _prepare_keys(db, row, bank, code, now_fn())
    signing = cast(dict[str, object], dict(row.config)["signing_key"])
    kid = str(signing.get("kid"))
    try:
        with GatewaySession(
            operator, _OPERATOR_ERRORS, transport=transport, clock=now_fn
        ) as session:
            client_id, certificate_pem = _register(
                session, bank, code, request, environment, credentials, kid
            )
    except ChannelError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.operator_message
        ) from exc
    now = now_fn()
    config = dict(row.config)
    config.update(
        api_base_url=operator.base_url,
        institution_code=code,
        environment=environment,
        client_id=client_id,
        client_certificate_pem=certificate_pem,
        signing_key={
            "provider": "vault",
            "kid": kid,
            "jwk_thumbprint": jwk_thumbprint(
                load_p256_private_key(
                    cast(str, credentials["signing_private_key_pem"])
                ).public_key()
            ),
            "created_at": signing.get("created_at") or now.isoformat(),
        },
        enrolled_at=config.get("enrolled_at") or now.isoformat(),
        software_version=software_version(),
    )
    if operator.forward_certificate:
        config["forward_client_certificate"] = True
    row.config = config
    ok, message = _connection_test(row, credentials, transport=transport, clock=now_fn)
    del credentials
    config = dict(row.config)
    config["last_connection_test"] = {"at": now_fn().isoformat(), "ok": ok}
    row.config = config
    db.flush()
    record_event(
        db,
        TenantContext(organization_id=bank.organization_id),
        event_type="regulatory_channel_config.updated",
        entity_type="regulatory_channel_config",
        entity_id=row.id,
        details={
            "bank_id": bank.id,
            "channel": CHANNEL_CODE,
            "action": "gateway_enrolled",
            "performed_by": "AequorOS channel operator",
            "institution_code": code,
            "environment": environment,
            "client_id": client_id,
            "signing_kid": kid,
            "connection_test_ok": ok,
        },
        bank_id=bank.id,
    )
    return enrolment_read(bank, row, message)
