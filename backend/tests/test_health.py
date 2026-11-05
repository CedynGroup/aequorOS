"""Tests for test_health."""

from __future__ import annotations

import asyncio
from threading import Event
from time import monotonic

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import JsonValue, TypeAdapter

from app.api import health
from app.core.config import get_settings
from app.core.key_management.settings import get_key_settings
from app.main import create_app
from app.operator.main import create_operator_app
from app.storage.client import StorageHealth


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
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
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


@pytest.mark.parametrize("required", [False, True])
def test_live_health(client: TestClient, monkeypatch: pytest.MonkeyPatch, required: bool) -> None:
    monkeypatch.setenv("BANK_KEY_REQUIRED", str(required).lower())
    get_key_settings.cache_clear()
    try:
        response = client.get("/api/health/live")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "location" not in response.headers
        assert response.json() == {
            "service": "risk-service",
            "environment": "test",
            "status": "ok",
            "bank_key_required": required,
        }
        with TestClient(create_operator_app()) as operator_client:
            response = operator_client.get("/operator/health")
        assert response.status_code == 200
        assert response.json() == {
            "service": "aequoros-operator",
            "environment": "test",
            "status": "ok",
            "bank_key_required": required,
        }
    finally:
        get_key_settings.cache_clear()


def test_ready_health_skips_database_when_unconfigured_in_test(client: TestClient) -> None:
    response = client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    body = response.json()
    assert body["service"] == "risk-service"
    assert body["environment"] == "test"
    assert body["status"] == "ok"
    assert body["checks"]["database"] == {
        "status": "skipped",
        "detail": "DATABASE_URL is unset.",
    }
    assert body["checks"]["worker"]["status"] == "skipped"


@pytest.mark.parametrize("healthy", [True, False])
def test_overlapping_readiness_callers_share_dependency_result(
    monkeypatch: pytest.MonkeyPatch, healthy: bool
) -> None:
    entered, release = Event(), Event()
    calls: list[str] = []

    class SlowStorage:
        def health_check(self) -> StorageHealth:
            calls.append("probed")
            entered.set()
            _ = release.wait()
            return StorageHealth(
                healthy=healthy, backend="s3", detail="synthetic-private-storage-detail"
            )

    monkeypatch.setattr(health, "get_storage_client", SlowStorage)
    # The release event controls coalescing; deadline behavior is covered separately.
    monkeypatch.setattr(health, "_READINESS_TIMEOUT_SECONDS", None)

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://testserver"
        ) as client:
            requests = [asyncio.create_task(client.get("/api/health/ready"))]
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                requests.append(asyncio.create_task(client.get("/api/health/ready")))
                done, _ = await asyncio.wait(requests, timeout=0.05)
                assert not done
                assert calls == ["probed"]
                assert (await client.get("/api/health/live")).status_code == 200
                release.set()
                responses = await asyncio.gather(*requests)
                for response in responses:
                    assert response.status_code == (200 if healthy else 503)
                    assert response.headers["cache-control"] == "no-store"
                    assert "synthetic-private" not in response.text
                    if healthy:
                        assert response.json()["status"] == "ok"
                        assert response.json()["checks"]["storage"]["status"] == "ok"
                    else:
                        assert response.json()["error"]["message"] == (
                            "Storage connectivity check failed."
                        )
                response_bodies = [
                    TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
                    for response in responses
                ]
                assert response_bodies[0].get("checks") == response_bodies[1].get("checks")
                assert calls == ["probed"]
            finally:
                release.set()
                await asyncio.gather(*requests, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_readiness_deadline_preserves_liveness_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    entered, release, finished = Event(), Event(), Event()
    calls: list[str] = []

    class SlowStorage:
        def health_check(self) -> StorageHealth:
            calls.append("probed")
            entered.set()
            try:
                assert release.wait(timeout=5)
                return StorageHealth(healthy=True, backend="s3")
            finally:
                finished.set()

    monkeypatch.setattr(health, "get_storage_client", SlowStorage)
    monkeypatch.setattr(health, "_READINESS_TIMEOUT_SECONDS", 0.1)

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://testserver"
        ) as client:
            started = monotonic()
            requests = [asyncio.create_task(client.get("/api/health/ready"))]
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                monkeypatch.setattr(health, "_READINESS_TIMEOUT_SECONDS", 1)
                requests.append(asyncio.create_task(client.get("/api/health/ready")))
                done, _ = await asyncio.wait([requests[1]], timeout=0.05)
                assert not done
                if cancel:
                    requests[0].cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await requests[0]
                else:
                    response = await requests[0]
                    assert monotonic() - started < 2
                    assert response.status_code == 503
                    assert response.json()["error"]["message"] == "Readiness check timed out."
                    assert response.headers["cache-control"] == "no-store"
                assert not requests[1].done()
                assert not finished.is_set()
                requests.append(asyncio.create_task(client.get("/api/health/ready")))
                done, _ = await asyncio.wait(requests[1:], timeout=0.05)
                assert not done
                assert calls == ["probed"]
                assert (await client.get("/api/health/live")).status_code == 200
                release.set()
                for response in await asyncio.gather(*requests[1:]):
                    assert response.status_code == 200
                    assert response.headers["cache-control"] == "no-store"
                assert calls == ["probed"]
                assert (await client.get("/api/health/ready")).status_code == 200
                assert calls == ["probed", "probed"]
            finally:
                release.set()
                await asyncio.gather(*requests, return_exceptions=True)
                assert await asyncio.to_thread(finished.wait, 2)

    asyncio.run(exercise())


def test_ready_dependency_failure_is_uncached_and_sanitized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UnreachableStorage:
        def health_check(self) -> StorageHealth:
            raise RuntimeError("synthetic-private-credential-and-driver-detail")

    monkeypatch.setattr(health, "get_storage_client", UnreachableStorage)
    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert response.json()["error"]["message"] == "Storage connectivity check failed."
    assert "synthetic-private" not in response.text
    assert client.get("/api/health/live").status_code == 200


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
    monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
    # A production app refuses to construct when it cannot sign, because every
    # return requires signatures and an unsignable deployment cannot file. That
    # guard is asserted in its own test; here it is satisfied so the readiness
    # probe is what is under test.
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "health-test-pepper-not-production")

    get_settings.cache_clear()

    client = TestClient(create_app(), raise_server_exceptions=False, base_url="https://testserver")

    response = client.get("/api/health/ready", headers={"X-Request-ID": "ready-request"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "Database is not configured.",
            "request_id": "ready-request",
        },
    }
