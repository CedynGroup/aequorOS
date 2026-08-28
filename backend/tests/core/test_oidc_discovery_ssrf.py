"""SSRF defence on the OIDC discovery / JWKS path — ``app.core.security``.

Residual of audit finding P0-6. ``_discover_jwks_uri`` fetched the per-org SSO
``issuer`` behind nothing but an ``https://`` prefix test, so
``https://169.254.169.254/.well-known/openid-configuration`` passed — and the
``jwks_uri`` that document names is a SECOND server-controlled URL handed
straight to ``PyJWKClient``. The issuer is admin-settable per organization
(``SsoConnectionUpdateRequest.issuer``), which made this tenant-reachable.

Nothing here touches the network. DNS is stubbed at
``app.core.outbound.resolve_host`` (the same seam ``tests/core/test_outbound.py``
uses) and the HTTP fetches are stubbed at ``app.core.security._oidc_opener``, so
every case — "resolves to a private address" included — is deterministic and
offline.
"""

from __future__ import annotations

import io
import json
import urllib.request
from http.client import HTTPMessage
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core import security
from app.core.config import get_settings
from app.core.outbound import get_outbound_settings
from app.schemas.auth import SsoConnectionUpdateRequest
from tests.factories.outbound import PUBLIC_IP, stub_dns, stub_public_dns

if TYPE_CHECKING:
    from collections.abc import Iterator

_ISSUER = "https://idp.example.test"
_DISCOVERY = f"{_ISSUER}/.well-known/openid-configuration"
_JWKS_URI = f"{_ISSUER}/jwks"
_AUDIENCE = "aequoros-dashboard"
_KID = "test-signing-key"

# The metadata address in the finding, plus the notation forms an issuer field
# could carry it in.
BLOCKED_ISSUERS = [
    "https://169.254.169.254",
    "https://localhost",
    "https://127.0.0.1",
    "https://127.0.0.1:8443/realms/bank",
    "https://[::1]",
    "https://10.1.2.3",
    "https://192.168.1.5/realms/bank",
    "https://[::ffff:127.0.0.1]",
    "https://[fd00:ec2::254]",
    "https://172.16.4.4",
    "https://idp.localhost",
]


# Bound at import so teardown still finds the real lru_cache wrappers after a
# test has monkeypatched the module attribute.
_CACHED = (security._discover_jwks_uri, security._jwks_client, security._oidc_opener)


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    """Every entry point here is ``lru_cache``d; a leaked entry would let one
    test's stub answer another's question. Also pins the egress escape hatch to
    its default (off)."""
    for cached in _CACHED:
        cached.cache_clear()
    get_outbound_settings.cache_clear()
    yield
    for cached in _CACHED:
        cached.cache_clear()
    get_outbound_settings.cache_clear()
    get_settings.cache_clear()


class _FakeOpener:
    """A stand-in for the guarded ``urllib`` opener: serves a fixed URL table.

    Records what was opened so a test can prove a fetch never happened — the
    point of the guard is that a blocked destination is refused *before* the
    socket, not after.
    """

    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self.documents = documents
        self.opened: list[str] = []

    def open(self, request: Any, timeout: float | None = None) -> io.BytesIO:  # noqa: ARG002
        url = getattr(request, "full_url", request)
        self.opened.append(str(url))
        if str(url) not in self.documents:
            msg = f"fake opener: nothing served at {url!r}"
            raise OSError(msg)
        return io.BytesIO(json.dumps(self.documents[str(url)]).encode())


def _serve(monkeypatch: pytest.MonkeyPatch, documents: dict[str, dict[str, Any]]) -> _FakeOpener:
    opener = _FakeOpener(documents)
    monkeypatch.setattr(security, "_oidc_opener", lambda: opener)
    return opener


# --- the issuer (first, tenant-controlled URL) -------------------------------


@pytest.mark.parametrize("issuer", BLOCKED_ISSUERS)
def test_blocked_issuer_literal_is_refused_before_any_fetch(
    issuer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = _serve(monkeypatch, {})
    with pytest.raises(security.TokenInvalidError):
        security._discover_jwks_uri(issuer)
    assert opener.opened == []


def test_issuer_hostname_resolving_to_cloud_metadata_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The dangerous shape: a perfectly ordinary public-looking name whose only
    # A record is the instance-metadata service.
    stub_dns(monkeypatch, {"idp.attacker.example": ("169.254.169.254",)})
    opener = _serve(monkeypatch, {})
    with pytest.raises(security.TokenInvalidError):
        security._discover_jwks_uri("https://idp.attacker.example")
    assert opener.opened == []


def test_issuer_hostname_resolving_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_dns(monkeypatch, {"idp.attacker.example": ("10.0.0.5",)})
    opener = _serve(monkeypatch, {})
    with pytest.raises(security.TokenInvalidError):
        security._discover_jwks_uri("https://idp.attacker.example")
    assert opener.opened == []


def test_issuer_is_refused_when_any_resolved_address_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One public and one loopback record: the guard must judge every answer.
    stub_dns(monkeypatch, {"idp.attacker.example": (PUBLIC_IP, "127.0.0.1")})
    opener = _serve(monkeypatch, {})
    with pytest.raises(security.TokenInvalidError):
        security._discover_jwks_uri("https://idp.attacker.example")
    assert opener.opened == []


def test_unresolvable_issuer_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_dns(monkeypatch, {})
    opener = _serve(monkeypatch, {_DISCOVERY: {"jwks_uri": _JWKS_URI}})
    with pytest.raises(security.TokenInvalidError):
        security._discover_jwks_uri(_ISSUER)
    assert opener.opened == []


def test_refusal_does_not_leak_the_resolved_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The block reason and the resolved address are log-only. A caller-visible
    message carrying them would turn the SSO endpoint into an internal-DNS
    oracle for whoever can set the issuer."""
    stub_dns(monkeypatch, {"idp.attacker.example": ("10.0.0.5",)})
    _serve(monkeypatch, {})
    with pytest.raises(security.TokenInvalidError) as excinfo:
        security._discover_jwks_uri("https://idp.attacker.example")
    rendered = str(excinfo.value)
    assert "10.0.0.5" not in rendered
    assert "private" not in rendered
    assert "resolved" not in rendered


def test_a_genuine_public_issuer_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_public_dns(monkeypatch, "idp.example.test")
    opener = _serve(monkeypatch, {_DISCOVERY: {"jwks_uri": _JWKS_URI}})
    assert security._discover_jwks_uri(_ISSUER) == _JWKS_URI
    assert opener.opened == [_DISCOVERY]


# --- the jwks_uri (second, separately server-controlled URL) -----------------


@pytest.mark.parametrize(
    "malicious",
    [
        "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "https://127.0.0.1/jwks",
        "https://[::1]/jwks",
        "https://10.0.0.5/jwks",
        "https://192.168.1.5/jwks",
        "https://[::ffff:127.0.0.1]/jwks",
    ],
)
def test_malicious_jwks_uri_in_an_otherwise_public_document_is_refused(
    malicious: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issuer passing says nothing about the jwks_uri: a public IdP (or one
    that merely looks public) names its own key endpoint, and that name is
    handed to PyJWKClient. It is checked separately, on its own merits."""
    stub_public_dns(monkeypatch, "idp.example.test")
    _serve(monkeypatch, {_DISCOVERY: {"jwks_uri": malicious}})
    with pytest.raises(security.TokenInvalidError):
        security._discover_jwks_uri(_ISSUER)


def test_jwks_uri_hostname_resolving_to_metadata_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_dns(
        monkeypatch,
        {"idp.example.test": (PUBLIC_IP,), "keys.attacker.example": ("169.254.169.254",)},
    )
    _serve(monkeypatch, {_DISCOVERY: {"jwks_uri": "https://keys.attacker.example/jwks"}})
    with pytest.raises(security.TokenInvalidError):
        security._discover_jwks_uri(_ISSUER)


def test_a_public_jwks_uri_on_another_host_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Legitimate and common (Google: accounts.google.com -> www.googleapis.com).
    stub_public_dns(monkeypatch, "idp.example.test", "keys.example.test")
    _serve(monkeypatch, {_DISCOVERY: {"jwks_uri": "https://keys.example.test/jwks"}})
    assert security._discover_jwks_uri(_ISSUER) == "https://keys.example.test/jwks"


# --- redirects (both fetches; urllib follows them by default) ---------------


def _redirect_handler() -> urllib.request.HTTPRedirectHandler:
    opener = security._oidc_opener()
    return next(
        handler
        for handler in opener.handlers
        if isinstance(handler, urllib.request.HTTPRedirectHandler)
    )


def test_redirect_hop_to_a_blocked_destination_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permitted issuer answering ``302 Location: http://169.254.169.254/``
    would otherwise walk straight past the check on the original URL."""
    stub_public_dns(monkeypatch, "idp.example.test")
    request = urllib.request.Request(_DISCOVERY)  # noqa: S310 - test fixture, never opened
    with pytest.raises(security.TokenInvalidError):
        _redirect_handler().redirect_request(
            request, io.BytesIO(b""), 302, "Found", HTTPMessage(), "http://169.254.169.254/"
        )


def test_redirect_hop_to_a_public_destination_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_public_dns(monkeypatch, "idp.example.test", "keys.example.test")
    request = urllib.request.Request(_DISCOVERY)  # noqa: S310 - test fixture, never opened
    redirected = _redirect_handler().redirect_request(
        request, io.BytesIO(b""), 302, "Found", HTTPMessage(), "https://keys.example.test/jwks"
    )
    assert redirected is not None
    assert redirected.full_url == "https://keys.example.test/jwks"


def test_jwks_client_fetches_through_the_guarded_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyJWKClient's own ``fetch_data`` uses urllib's default opener, which
    follows redirects unvalidated. Pin that our client does not."""
    opener = _serve(monkeypatch, {_JWKS_URI: {"keys": []}})
    client = security._jwks_client(_JWKS_URI)
    assert client.fetch_data() == {"keys": []}
    assert opener.opened == [_JWKS_URI]


# --- the non-production loopback carve-out (local development) --------------


def test_loopback_http_issuer_still_works_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-existing dev exemption: a stub IdP on plain http loopback. It is
    ordered BEFORE the egress guard, so local development keeps working — and
    no DNS is consulted (the stub table below is empty)."""
    stub_dns(monkeypatch, {})
    issuer = "http://127.0.0.1:8110"
    _serve(
        monkeypatch,
        {f"{issuer}/.well-known/openid-configuration": {"jwks_uri": f"{issuer}/jwks"}},
    )
    assert security._discover_jwks_uri(issuer) == f"{issuer}/jwks"


def test_loopback_http_issuer_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    _serve(monkeypatch, {})
    with pytest.raises(security.TokenInvalidError, match="must be https"):
        security._discover_jwks_uri("http://127.0.0.1:8110")


def test_https_loopback_issuer_is_refused_even_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The carve-out is plain-http-loopback only. ``https://127.0.0.1`` sailed
    past the old prefix test; it is a blocked destination like any other."""
    opener = _serve(monkeypatch, {})
    with pytest.raises(security.TokenInvalidError):
        security._discover_jwks_uri("https://127.0.0.1:8443")
    assert opener.opened == []


# --- the schema boundary ----------------------------------------------------


@pytest.mark.parametrize("issuer", BLOCKED_ISSUERS)
def test_sso_connection_update_rejects_a_blocked_issuer(issuer: str) -> None:
    with pytest.raises(ValueError, match="permitted destination|must use one of"):
        SsoConnectionUpdateRequest(issuer=issuer, client_id="client")


def test_sso_connection_update_accepts_a_public_issuer() -> None:
    payload = SsoConnectionUpdateRequest(issuer="https://accounts.google.com", client_id="client")
    assert payload.issuer == "https://accounts.google.com"


def test_sso_connection_update_keeps_the_loopback_dev_carve_out() -> None:
    payload = SsoConnectionUpdateRequest(issuer="http://127.0.0.1:8110", client_id="client")
    assert payload.issuer == "http://127.0.0.1:8110"


# --- regression: the whole verification path still works --------------------


def _rsa_idp() -> tuple[Any, dict[str, Any]]:
    """A real RS256 signing key plus the JWKS document that publishes it."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": _KID, "use": "sig", "alg": "RS256"})
    return private_key, {"keys": [jwk]}


def test_a_normal_sso_verification_still_succeeds_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery -> jwks_uri -> JWKS fetch -> RS256 signature/iss/aud/exp, with
    a genuine key and a genuine token. The guard sits on every hop of this path,
    so this is the test that proves it lets the real flow through."""
    private_key, jwks = _rsa_idp()
    now = int(security.utc_now().timestamp())
    id_token = jwt.encode(
        {
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "sub": "idp-subject-1",
            "email": "treasury@bank.example",
            "email_verified": True,
            "iat": now,
            "exp": now + 600,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": _KID},
    )
    stub_public_dns(monkeypatch, "idp.example.test")
    opener = _serve(monkeypatch, {_DISCOVERY: {"jwks_uri": _JWKS_URI}, _JWKS_URI: jwks})

    claims = security.verify_oidc_id_token(id_token, issuer=_ISSUER, audience=_AUDIENCE)

    assert claims["sub"] == "idp-subject-1"
    assert claims["email"] == "treasury@bank.example"
    assert opener.opened == [_DISCOVERY, _JWKS_URI]


@pytest.mark.parametrize(
    ("claims", "accepted"),
    [
        ({"aud": _AUDIENCE}, True),
        ({"aud": [_AUDIENCE]}, True),
        ({"aud": ["another-client", _AUDIENCE], "azp": _AUDIENCE}, True),
        ({"aud": ["another-client", _AUDIENCE]}, False),
        ({"aud": ["another-client", _AUDIENCE], "azp": "another-client"}, False),
        ({"aud": ["another-client", _AUDIENCE], "azp": None}, False),
        ({"aud": _AUDIENCE, "azp": "another-client"}, False),
        ({"aud": _AUDIENCE, "azp": None}, False),
    ],
)
def test_oidc_authorized_party_rules(claims: dict[str, Any], accepted: bool) -> None:
    if accepted:
        security.validate_oidc_authorized_party(claims, audience=_AUDIENCE)
        return

    with pytest.raises(security.TokenInvalidError):
        security.validate_oidc_authorized_party(claims, audience=_AUDIENCE)


def test_a_blocked_issuer_surfaces_as_an_auth_failure_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``OutboundTargetBlocked`` is a ``ValueError``; if it escaped, the SSO
    login route would 500 instead of 401. Every caller catches ``AuthError``,
    so the guard must raise inside that hierarchy."""
    stub_dns(monkeypatch, {"idp.attacker.example": ("169.254.169.254",)})
    _serve(monkeypatch, {})
    private_key, _jwks = _rsa_idp()
    now = int(security.utc_now().timestamp())
    id_token = jwt.encode(
        {
            "iss": "https://idp.attacker.example",
            "aud": _AUDIENCE,
            "sub": "x",
            "iat": now,
            "exp": now + 600,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": _KID},
    )
    with pytest.raises(security.AuthError):
        security.verify_oidc_id_token(
            id_token, issuer="https://idp.attacker.example", audience=_AUDIENCE
        )
