"""Google-shaped signed tokens cross the real verifier and operator boundary."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import JsonValue, ValidationError
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import OperatorSettings, get_operator_settings, get_settings
from app.operator.services import workforce
from tests.operator.conftest import operator_headers
from tests.operator.test_operator_auth import make_operator
from tests.operator.test_workforce import configure

SUBJECT = "110169484474386276334"
ISSUER = "https://accounts.google.com"


class MemoryDirectory:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, JsonValue]] = {
            SUBJECT: {
                "suspended": False,
                "isEnrolledIn2Sv": True,
                "isEnforcedIn2Sv": True,
            }
        }
        self.members = {(SUBJECT, "staff@aequoros.com")}
        self.unavailable = False
        self.status_calls: list[str] = []
        self.membership_calls: list[tuple[str, str]] = []

    def user_status(self, subject: str) -> workforce.WorkspaceUserStatus:
        self.status_calls.append(subject)
        if self.unavailable or subject not in self.users:
            raise workforce.DirectoryUnavailable
        try:
            return workforce.WorkspaceUserStatus.model_validate(self.users[subject])
        except ValidationError as exc:
            raise workforce.DirectoryUnavailable from exc

    def has_member(self, group: str, subject: str) -> bool:
        self.membership_calls.append((subject, group))
        if self.unavailable:
            raise workforce.DirectoryUnavailable
        return (subject, group) in self.members


@dataclass
class GoogleTokens:
    key: rsa.RSAPrivateKey
    directory: MemoryDirectory

    def mint(self, changes: dict[str, JsonValue] | None = None, *, bad_key: bool = False) -> str:
        now = int(time.time())
        claims: dict[str, JsonValue] = {
            "iss": ISSUER,
            "aud": "console-client-id",
            "sub": SUBJECT,
            "email": "ama@aequoros.com",
            "email_verified": True,
            "hd": "aequoros.com",
            "iat": now,
            "exp": now + 3600,
        }
        for name, value in (changes or {}).items():
            if value is None:
                _ = claims.pop(name, None)
            else:
                claims[name] = value
        key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048) if bad_key else self.key
        )
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "google-fixture"})


@pytest.fixture
def google_tokens(operator_db: Session, monkeypatch: pytest.MonkeyPatch) -> GoogleTokens:
    make_operator(operator_db, role="operator_admin")
    configure(
        monkeypatch,
        OIDC_ISSUER=ISSUER,
        OIDC_CLIENT_ID="console-client-id",
        OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]',
        GOOGLE_REQUIRE_2SV="true",
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = cast("dict[str, JsonValue]", RSAAlgorithm.to_jwk(key.public_key(), as_dict=True))
    jwk.update(kid="google-fixture", use="sig", alg="RS256")
    jwks: dict[str, JsonValue] = {"keys": [jwk]}
    client = jwt.PyJWKClient("https://google-fixture.example/jwks")
    # Only the external metadata/key transport is replaced. PyJWKClient key
    # selection and the application's signature/issuer/audience checks execute.
    monkeypatch.setattr(client, "fetch_data", lambda: jwks)
    directory = MemoryDirectory()

    def discover(_issuer: str) -> str:
        return "https://google-fixture.example/jwks"

    def keys(_uri: str) -> jwt.PyJWKClient:
        return client

    def lookup(_settings: OperatorSettings) -> workforce.DirectoryLookup:
        return directory

    monkeypatch.setattr(security, "_discover_jwks_uri", discover)
    monkeypatch.setattr(security, "_jwks_client", keys)
    monkeypatch.setattr(workforce, "GoogleDirectory", lookup)
    return GoogleTokens(key, directory)


def test_signed_google_token_needs_no_optional_assurance_claims(
    operator_client: TestClient, google_tokens: GoogleTokens
) -> None:
    token = google_tokens.mint()
    headers = operator_headers(token)
    assert operator_client.get("/operator/v1/operators", headers=headers).status_code == 200
    assert (
        operator_client.post(
            "/operator/v1/operators/ama@aequoros.com/reset-password", headers=headers
        ).status_code
        == 200
    )
    assert google_tokens.directory.status_calls == [SUBJECT, SUBJECT]


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": 1},
        {"aud": "other-client"},
        {"iss": "https://other-issuer.example"},
        {"hd": None},
        {"hd": "consumer.example"},
        {"email_verified": False},
        {"email": "ama@consumer.example"},
    ],
    ids=["expired", "audience", "issuer", "missing-hd", "wrong-hd", "unverified", "email"],
)
def test_signed_google_token_refusals(
    operator_client: TestClient, google_tokens: GoogleTokens, changes: dict[str, JsonValue]
) -> None:
    assert (
        operator_client.get(
            "/operator/v1/operators", headers=operator_headers(google_tokens.mint(changes))
        ).status_code
        == 401
    )
    assert not google_tokens.directory.status_calls


def test_bad_google_signature_is_refused(
    operator_client: TestClient, google_tokens: GoogleTokens
) -> None:
    assert (
        operator_client.get(
            "/operator/v1/operators", headers=operator_headers(google_tokens.mint(bad_key=True))
        ).status_code
        == 401
    )
    assert not google_tokens.directory.status_calls


def test_signed_issue_time_caps_session(
    operator_client: TestClient, google_tokens: GoogleTokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, OIDC_SESSION_MAX_AGE_SECONDS="900")
    token = google_tokens.mint({"iat": int(time.time()) - 901})
    assert (
        operator_client.get("/operator/v1/operators", headers=operator_headers(token)).status_code
        == 401
    )
    assert (
        operator_client.get(
            "/operator/v1/operators", headers=operator_headers(google_tokens.mint())
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "failure",
    ["suspended", "nonmember", "unenrolled", "unenforced", "unavailable", "missing", "malformed"],
)
def test_live_directory_changes_refuse_the_same_signed_token(
    operator_client: TestClient, google_tokens: GoogleTokens, failure: str
) -> None:
    directory = google_tokens.directory
    token = google_tokens.mint()
    headers = operator_headers(token)
    path = "/operator/v1/operators"
    assert operator_client.get(path, headers=headers).status_code == 200
    match failure:
        case "suspended":
            directory.users[SUBJECT]["suspended"] = True
        case "nonmember":
            directory.members.clear()
        case "unenrolled":
            directory.users[SUBJECT]["isEnrolledIn2Sv"] = False
        case "unenforced":
            directory.users[SUBJECT]["isEnforcedIn2Sv"] = False
        case "unavailable":
            directory.unavailable = True
        case "missing":
            _ = directory.users[SUBJECT].pop("isEnrolledIn2Sv")
        case "malformed":
            directory.users[SUBJECT]["isEnforcedIn2Sv"] = "true"
        case _:
            pytest.fail("Unknown failure case")
    assert operator_client.get(path, headers=headers).status_code == 401
    assert directory.status_calls == [SUBJECT, SUBJECT]


@pytest.mark.parametrize("environment", ["local", "test", "staging", "production", "preview"])
def test_google_policy_test_mode_is_undeployed_only(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE", "true")
    get_settings.cache_clear()
    try:
        if environment in {"local", "test"}:
            settings = get_operator_settings()
            assert settings.uses_google_policy
        else:
            with pytest.raises(ValueError):
                _ = get_operator_settings()
    finally:
        get_settings.cache_clear()
        get_operator_settings.cache_clear()


def test_google_policy_can_check_a_mock_issuer(
    operator_client: TestClient, google_tokens: GoogleTokens, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, OIDC_ISSUER="https://mock.example", OIDC_GOOGLE_POLICY_TEST_MODE="true")
    token = google_tokens.mint({"iss": "https://mock.example"})
    assert (
        operator_client.get("/operator/v1/operators", headers=operator_headers(token)).status_code
        == 200
    )
    token = google_tokens.mint({"iss": "https://mock.example", "hd": None})
    assert (
        operator_client.get("/operator/v1/operators", headers=operator_headers(token)).status_code
        == 401
    )
