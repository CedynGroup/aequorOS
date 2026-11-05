"""Workforce controls execute at the operator boundary; passwords stay independent."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import OperatorSettings, get_operator_settings
from app.db.base import utc_now
from app.models import OperatorAuditLog, OperatorUser
from app.operator.schemas import OperatorLoginRead
from app.operator.services import operator_auth, workforce
from tests.operator.conftest import OPERATOR_JWT_SECRET, operator_headers
from tests.operator.test_operator_auth_integration import PASSWORD, make_operator


@pytest.fixture
def oidc(monkeypatch: pytest.MonkeyPatch) -> dict[str, JsonValue]:
    monkeypatch.setenv("OPERATOR_OIDC_ISSUER", "https://accounts.google.com")
    monkeypatch.setenv("OPERATOR_OIDC_CLIENT_ID", "console-client-id")
    claims: dict[str, JsonValue] = {
        "email": "ama@aequoros.com",
        "email_verified": True,
        "sub": "110169484474386276334",
        "hd": "aequoros.com",
        "iat": int(time.time()),
    }

    def verify(_token: str, *, issuer: str, audience: str) -> dict[str, JsonValue]:
        assert issuer == get_operator_settings().oidc_issuer
        assert audience == "console-client-id"
        return claims

    def user_status(
        _self: workforce.GoogleDirectory, _subject: str
    ) -> workforce.WorkspaceUserStatus:
        return workforce.WorkspaceUserStatus(suspended=False)

    monkeypatch.setattr(workforce.GoogleDirectory, "user_status", user_status)
    monkeypatch.setattr(security, "verify_oidc_id_token", verify)
    get_operator_settings.cache_clear()
    return claims


def configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(f"OPERATOR_{key}", value)
    get_operator_settings.cache_clear()


def read_staff(client: TestClient) -> int:
    return client.get("/operator/v1/operators", headers=operator_headers("oidc")).status_code


@pytest.mark.parametrize("hd", [None, "other.example", "AEQUOROS.COM"])
def test_hosted_domain_required(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    hd: str | None,
) -> None:
    make_operator(operator_db, role="operator_admin")
    oidc["hd"] = hd
    assert read_staff(operator_client) == 401
    oidc["hd"] = "aequoros.com"
    assert read_staff(operator_client) == 200


@pytest.mark.parametrize("claim,value", [("email", "ama@elsewhere.com"), ("email_verified", False)])
def test_domain_and_verified_email_remain_required(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    claim: str,
    value: JsonValue,
) -> None:
    make_operator(operator_db, role="operator_admin")
    oidc[claim] = value
    assert read_staff(operator_client) == 401


def test_generic_oidc_preserves_access_without_google_claims(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_operator(operator_db, role="operator_admin")
    oidc.pop("hd")
    assert read_staff(operator_client) == 401
    configure(monkeypatch, OIDC_ISSUER="https://workforce.example")
    oidc["iat"] = 0
    oidc["sub"] = "ama@aequoros.com"
    assert read_staff(operator_client) == 200
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]')
    configure(
        monkeypatch,
        GOOGLE_REQUIRE_2SV="true",
        OIDC_SESSION_MAX_AGE_SECONDS="1",
    )
    assert read_staff(operator_client) == 200
    assert (
        operator_client.post(
            "/operator/v1/operators/ama@aequoros.com/reset-password",
            headers=operator_headers("oidc"),
        ).status_code
        == 200
    )


@pytest.mark.parametrize("active", [None, False])
def test_group_membership_never_provisions_or_activates_staff(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    active: bool | None,
) -> None:
    _ = oidc
    if active is not None:
        make_operator(operator_db, role="operator_admin", active=active)
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]')

    def has_member(_self: workforce.GoogleDirectory, _group: str, _subject: str) -> bool:
        return True

    monkeypatch.setattr(workforce.GoogleDirectory, "has_member", has_member)
    assert read_staff(operator_client) == 401
    operator_db.expire_all()
    row = operator_db.scalar(select(OperatorUser).where(OperatorUser.email == "ama@aequoros.com"))
    if active is None:
        assert row is None
    else:
        assert row is not None and row.is_active is False


@pytest.mark.parametrize("membership", [True, False, "unavailable"])
def test_group_grant_removal_and_failure(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    membership: bool | Literal["unavailable"],
) -> None:
    _ = oidc
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]')

    def has_member(_self: workforce.GoogleDirectory, group: str, subject: str) -> bool:
        assert group == "staff@aequoros.com" and subject == "110169484474386276334"
        if membership == "unavailable":
            raise workforce.DirectoryUnavailable("provider secret must not escape")
        return membership

    monkeypatch.setattr(workforce.GoogleDirectory, "has_member", has_member)
    response = operator_client.get("/operator/v1/operators", headers=operator_headers("oidc"))
    assert response.status_code == (200 if membership is True else 401)
    assert "provider secret" not in response.text
    if membership == "unavailable":
        assert "directory data is unavailable" in response.text


def test_membership_rechecked_and_role_determined_by_row(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = oidc
    user = make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]')
    member = True

    def has_member(_self: workforce.GoogleDirectory, _group: str, _subject: str) -> bool:
        return member

    monkeypatch.setattr(workforce.GoogleDirectory, "has_member", has_member)
    assert read_staff(operator_client) == 200
    user.role = "developer"
    operator_db.commit()
    assert read_staff(operator_client) == 403
    member = False
    assert (
        operator_client.get("/operator/v1/tenants", headers=operator_headers("oidc")).status_code
        == 401
    )


@pytest.mark.parametrize("role", ["developer", "operator_admin", "super_admin"])
def test_group_membership_preserves_provisioned_staff_authority(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    role: Literal["developer", "operator_admin", "super_admin"],
) -> None:
    _ = oidc
    make_operator(operator_db, role=role)
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["other@aequoros.com", "staff@aequoros.com"]')

    def has_member(_self: workforce.GoogleDirectory, group: str, _subject: str) -> bool:
        return group == "staff@aequoros.com"

    monkeypatch.setattr(workforce.GoogleDirectory, "has_member", has_member)
    assert read_staff(operator_client) == (403 if role == "developer" else 200)
    assert (
        operator_client.get("/operator/v1/tenants", headers=operator_headers("oidc")).status_code
        == 200
    )


@pytest.mark.parametrize("required", [False, True])
def test_directory_2sv_policy_is_opt_in(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
) -> None:
    _ = oidc
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, GOOGLE_REQUIRE_2SV=str(required))
    directory = FakeDirectory()

    def lookup(_settings: OperatorSettings) -> workforce.DirectoryLookup:
        return directory

    monkeypatch.setattr(workforce, "GoogleDirectory", lookup)
    directory.enrolled = False
    directory.enforced = False
    assert read_staff(operator_client) == (401 if required else 200)
    directory.enrolled = True
    assert read_staff(operator_client) == (401 if required else 200)
    directory.enforced = True
    assert read_staff(operator_client) == 200
    assert len(directory.status_requests) == (3 if required else 0)


@pytest.mark.parametrize("issued", [None, True, "123", -1, 999999999999, 0])
def test_short_session_refuses_missing_invalid_or_old_issue_time(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    issued: JsonValue,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_SESSION_MAX_AGE_SECONDS="900")
    oidc["iat"] = issued
    assert read_staff(operator_client) == 401
    oidc["iat"] = int(time.time())
    assert read_staff(operator_client) == 200
    oidc["iat"] = int(time.time()) - 900
    assert read_staff(operator_client) == 401


@pytest.mark.parametrize("evidence_present", [False, True])
def test_writes_do_not_require_token_assurance_but_audit_supplied_evidence(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    evidence_present: bool,
) -> None:
    make_operator(operator_db, role="operator_admin")
    evidence: dict[str, JsonValue] = {}
    if evidence_present:
        evidence = {"auth_time": 0, "amr": ["pwd"], "acr": "issuer-value"}
        oidc.update(evidence)
    result = operator_client.post(
        "/operator/v1/operators/ama@aequoros.com/reset-password", headers=operator_headers("oidc")
    )
    assert result.status_code == 200
    audit = operator_db.scalar(
        select(OperatorAuditLog).where(OperatorAuditLog.action == "operators.reset_password")
    )
    assert audit is not None and audit.detail["oidc_assurance"] == evidence


def test_all_oidc_controls_leave_password_login_and_actions_unchanged(
    operator_client: TestClient,
    operator_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(
        monkeypatch,
        GOOGLE_REQUIRE_2SV="true",
        OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]',
        OIDC_SESSION_MAX_AGE_SECONDS="1",
    )
    response = operator_client.post(
        "/operator/auth/login", json={"email": "ama@aequoros.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = OperatorLoginRead.model_validate_json(response.content)
    assert abs((body.expires_at - utc_now()).total_seconds() - 8 * 3600) < 60
    headers = operator_headers(body.access_token)
    assert operator_client.get("/operator/v1/operators", headers=headers).status_code == 200
    # Existing password sessions also retain their original lifetime under OIDC policy.
    old_token, _ = operator_auth.mint_operator_token(
        email="ama@aequoros.com",
        role="operator_admin",
        secret=OPERATOR_JWT_SECRET,
        now=utc_now() - timedelta(hours=1),
    )
    headers = operator_headers(old_token)
    assert operator_client.get("/operator/v1/operators", headers=headers).status_code == 200
    assert (
        operator_client.post(
            "/operator/v1/operators/ama@aequoros.com/reset-password", headers=headers
        ).status_code
        == 200
    )


def test_missing_directory_credentials_denies_explicitly(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = oidc
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]')
    response = operator_client.get("/operator/v1/operators", headers=operator_headers("oidc"))
    assert response.status_code == 401
    assert "directory data is unavailable" in response.text


class FakeDirectory:
    def __init__(self) -> None:
        self.suspended: bool | Literal["unavailable"] = False
        self.status_requests: list[str] = []
        self.enrolled: bool | None = True
        self.enforced: bool | None = True

    def user_status(self, subject: str) -> workforce.WorkspaceUserStatus:
        self.status_requests.append(subject)
        if self.suspended == "unavailable":
            raise workforce.DirectoryUnavailable("provider secret must not escape")
        return workforce.WorkspaceUserStatus(
            suspended=self.suspended,
            isEnrolledIn2Sv=self.enrolled,
            isEnforcedIn2Sv=self.enforced,
        )

    def has_member(self, group: str, subject: str) -> bool:
        return group == "staff@aequoros.com" and subject == "110169484474386276334"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_live_suspension_and_status_failure_revoke_existing_oidc_access(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    _ = oidc
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]')
    directory = FakeDirectory()

    def lookup(_settings: OperatorSettings) -> workforce.DirectoryLookup:
        return directory

    monkeypatch.setattr(workforce, "GoogleDirectory", lookup)
    path = (
        "/operator/v1/tenants"
        if method == "GET"
        else "/operator/v1/operators/ama@aequoros.com/reset-password"
    )
    states: tuple[bool | Literal["unavailable"], ...] = (False, True, "unavailable", False)
    for state in states:
        directory.suspended = state
        result = operator_client.request(method, path, headers=operator_headers("oidc"))
        assert result.status_code == (200 if state is False else 401)
        assert "provider secret" not in result.text
        if state is True:
            assert "suspended" in result.text
        elif state == "unavailable":
            assert "directory data is unavailable" in result.text
    assert directory.status_requests == ["110169484474386276334"] * 4


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_reassigned_email_cannot_replace_the_verified_directory_account(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]')
    accounts: dict[str, tuple[bool, bool]] = {
        "110169484474386276334": (False, True),
        "ama@aequoros.com": (False, True),
    }
    calls: list[tuple[str, str]] = []
    unavailable: str | None = None

    def user_status(
        _self: workforce.GoogleDirectory, subject: str
    ) -> workforce.WorkspaceUserStatus:
        calls.append(("status", subject))
        if unavailable == "status":
            raise workforce.DirectoryUnavailable("provider secret must not escape")
        return workforce.WorkspaceUserStatus(suspended=accounts[subject][0])

    def has_member(_self: workforce.GoogleDirectory, group: str, subject: str) -> bool:
        assert group == "staff@aequoros.com"
        calls.append(("membership", subject))
        if unavailable == "membership":
            raise workforce.DirectoryUnavailable("provider secret must not escape")
        return accounts[subject][1]

    monkeypatch.setattr(workforce.GoogleDirectory, "user_status", user_status)
    monkeypatch.setattr(workforce.GoogleDirectory, "has_member", has_member)
    path = (
        "/operator/v1/tenants"
        if method == "GET"
        else "/operator/v1/operators/ama@aequoros.com/reset-password"
    )

    def request_status() -> int:
        result = operator_client.request(method, path, headers=operator_headers("oidc"))
        assert "provider secret" not in result.text
        return result.status_code

    assert request_status() == 200
    original = accounts.pop("ama@aequoros.com")
    accounts["renamed@aequoros.com"] = (True, original[1])
    accounts["110169484474386276334"] = accounts["renamed@aequoros.com"]
    accounts["ama@aequoros.com"] = (False, True)
    assert request_status() == 401
    accounts["110169484474386276334"] = (False, False)
    assert request_status() == 401
    accounts["110169484474386276334"] = (False, True)
    for phase in ("status", "membership"):
        unavailable = phase
        assert request_status() == 401
    unavailable = None
    assert request_status() == 200
    assert {subject for _, subject in calls} == {"110169484474386276334"}
    assert {phase for phase, _ in calls} == {"status", "membership"}
    assert oidc["email"] == "ama@aequoros.com"
    operator_db.expire_all()
    user = operator_db.scalar(select(OperatorUser).where(OperatorUser.email == "ama@aequoros.com"))
    assert user is not None and user.is_active and user.role == "operator_admin"


@pytest.mark.parametrize(
    "subject",
    [
        None,
        True,
        123,
        [],
        {},
        "",
        " ",
        " subject ",
        "with space",
        "bad\x00subject",
        "é",
        "x" * 256,
        "ama@aequoros.com",
    ],
    ids=[
        "missing",
        "boolean",
        "number",
        "array",
        "object",
        "empty",
        "blank",
        "padded",
        "space",
        "control",
        "unicode",
        "long",
        "email",
    ],
)
def test_invalid_directory_subject_denies_without_email_fallback(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    subject: JsonValue,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_ALLOWED_GROUPS='["staff@aequoros.com"]')
    if subject is None:
        oidc.pop("sub")
    else:
        oidc["sub"] = subject

    def user_status(
        _self: workforce.GoogleDirectory, _subject: str
    ) -> workforce.WorkspaceUserStatus:
        pytest.fail("Invalid subjects must be refused before any Directory lookup.")

    monkeypatch.setattr(workforce.GoogleDirectory, "user_status", user_status)
    assert read_staff(operator_client) == 401
