"""The staff plane's scheduled check of gateway client certificates.

Renewing a bank's gateway certificate needs the gateway's operator credential
(scope ``ops:enrol``). That credential belongs to the staff control plane only, so
the scheduled check runs here, in the staff API process, and never in the tenant
worker. A daemon thread runs :func:`app.reporting.public.gateway_renewal.run_scheduled_check`
shortly after start-up and then every ``REPORTING_GATEWAY_RENEWAL_CHECK_SECONDS``.

Only one check runs at a time across every staff-plane process: each run holds a
PostgreSQL advisory lock for its duration, and a process that cannot take it skips
that run. A renewal's own lease (``gateway_renewal.RENEWAL_LEASE``) separately keeps
a staff-triggered renewal and the scheduled one off the same bank.

The check starts only when ``REPORTING_GATEWAY_RENEWAL_ENABLED`` is on (the default)
and the gateway and its operator client are configured; it never starts under
``APP_ENV=test``.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import OperatorSettings
from app.operator.models import OperatorAuditLog
from app.reporting.public import GatewayIdentity, gateway_enrolment, gateway_renewal

logger = logging.getLogger(__name__)

#: The advisory lock every staff-plane process contends for; one check at a time.
_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"aequoros:gateway-certificate-renewal").digest()[:8], signed=True
)
#: The first check waits this long after start-up, so a restart loop cannot hammer
#: the gateway and the process is serving before it does background work.
FIRST_CHECK_DELAY_SECONDS = 60.0


def scheduled_check_enabled(settings: OperatorSettings, app_env: str) -> bool:
    """Whether this staff-plane process should run the scheduled check."""
    return (
        settings.reporting_gateway_renewal_enabled
        and settings.reporting_gateway_url is not None
        and settings.reporting_gateway_operator_dir is not None
        and app_env != "test"
    )


def operator_identity_from(settings: OperatorSettings) -> GatewayIdentity:
    """The gateway operator client, read afresh so a rotated credential is used."""
    return gateway_enrolment.operator_identity(
        base_url=settings.reporting_gateway_url,
        directory=settings.reporting_gateway_operator_dir,
        forward_certificate=settings.reporting_gateway_forward_client_certificate,
    )


def record_scheduled_outcomes(db: Session, result: gateway_renewal.RenewalCheckResult) -> None:
    """Write each scheduled renewal and failure to the staff log, as the system.

    Staff renewals are logged by the endpoint that ran them; the scheduled check has no
    person behind it, so its rows carry ``auth_mode='system'`` and name the job. A check
    that found nothing due writes nothing here: the bank's own channel record keeps the
    time of every check.
    """
    rows: list[OperatorAuditLog] = []
    for bank_id in result.renewed:
        switch = result.switches.get(bank_id, {})
        rows.append(
            OperatorAuditLog(
                operator_email=gateway_renewal.SCHEDULED_BY,
                auth_mode="system",
                action="reporting_gateway.certificate_renewed",
                target_org=result.organizations.get(bank_id),
                detail={
                    "bank_id": bank_id,
                    "trigger": "scheduled",
                    "reason": "Scheduled renewal before the certificate expires",
                    "support_reference": None,
                    "outcome": "success",
                    "previous_client_id": switch.get("previous_client_id"),
                    "new_client_id": switch.get("new_client_id"),
                },
            )
        )
    for bank_id, error in result.failed.items():
        rows.append(
            OperatorAuditLog(
                operator_email=gateway_renewal.SCHEDULED_BY,
                auth_mode="system",
                action="reporting_gateway.certificate_renewal_failed",
                target_org=result.organizations.get(bank_id),
                detail={
                    "bank_id": bank_id,
                    "trigger": "scheduled",
                    "reason": "Scheduled renewal before the certificate expires",
                    "support_reference": None,
                    "outcome": "failure",
                    "succeeded": False,
                    "error": error,
                },
            )
        )
    db.add_all(rows)


def _try_lock(db: Session) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return True
    return bool(db.scalar(select(func.pg_try_advisory_lock(_LOCK_KEY))))


def _unlock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        _ = db.scalar(select(func.pg_advisory_unlock(_LOCK_KEY)))


class GatewayRenewalScheduler:
    """Runs the scheduled certificate check on a timer, one runner at a time."""

    def __init__(  # noqa: PLR0913 - the check's whole context, injectable for tests
        self,
        *,
        session_factory: Callable[[], Session],
        operator: Callable[[], GatewayIdentity],
        renew_before_days: int,
        interval_seconds: float,
        first_delay_seconds: float = FIRST_CHECK_DELAY_SECONDS,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._operator = operator
        self._renew_before_days = renew_before_days
        self._interval = interval_seconds
        self._first_delay = first_delay_seconds
        self._transport = transport
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> gateway_renewal.RenewalCheckResult | None:
        """One scheduled check; ``None`` when another process holds the check."""
        try:
            operator = self._operator()
        except HTTPException as exc:
            logger.error("gateway_certificate_renewal.unconfigured detail=%s", str(exc.detail))
            return None
        with self._session_factory() as lock_session:
            if not _try_lock(lock_session):
                logger.info("gateway_certificate_renewal.skipped reason=another_runner")
                return None
            try:
                result = gateway_renewal.run_scheduled_check(
                    self._session_factory,
                    operator=operator,
                    renew_before_days=self._renew_before_days,
                    transport=self._transport,
                    clock=self._clock,
                )
                if result.renewed or result.failed:
                    with self._session_factory() as log_session:
                        record_scheduled_outcomes(log_session, result)
                        log_session.commit()
                return result
            finally:
                _unlock(lock_session)
                lock_session.commit()

    def _loop(self) -> None:
        delay = self._first_delay
        while not self._stop.wait(delay):
            try:
                _ = self.run_once()
            except Exception:
                logger.exception("gateway_certificate_renewal.check_crashed")
            delay = self._interval

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="gateway-certificate-renewal", daemon=True
        )
        self._thread.start()
        logger.info(
            "gateway_certificate_renewal.scheduled every_seconds=%s renew_before_days=%s",
            self._interval,
            self._renew_before_days,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None


def start_scheduled_check(
    settings: OperatorSettings,
    app_env: str,
    session_factory: Callable[[], Callable[[], Session]],
) -> GatewayRenewalScheduler | None:
    """Start the scheduled check for this process, or ``None`` when it should not run."""
    if not scheduled_check_enabled(settings, app_env):
        logger.info("gateway_certificate_renewal.not_scheduled")
        return None
    try:
        sessions = session_factory()
    except HTTPException as exc:
        logger.error("gateway_certificate_renewal.no_database detail=%s", str(exc.detail))
        return None
    scheduler = GatewayRenewalScheduler(
        session_factory=sessions,
        operator=lambda: operator_identity_from(settings),
        renew_before_days=settings.reporting_gateway_renew_before_days,
        interval_seconds=float(settings.reporting_gateway_renewal_check_seconds),
    )
    scheduler.start()
    return scheduler
