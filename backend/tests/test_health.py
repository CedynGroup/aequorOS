from __future__ import annotations

import os

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api import health
from app.core.config import get_settings
from app.main import create_app
from app.storage.client import StorageHealth

#: Stands in for the deployment's BYPASSRLS worker role. Deliberately NOT the
#: real name: this repository is public, and the assertions below are about
#: what SHAPE of value must never reach an unauthenticated caller.
_SYNTHETIC_WORKER_ROLE = "example_db_worker_role"


class _HealthyStorage:
    def health_check(self) -> StorageHealth:
        return StorageHealth(healthy=True, backend="s3")


@pytest.fixture(autouse=True)
def stub_storage_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Readiness now performs a REAL object-store round trip.

    That is the point of the fix (audit finding P0-17), and it means every
    readiness assertion in this module would otherwise make a network call to an
    object store the hermetic suite does not have. Tests that are about storage
    replace this stub with the behaviour they need.
    """
    monkeypatch.setattr(health, "get_storage_client", _HealthyStorage)


def test_create_app() -> None:
    app = create_app()

    assert app.title == "risk-service"


def test_production_still_starts_when_it_cannot_sign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured signing key must not take the whole platform down.

    This asserts the absence of a guard that used to be here, so it needs its
    reason recorded. `create_app` raised on this condition until 2026-07-26,
    when a production deploy without `SIGNER_ID_PEPPER` crash-looped the API:
    liquidity, FX, Basel and treasury all went down over a capability none of
    them use, and no administrator could sign in to relax the signing policy —
    the documented way out. The guard removed the escape hatch it pointed at.

    "Nobody discovers this at a filing deadline" is preserved by two narrower
    mechanisms, each with its own test: `/health/ready` fails in production, and
    the filing path refuses via `ensure_signing_configured`.
    """
    monkeypatch.setenv("APP_ENV", "production")
    # setenv("") not delenv: deleting lets pydantic-settings read the value
    # back out of a developer's .env (see tests/conftest.py).
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    get_settings.cache_clear()

    assert create_app().title == "risk-service"
    get_settings.cache_clear()


def test_an_openbao_deployment_with_no_trust_anchor_says_so_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An institutional root exists on this backend; only the pointer is missing.

    Every officer certificate is issued by the OpenBao PKI mount, so with
    `ATTESTATION_TRUST_ROOTS` unset the verifier falls back to the chain each
    signature carries and reports `trust_anchor: "embedded_chain"` — issued by
    the authority it names, which is NOT the same as issued by an authority the
    institution recognises. A green report makes the two easy to confuse, so an
    operator is told before an examiner is. A warning, not a refusal: it does not
    stop a return being filed, and taking the API down over it would repeat the
    2026-07-26 outage above.
    """
    from loguru import logger  # noqa: PLC0415

    monkeypatch.setenv("SIGNING_BACKEND", "openbao")
    monkeypatch.setenv("ATTESTATION_TRUST_ROOTS", "")
    # `configure_logging` resets loguru's handlers, which would drop the sink
    # below before anything could be written to it.
    monkeypatch.setattr("app.main.configure_logging", lambda level: None)
    get_settings.cache_clear()

    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        create_app()
    finally:
        logger.remove(sink_id)
        get_settings.cache_clear()

    assert any("ATTESTATION_TRUST_ROOTS" in record for record in records)


def _worker_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the production/staging WORKER_DATABASE_URL contract.

    Outside `local`/`test` the worker refuses to bind to the tenant API role, so
    readiness reports it as unclaimable. That is correct, and it is a different
    failure from the one these tests are about.
    """
    monkeypatch.setenv("WORKER_DATABASE_URL", os.environ["DATABASE_URL"])


def test_ready_fails_in_production_when_signing_is_unconfigured(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness is what surfaces the gap at deploy time now.

    It has to name the missing settings and say the blast radius is limited to
    filing, because the operator reading it is deciding whether to roll back.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    _worker_url_from_env(monkeypatch)
    get_settings.cache_clear()

    response = db_client.get("/api/health/ready")

    assert response.status_code == 503
    message = response.json()["error"]["message"]
    assert "ATTESTATION_SIGNING_ENABLED" in message
    assert "SIGNER_ID_PEPPER" in message
    assert "unaffected" in message
    get_settings.cache_clear()


def test_ready_ignores_the_signing_gap_outside_production(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer without a signing key has a healthy service, not a red probe."""
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    _worker_url_from_env(monkeypatch)
    get_settings.cache_clear()

    assert db_client.get("/api/health/ready").status_code == 200
    get_settings.cache_clear()


def test_ready_ignores_the_signing_gap_when_esign_is_disabled(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the ATTESTATION_ESIGN_REQUIRED kill-switch off, no return can demand
    a signature, so a production deployment that cannot sign is not a filing
    outage and must not fail its probe."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    _worker_url_from_env(monkeypatch)
    get_settings.cache_clear()

    assert db_client.get("/api/health/ready").status_code == 200
    get_settings.cache_clear()


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


def test_ready_fails_when_storage_health_probe_raises(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "get_storage_client", lambda: (_ for _ in ()).throw(OSError()))

    response = db_client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Storage connectivity check failed."


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


def test_ready_reports_each_subsystem_separately(db_client: TestClient) -> None:
    """Storage used to be reported as `database.storage`, which reads as a
    property of the database and made a storage outage look like a database one.
    """
    body = db_client.get("/api/health/ready").json()

    assert set(body["checks"]) == {"database", "storage", "worker", "signing"}
    assert body["checks"]["database"]["status"] == "ok"
    assert "storage" not in body.get("database", {})


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
        "signing": {
            "status": "skipped",
            "detail": "Signing is not required in this environment.",
        },
    }


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


def test_startup_names_the_disabled_esign_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kill-switch replaces the signing-gaps warning with one explicit
    statement of the suspended requirement."""
    from loguru import logger  # noqa: PLC0415

    monkeypatch.setenv("ATTESTATION_ESIGN_REQUIRED", "0")
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    monkeypatch.setattr("app.main.configure_logging", lambda level: None)
    get_settings.cache_clear()

    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        create_app()
    finally:
        logger.remove(sink_id)
        get_settings.cache_clear()

    assert any("ATTESTATION_ESIGN_REQUIRED=0" in record for record in records)
    assert not any("no regulatory return can be certified" in record for record in records)


def test_live_health(client: TestClient) -> None:
    response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "service": "risk-service",
        "environment": "test",
        "status": "ok",
    }


def test_ready_health_skips_database_when_unconfigured_in_test(client: TestClient) -> None:
    response = client.get("/api/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "risk-service"
    assert body["environment"] == "test"
    assert body["status"] == "ok"
    assert body["checks"]["database"] == {
        "status": "skipped",
        "detail": "DATABASE_URL is unset.",
    }
    assert body["checks"]["worker"]["status"] == "skipped"


def test_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/api/health/live", headers={"X-Request-ID": "test-request-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-id"


def test_http_error_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/missing", headers={"X-Request-ID": "missing-request"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "request_id": "missing-request",
        },
    }


def test_unexpected_error_uses_initial_error_shape() -> None:
    router = APIRouter()

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    app = create_app()
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/boom", headers={"X-Request-ID": "req_test"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "An unexpected error occurred.",
            "request_id": "req_test",
        },
    }


def test_ready_health_requires_database_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    # A production app refuses to construct when it cannot sign, because every
    # return requires signatures and an unsignable deployment cannot file. That
    # guard is asserted in its own test; here it is satisfied so the readiness
    # probe is what is under test.
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "health-test-pepper-not-production")

    get_settings.cache_clear()

    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/health/ready", headers={"X-Request-ID": "ready-request"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "Database is not configured.",
            "request_id": "ready-request",
        },
    }


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
