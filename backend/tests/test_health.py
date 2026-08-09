from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


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
    get_settings.cache_clear()

    assert db_client.get("/api/health/ready").status_code == 200
    get_settings.cache_clear()


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
    assert response.json() == {
        "service": "risk-service",
        "environment": "test",
        "status": "ok",
        "database": {"status": "skipped", "storage": "ok"},
    }


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
