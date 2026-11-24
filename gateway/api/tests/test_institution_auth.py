# pyright: standard
"""Layers 1 and 2: client certificate, private_key_jwt and certificate-bound tokens."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import jwt

from app.core.clock import utc
from app.seed.client import new_key
from tests.harness import Harness, cert_header, make_harness


def _code(response) -> str:
    return response.json()["code"]


def test_token_happy_path_and_claims(harness: Harness) -> None:
    bank = harness.enrol()
    response = harness.token_response(
        bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert set(body["scope"].split()) == {
        "returns:read",
        "obligations:read",
        "submissions:write",
        "submissions:read",
        "queries:read",
        "queries:write",
    }
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["institution_code"] == "0142"
    assert claims["sub"] == bank.client_id
    assert claims["cnf"]["x5t#S256"]
    assert claims["exp"] - claims["iat"] == 600


def test_token_requires_certificate(harness: Harness) -> None:
    bank = harness.enrol()
    form = {
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": harness.assertion(
            bank.client_id, bank.keys.client_key, bank.keys.client_kid
        ),
    }
    response = harness.client.post("/oauth/token", data=form)
    assert response.status_code == 401
    assert _code(response) == "certificate_required"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_token_refuses_another_clients_certificate(harness: Harness) -> None:
    bank = harness.enrol()
    other = harness.enrol("0117", "Palmgrove Bank")
    response = harness.token_response(
        bank.client_id, bank.keys.client_key, bank.keys.client_kid, other.certificate_pem
    )
    assert response.status_code == 401
    assert _code(response) == "certificate_mismatch"


def test_assertion_refusals(harness: Harness) -> None:
    bank = harness.enrol()
    args = (bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem)
    cases = {
        "wrong audience": {"aud": "https://elsewhere.example/oauth/token"},
        "expired": {"iat_offset": -400, "lifetime": 120},
        "exp too far ahead": {"lifetime": 900},
        "issued in the future": {"iat_offset": 600},
        "iss differs from sub": {"iss": "someone-else"},
    }
    for label, kwargs in cases.items():
        response = harness.token_response(*args, **kwargs)
        assert response.status_code == 401, label
        assert _code(response) == "invalid_client_assertion", label
    wrong_key = harness.token_response(
        bank.client_id, new_key(), bank.keys.client_kid, bank.certificate_pem
    )
    assert wrong_key.status_code == 401
    assert _code(wrong_key) == "invalid_client_assertion"


def test_replayed_assertion_is_refused(harness: Harness) -> None:
    bank = harness.enrol()
    assertion = harness.assertion(
        bank.client_id, bank.keys.client_key, bank.keys.client_kid, jti="same-jti"
    )
    form = {
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
    }
    first = harness.client.post(
        "/oauth/token", data=form, headers=cert_header(bank.certificate_pem)
    )
    assert first.status_code == 200
    again = harness.client.post(
        "/oauth/token", data=form, headers=cert_header(bank.certificate_pem)
    )
    assert again.status_code == 401
    assert _code(again) == "invalid_client_assertion"
    assert "used before" in again.json()["detail"]


def test_calls_need_the_bound_certificate_and_scope(harness: Harness) -> None:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol()
    other = harness.enrol("0117", "Palmgrove Bank")
    token = harness.bank_token(bank, scope="returns:read")
    ok = harness.client.get(
        "/v1/returns",
        headers={**cert_header(bank.certificate_pem), "Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200
    assert [r["code"] for r in ok.json()["items"]] == ["BSD2"]
    no_cert = harness.client.get("/v1/returns", headers={"Authorization": f"Bearer {token}"})
    assert _code(no_cert) == "certificate_required"
    swapped = harness.client.get(
        "/v1/returns",
        headers={**cert_header(other.certificate_pem), "Authorization": f"Bearer {token}"},
    )
    assert swapped.status_code == 401
    assert _code(swapped) == "certificate_mismatch"
    no_token = harness.client.get("/v1/returns", headers=cert_header(bank.certificate_pem))
    assert _code(no_token) == "unauthenticated"
    scoped = harness.client.get(
        "/v1/obligations?from=2026-10-01&to=2026-10-31",
        headers={**cert_header(bank.certificate_pem), "Authorization": f"Bearer {token}"},
    )
    assert scoped.status_code == 403
    assert _code(scoped) == "insufficient_scope"
    assert "insufficient_scope" in scoped.headers["www-authenticate"]


def test_expired_access_token_is_refused(harness: Harness) -> None:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol()
    headers = harness.bank_headers(bank)
    harness.clock.advance(seconds=601)
    response = harness.client.get("/v1/returns", headers=headers)
    assert response.status_code == 401
    assert _code(response) == "unauthenticated"


def test_revoked_client_loses_access_at_once(harness: Harness) -> None:
    from app.enrolment.service import revoke_client
    from tests.harness import OPERATOR

    harness.bootstrap(("BSD2",))
    bank = harness.enrol()
    headers = harness.bank_headers(bank)
    with harness.ctx.db.session() as session:
        revoke_client(session, harness.ctx, OPERATOR, bank.client_id, "test")
    assert harness.client.get("/v1/returns", headers=headers).status_code == 401
    again = harness.token_response(
        bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem
    )
    assert _code(again) == "invalid_client_assertion"


def test_rate_limit(harness: Harness) -> None:
    harness.bootstrap(("BSD2",))
    bank = harness.enrol()
    harness.ctx.rate_limiter.per_minute = 3
    headers = harness.bank_headers(bank)
    statuses = [harness.client.get("/v1/returns", headers=headers).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    limited = harness.client.get("/v1/returns", headers=headers)
    assert _code(limited) == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1


def test_header_injected_certificates_refused_outside_trusted_proxies(
    tmp_path: Path, keys_dir: Path, sqlite_template: Path
) -> None:
    from app.keys.provider import LocalFileKeyProvider

    for env in ("production", "sandbox"):
        keys = LocalFileKeyProvider(
            keys_dir,
            {
                "receipt": "gateway-receipt-2026-q4",
                "audit": "gateway-audit-2026-q4",
                "devidp": "dev-oidc-2026-q4",
            },
        )
        harness = make_harness(tmp_path / env, keys_dir, sqlite_template, env=env, keys=keys)
        bank = harness.enrol()
        response = harness.token_response(
            bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem
        )
        assert response.status_code == 401, env
        assert _code(response) == "certificate_required", env


def test_trusted_proxy_header_is_accepted_in_production(
    tmp_path: Path, keys_dir: Path, sqlite_template: Path
) -> None:
    from app.keys.provider import LocalFileKeyProvider

    keys = LocalFileKeyProvider(
        keys_dir,
        {
            "receipt": "gateway-receipt-2026-q4",
            "audit": "gateway-audit-2026-q4",
            "devidp": "dev-oidc-2026-q4",
        },
    )
    harness = make_harness(
        tmp_path,
        keys_dir,
        sqlite_template,
        env="production",
        keys=keys,
        trusted_proxies="10.0.0.0/8",
        client_host="10.1.2.3",
    )
    bank = harness.enrol()
    response = harness.token_response(
        bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem
    )
    assert response.status_code == 200, response.text


def test_expired_certificate_is_refused(harness: Harness) -> None:
    bank = harness.enrol()
    harness.clock.set(utc(harness.clock.now() + timedelta(days=400)))
    response = harness.token_response(
        bank.client_id, bank.keys.client_key, bank.keys.client_kid, bank.certificate_pem
    )
    assert response.status_code == 401
    assert _code(response) == "certificate_required"


def _traefik_header(*pems: str) -> dict[str, str]:
    """The header exactly as Traefik's passTLSClientCert (``pem``) sends it.

    Each certificate's base64 body without the BEGIN/END lines or newlines,
    URL-escaped, chain entries comma-separated with the leaf first.
    """
    import urllib.parse

    bodies = [
        "".join(line for line in pem.strip().splitlines() if "CERTIFICATE" not in line)
        for pem in pems
    ]
    return {"X-Forwarded-Tls-Client-Cert": urllib.parse.quote(",".join(bodies), safe="")}


def test_a_certificate_forwarded_in_traefiks_format_is_read(harness: Harness) -> None:
    """Coolify's ingress is Traefik; the gateway read only nginx's full-PEM form."""
    bank = harness.enrol()
    other = harness.enrol(code="0143", name="Harbourline Bank")
    form = {
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": harness.assertion(
            bank.client_id, bank.keys.client_key, bank.keys.client_kid
        ),
    }
    # The leaf, then an issuer: only the leaf identifies the client.
    response = harness.client.post(
        "/oauth/token",
        data=form,
        headers=_traefik_header(bank.certificate_pem, other.certificate_pem),
    )
    assert response.status_code == 200, response.text
