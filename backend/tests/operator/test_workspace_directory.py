"""Directory protocol behavior at a fake HTTP boundary; no Cloud credentials needed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import JsonValue

from app.core.config import OperatorSettings
from app.operator.services.workforce import GoogleDirectoryGroups, GroupsUnavailable


def response(status: int, body: dict[str, JsonValue]) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()  # pyright: ignore[reportPrivateUsage]
    return result


@pytest.fixture
def directory_settings(tmp_path: Path) -> OperatorSettings:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    credential = tmp_path / "directory.json"
    credential.write_text(json.dumps({"client_email": "service@example.test", "private_key": pem}))
    return OperatorSettings(
        OPERATOR_GOOGLE_CREDENTIALS_FILE=str(credential),
        OPERATOR_GOOGLE_DELEGATED_ADMIN="admin@example.test",
    )


@pytest.mark.parametrize("member", [True, False])
def test_delegated_assertion_and_strict_membership_response(
    directory_settings: OperatorSettings, monkeypatch: pytest.MonkeyPatch, member: bool
) -> None:
    posts = 0

    def post(
        url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        nonlocal posts
        posts += 1
        assert url == "https://oauth2.googleapis.com/token"
        assert timeout == 5 and allow_redirects is False
        assert data["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
        path = directory_settings.google_credentials_file
        assert path is not None
        account = cast(dict[str, str], json.loads(Path(path).read_text()))
        private = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
        assert isinstance(private, rsa.RSAPrivateKey)
        claims = cast(
            dict[str, JsonValue],
            jwt.decode(
                data["assertion"],
                private.public_key(),
                algorithms=["RS256"],
                audience="https://oauth2.googleapis.com/token",
            ),
        )
        assert claims["iss"] == "service@example.test"
        assert claims["sub"] == "admin@example.test"
        assert (
            claims["scope"]
            == "https://www.googleapis.com/auth/admin.directory.group.member.readonly"
        )
        assert isinstance(claims["exp"], int) and isinstance(claims["iat"], int)
        assert claims["exp"] - claims["iat"] == 300
        return response(200, {"access_token": "directory-token"})

    def get(
        url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        assert url.endswith("/staff%40example.test/hasMember/person%2Bstaff%40example.test")
        assert headers == {"Authorization": "Bearer directory-token"}
        assert timeout == 5 and allow_redirects is False
        return response(200, {"isMember": member})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    directory = GoogleDirectoryGroups(directory_settings)
    for _ in range(2):
        assert directory.has_member("staff@example.test", "person+staff@example.test") is member
    assert posts == 1


@pytest.mark.parametrize("phase", ["token", "membership"])
@pytest.mark.parametrize(
    "status,body",
    [
        (403, {}),
        (404, {}),
        (429, {}),
        (500, {}),
        (302, {}),
        (200, {}),
        (200, {"isMember": "true", "access_token": True}),
    ],
)
def test_directory_failures_never_supply_membership(
    directory_settings: OperatorSettings,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    status: int,
    body: dict[str, JsonValue],
) -> None:
    def post(
        _url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = data, timeout, allow_redirects
        return response(status, body) if phase == "token" else response(200, {"access_token": "ok"})

    def get(
        _url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = headers, timeout, allow_redirects
        return response(status, body)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(GroupsUnavailable):
        _ = GoogleDirectoryGroups(directory_settings).has_member(
            "staff@example.test", "p@example.test"
        )


def test_unreadable_credentials_fail_closed(tmp_path: Path) -> None:
    settings = OperatorSettings(
        OPERATOR_GOOGLE_CREDENTIALS_FILE=str(tmp_path / "missing"),
        OPERATOR_GOOGLE_DELEGATED_ADMIN="admin@example.test",
    )
    with pytest.raises(GroupsUnavailable):
        _ = GoogleDirectoryGroups(settings).has_member("staff@example.test", "p@example.test")


@pytest.mark.parametrize("phase", ["token", "membership"])
def test_network_timeouts_fail_closed(
    directory_settings: OperatorSettings, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    def post(
        _url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = data, timeout, allow_redirects
        if phase == "token":
            raise requests.Timeout("provider details")
        return response(200, {"access_token": "ok"})

    def get(
        _url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = headers, timeout, allow_redirects
        raise requests.Timeout("provider details")

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(GroupsUnavailable):
        _ = GoogleDirectoryGroups(directory_settings).has_member(
            "staff@example.test", "p@example.test"
        )
