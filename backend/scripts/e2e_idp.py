"""Local OIDC issuer for the dashboard e2e stack — the tenant's "identity provider".

A small, spec-shaped OpenID Provider (discovery, authorization endpoint with a
real credential form, token endpoint, JWKS, userinfo; RS256; state, nonce,
PKCE S256, ``client_secret_basic`` and ``client_secret_post``) so the browser
SSO round trip — dashboard → IdP → NextAuth callback → ``/auth/sso`` exchange —
and the attestation step-up (``prompt=login`` + ``max_age=0`` → ``auth_time``)
can be proved end to end without a bank's Google Workspace / Entra / Okta.

Bound on loopback only. Plain-http issuers are accepted by the backend and by
the dashboard's egress guard ONLY on loopback and ONLY on an undeployed
environment (``app/core/security._is_loopback_issuer_allowed`` and
``dashboard/lib/outbound.ts``), so this issuer cannot be pointed at from a
deployment, by construction. Every credential below is a repository literal
precisely because none of it may ever be real.

    E2E_IDP_PORT=8120 uv run python scripts/e2e_idp.py

``playwright.config.ts`` starts it beside the API and the dashboard, and
``scripts/e2e_bootstrap.py`` registers it as the e2e tenant's SSO connection.
The three static accounts cover the three outcomes a bank's IdP can produce:
an invited officer (linked by email to a provisioned account), an employee the
bank never invited (verified identity, no account), and an identity from
outside the connection's allowed domains.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets
import time
import urllib.parse
import uuid
from typing import Any

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

HOST = "127.0.0.1"
DEFAULT_PORT = 8120

#: The relying party the e2e tenant's SSO connection names. Mirrored in
#: dashboard/e2e/support/sso.ts — the two must agree, and both say what they are.
CLIENT_ID = "aequoros-dashboard-e2e"
CLIENT_SECRET = "e2e-idp-client-secret-not-production-000"  # noqa: S105 - disposable fixture
#: One password for every static account; the journeys type it into the form.
PASSWORD = "e2e-idp-password-not-production-000"  # noqa: S105 - disposable fixture
#: email → display name. Which of these the tenant accepts is decided by the
#: backend (provisioned account + domain allow-list), never here.
ACCOUNTS: dict[str, str] = {
    "e2e.sso_analyst@aequoros.example": "E2E SSO Analyst",
    "e2e.sso_unprovisioned@aequoros.example": "E2E SSO Unprovisioned",
    "e2e.sso_outsider@contractor.example": "E2E SSO Outsider",
}
#: Authorization codes and access tokens live this long; a journey is far quicker.
_CODE_TTL_SECONDS = 120
_ID_TOKEN_TTL_SECONDS = 600

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_kid = uuid.uuid4().hex
# code -> the authorization request it answers, plus who authenticated and when
_codes: dict[str, dict[str, Any]] = {}
# access token -> the claims userinfo answers with
_access_tokens: dict[str, dict[str, Any]] = {}

app = FastAPI(title="aequoros-e2e-idp")


def issuer() -> str:
    return f"http://{HOST}:{int(os.environ.get('E2E_IDP_PORT', DEFAULT_PORT))}"


def subject_for(email: str) -> str:
    """Stable per account, so a re-sign-in resolves to the linked user."""
    return "e2e-" + hashlib.sha256(email.encode()).hexdigest()[:16]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_uint(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


@app.get("/.well-known/openid-configuration")
def discovery() -> JSONResponse:
    base = issuer()
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "userinfo_endpoint": f"{base}/userinfo",
            "jwks_uri": f"{base}/jwks",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
            ],
            "claims_supported": ["sub", "email", "email_verified", "name", "auth_time"],
            "code_challenge_methods_supported": ["S256"],
        }
    )


@app.get("/jwks")
def jwks() -> JSONResponse:
    numbers = _key.public_key().public_numbers()
    return JSONResponse(
        {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": _kid,
                    "n": _b64url_uint(numbers.n),
                    "e": _b64url_uint(numbers.e),
                }
            ]
        }
    )


def _error_redirect(redirect_uri: str, state: str | None, error: str) -> RedirectResponse:
    params = {"error": error}
    if state is not None:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urllib.parse.urlencode(params)}", status_code=302)


def _login_page(params: dict[str, str], *, failed: bool = False) -> HTMLResponse:
    """The credential form. Rendered for EVERY authorization request — this
    issuer keeps no session, so ``prompt=login`` / ``max_age=0`` are honoured
    trivially and ``auth_time`` is always the moment this form was submitted."""
    fields = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in params.items()
    )
    notice = '<p role="alert" class="error">Wrong email or password.</p>' if failed else ""
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>E2E Identity Provider</title>
<style>
body{{font-family:system-ui;background:#0b1220;color:#e2e8f0;display:flex;
align-items:center;justify-content:center;min-height:100vh;margin:0}}
form{{background:#101a2e;border:1px solid #24324d;border-radius:12px;padding:2rem;width:22rem}}
h1{{font-size:1rem;margin:0 0 .25rem}}p{{font-size:.8rem;color:#94a3b8}}
label{{display:block;margin-top:.75rem;font-size:.8rem}}
input[type=email],input[type=password]{{width:100%;box-sizing:border-box;margin-top:.25rem;
padding:.5rem;border-radius:6px;border:1px solid #24324d;background:#0b1220;color:#e2e8f0}}
button{{margin-top:1rem;width:100%;padding:.6rem;border:0;border-radius:6px;
background:#3b82f6;color:#fff;font-weight:600}}.error{{color:#fca5a5}}
</style></head><body>
<form method="post" action="/authorize">
  <h1>E2E Identity Provider</h1>
  <p>Local OpenID Connect test double for the AequorOS e2e stack. Nothing here is real.</p>
  {fields}{notice}
  <label>Email
    <input type="email" name="_email" autocomplete="username" required></label>
  <label>Password
    <input type="password" name="_password" autocomplete="current-password" required></label>
  <button type="submit">Sign in</button>
</form></body></html>"""
    return HTMLResponse(body)


def _authorization_request_error(params: dict[str, str]) -> str | None:
    if params.get("client_id") != CLIENT_ID:
        return "unauthorized_client"
    if params.get("response_type") != "code":
        return "unsupported_response_type"
    if "openid" not in params.get("scope", "").split():
        return "invalid_scope"
    if params.get("code_challenge") and params.get("code_challenge_method", "S256") != "S256":
        return "invalid_request"
    return None


@app.get("/authorize")
def authorize(request: Request) -> Response:
    params = dict(request.query_params)
    redirect_uri = params.get("redirect_uri")
    if not redirect_uri:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    error = _authorization_request_error(params)
    if error:
        return _error_redirect(redirect_uri, params.get("state"), error)
    return _login_page(params)


@app.post("/authorize")
async def authorize_submit(request: Request) -> Response:
    form = await request.form()
    data = {k: str(v) for k, v in form.items()}
    email = data.pop("_email", "").strip().lower()
    password = data.pop("_password", "")
    redirect_uri = data.get("redirect_uri")
    if not redirect_uri:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    error = _authorization_request_error(data)
    if error:
        return _error_redirect(redirect_uri, data.get("state"), error)
    if email not in ACCOUNTS or not secrets.compare_digest(password, PASSWORD):
        return _login_page(data, failed=True)

    code = secrets.token_urlsafe(24)
    _codes[code] = {
        "redirect_uri": redirect_uri,
        "nonce": data.get("nonce"),
        "code_challenge": data.get("code_challenge"),
        "email": email,
        "auth_time": int(time.time()),
        "expires_at": time.time() + _CODE_TTL_SECONDS,
    }
    params = {"code": code}
    if data.get("state") is not None:
        params["state"] = data["state"]
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urllib.parse.urlencode(params)}", status_code=302)


def _client_authenticated(form: dict[str, str], authorization: str | None) -> bool:
    """``client_secret_basic`` (NextAuth's default) or ``client_secret_post``
    (the attestation step-up route); either proves the same static client."""
    if authorization and authorization.lower().startswith("basic "):
        try:
            raw = base64.b64decode(authorization[6:]).decode()
        except (ValueError, UnicodeDecodeError):
            return False
        client_id, _, secret = raw.partition(":")
        client_id = urllib.parse.unquote(client_id)
        secret = urllib.parse.unquote(secret)
    else:
        client_id = form.get("client_id", "")
        secret = form.get("client_secret", "")
    return client_id == CLIENT_ID and secrets.compare_digest(secret, CLIENT_SECRET)


def _pkce_verified(record: dict[str, Any], code_verifier: str | None) -> bool:
    challenge = record["code_challenge"]
    if not challenge:
        return True
    if not code_verifier:
        return False
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return secrets.compare_digest(_b64url(digest), challenge)


@app.post("/token")
async def token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not _client_authenticated(form, authorization):
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    record = _codes.pop(form.get("code", ""), None)
    if form.get("grant_type") != "authorization_code" or record is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if record["expires_at"] < time.time():
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if form.get("redirect_uri") != record["redirect_uri"]:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if not _pkce_verified(record, form.get("code_verifier")):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    now = int(time.time())
    email = record["email"]
    claims: dict[str, Any] = {
        "iss": issuer(),
        "aud": CLIENT_ID,
        "sub": subject_for(email),
        "email": email,
        "email_verified": True,
        "name": ACCOUNTS[email],
        "iat": now,
        "exp": now + _ID_TOKEN_TTL_SECONDS,
        # Freshness evidence: the step-up path refuses a token without it.
        "auth_time": record["auth_time"],
    }
    if record["nonce"]:
        claims["nonce"] = record["nonce"]
    access_token = secrets.token_urlsafe(24)
    _access_tokens[access_token] = {
        k: v for k, v in claims.items() if k in {"sub", "email", "email_verified", "name"}
    }
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": _ID_TOKEN_TTL_SECONDS,
            "id_token": jwt.encode(claims, _key, algorithm="RS256", headers={"kid": _kid}),
        }
    )


@app.get("/userinfo")
def userinfo(authorization: str | None = Header(default=None)) -> JSONResponse:
    if not authorization or not authorization.lower().startswith("bearer "):
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    claims = _access_tokens.get(authorization[7:])
    if claims is None:
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    return JSONResponse(claims)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=HOST,
        port=int(os.environ.get("E2E_IDP_PORT", DEFAULT_PORT)),
        log_level="warning",
    )
