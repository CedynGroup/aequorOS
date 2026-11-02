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
from app.operator.services import workforce
from app.operator.services.workforce import GoogleDirectory, DirectoryUnavailable


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
    gets = 0

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
            == "https://www.googleapis.com/auth/admin.directory.group.member.readonly "
            "https://www.googleapis.com/auth/admin.directory.user.readonly"
        )
        assert isinstance(claims["exp"], int) and isinstance(claims["iat"], int)
        assert claims["exp"] - claims["iat"] == 300
        return response(200, {"access_token": "directory-token", "expires_in": 3600})

    def get(
        url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        nonlocal gets
        gets += 1
        assert url.endswith("/staff%40example.test/hasMember/person%2Bstaff%40example.test")
        assert headers == {"Authorization": "Bearer directory-token"}
        assert timeout == 5 and allow_redirects is False
        return response(200, {"isMember": member})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    for _ in range(2):
        assert (
            GoogleDirectory(directory_settings).has_member(
                "staff@example.test", "person+staff@example.test"
            )
            is member
        )
    assert posts == 1
    assert gets == 2


@pytest.mark.parametrize("phase", ["token", "membership", "status"])
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
        return (
            response(status, body)
            if phase == "token"
            else response(200, {"access_token": "ok", "expires_in": 3600})
        )

    def get(
        _url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = headers, timeout, allow_redirects
        return response(status, body)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(DirectoryUnavailable):
        directory = GoogleDirectory(directory_settings)
        if phase == "status":
            _ = directory.is_suspended("p@example.test")
        else:
            _ = directory.has_member("staff@example.test", "p@example.test")


def test_unreadable_credentials_fail_closed(tmp_path: Path) -> None:
    settings = OperatorSettings(
        OPERATOR_GOOGLE_CREDENTIALS_FILE=str(tmp_path / "missing"),
        OPERATOR_GOOGLE_DELEGATED_ADMIN="admin@example.test",
    )
    with pytest.raises(DirectoryUnavailable):
        _ = GoogleDirectory(settings).has_member("staff@example.test", "p@example.test")


@pytest.mark.parametrize("phase", ["token", "membership", "status"])
def test_network_timeouts_fail_closed(
    directory_settings: OperatorSettings, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    def post(
        _url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = data, timeout, allow_redirects
        if phase == "token":
            raise requests.Timeout("provider details")
        return response(200, {"access_token": "ok", "expires_in": 3600})

    def get(
        _url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = headers, timeout, allow_redirects
        raise requests.Timeout("provider details")

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(DirectoryUnavailable):
        directory = GoogleDirectory(directory_settings)
        if phase == "status":
            _ = directory.is_suspended("p@example.test")
        else:
            _ = directory.has_member("staff@example.test", "p@example.test")


def test_cached_token_expires_and_tracks_credentials_while_membership_stays_live(
    directory_settings: OperatorSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    moment = 100.0
    posts = 0
    gets = 0
    member = True

    def post(
        _url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        nonlocal posts
        _ = data, timeout, allow_redirects
        posts += 1
        return response(200, {"access_token": f"token-{posts}", "expires_in": 120})

    def get(
        _url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        nonlocal gets
        _ = timeout, allow_redirects
        gets += 1
        assert headers == {"Authorization": f"Bearer token-{posts}"}
        return response(200, {"isMember": member})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(workforce, "monotonic", lambda: moment)

    def check() -> bool:
        return GoogleDirectory(directory_settings).has_member(
            "staff@example.test", "p@example.test"
        )

    assert check() is True
    member = False
    moment = 159.0
    assert check() is False
    assert posts == 1 and gets == 2
    moment = 160.0
    assert check() is False
    assert posts == 2
    path = directory_settings.google_credentials_file
    assert path is not None
    credential = Path(path)
    credential.write_bytes(credential.read_bytes() + b"\n")
    assert check() is False
    assert posts == 3
    credential.unlink()
    with pytest.raises(DirectoryUnavailable):
        _ = check()
    assert posts == 3 and gets == 4


@pytest.mark.parametrize("phase", ["membership", "status"])
def test_refused_cached_token_is_replaced_on_the_next_request(
    directory_settings: OperatorSettings, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    posts = 0
    gets = 0

    def post(
        _url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        nonlocal posts
        _ = data, timeout, allow_redirects
        posts += 1
        return response(200, {"access_token": f"token-{posts}", "expires_in": 3600})

    def get(
        _url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        nonlocal gets
        _ = headers, timeout, allow_redirects
        gets += 1
        return (
            response(401, {})
            if gets == 1
            else response(200, {"isMember": True, "suspended": False})
        )

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)

    def check() -> bool:
        directory = GoogleDirectory(directory_settings)
        return (
            not directory.is_suspended("p@example.test")
            if phase == "status"
            else directory.has_member("staff@example.test", "p@example.test")
        )

    with pytest.raises(DirectoryUnavailable):
        _ = check()
    assert check() is True
    assert posts == 2 and gets == 2


@pytest.mark.parametrize("suspended", [False, True])
def test_account_status_is_fetched_live_with_cached_delegated_token(
    directory_settings: OperatorSettings,
    monkeypatch: pytest.MonkeyPatch,
    suspended: bool,
) -> None:
    posts = 0
    gets = 0
    current = suspended

    def post(
        _url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        nonlocal posts
        _ = data, timeout, allow_redirects
        posts += 1
        return response(200, {"access_token": "token", "expires_in": 3600})

    def get(
        url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        nonlocal gets
        gets += 1
        assert url == (
            "https://admin.googleapis.com/admin/directory/v1/users/person%2Bstaff%40example.test"
        )
        assert headers == {"Authorization": "Bearer token"}
        assert timeout == 5 and allow_redirects is False
        return response(200, {"suspended": current})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    assert GoogleDirectory(directory_settings).is_suspended("person+staff@example.test") is current
    current = not current
    assert GoogleDirectory(directory_settings).is_suspended("person+staff@example.test") is current
    assert posts == 1 and gets == 2


@pytest.mark.parametrize(
    "suspended", [None, "false", 0, []], ids=["missing", "string", "integer", "array"]
)
def test_account_status_requires_an_explicit_boolean(
    directory_settings: OperatorSettings,
    monkeypatch: pytest.MonkeyPatch,
    suspended: JsonValue,
) -> None:
    def post(
        _url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = data, timeout, allow_redirects
        return response(200, {"access_token": "token", "expires_in": 3600})

    def get(
        _url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> requests.Response:
        _ = headers, timeout, allow_redirects
        return response(200, {"suspended": suspended})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(DirectoryUnavailable):
        _ = GoogleDirectory(directory_settings).is_suspended("person@example.test")
