from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.db.session import get_engine, get_worker_sessionmaker, worker_visibility
from app.schemas.health import ComponentHealth, HealthResponse, ReadinessResponse
from app.storage.factory import get_storage_client

router = APIRouter(prefix="/health", tags=["health"])

_OK = ComponentHealth(status="ok")

# ---------------------------------------------------------------------------
# What an UNAUTHENTICATED caller is allowed to be told (audit finding D-28).
#
# `/health/ready` carries no principal dependency — it cannot, because the thing
# that calls it is an orchestrator with no credentials (see the Docker
# HEALTHCHECK in docker-compose.prod.yml). Everything it returns is therefore
# permanently public, and until 2026-08-22 that included
# `f"{role} bypasses row-level security."` built from `SELECT current_user` on
# the worker connection — the database ROLE NAME, i.e. the username half of a
# credential for a host whose only other protection is an IP allow-list. It also
# published the internal table names the worker claims across, the object-store
# backend, the driver's exception class, and the live queue backlog.
#
# The line drawn here: a readiness probe answers WHETHER the platform is ready,
# per subsystem. It is not an operations console. The diagnosis — which role,
# which tables, how deep the backlog — goes to the structured log, which is
# already access-controlled and is where an operator investigating a red probe
# actually looks; the authenticated fleet board `/operator/v1/worker-health`
# is the console surface and already exists (its own docstring says the public
# route "remains an availability probe, not an operations dashboard").
#
# Logs rather than a second authenticated route because the value being
# protected is a deploy-time diagnosis, not a dashboard: it is read once, by the
# person who just deployed, and duplicating the operator board on the tenant API
# would put cross-tenant infrastructure state back on the tenant surface.
#
# Deliberately NOT redacted: SETTING NAMES (`DATABASE_URL`,
# `ATTESTATION_SIGNING_ENABLED`, `SIGNER_ID_PEPPER`, …). Those are published
# configuration vocabulary — `.env.example` and `docs/` name every one of them —
# so they disclose nothing an attacker cannot read in the repository, and they
# are the whole operational content of the signing gap: the operator staring at
# a 503 during a deploy has to be told which settings to supply.
# ---------------------------------------------------------------------------
_WORKER_READY = "Background worker can claim jobs."
_WORKER_BLIND = "Background worker cannot claim jobs."
_WORKER_STARVED = "Queued jobs are not being drained; the worker process may not be running."
_STORAGE_READY = "Object storage is reachable."


@router.get("/live", response_model=HealthResponse)
def live(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        service=settings.app.app_name,
        environment=settings.app.app_env,
        status="ok",
    )


def _unavailable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _storage_health(settings: Settings) -> ComponentHealth:
    """Probe the object store, rather than the presence of its env vars.

    ``settings.storage_configured`` only tests that the S3 variables are
    non-empty, so a deleted bucket, revoked keys or an unreachable MinIO all
    reported ``ok`` (audit finding P0-17). Both failure modes now fail the
    probe closed.

    The backend's own name is logged, not returned: ``"minio reachable."`` on an
    unauthenticated route names the storage product an attacker would then go
    looking for CVEs in, and tells them nothing they need in order to know the
    platform is up (audit finding D-28).
    """
    if not settings.storage_configured:
        raise _unavailable("Storage is not configured.")
    try:
        health = get_storage_client().health_check()
    except Exception as exc:  # noqa: BLE001 - readiness converts driver failures to 503
        logger.opt(exception=exc).warning("Readiness: object-store probe raised.")
        raise _unavailable("Storage connectivity check failed.") from exc
    if not health.healthy:
        logger.bind(storage_backend=health.backend, storage_detail=health.detail).warning(
            "Readiness: object store reported unhealthy."
        )
        raise _unavailable("Storage connectivity check failed.")
    logger.bind(storage_backend=health.backend).debug("Readiness: object store reachable.")
    return ComponentHealth(status="ok", detail=_STORAGE_READY)


def _overdue_job_count(stale_after_seconds: float) -> int | None:
    """Queued jobs whose run time has passed by more than the stale threshold.

    A worker that cannot see the queue and a worker that is not running produce
    the same symptom — jobs queued and never claimed — and neither logs
    anything. This is the number that makes it visible from the API process.
    """
    try:
        engine = get_worker_sessionmaker().kw["bind"]
        if engine.dialect.name != "postgresql":
            return None
        with Session(engine) as session:
            return session.execute(
                text(
                    "SELECT count(*) FROM jobs WHERE status = 'queued' "
                    "AND run_after < now() - make_interval(secs => :stale_after)"
                ),
                {"stale_after": stale_after_seconds},
            ).scalar_one()
    except SQLAlchemyError:
        return None


def _worker_health(settings: Settings) -> ComponentHealth:
    """Whether the background worker can claim jobs at all, and whether it is.

    ``jobs`` is FORCE-RLS and the worker claims across tenants with no
    organization set, so a role without BYPASSRLS matches zero rows for every
    tenant forever — no error, no log, no signal (audit finding P0-16). The
    condition is deterministic, so readiness states it outright instead of
    inferring it from a backlog.

    The verdict is public; the EVIDENCE for it is not. ``worker_visibility()``
    reports the role name, the FORCE-RLS tables and the setting to repoint, and
    every one of those is a fact about how this deployment connects to its
    database — so it is bound onto a log record here and never onto the response
    (audit finding D-28).
    """
    visibility = worker_visibility()
    if visibility.blind:
        logger.bind(
            worker_role=visibility.role, worker_visibility_detail=visibility.detail
        ).error(
            "Readiness: the background worker cannot claim jobs. Every scheduled "
            "refresh, official run and vendor pull is stalled."
        )
        if settings.app.app_env in {"production", "staging"}:
            raise _unavailable(
                f"{_WORKER_BLIND} Every scheduled refresh, official run and vendor "
                "pull is stalled. See the service log for the cause."
            )
        return ComponentHealth(status="failed", detail=_WORKER_BLIND)

    overdue = _overdue_job_count(settings.worker.worker_stale_job_seconds)
    if overdue:
        # The COUNT stays internal: queue depth is live operational state, and an
        # unauthenticated caller watching it move is watching whether their own
        # load is landing.
        logger.bind(
            overdue_jobs=overdue,
            stale_after_seconds=settings.worker.worker_stale_job_seconds,
        ).warning("Readiness: jobs are queued past their run time; the worker may be down.")
        return ComponentHealth(status="degraded", detail=_WORKER_STARVED)
    logger.bind(worker_role=visibility.role, worker_visibility_detail=visibility.detail).debug(
        "Readiness: the background worker can claim jobs."
    )
    return ComponentHealth(status="ok", detail=_WORKER_READY)


def _signing_health(settings: Settings) -> ComponentHealth:
    """Signing is required for every return by default, so a production
    deployment that cannot sign cannot file. This check is what makes that
    visible at deploy time. ``create_app`` used to raise instead, which took the
    whole API down over an unrelated capability and locked out the very
    administrator who could have relaxed the policy — see
    ``_warn_if_signing_unconfigured``.

    Only production fails the probe: local and test deployments legitimately run
    without a signing key. And only while the e-sign requirement is in force:
    with ``ATTESTATION_ESIGN_REQUIRED`` off, no return can demand a signature,
    so a deployment that cannot sign is not a filing outage.

    The gap list NAMES SETTINGS, and that survives the D-28 sweep deliberately:
    a setting name is published vocabulary (``.env.example`` carries all of
    them), it is not a value, a host, a role or a version, and it is the entire
    reason the message exists — an operator mid-deploy is deciding whether to
    roll back and has to be told what to supply. The 200 path stays quiet about
    which ones, because a healthy deployment publishes that list forever.
    """
    gaps = settings.attestation.signing_readiness_gaps()
    if not gaps:
        return _OK
    if settings.app.app_env == "production" and settings.attestation.esign_required:
        raise _unavailable(
            "Attestation signing is not configured "
            f"({' and '.join(gaps)}), so no regulatory return can be "
            "certified or filed. Other capabilities are unaffected."
        )
    logger.bind(missing=gaps).debug("Readiness: signing is unconfigured but not required here.")
    return ComponentHealth(status="skipped", detail="Signing is not required in this environment.")


@router.get("/ready", response_model=ReadinessResponse)
def ready(settings: Annotated[Settings, Depends(get_settings)]) -> ReadinessResponse:
    checks: dict[str, ComponentHealth] = {}

    if settings.database.database_url is None:
        if settings.app.app_env not in {"local", "test"}:
            raise _unavailable("Database is not configured.")
        checks["database"] = ComponentHealth(status="skipped", detail="DATABASE_URL is unset.")
        checks["storage"] = _storage_health(settings)
        checks["worker"] = ComponentHealth(status="skipped", detail="DATABASE_URL is unset.")
        checks["signing"] = _signing_health(settings)
        return _respond(settings, checks)

    try:
        with Session(get_engine(settings.database.database_url)) as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.opt(exception=exc).warning("Readiness: database connectivity check failed.")
        raise _unavailable("Database connectivity check failed.") from exc
    checks["database"] = _OK
    checks["storage"] = _storage_health(settings)
    checks["worker"] = _worker_health(settings)
    checks["signing"] = _signing_health(settings)
    return _respond(settings, checks)


def _respond(settings: Settings, checks: dict[str, ComponentHealth]) -> ReadinessResponse:
    degraded = any(check.status in {"degraded", "failed"} for check in checks.values())
    return ReadinessResponse(
        service=settings.app.app_name,
        environment=settings.app.app_env,
        status="degraded" if degraded else "ok",
        checks=checks,
    )
