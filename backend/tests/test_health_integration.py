"""Tests for test_health."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.core.config import get_settings
from app.db.session import get_worker_sessionmaker
from app.storage.client import StorageHealth
from tests.test_health import stub_storage_probe as stub_storage_probe

# Readiness probes open independent database connections by design.
requires_committing_db = pytest.mark.committing_db
#: Stands in for the deployment's BYPASSRLS worker role. Deliberately NOT the
#: real name: this repository is public, and the assertions below are about
#: what SHAPE of value must never reach an unauthenticated caller.
_SYNTHETIC_WORKER_ROLE = "example_db_worker_role"


def _worker_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the production/staging WORKER_DATABASE_URL contract.

    Outside `local`/`test` the worker refuses to bind to the tenant API role, so
    readiness reports it as unclaimable. That is correct, and it is a different
    failure from the one these tests are about.

    Prepare the independently named worker pool while APP_ENV is still test:
    the disposable database has local transport, even when the signing probe
    subsequently simulates production or staging.
    """
    monkeypatch.setenv("WORKER_DATABASE_URL", os.environ["DATABASE_URL"])
    get_settings.cache_clear()
    get_worker_sessionmaker()


@requires_committing_db
def test_ready_fails_in_production_when_signing_is_unconfigured(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness is what surfaces the gap at deploy time now.

    It has to name the missing settings and say the blast radius is limited to
    filing, because the operator reading it is deciding whether to roll back.
    """
    _worker_url_from_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    get_settings.cache_clear()

    response = db_client.get("/api/health/ready")

    assert response.status_code == 503
    message = response.json()["error"]["message"]
    assert "ATTESTATION_SIGNING_ENABLED" in message
    assert "SIGNER_ID_PEPPER" in message
    assert "unaffected" in message
    get_settings.cache_clear()


@requires_committing_db
def test_ready_ignores_the_signing_gap_outside_production(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer without a signing key has a healthy service, not a red probe."""
    _worker_url_from_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    get_settings.cache_clear()

    assert db_client.get("/api/health/ready").status_code == 200
    get_settings.cache_clear()


@requires_committing_db
def test_ready_ignores_the_signing_gap_when_esign_is_disabled(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the ATTESTATION_ESIGN_REQUIRED kill-switch off, no return can demand
    a signature, so a production deployment that cannot sign is not a filing
    outage and must not fail its probe."""
    _worker_url_from_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    get_settings.cache_clear()

    assert db_client.get("/api/health/ready").status_code == 200
    get_settings.cache_clear()


@requires_committing_db
def test_ready_fails_when_configured_storage_is_unhealthy(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`storage_configured` only proves the env vars are non-empty.

    A deleted bucket, revoked keys or an unreachable MinIO all left it True and
    readiness answered 200 with `"storage": "ok"` (audit finding P0-17). The
    probe is what closes that, so it has to be the thing under test.
    """

    class UnhealthyStorage:
        def health_check(self) -> StorageHealth:
            return StorageHealth(healthy=False, backend="s3", detail="fixture unavailable")

    unhealthy_storage = UnhealthyStorage()

    def get_unhealthy_storage() -> UnhealthyStorage:
        return unhealthy_storage

    monkeypatch.setattr(health, "get_storage_client", get_unhealthy_storage)

    response = db_client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Storage connectivity check failed."


@requires_committing_db
def test_ready_fails_when_storage_health_probe_raises(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "get_storage_client", lambda: (_ for _ in ()).throw(OSError()))

    response = db_client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Storage connectivity check failed."


@requires_committing_db
def test_ready_probes_storage_for_real_outside_the_hermetic_suite(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe must actually be called, not merely available."""
    calls: list[str] = []

    class HealthyStorage:
        def health_check(self) -> StorageHealth:
            calls.append("probed")
            return StorageHealth(healthy=True, backend="s3")

    monkeypatch.setattr(health, "get_storage_client", HealthyStorage)

    response = db_client.get("/api/health/ready")

    assert response.status_code == 200
    assert calls == ["probed"]
    assert response.json()["checks"]["storage"]["status"] == "ok"


@requires_committing_db
def test_ready_reports_each_subsystem_separately(db_client: TestClient) -> None:
    """Storage used to be reported as `database.storage`, which reads as a
    property of the database and made a storage outage look like a database one.
    """
    body = db_client.get("/api/health/ready").json()

    assert set(body["checks"]) == {"database", "storage", "worker", "ai", "signing"}
    assert body["checks"]["database"]["status"] == "ok"
    assert "storage" not in body.get("database", {})


@requires_committing_db
def test_ready_discloses_no_deployment_topology_to_an_unauthenticated_caller(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leak canary for the whole unauthenticated readiness payload (D-28).

    `/api/health/ready` carries no principal dependency — it cannot, because the
    caller is an orchestrator with no credentials — so everything it returns is
    permanently public. It used to return
    `f"{role} bypasses row-level security."`, built from `SELECT current_user`
    on the worker connection: on the primary that string is the actual role name
    the real BYPASSRLS role name — the username half of a credential for a host
    whose other protection is an IP allow-list. Alongside it went the FORCE-RLS
    table names, the object-store product, and the setting to repoint.

    The fixture below uses a SYNTHETIC role name on purpose. This repository is
    public, and a test that proves the endpoint does not disclose the role name
    has no business hardcoding it: the assertion is about the SHAPE of what the
    payload may contain, and a placeholder proves that exactly as well.

    Every one of those is asserted absent here from the SERIALISED response, not
    from one field, so a future check that reintroduces any of them anywhere in
    the payload fails.
    """
    from app.db.session import WorkerVisibility  # noqa: PLC0415

    monkeypatch.setattr(
        health,
        "worker_visibility",
        lambda: WorkerVisibility(
            can_claim=True,
            role=_SYNTHETIC_WORKER_ROLE,
            detail=f"{_SYNTHETIC_WORKER_ROLE} bypasses row-level security.",
        ),
    )

    class MinioStorage:
        def health_check(self) -> StorageHealth:
            return StorageHealth(healthy=True, backend="minio")

    monkeypatch.setattr(health, "get_storage_client", MinioStorage)

    response = db_client.get("/api/health/ready")

    assert response.status_code == 200
    payload = response.text
    for leaked in (
        _SYNTHETIC_WORKER_ROLE,  # the database role name
        "row-level security",  # the deployment's privilege model
        "BYPASSRLS",
        "jobs do not force",  # internal table names, as worker_visibility spells them
        "minio",  # the storage product, i.e. a CVE target
        "WORKER_DATABASE_URL",  # which connection string to go after
        "postgresql",
        "sqlite",
        "5433",
    ):
        assert leaked not in payload, f"{leaked!r} is disclosed by /health/ready"
    assert response.json()["checks"] == {
        "database": {"status": "ok", "detail": None},
        "storage": {"status": "ok", "detail": "Object storage is reachable."},
        "worker": {"status": "ok", "detail": "Background worker can claim jobs."},
        # The AI lane is a SEPARATE deployment, so its check says only whether
        # drafting requests are being drained — never which app runs them, and
        # never whether a model credential exists.
        "ai": {"status": "skipped", "detail": "AI drafting is switched off on this deployment."},
        "signing": {
            "status": "skipped",
            "detail": "Signing is not required in this environment.",
        },
    }


@requires_committing_db
def test_ready_reports_a_worker_that_cannot_claim_jobs(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker whose role cannot see the FORCE-RLS job queue claims nothing,
    forever, with no error and no log (audit finding P0-16). Readiness is where
    that becomes visible from outside the worker process.

    The VERDICT is public; the evidence is not. `worker_visibility()` builds its
    detail out of `SELECT current_user`, and this route has no principal
    dependency, so returning it published the database role name to anyone who
    could reach the API (audit finding D-28). The diagnosis goes to the log
    instead — asserted here, because "we stopped saying it" is only a fix if
    somebody is still being told.
    """
    from loguru import logger  # noqa: PLC0415

    from app.db.session import WorkerVisibility  # noqa: PLC0415

    monkeypatch.setattr(
        health,
        "worker_visibility",
        lambda: WorkerVisibility(
            can_claim=False,
            role="tenant_role",
            detail="tenant_role does not bypass row-level security.",
        ),
    )

    records: list[str] = []
    sink_id = logger.add(
        lambda message: records.append(f"{message}{message.record['extra']}"), level="WARNING"
    )
    try:
        response = db_client.get("/api/health/ready")
    finally:
        logger.remove(sink_id)
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["checks"]["worker"]["status"] == "failed"
    assert body["checks"]["worker"]["detail"] == "Background worker cannot claim jobs."
    assert "tenant_role" not in response.text
    assert "row-level security" not in response.text
    # ...and the operator is still told exactly which role, in the log.
    assert any("tenant_role" in record for record in records)


@requires_committing_db
def test_ready_fails_closed_on_a_blind_worker_in_production(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.session import WorkerVisibility  # noqa: PLC0415

    monkeypatch.setattr(
        health,
        "worker_visibility",
        lambda: WorkerVisibility(can_claim=False, role="tenant_role", detail="no BYPASSRLS."),
    )
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "health-test-pepper-not-production")
    get_settings.cache_clear()

    response = db_client.get("/api/health/ready")

    assert response.status_code == 503
    assert "cannot claim jobs" in response.json()["error"]["message"]
    # The 503 is served to the same unauthenticated caller as the 200, so the
    # role name is no more publishable here (audit finding D-28).
    assert "tenant_role" not in response.text
    get_settings.cache_clear()


@requires_committing_db
def test_ready_reports_a_starved_job_queue(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jobs queued long past their run time mean nothing is draining them.

    The depth of the backlog is live operational state — an unauthenticated
    caller watching the number move is watching whether their own load is
    landing — so the count is logged and the payload says only that the queue is
    not draining (audit finding D-28).
    """
    from loguru import logger  # noqa: PLC0415

    monkeypatch.setattr(health, "_overdue_job_count", lambda _stale_after: 7)

    records: list[str] = []
    sink_id = logger.add(
        lambda message: records.append(str(message.record["extra"])), level="WARNING"
    )
    try:
        response = db_client.get("/api/health/ready")
    finally:
        logger.remove(sink_id)
    body = response.json()

    assert body["status"] == "degraded"
    assert body["checks"]["worker"]["status"] == "degraded"
    assert body["checks"]["worker"]["detail"] == (
        "Queued jobs are not being drained; the worker process may not be running."
    )
    assert "7" not in response.text
    assert any("'overdue_jobs': 7" in record for record in records)


@requires_committing_db
def test_ready_health_requires_storage_when_database_is_configured(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Storage credentials are the shared MinIO set (S3_*); emptying the bucket
    # and region makes StorageSettings.configured False.
    monkeypatch.setenv("S3_BUCKET", "")
    monkeypatch.setenv("S3_REGION", "")
    get_settings.cache_clear()

    response = db_client.get("/api/health/ready", headers={"X-Request-ID": "ready-request"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "Storage is not configured.",
            "request_id": "ready-request",
        },
    }


@requires_committing_db
def test_a_missing_ai_worker_never_makes_the_platform_unready(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI drafting is optional and runs in a SEPARATE deployment.

    The normal state until the production gate is passed is "the AI app is not
    deployed at all". A queued draft must never take a bank's regulatory
    reporting down, so the worst this check may ever say is ``degraded`` — it
    has no path to a 503.
    """
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(health, "_ai_overdue_count", lambda _stale: 25)

    response = db_client.get("/api/health/ready")

    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks["ai"]["status"] == "degraded"
    # The COUNT stays internal: queue depth on an unauthenticated endpoint is
    # live operational state.
    assert "25" not in response.text
