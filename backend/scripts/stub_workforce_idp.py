"""Local stub workforce IdP — exercise the FULL operator OAuth path offline.

A minimal but spec-shaped OIDC provider (discovery, authorization endpoint,
token endpoint, JWKS; RS256; honors state/nonce/PKCE) so the operator
console's authorization-code flow and the operator API's id_token
verification can be tested end-to-end WITHOUT Google Workspace credentials.

    uv run python scripts/stub_workforce_idp.py  # serves http://127.0.0.1:8110

Wire-up (see console/.env.example): point OPERATOR_OIDC_ISSUER on BOTH apps
at http://127.0.0.1:8110 with any matching client id. Plain-http issuers are
accepted by the backend only on loopback and only outside production
(app/core/security.py) — this stub cannot be used against a deployed
environment, by construction.

The consent page shows an email box (default eric@aequoros.com) instead of
real authentication — it is a test double for the IdP, not an identity
system. NEVER deploy it.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
import uuid
from typing import Any

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

HOST, PORT = "127.0.0.1", 8110
ISSUER = f"http://{HOST}:{PORT}"
DEFAULT_EMAIL = "eric@aequoros.com"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_kid = uuid.uuid4().hex
# code -> {client_id, redirect_uri, nonce, code_challenge, email}
_codes: dict[str, dict[str, Any]] = {}

app = FastAPI(title="stub-workforce-idp")


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@app.get("/.well-known/openid-configuration")
def discovery() -> JSONResponse:
    return JSONResponse(
        {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
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


@app.get("/authorize", response_class=HTMLResponse)
def authorize(request: Request) -> HTMLResponse:
    q = dict(request.query_params)
    fields = "".join(
        f'<input type="hidden" name="{k}" value="{urllib.parse.quote(v, safe="")}">'
        for k, v in q.items()
    )
    return HTMLResponse(
        f"""<!doctype html><html><head><title>Stub Workforce IdP</title>
        <style>body{{font-family:system-ui;background:#0b1220;color:#e2e8f0;
        display:flex;align-items:center;justify-content:center;height:100vh}}
        form{{background:#101a2e;border:1px solid #24324d;border-radius:12px;
        padding:2rem;width:22rem}}h1{{font-size:1rem;margin:0 0 .25rem}}
        p{{font-size:.8rem;color:#94a3b8}}input[type=email]{{width:100%;
        padding:.5rem;border-radius:6px;border:1px solid #24324d;
        background:#0b1220;color:#e2e8f0;font-family:monospace}}
        button{{margin-top:1rem;width:100%;padding:.6rem;border:0;
        border-radius:6px;background:#3b82f6;color:#fff;font-weight:600}}
        </style></head><body>
        <form method="post" action="/authorize">
          <h1>Stub Workforce IdP</h1>
          <p>Local OAuth test double — no real authentication happens here.</p>
          {fields}
          <label><p>Sign in as</p>
          <input type="email" name="_email" value="{DEFAULT_EMAIL}"></label>
          <button type="submit">Continue</button>
        </form></body></html>"""
    )


@app.post("/authorize")
async def authorize_submit(request: Request) -> RedirectResponse:
    form = await request.form()
    data = {k: urllib.parse.unquote(str(v)) for k, v in form.items()}
    email = data.pop("_email", DEFAULT_EMAIL).strip() or DEFAULT_EMAIL
    code = secrets.token_urlsafe(24)
    _codes[code] = {
        "client_id": data.get("client_id", ""),
        "redirect_uri": data.get("redirect_uri", ""),
        "nonce": data.get("nonce"),
        "code_challenge": data.get("code_challenge"),
        "email": email,
    }
    sep = "&" if "?" in data.get("redirect_uri", "") else "?"
    target = (
        f"{data.get('redirect_uri', '')}{sep}"
        f"code={urllib.parse.quote(code)}&state={urllib.parse.quote(data.get('state', ''))}"
    )
    return RedirectResponse(target, status_code=302)


@app.post("/token")
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    grant_type = str(form.get("grant_type", ""))
    code = str(form.get("code", ""))
    redirect_uri = str(form.get("redirect_uri", ""))
    client_id = str(form.get("client_id", ""))
    code_verifier = form.get("code_verifier")

    record = _codes.pop(code, None)
    if grant_type != "authorization_code" or record is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if record["client_id"] != client_id or record["redirect_uri"] != redirect_uri:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if record["code_challenge"]:
        if not code_verifier:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        digest = hashlib.sha256(str(code_verifier).encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if challenge != record["code_challenge"]:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": client_id,
        "sub": hashlib.sha256(record["email"].encode()).hexdigest()[:16],
        "email": record["email"],
        "email_verified": True,
        "iat": now,
        "exp": now + 3600,
    }
    if record["nonce"]:
        claims["nonce"] = record["nonce"]
    id_token = jwt.encode(claims, _key, algorithm="RS256", headers={"kid": _kid})
    return JSONResponse(
        {"access_token": secrets.token_urlsafe(16), "token_type": "Bearer", "id_token": id_token}
    )


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
