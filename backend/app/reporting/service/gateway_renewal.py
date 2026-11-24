"""Renew a bank's client certificate on the AequorOS Reporting Gateway.

The client certificate that identifies a bank's AequorOS connection on the gateway
is valid for a year (the gateway's ``client_certificate_days``). Renewal replaces
the key as well as the certificate, and never leaves the bank without a working
connection:

1. a new P-256 client key is created in the platform vault and committed *before*
   the gateway is called, so a retried renewal reuses it: the gateway matches a
   repeated key to the client it already issued and never issues a second;
2. the gateway's certificate authority issues a new client for that key, from a
   CSR. It has its own client id; the bank's current client keeps working;
3. the new client is tested *as the bank* (a token and the gateway's keys, nothing
   filed). A failed test stops the renewal there: the bank keeps filing with its
   current certificate, and the renewal can be retried or cancelled;
4. AequorOS switches to the new client in one transaction (client id, certificate
   and key together) and discards the old key; and
5. the old client is revoked on the gateway. If the gateway cannot be reached for
   this last step the switch stands, and the revocation is retried.

Each step is kept on the bank's channel configuration (``certificate_renewal``)
and recorded in the bank's own audit trail; the gateway records issuance and
revocation in its own audit log. Staff renew from the console; the staff plane's
scheduled check (:func:`run_scheduled_check`) renews certificates that are within
the renewal window of expiry and resumes unfinished renewals. Nothing here signs
or files anything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import quote

import httpx
from fastapi import HTTPException, status
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
from app.identity.public import Bank
from app.reporting.models.packages import RegulatoryChannelConfig
from app.reporting.schemas.reporting_gateway import GatewayEnrolmentRead, GatewayRenewalTrigger
from app.reporting.service.channel_config import (
    decrypt_channel_credentials,
    store_channel_credentials,
)
from app.reporting.service.channels.errors import ChannelError
from app.reporting.service.channels.gateway_http import (
    GatewayErrors,
    GatewayIdentity,
    GatewayRefusal,
    GatewaySession,
)
from app.reporting.service.channels.reporting_gateway import (
    CHANNEL_CODE,
    SOFTWARE_NAME,
    ReportingGatewayChannel,
)
from app.reporting.service.gateway_enrolment import (
    DEFAULT_RENEW_BEFORE_DAYS,
    PENDING_CLIENT_KEY,
    RENEWAL_CHECK,
    RENEWAL_RECORD,
    bank_in,
    certificate_not_after,
    client_csr,
    enrolment_read,
    software_version,
)
from app.services.audit import record_event

logger = logging.getLogger(__name__)

#: How long one renewal holds a bank before another runner may resume it. Longer
#: than any renewal takes (a few gateway calls), short enough that a crashed one is
#: picked up by the next scheduled check.
RENEWAL_LEASE = timedelta(minutes=10)
#: Who the scheduled check records as having asked.
SCHEDULED_BY = "AequorOS scheduled renewal"
_OPERATOR_SCOPE = "ops:enrol"
#: A renewal in one of these states is unfinished: the scheduled check resumes it.
_UNFINISHED = frozenset({"in_progress", "failed", "revocation_pending"})

#: Staff-facing wording per gateway error code (the gateway's own detail is added).
_ERRORS = GatewayErrors(
    by_code={
        "not_found": "The gateway has no record of this institution or client.",
        "conflict": "The gateway refused the new client: it conflicts with one it already holds.",
        "invalid_request": "The gateway rejected the renewal request as malformed.",
        "certificate_required": "The gateway did not receive the operator client certificate.",
        "certificate_mismatch": (
            "The operator client certificate does not match its gateway registration."
        ),
        "invalid_client_assertion": "The gateway refused the operator client's credentials.",
        "insufficient_scope": "The operator client is not allowed to manage institution clients.",
        "forbidden": "The gateway refused the operator client.",
        "rate_limited": "The gateway is busy; try again shortly.",
    },
    refused="The gateway refused the renewal request.",
    unreachable="The reporting gateway could not be reached.",
    unavailable="The reporting gateway is unavailable.",
    no_token="The reporting gateway issued no access token to the operator client.",
    show_detail=True,
)


@dataclass(frozen=True)
class RenewalRequest:
    """Who asked for a renewal and why; recorded with every step of it."""

    trigger: GatewayRenewalTrigger
    requested_by: str
    reason: str


@dataclass
class RenewalCheckResult:
    """What one scheduled check did, bank by bank."""

    checked: list[str] = field(default_factory=lambda: [])
    renewed: list[str] = field(default_factory=lambda: [])
    failed: dict[str, str] = field(default_factory=lambda: {})
    skipped: dict[str, str] = field(default_factory=lambda: {})
    #: Each bank's organization, for the staff log's tenant column.
    organizations: dict[str, str] = field(default_factory=lambda: {})
    #: Each renewed bank's client switch: ``previous_client_id`` and ``new_client_id``.
    switches: dict[str, dict[str, str | None]] = field(default_factory=lambda: {})


class _RenewalFailed(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _record(config: Mapping[str, object]) -> dict[str, object] | None:
    raw = config.get(RENEWAL_RECORD)
    return dict(cast(dict[str, object], raw)) if isinstance(raw, dict) else None


def _credentials(row: RegulatoryChannelConfig) -> dict[str, object]:
    if not row.credential_ciphertext:
        return {}
    return dict(decrypt_channel_credentials(row) or {})


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _locked_row(db: Session, bank: Bank) -> RegulatoryChannelConfig | None:
    return db.scalar(
        select(RegulatoryChannelConfig)
        .where(
            RegulatoryChannelConfig.organization_id == bank.organization_id,
            RegulatoryChannelConfig.bank_id == bank.id,
            RegulatoryChannelConfig.channel == CHANNEL_CODE,
        )
        .with_for_update()
    )


def _lease_live(record: Mapping[str, object], now: datetime) -> bool:
    lease = _time(record.get("lease_until"))
    return record.get("status") == "in_progress" and lease is not None and lease > now


def renewal_due(config: Mapping[str, object], now: datetime, *, renew_before_days: int) -> bool:
    """Whether the scheduled check should act on this bank now: an unfinished
    renewal no one is running, or a certificate inside the renewal window."""
    if not config.get("enrolled_at"):
        return False
    record = _record(config)
    if record is not None and record.get("status") in _UNFINISHED:
        return not _lease_live(record, now)
    not_after = certificate_not_after(config)
    return not_after is not None and not_after - timedelta(days=renew_before_days) <= now


def _audit(
    db: Session, bank: Bank, row: RegulatoryChannelConfig, action: str, details: dict[str, object]
) -> None:
    db.flush()
    record_event(
        db,
        TenantContext(organization_id=bank.organization_id),
        event_type="regulatory_channel_config.updated",
        entity_type="regulatory_channel_config",
        entity_id=row.id,
        details={"bank_id": bank.id, "channel": CHANNEL_CODE, "action": action, **details},
        bank_id=bank.id,
    )


def _save(db: Session, row: RegulatoryChannelConfig, record: dict[str, object]) -> None:
    config: dict[str, object] = dict(row.config)
    config[RENEWAL_RECORD] = record
    row.config = config
    db.commit()


# -- claiming a bank ------------------------------------------------------------------


def _claim(
    db: Session, bank: Bank, request: RenewalRequest, operator: GatewayIdentity, now: datetime
) -> RegulatoryChannelConfig:
    """Take the bank for this renewal (a lease), with its new key committed."""
    row = _locked_row(db, bank)
    config: dict[str, object] = dict(row.config) if row is not None else {}
    current = _text(config.get("client_id"))
    not_after = certificate_not_after(config)
    if row is None or not config.get("enrolled_at") or current is None or not_after is None:
        raise _conflict(
            "This bank is not enrolled on the AequorOS Reporting Gateway; enrol it before "
            "renewing its certificate."
        )
    enrolled_on = _text(config.get("api_base_url"))
    if enrolled_on != operator.base_url:
        raise _conflict(
            f"This bank is enrolled on the gateway at {enrolled_on or 'an unrecorded address'}, "
            f"not {operator.base_url}. Renew it from the staff plane connected to that gateway."
        )
    record = _record(config)
    if record is not None and _lease_live(record, now):
        raise _conflict(
            "A renewal of this bank's certificate is already running. Try again in a few minutes."
        )
    credentials = _credentials(row)
    resumable = (
        record is not None
        and record.get("status") in _UNFINISHED
        and (record.get("stage") == "switched" or _text(credentials.get(PENDING_CLIENT_KEY)))
    )
    claimed: dict[str, object]
    if record is not None and resumable:
        claimed = {**record, "status": "in_progress", "resumed_by": request.requested_by}
    else:
        credentials[PENDING_CLIENT_KEY] = private_key_pem(generate_p256_key())
        store_channel_credentials(row, bank.id, credentials)
        claimed = {
            "status": "in_progress",
            "stage": "key_prepared",
            "trigger": request.trigger,
            "requested_by": request.requested_by,
            "reason": request.reason,
            "started_at": _iso(now),
            "failures": 0,
            "previous_client_id": current,
            "previous_not_after": _iso(not_after),
        }
    claimed.update(updated_at=_iso(now), lease_until=_iso(now + RENEWAL_LEASE), error=None)
    _save(db, row, claimed)
    return row


# -- the steps ------------------------------------------------------------------------


def _issue(
    session: GatewaySession,
    token: str,
    bank: Bank,
    config: Mapping[str, object],
    credentials: Mapping[str, object],
) -> tuple[str, str]:
    """Have the gateway issue the new client (idempotent on the new key)."""
    code = str(config.get("institution_code") or "")
    key = load_p256_private_key(str(credentials[PENDING_CLIENT_KEY]))
    issued: dict[str, object] = session.send(
        "POST",
        f"/ops/api/institutions/{quote(code, safe='')}/clients",
        token=token,
        json_body={
            "environment": config.get("environment"),
            "csr_pem": client_csr(key, code, bank),
            "jwks": {"keys": [public_jwk(key.public_key(), kid=jwk_thumbprint(key.public_key()))]},
            "software": {"name": SOFTWARE_NAME, "version": software_version()},
        },
    )
    client_id = issued.get("client_id")
    certificate_pem = issued.get("certificate_pem")
    if not isinstance(client_id, str) or not isinstance(certificate_pem, str):
        raise ChannelError(
            "The gateway did not return the issued client.",
            internal_detail=f"client response keys={sorted(issued)}",
        )
    if client_id == config.get("client_id"):
        raise ChannelError("The gateway returned the bank's current client instead of a new one.")
    return client_id, certificate_pem


def _test_as_bank(  # noqa: PLR0913 - the candidate identity and the injectable transport
    config: Mapping[str, object],
    credentials: Mapping[str, object],
    client_id: str,
    certificate_pem: str,
    transport: httpx.BaseTransport | None,
    clock: Callable[[], datetime],
) -> None:
    candidate: dict[str, object] = {
        **config,
        "client_id": client_id,
        "client_certificate_pem": certificate_pem,
    }
    keys: dict[str, object] = {
        **credentials,
        "client_private_key_pem": credentials[PENDING_CLIENT_KEY],
    }
    try:
        ReportingGatewayChannel(
            config=candidate, credentials=keys, transport=transport, clock=clock
        ).connection_test()
    except ChannelError as exc:
        raise _RenewalFailed(
            f"The gateway issued the new client {client_id}, but the bank's connection test "
            f"with it failed: {exc.operator_message} The bank keeps filing with its current "
            "certificate."
        ) from exc


def _switch(  # noqa: PLR0913 - the bank, its row, the request, its keys and the time
    db: Session,
    bank: Bank,
    row: RegulatoryChannelConfig,
    request: RenewalRequest,
    credentials: dict[str, object],
    now: datetime,
) -> None:
    """Move the bank to the new client: client id, certificate and key together."""
    config: dict[str, object] = dict(row.config)
    record = _record(config) or {}
    new_client = str(record["new_client_id"])
    certificate_pem = str(record.pop("pending_certificate_pem"))
    previous_client = _text(config.get("client_id"))
    previous_not_after = certificate_not_after(config)
    credentials["client_private_key_pem"] = credentials.pop(PENDING_CLIENT_KEY)
    store_channel_credentials(row, bank.id, credentials)
    config.update(
        client_id=new_client,
        client_certificate_pem=certificate_pem,
        software_version=software_version(),
        certificate_renewed_at=_iso(now),
        last_connection_test={"at": _iso(now), "ok": True},
    )
    record.update(stage="switched", switched_at=_iso(now), updated_at=_iso(now))
    config[RENEWAL_RECORD] = record
    row.config = config
    not_after = certificate_not_after(config)
    _audit(
        db,
        bank,
        row,
        "gateway_certificate_renewed",
        {
            "trigger": request.trigger,
            "requested_by": request.requested_by,
            "reason": request.reason,
            "previous_client_id": previous_client,
            "previous_not_after": _iso(previous_not_after) if previous_not_after else None,
            "client_id": new_client,
            "not_after": _iso(not_after) if not_after else None,
        },
    )
    db.commit()


def _revoke_previous(  # noqa: PLR0913 - the open gateway session and the bank's row
    session: GatewaySession,
    token: str,
    db: Session,
    bank: Bank,
    row: RegulatoryChannelConfig,
    now: datetime,
) -> None:
    """Revoke the client the bank no longer uses; a client the gateway no longer
    holds has nothing left to revoke."""
    record = _record(row.config) or {}
    previous = str(record.get("previous_client_id") or "")
    replacement = str(record.get("new_client_id") or "")
    try:
        session.send(
            "POST",
            f"/ops/api/clients/{quote(previous, safe='')}/revoke",
            token=token,
            json_body={"reason": f"Replaced by {replacement} (certificate renewal)."},
        )
    except GatewayRefusal as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND or exc.code != "not_found":
            raise
    record.update(
        status="complete",
        stage="previous_revoked",
        previous_revoked_at=_iso(now),
        completed_at=_iso(now),
        updated_at=_iso(now),
        lease_until=None,
        error=None,
        failures=0,
    )
    config: dict[str, object] = dict(row.config)
    config[RENEWAL_RECORD] = record
    row.config = config
    _audit(
        db,
        bank,
        row,
        "gateway_previous_certificate_revoked",
        {"client_id": previous, "replaced_by": replacement},
    )
    db.commit()


def _advance(  # noqa: PLR0913 - one renewal, with injectable transport and clock
    db: Session,
    bank: Bank,
    row: RegulatoryChannelConfig,
    request: RenewalRequest,
    *,
    operator: GatewayIdentity,
    transport: httpx.BaseTransport | None,
    clock: Callable[[], datetime],
) -> str:
    """Carry a claimed renewal as far as it goes; returns the outcome in words."""
    record = _record(row.config) or {}
    switched_now = False
    try:
        with GatewaySession(operator, _ERRORS, transport=transport, clock=clock) as session:
            token = session.access_token(_OPERATOR_SCOPE)
            if record.get("stage") in ("key_prepared", "client_issued"):
                credentials = _credentials(row)
                client_id, certificate_pem = _issue(session, token, bank, row.config, credentials)
                record.update(
                    stage="client_issued",
                    new_client_id=client_id,
                    pending_certificate_pem=certificate_pem,
                    updated_at=_iso(clock()),
                )
                _save(db, row, record)
                _test_as_bank(row.config, credentials, client_id, certificate_pem, transport, clock)
                _switch(db, bank, row, request, credentials, clock())
                switched_now = True
            try:
                _revoke_previous(session, token, db, bank, row, clock())
            except ChannelError as exc:
                if not switched_now:
                    raise
                # The bank already files with its new certificate: that stands. The
                # old client's revocation is recorded as outstanding and retried.
                _fail(db, bank, row, exc.operator_message, clock())
                return (
                    f"Renewed: the bank now uses {record.get('new_client_id')}. The previous "
                    f"client could not be revoked on the gateway yet ({exc.operator_message}); "
                    "the scheduled check retries it."
                )
    except ChannelError as exc:
        raise _RenewalFailed(exc.operator_message) from exc
    completed = _record(row.config) or {}
    return (
        f"Renewed: the bank now uses {completed.get('new_client_id')}, and "
        f"{completed.get('previous_client_id')} is revoked on the gateway."
    )


def _fail(
    db: Session, bank: Bank, row: RegulatoryChannelConfig, message: str, now: datetime
) -> None:
    """Record where the renewal stopped. A switched renewal keeps its new certificate
    and is only waiting to revoke the old one."""
    db.rollback()
    record = _record(row.config) or {}
    failures = record.get("failures")
    switched = record.get("stage") == "switched"
    record.update(
        status="revocation_pending" if switched else "failed",
        error=message,
        failures=(failures if isinstance(failures, int) else 0) + 1,
        updated_at=_iso(now),
        lease_until=None,
    )
    config: dict[str, object] = dict(row.config)
    config[RENEWAL_RECORD] = record
    row.config = config
    _audit(
        db,
        bank,
        row,
        "gateway_certificate_renewal_failed",
        {"stage": record.get("stage"), "error": message, "trigger": record.get("trigger")},
    )
    db.commit()


# -- entry points -----------------------------------------------------------------------


def renew_certificate(  # noqa: PLR0913 - one renewal, with injectable transport and clock
    db: Session,
    organization_id: str,
    bank_id: str,
    request: RenewalRequest,
    *,
    operator: GatewayIdentity,
    renew_before_days: int = DEFAULT_RENEW_BEFORE_DAYS,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GatewayEnrolmentRead:
    """Renew ``bank_id``'s gateway client certificate, or resume an unfinished renewal.

    Commits as it goes. A failure before the switch leaves the bank on its current
    certificate (502, recorded as ``failed``); a failure to revoke the old client
    after the switch is recorded as ``revocation_pending`` and retried.
    """
    now_fn = clock or (lambda: datetime.now(UTC))
    bank = bank_in(db, organization_id, bank_id)
    row = _claim(db, bank, request, operator, now_fn())
    try:
        message = _advance(
            db, bank, row, request, operator=operator, transport=transport, clock=now_fn
        )
    except _RenewalFailed as failure:
        _fail(db, bank, row, failure.message, now_fn())
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=failure.message
        ) from failure
    return enrolment_read(bank, row, message, renew_before_days=renew_before_days)


def cancel_renewal(  # noqa: PLR0913 - one cancellation, with injectable transport and clock
    db: Session,
    organization_id: str,
    bank_id: str,
    request: RenewalRequest,
    *,
    operator: GatewayIdentity,
    renew_before_days: int = DEFAULT_RENEW_BEFORE_DAYS,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GatewayEnrolmentRead:
    """Abandon an unfinished renewal that has not switched the bank yet.

    A new client the gateway already issued is revoked; the new key is discarded.
    The bank keeps its current certificate throughout.
    """
    now = (clock or (lambda: datetime.now(UTC)))()
    bank = bank_in(db, organization_id, bank_id)
    row = _locked_row(db, bank)
    record = _record(row.config) if row is not None else None
    if row is None or record is None or record.get("status") not in ("in_progress", "failed"):
        raise _conflict("There is no unfinished renewal of this bank's certificate to cancel.")
    if _lease_live(record, now):
        raise _conflict("This renewal is running now. Try again in a few minutes.")
    if record.get("stage") == "switched":
        raise _conflict(
            "The bank already files with its new certificate; only the old client's "
            "revocation is outstanding, and that cannot be cancelled."
        )
    issued = _text(record.get("new_client_id")) if record.get("stage") == "client_issued" else None
    if issued is not None:
        try:
            with GatewaySession(operator, _ERRORS, transport=transport, clock=clock) as session:
                session.send(
                    "POST",
                    f"/ops/api/clients/{quote(issued, safe='')}/revoke",
                    token=session.access_token(_OPERATOR_SCOPE),
                    json_body={"reason": f"Certificate renewal cancelled: {request.reason}"},
                )
        except GatewayRefusal as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND or exc.code != "not_found":
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.operator_message
                ) from exc
        except ChannelError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.operator_message
            ) from exc
    credentials = _credentials(row)
    if credentials.pop(PENDING_CLIENT_KEY, None) is not None:
        store_channel_credentials(row, bank.id, credentials)
    record.pop("pending_certificate_pem", None)
    record.update(
        status="cancelled",
        cancelled_by=request.requested_by,
        cancel_reason=request.reason,
        completed_at=_iso(now),
        updated_at=_iso(now),
        lease_until=None,
        error=None,
    )
    config: dict[str, object] = dict(row.config)
    config[RENEWAL_RECORD] = record
    row.config = config
    _audit(
        db,
        bank,
        row,
        "gateway_certificate_renewal_cancelled",
        {
            "requested_by": request.requested_by,
            "reason": request.reason,
            "revoked_client_id": issued,
        },
    )
    db.commit()
    return enrolment_read(
        bank,
        row,
        "Renewal cancelled. The bank keeps its current certificate.",
        renew_before_days=renew_before_days,
    )


def _stamp_check(
    db: Session, row: RegulatoryChannelConfig, now: datetime, renew_before_days: int
) -> None:
    """Record that the scheduled check looked at this bank: the bank's settings page
    states automatic renewal only while this stays fresh."""
    config: dict[str, object] = dict(row.config)
    not_after = certificate_not_after(config)
    config[RENEWAL_CHECK] = {
        "checked_at": _iso(now),
        "renew_from": _iso(not_after - timedelta(days=renew_before_days)) if not_after else None,
    }
    row.config = config
    db.commit()


def _scheduled_reason(config: Mapping[str, object], renew_before_days: int) -> str:
    record = _record(config)
    if record is not None and record.get("status") in _UNFINISHED:
        return "Resuming an unfinished renewal."
    not_after = certificate_not_after(config)
    expiry = f"{not_after:%d %b %Y}" if not_after else "soon"
    return (
        f"The certificate expires on {expiry}; renewed within {renew_before_days} days of expiry."
    )


def run_scheduled_check(  # noqa: PLR0913 - the check's whole context, injectable for tests
    session_factory: Callable[[], Session],
    *,
    operator: GatewayIdentity,
    renew_before_days: int = DEFAULT_RENEW_BEFORE_DAYS,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RenewalCheckResult:
    """Renew every certificate due on ``operator``'s gateway; resume unfinished ones.

    Each bank is handled in its own session and transaction, so one bank's failure
    never stops another's renewal. Banks enrolled on another gateway are skipped.
    """
    now_fn = clock or (lambda: datetime.now(UTC))
    result = RenewalCheckResult()
    with session_factory() as db:
        targets = list(
            db.execute(
                select(RegulatoryChannelConfig.organization_id, RegulatoryChannelConfig.bank_id)
                .where(RegulatoryChannelConfig.channel == CHANNEL_CODE)
                .order_by(RegulatoryChannelConfig.organization_id, RegulatoryChannelConfig.bank_id)
            ).tuples()
        )
    for organization_id, bank_id in targets:
        result.organizations[bank_id] = organization_id
        with session_factory() as db:
            try:
                bank = bank_in(db, organization_id, bank_id)
                row = _locked_row(db, bank)
                config: dict[str, object] = dict(row.config) if row is not None else {}
                if row is None or not config.get("enrolled_at"):
                    db.rollback()
                    continue
                if config.get("api_base_url") != operator.base_url:
                    db.rollback()
                    result.skipped[bank_id] = "enrolled on another gateway"
                    continue
                now = now_fn()
                _stamp_check(db, row, now, renew_before_days)
                result.checked.append(bank_id)
                if not renewal_due(config, now, renew_before_days=renew_before_days):
                    continue
                renewed = renew_certificate(
                    db,
                    organization_id,
                    bank_id,
                    RenewalRequest(
                        trigger="scheduled",
                        requested_by=SCHEDULED_BY,
                        reason=_scheduled_reason(config, renew_before_days),
                    ),
                    operator=operator,
                    renew_before_days=renew_before_days,
                    transport=transport,
                    clock=now_fn,
                )
                result.renewed.append(bank_id)
                record = renewed.certificate_renewal
                result.switches[bank_id] = {
                    "previous_client_id": record.previous_client_id if record else None,
                    "new_client_id": record.new_client_id if record else None,
                }
            except HTTPException as exc:
                db.rollback()
                detail = str(cast(object, exc.detail))
                result.failed[bank_id] = detail
                logger.error(
                    "gateway_certificate_renewal.failed bank=%s org=%s detail=%s",
                    bank_id,
                    organization_id,
                    detail,
                )
            except Exception:
                db.rollback()
                result.failed[bank_id] = "unexpected error"
                logger.exception(
                    "gateway_certificate_renewal.error bank=%s org=%s", bank_id, organization_id
                )
    logger.info(
        "gateway_certificate_renewal.check checked=%d renewed=%d failed=%d skipped=%d",
        len(result.checked),
        len(result.renewed),
        len(result.failed),
        len(result.skipped),
    )
    return result
