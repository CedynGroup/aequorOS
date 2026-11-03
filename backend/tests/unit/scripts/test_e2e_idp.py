"""The e2e stack's local OIDC issuer (``scripts/e2e_idp.py``) is a conforming
relying-party counterpart.

The Playwright journeys prove the browser leg; these prove, offline, that the
issuer answers the two clients the dashboard runs — NextAuth (PKCE,
``client_secret_basic``) and the attestation step-up route
(``client_secret_post``, ``prompt=login``/``max_age=0``) — and that an
id_token it mints survives the backend's own verifier end to end (discovery →
JWKS → RS256/iss/aud/exp), with the ``auth_time`` the step-up path insists on.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core import security
from scripts import e2e_idp

if TYPE_CHECKING:
    from collections.abc import Iterator

REDIRECT_URI = "http://127.0.0.1:3000/api/auth/callback/sso"


@pytest.fixture
def issuer(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("E2E_IDP_PORT", "8199")
    return e2e_idp.issuer()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(e2e_idp.app) as client:
        yield client


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _authorize(
    client: TestClient,
    *,
    email: str = "e2e.sso_analyst@aequoros.example",
    password: str = e2e_idp.PASSWORD,
    challenge: str | None = None,
    **extra: str,
) -> Any:
    request = {
        "response_type": "code",
        "client_id": e2e_idp.CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid email profile",
        "state": "opaque-state",
        "nonce": "opaque-nonce",
        **extra,
    }
    if challenge:
        request |= {"code_challenge": challenge, "code_challenge_method": "S256"}
    form_page = client.get("/authorize", params=request)
    assert form_page.status_code == 200
    assert "E2E Identity Provider" in form_page.text
    return client.post(
        "/authorize",
        data={**request, "_email": email, "_password": password},
        follow_redirects=False,
    )


def _code_from(response: Any) -> str:
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT_URI
    query = parse_qs(location.query)
    assert query["state"] == ["opaque-state"]
    return query["code"][0]


def _basic(client_id: str, secret: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


def test_discovery_names_every_endpoint_nextauth_requires(client: TestClient, issuer: str) -> None:
    document = client.get("/.well-known/openid-configuration").json()
    assert document["issuer"] == issuer
    for endpoint in ("authorization", "token", "userinfo"):
        assert document[f"{endpoint}_endpoint"].startswith(issuer)
    assert document["jwks_uri"] == f"{issuer}/jwks"
    assert document["code_challenge_methods_supported"] == ["S256"]
    assert "auth_time" in document["claims_supported"]


def test_wrong_password_re_renders_the_form_without_a_code(client: TestClient) -> None:
    response = _authorize(client, password="not-the-password")
    assert response.status_code == 200
    assert "Wrong email or password" in response.text
    assert "code=" not in response.text


def test_unknown_client_is_bounced_back_with_an_oauth_error(client: TestClient) -> None:
    response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "someone-else",
            "redirect_uri": REDIRECT_URI,
            "scope": "openid",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert parse_qs(urlparse(response.headers["location"]).query)["error"] == [
        "unauthorized_client"
    ]


def test_nextauth_shape_pkce_and_client_secret_basic_yield_a_verifiable_id_token(
    client: TestClient, issuer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier, challenge = _pkce()
    code = _code_from(_authorize(client, challenge=challenge))

    token = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        headers=_basic(e2e_idp.CLIENT_ID, e2e_idp.CLIENT_SECRET),
    )
    assert token.status_code == 200, token.text
    body = token.json()
    assert body["token_type"] == "Bearer"

    # The backend's verifier, fed by the issuer's own discovery and JWKS
    # documents (served through the guarded opener's seam, offline).
    documents = {
        f"{issuer}/.well-known/openid-configuration": client.get(
            "/.well-known/openid-configuration"
        ).json(),
        f"{issuer}/jwks": client.get("/jwks").json(),
    }
    monkeypatch.setattr(security, "_oidc_opener", lambda: _Opener(documents))
    security._discover_jwks_uri.cache_clear()
    security._jwks_client.cache_clear()
    claims = security.verify_oidc_id_token(
        body["id_token"], issuer=issuer, audience=e2e_idp.CLIENT_ID
    )
    assert claims["email"] == "e2e.sso_analyst@aequoros.example"
    assert claims["email_verified"] is True
    assert claims["sub"] == e2e_idp.subject_for("e2e.sso_analyst@aequoros.example")
    assert claims["nonce"] == "opaque-nonce"
    assert isinstance(claims["auth_time"], int)

    # userinfo answers the access token with the same identity.
    userinfo = client.get("/userinfo", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert userinfo.json()["sub"] == claims["sub"]


def test_step_up_shape_client_secret_post_is_accepted_too(client: TestClient) -> None:
    verifier, challenge = _pkce()
    code = _code_from(_authorize(client, challenge=challenge, prompt="login", max_age="0"))
    token = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": e2e_idp.CLIENT_ID,
            "client_secret": e2e_idp.CLIENT_SECRET,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text
    assert "auth_time" in jwt.decode(token.json()["id_token"], options={"verify_signature": False})


@pytest.mark.parametrize(
    ("headers", "form", "status"),
    [
        (_basic(e2e_idp.CLIENT_ID, "wrong-secret"), {}, 401),
        ({}, {"client_id": e2e_idp.CLIENT_ID, "client_secret": "wrong-secret"}, 401),
        (_basic(e2e_idp.CLIENT_ID, e2e_idp.CLIENT_SECRET), {"code_verifier": "wrong"}, 400),
        (
            _basic(e2e_idp.CLIENT_ID, e2e_idp.CLIENT_SECRET),
            {"redirect_uri": "http://127.0.0.1:3000/elsewhere"},
            400,
        ),
    ],
)
def test_token_endpoint_refuses_a_bad_client_verifier_or_redirect(
    client: TestClient, headers: dict[str, str], form: dict[str, str], status: int
) -> None:
    verifier, challenge = _pkce()
    code = _code_from(_authorize(client, challenge=challenge))
    response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        }
        | form,
        headers=headers,
    )
    assert response.status_code == status


def test_an_authorization_code_is_single_use(client: TestClient) -> None:
    code = _code_from(_authorize(client))
    exchange = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    headers = _basic(e2e_idp.CLIENT_ID, e2e_idp.CLIENT_SECRET)
    assert client.post("/token", data=exchange, headers=headers).status_code == 200
    replay = client.post("/token", data=exchange, headers=headers)
    assert replay.status_code == 400
    assert replay.json() == {"error": "invalid_grant"}


class _Opener:
    """The guarded urllib opener's stand-in: serves the issuer's own documents."""

    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self.documents = documents

    def open(self, request: Any, timeout: float | None = None) -> io.BytesIO:  # noqa: ARG002
        url = str(getattr(request, "full_url", request))
        return io.BytesIO(json.dumps(self.documents[url]).encode())
