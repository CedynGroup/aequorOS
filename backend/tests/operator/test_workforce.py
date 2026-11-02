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
from app.core.config import get_operator_settings
from app.db.base import utc_now
from app.models import OperatorAuditLog, OperatorUser
from app.operator.schemas import OperatorLoginRead
from app.operator.services import operator_auth, workforce
from tests.operator.conftest import OPERATOR_JWT_SECRET, operator_headers
from tests.operator.test_operator_auth import PASSWORD, make_operator


@pytest.fixture
def oidc(monkeypatch: pytest.MonkeyPatch) -> dict[str, JsonValue]:
    monkeypatch.setenv("OPERATOR_OIDC_ISSUER", "https://accounts.google.com")
    monkeypatch.setenv("OPERATOR_OIDC_CLIENT_ID", "console-client-id")
    claims: dict[str, JsonValue] = {
        "email": "ama@aequoros.com",
        "email_verified": True,
        "sub": "workspace-subject",
        "hd": "aequoros.com",
        "iat": int(time.time()),
    }

    def verify(_token: str, *, issuer: str, audience: str) -> dict[str, JsonValue]:
        assert issuer == get_operator_settings().oidc_issuer
        assert audience == "console-client-id"
        return claims

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
    monkeypatch: pytest.MonkeyPatch,
    hd: str | None,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_REQUIRE_HOSTED_DOMAIN="true")
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


def test_generic_oidc_and_defaults_accept_missing_hd_and_assurance(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_operator(operator_db, role="operator_admin")
    oidc.pop("hd")
    assert read_staff(operator_client) == 200
    configure(monkeypatch, OIDC_ISSUER="https://workforce.example")
    assert read_staff(operator_client) == 200
    configure(monkeypatch, OIDC_GROUP_ROLES='{"staff@aequoros.com":"operator_admin"}')
    assert read_staff(operator_client) == 401


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
    configure(monkeypatch, OIDC_GROUP_ROLES='{"staff@example.com":"super_admin"}')

    def has_member(_self: workforce.GoogleDirectoryGroups, _group: str, _email: str) -> bool:
        return True

    monkeypatch.setattr(workforce.GoogleDirectoryGroups, "has_member", has_member)
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
    configure(monkeypatch, OIDC_GROUP_ROLES='{"staff@aequoros.com":"operator_admin"}')

    def has_member(_self: workforce.GoogleDirectoryGroups, group: str, email: str) -> bool:
        assert group == "staff@aequoros.com" and email == "ama@aequoros.com"
        if membership == "unavailable":
            raise workforce.GroupsUnavailable("provider secret must not escape")
        return membership

    monkeypatch.setattr(workforce.GoogleDirectoryGroups, "has_member", has_member)
    response = operator_client.get("/operator/v1/operators", headers=operator_headers("oidc"))
    assert response.status_code == (200 if membership is True else 401)
    assert "provider secret" not in response.text
    if membership == "unavailable":
        assert "group data is unavailable" in response.text


def test_membership_rechecked_and_role_capped_by_row(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = oidc
    user = make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_GROUP_ROLES='{"staff@aequoros.com":"super_admin"}')
    member = True

    def has_member(_self: workforce.GoogleDirectoryGroups, _group: str, _email: str) -> bool:
        return member

    monkeypatch.setattr(workforce.GoogleDirectoryGroups, "has_member", has_member)
    assert read_staff(operator_client) == 200
    user.role = "developer"
    operator_db.commit()
    assert read_staff(operator_client) == 403
    member = False
    assert (
        operator_client.get("/operator/v1/tenants", headers=operator_headers("oidc")).status_code
        == 401
    )


def test_group_role_can_reduce_but_never_raise_staff_authority(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = oidc
    make_operator(operator_db, role="super_admin")
    configure(monkeypatch, OIDC_GROUP_ROLES='{"staff@aequoros.com":"developer"}')

    def has_member(_self: workforce.GoogleDirectoryGroups, _group: str, _email: str) -> bool:
        return True

    monkeypatch.setattr(workforce.GoogleDirectoryGroups, "has_member", has_member)
    assert read_staff(operator_client) == 403
    assert (
        operator_client.get("/operator/v1/tenants", headers=operator_headers("oidc")).status_code
        == 200
    )


@pytest.mark.parametrize("required", [False, True])
def test_assurance_policy_is_opt_in(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    required: bool,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_REQUIRE_ASSURANCE=str(required))
    assert read_staff(operator_client) == (401 if required else 200)
    oidc.update({"amr": ["pwd", "mfa"], "acr": "workforce", "auth_time": int(time.time())})
    assert read_staff(operator_client) == 200
    configure(monkeypatch, OIDC_REQUIRED_ACR="stronger")
    assert read_staff(operator_client) == (401 if required else 200)
    configure(monkeypatch, OIDC_REQUIRED_AMR="[]", OIDC_REQUIRED_ACR="workforce")
    oidc.pop("amr")
    assert read_staff(operator_client) == 200
    configure(monkeypatch, OIDC_REQUIRED_ACR="")
    assert read_staff(operator_client) == (401 if required else 200)


@pytest.mark.parametrize("auth_time", [None, True, "123", -1, 999999999999])
def test_unusable_assurance_time_refuses(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
    auth_time: JsonValue,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_REQUIRE_ASSURANCE="true")
    oidc.update({"amr": ["mfa"], "auth_time": auth_time})
    assert read_staff(operator_client) == 401


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


def test_sensitive_write_requires_recent_auth_and_audits_issuer_evidence(
    operator_client: TestClient,
    operator_db: Session,
    oidc: dict[str, JsonValue],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(monkeypatch, OIDC_STEP_UP_MAX_AGE_SECONDS="300")
    path = "/operator/v1/operators/ama@aequoros.com/reset-password"

    def mutate() -> int:
        return operator_client.post(path, headers=operator_headers("oidc")).status_code

    assert read_staff(operator_client) == 200
    assert mutate() == 401
    assert (
        operator_db.scalar(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "operators.reset_password")
        )
        is None
    )
    oidc["auth_time"] = int(time.time()) - 301
    assert mutate() == 401
    oidc["auth_time"] = int(time.time()) + 600
    assert mutate() == 401
    evidence: dict[str, JsonValue] = {
        "auth_time": int(time.time()),
        "amr": ["pwd", "mfa"],
        "acr": "issuer-value",
    }
    oidc.update(evidence)
    assert mutate() == 200
    audit = operator_db.scalar(
        select(OperatorAuditLog).where(OperatorAuditLog.action == "operators.reset_password")
    )
    assert audit is not None
    assert audit.detail["oidc_assurance"] == evidence


def test_all_oidc_controls_leave_password_login_and_actions_unchanged(
    operator_client: TestClient,
    operator_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_operator(operator_db, role="operator_admin")
    configure(
        monkeypatch,
        OIDC_REQUIRE_HOSTED_DOMAIN="true",
        OIDC_REQUIRE_ASSURANCE="true",
        OIDC_GROUP_ROLES='{"staff@aequoros.com":"super_admin"}',
        OIDC_SESSION_MAX_AGE_SECONDS="1",
        OIDC_STEP_UP_MAX_AGE_SECONDS="1",
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
    configure(monkeypatch, OIDC_GROUP_ROLES='{"staff@aequoros.com":"operator_admin"}')
    response = operator_client.get("/operator/v1/operators", headers=operator_headers("oidc"))
    assert response.status_code == 401
    assert "group data is unavailable" in response.text
