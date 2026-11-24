"""The gateway's own OpenID provider for supervisors (``/idp``, SPEC Part 4).

Authorization code flow with S256 PKCE for the portal, a public client: the portal
redirects here, the supervisor signs in with their email and password, and the portal
exchanges the code for an ID token signed with the provider key. The portal never sees
a password. Mounted only when no external provider is federated
(``Settings.uses_builtin_idp``).

The sign-in form carries a double-submit token (a random value in an HttpOnly cookie and
the form), so a page elsewhere cannot sign a supervisor into an account of its choosing.
"""

from __future__ import annotations

import hashlib
import html
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Cookie, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.clock import utc
from app.core.context import AppContext, app_state, get_ctx
from app.core.encoding import b64url_encode
from app.core.errors import ApiError
from app.db.models import StaffAccount
from app.idp.accounts import SignInResult, sign_in
from app.idp.tokens import ID_TOKEN_SECONDS, AccountIdentity, mint_id_token
from app.keys.provider import jwks_document

router = APIRouter(prefix="/idp", include_in_schema=False)

CODE_TTL_SECONDS = 120
CSRF_COOKIE = "aequoros_idp_csrf"


@dataclass(frozen=True)
class PendingCode:
    subject: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    nonce: str | None
    expires: float


class IdpState:
    """Authorization codes awaiting exchange: single use, two minutes."""

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._codes: dict[str, PendingCode] = {}

    def issue(self, pending: PendingCode) -> str:
        code = secrets.token_urlsafe(24)
        with self._lock:
            now = time.monotonic()
            self._codes = {k: v for k, v in self._codes.items() if v.expires > now}
            self._codes[code] = pending
        return code

    def redeem(self, code: str) -> PendingCode | None:
        with self._lock:
            pending = self._codes.pop(code, None)
        if pending is None or pending.expires < time.monotonic():
            return None
        return pending


def _state(request: Request) -> IdpState:
    state = app_state(request, "idp")
    if not isinstance(state, IdpState):
        raise ApiError("not_found")
    return state


def _check_client(ctx: AppContext, client_id: str, redirect_uri: str) -> None:
    if client_id != ctx.settings.oidc_audience:
        raise ApiError("invalid_request", "Unknown client_id.")
    if not any(redirect_uri.startswith(prefix) for prefix in ctx.settings.redirect_prefixes()):
        raise ApiError("invalid_request", "redirect_uri is not registered with this provider.")


@router.get("/.well-known/openid-configuration")
def discovery(request: Request) -> JSONResponse:
    issuer = get_ctx(request).settings.idp_issuer
    return JSONResponse(
        {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/authorize",
            "token_endpoint": issuer + "/token",
            "jwks_uri": issuer + "/jwks",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["ES256"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["openid", "profile", "email", "groups"],
            "claims_supported": [
                "sub",
                "name",
                "given_name",
                "family_name",
                "email",
                "groups",
                "amr",
            ],
        }
    )


@router.get("/jwks")
def jwks(request: Request) -> JSONResponse:
    return JSONResponse(jwks_document(get_ctx(request).keys.public_keys("devidp")))


_STYLE = """
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;background:#F4F6F8;
color:#0E1726;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:48px 16px}
main{width:100%;max-width:420px;background:#FFFFFF;border:1px solid #DDE2E8;border-radius:14px;
padding:36px 32px;display:flex;flex-direction:column;gap:22px}
.brand{display:flex;align-items:center;gap:12px}
.mark{width:40px;height:40px;border-radius:9px;background:#2B54D6;color:#fff;font-weight:700;
display:flex;align-items:center;justify-content:center;font-size:18px}
.brand strong{display:block;font-size:15px}.brand span{font-size:13px;color:#5B6676}
h1{font-size:24px;font-weight:600;margin:0}
p{margin:0;font-size:14px;line-height:1.5;color:#4B5565}
form{display:flex;flex-direction:column;gap:16px}
label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600;color:#344054}
input[type=email],input[type=password]{min-height:44px;border:1px solid #C9D1DB;border-radius:8px;
padding:0 12px;font:inherit;font-size:15px;color:#0E1726;background:#fff}
input:focus{outline:2px solid #1F4FD1;outline-offset:1px;border-color:#1F4FD1}
button{min-height:48px;border:0;border-radius:8px;background:#1F4FD1;color:#fff;font:inherit;
font-size:15px;font-weight:600;cursor:pointer}
button:hover,button:focus{background:#1A43B3}
.alert{border-radius:8px;background:#FDECEC;color:#B42318;padding:12px 14px;font-size:14px;font-weight:500}
.fine{font-size:12px;color:#5B6676;border-top:1px solid #EEF1F4;padding-top:16px}
"""


def _page(hidden: dict[str, str], csrf: str, *, email: str = "", error: str | None = None) -> str:
    fields = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in hidden.items()
    )
    alert = f'<p class="alert" role="alert">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Sign in · AequorOS Portal</title><style>{_STYLE}</style></head><body><main>
<div class="brand"><div class="mark" aria-hidden="true">A</div>
<div><strong>AequorOS Portal</strong><span>Banking Supervision</span></div></div>
<div><h1>Sign in</h1></div>
{alert}
<form method="post" action="authorize" autocomplete="on">{fields}
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
<label>Email<input type="email" name="email" value="{html.escape(email)}" required
autocomplete="username" autofocus maxlength="200"></label>
<label>Password<input type="password" name="password" required
autocomplete="current-password" maxlength="1024"></label>
<button type="submit">Sign in</button>
</form>
<p class="fine">Restricted system. Every sign-in and action is recorded in a tamper-evident
audit log.</p>
</main></body></html>"""


def _origin(uri: str) -> str:
    parts = urlsplit(uri)
    return f"{parts.scheme}://{parts.netloc}"


def _render(
    hidden: dict[str, str], *, email: str = "", error: str | None = None, status: int = 200
) -> HTMLResponse:
    csrf = secrets.token_urlsafe(24)
    response = HTMLResponse(_page(hidden, csrf, email=email, error=error), status_code=status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    # A form's redirect is held to form-action as well, so the registered return origin
    # (already checked against the provider's redirect prefixes) is admitted beside 'self'.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; "
        f"form-action 'self' {_origin(hidden['redirect_uri'])}; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    response.set_cookie(CSRF_COOKIE, csrf, max_age=1800, httponly=True, samesite="lax", path="/idp")
    return response


@router.get("/authorize", response_class=HTMLResponse)
def authorize_page(
    request: Request,
    client_id: Annotated[str, Query()],
    redirect_uri: Annotated[str, Query()],
    code_challenge: Annotated[str, Query()],
    response_type: Annotated[str, Query()] = "code",
    code_challenge_method: Annotated[str, Query()] = "S256",
    state: Annotated[str, Query()] = "",
    nonce: Annotated[str, Query()] = "",
    scope: Annotated[str, Query()] = "openid",
) -> HTMLResponse:
    ctx = get_ctx(request)
    _check_client(ctx, client_id, redirect_uri)
    if response_type != "code" or code_challenge_method != "S256" or not code_challenge:
        raise ApiError("invalid_request", "Only response_type=code with S256 PKCE is supported.")
    return _render(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "state": state,
            "nonce": nonce,
            "scope": scope,
        }
    )


def _refusal(result: SignInResult) -> str:
    """What the supervisor is told. Read inside the session: it touches the account."""
    if result.outcome == "locked" and result.account is not None:
        until = result.account.locked_until
        when = f" after {utc(until):%H:%M} GMT" if until is not None else " later"
        return (
            "This account is locked after repeated failed sign-ins. Try again"
            f"{when}, or ask your administrator to unlock it."
        )
    if result.outcome == "disabled":
        return "This account is disabled. Ask your administrator."
    return "The email or password is incorrect."


@router.post("/authorize", response_model=None)
def authorize_submit(
    request: Request,
    email: Annotated[str, Form(max_length=200)],
    password: Annotated[str, Form(max_length=1024)],
    csrf: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    redirect_uri: Annotated[str, Form()],
    code_challenge: Annotated[str, Form()],
    state: Annotated[str, Form()] = "",
    nonce: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "openid",
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
) -> RedirectResponse | HTMLResponse:
    ctx = get_ctx(request)
    _check_client(ctx, client_id, redirect_uri)
    if not code_challenge:
        raise ApiError("invalid_request", "PKCE code_challenge is required.")
    hidden = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "state": state,
        "nonce": nonce,
        "scope": scope,
    }
    if not csrf_cookie or not secrets.compare_digest(csrf, csrf_cookie):
        return _render(
            hidden,
            email=email,
            error="Your sign-in page expired. Enter your details again.",
            status=400,
        )
    with ctx.db.session() as session:
        result = sign_in(session, ctx, email, password)
        signed_in = result.outcome == "signed_in" and result.account is not None
        subject = result.account.subject if signed_in and result.account is not None else None
        refusal = None if signed_in else _refusal(result)
    if subject is None:
        return _render(hidden, email=email, error=refusal, status=401)
    code = _state(request).issue(
        PendingCode(
            subject=subject,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            nonce=nonce or None,
            expires=time.monotonic() + CODE_TTL_SECONDS,
        )
    )
    params = {"code": code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    response = RedirectResponse(redirect_uri + separator + urlencode(params), status_code=303)
    response.delete_cookie(CSRF_COOKIE, path="/idp")
    return response


def _active_account(session: Session, subject: str) -> StaffAccount | None:
    account = session.get(StaffAccount, subject)
    return account if account is not None and account.status == "active" else None


@router.post("/token")
def token(
    request: Request,
    grant_type: Annotated[str, Form()],
    code: Annotated[str, Form()],
    redirect_uri: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    code_verifier: Annotated[str, Form()],
) -> JSONResponse:
    ctx = get_ctx(request)
    if grant_type != "authorization_code":
        raise ApiError("invalid_request", "Only the authorization_code grant is supported.")
    pending = _state(request).redeem(code)
    if pending is None:
        raise ApiError("invalid_request", "The code is unknown, used or expired.")
    if pending.client_id != client_id or pending.redirect_uri != redirect_uri:
        raise ApiError("invalid_request", "client_id or redirect_uri does not match the code.")
    challenge = b64url_encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
    if not secrets.compare_digest(challenge, pending.code_challenge):
        raise ApiError("invalid_request", "PKCE verification failed.")
    with ctx.db.session() as session:
        account = _active_account(session, pending.subject)
        if account is None:
            raise ApiError("invalid_request", "The account is no longer active.")
        who = AccountIdentity(
            subject=account.subject,
            name=account.name,
            given_name=account.given_name,
            family_name=account.family_name,
            email=account.email,
        )
        id_token = mint_id_token(
            ctx, who, groups=list(account.groups), mfa=False, nonce=pending.nonce
        )
    return JSONResponse(
        {
            "access_token": id_token,
            "id_token": id_token,
            "token_type": "Bearer",
            "expires_in": ID_TOKEN_SECONDS,
            "scope": "openid profile email groups",
        },
        headers={"Cache-Control": "no-store"},
    )
