"""Audited break-glass emergency staff sign-in.

Covers the contract the operator plane promises: default-off enablement,
explicit row designation, the row's role and nothing more, a short fixed
session that revokes immediately, its own durable lockout, and one audit row
plus one alert for every attempt — success or failure.
"""

from __future__ import annotations

import datetime as dt
from typing import cast

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_operator_settings
from app.db.base import utc_now
from app.models import (
    OperatorAuditLog,
    OperatorBreakGlassCredential,
    OperatorUser,
)
from app.operator.services import break_glass
from tests.operator.conftest import OPERATOR_JWT_SECRET

PASSWORD = "correct horse battery staple"


def make_operator(
    db: Session,
    email: str = "ama@aequoros.com",
    *,
    role: str = "developer",
    active: bool = True,
    password: str | None = PASSWORD,
) -> OperatorUser:
    user = OperatorUser(
        email=email,
        display_name="Ama Staff",
        role=role,
        password_hash=security.hash_password(password) if password is not None else None,
        is_active=active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def designate(db: Session, user: OperatorUser) -> tuple[OperatorBreakGlassCredential, str]:
    credential, code = break_glass.issue_credential(db, user)
    db.commit()
    db.refresh(credential)
    return credential, code


def enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_BREAK_GLASS_ENABLED", "true")
    get_operator_settings.cache_clear()


def emergency_login(client: TestClient, email: str, code: str):
    return client.post("/operator/auth/break-glass", json={"email": email, "code": code})


def bearer(response: Response) -> dict[str, str]:
    body = cast("dict[str, object]", response.json())
    return {"Authorization": f"Bearer {cast('str', body['access_token'])}"}


def error_message(response: Response) -> str:
    body = cast("dict[str, object]", response.json())
    error = cast("dict[str, object]", body["error"])
    return cast("str", error["message"])


class AlertRecorder:
    """Records the operational alerts the service emits for one test."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, success: bool, reason: str, **fields: object) -> None:
        self.calls.append({"success": success, "reason": reason, **fields})


def audit_rows(db: Session, prefix: str = "auth.break_glass.") -> list[OperatorAuditLog]:
    db.expire_all()
    return list(
        db.scalars(
            select(OperatorAuditLog)
            .where(OperatorAuditLog.action.startswith(prefix))
            .order_by(OperatorAuditLog.created_at, OperatorAuditLog.id)
        )
    )


class TestEnablement:
    def test_disabled_by_default_answers_404_and_writes_nothing(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        user = make_operator(operator_db)
        _credential, code = designate(operator_db, user)

        response = emergency_login(operator_client, user.email, code)

        assert response.status_code == 404
        assert error_message(response) == "Emergency sign-in is not available."
        assert audit_rows(operator_db) == []

    def test_enabled_without_jwt_secret_answers_503(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        monkeypatch.setenv("OPERATOR_JWT_SECRET", "")
        get_operator_settings.cache_clear()
        user = make_operator(operator_db)
        _credential, code = designate(operator_db, user)

        response = emergency_login(operator_client, user.email, code)

        assert response.status_code == 503
        assert "OPERATOR_JWT_SECRET" in error_message(response)


class TestDesignatedRows:
    def test_undesignated_unknown_inactive_and_wrong_code_are_identical_401s(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        designated = make_operator(operator_db)
        _credential, code = designate(operator_db, designated)
        make_operator(operator_db, "not-designated@aequoros.com")
        off = make_operator(operator_db, "off@aequoros.com")
        designate(operator_db, off)
        off.is_active = False
        operator_db.commit()

        responses = [
            emergency_login(operator_client, designated.email, "wrong-code"),
            emergency_login(operator_client, "not-designated@aequoros.com", code),
            emergency_login(operator_client, "off@aequoros.com", code),
            emergency_login(operator_client, "nobody@aequoros.com", code),
        ]

        for response in responses:
            assert response.status_code == 401
        # One generic failure message — no user enumeration.
        assert {error_message(response) for response in responses} == {
            "Invalid emergency access credentials."
        }

    def test_a_code_only_opens_the_row_it_was_issued_for(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        ama = make_operator(operator_db)
        kofi = make_operator(operator_db, "kofi@aequoros.com")
        _credential, ama_code = designate(operator_db, ama)
        designate(operator_db, kofi)

        assert emergency_login(operator_client, kofi.email, ama_code).status_code == 401
        assert emergency_login(operator_client, ama.email, ama_code).status_code == 200

    def test_revoked_credential_refuses_even_a_minted_session(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db)
        credential, code = designate(operator_db, user)
        signed_in = emergency_login(operator_client, user.email, code)
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 200
        )

        break_glass.disable_credential(operator_db, credential)
        operator_db.commit()

        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 401
        )
        assert emergency_login(operator_client, user.email, code).status_code == 401

    def test_turning_the_master_switch_off_revokes_a_minted_session(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db)
        _credential, code = designate(operator_db, user)
        signed_in = emergency_login(operator_client, user.email, code)
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 200
        )

        monkeypatch.setenv("OPERATOR_BREAK_GLASS_ENABLED", "false")
        get_operator_settings.cache_clear()

        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 401
        )

    def test_deactivating_the_staff_row_refuses_even_a_minted_session(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db)
        _credential, code = designate(operator_db, user)
        signed_in = emergency_login(operator_client, user.email, code)

        user.is_active = False
        operator_db.commit()

        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 401
        )


class TestSession:
    def test_success_mints_a_fifteen_minute_session_with_the_rows_role(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db, role="developer")
        credential, code = designate(operator_db, user)

        response = emergency_login(operator_client, user.email, code)

        assert response.status_code == 200
        body = cast("dict[str, object]", response.json())
        assert body["operator"] == {
            "email": user.email,
            "display_name": "Ama Staff",
            "role": "developer",
        }
        claims = cast(
            "dict[str, object]",
            break_glass.verify_token(cast("str", body["access_token"]), secret=OPERATOR_JWT_SECRET),
        )
        assert claims["typ"] == "operator_break_glass"
        assert claims["sub"] == user.email
        assert claims["role"] == "developer"
        assert claims["credential_id"] == str(credential.id)
        assert cast("int", claims["exp"]) - cast("int", claims["iat"]) == (
            break_glass.BREAK_GLASS_TOKEN_TTL_SECONDS
        )
        expires_at = dt.datetime.fromisoformat(cast("str", body["expires_at"]))
        assert abs((expires_at - utc_now()).total_seconds() - 15 * 60) < 60

        # The token works on the operator API, and even a routine read is
        # audited under the break-glass auth mode.
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(response)).status_code == 200
        )
        read_rows = [
            row for row in audit_rows(operator_db, "tenants.") if row.auth_mode == "break_glass"
        ]
        assert len(read_rows) == 1

        operator_db.expire_all()
        stored = operator_db.get(OperatorBreakGlassCredential, credential.id)
        assert stored is not None and stored.last_used_at is not None

    def test_the_emergency_door_never_exceeds_the_rows_role(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db, role="developer")
        _credential, code = designate(operator_db, user)
        signed_in = emergency_login(operator_client, user.email, code)
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 200
        )
        assert (
            operator_client.get("/operator/v1/operators", headers=bearer(signed_in)).status_code
            == 403
        )

        # The row is the authority on every request: promoting it grants the
        # new powers immediately, and demoting it takes them away immediately.
        # The credential itself never adds a role.
        user.role = "operator_admin"
        operator_db.commit()
        assert (
            operator_client.get("/operator/v1/operators", headers=bearer(signed_in)).status_code
            == 200
        )

        user.role = "developer"
        operator_db.commit()
        assert (
            operator_client.get("/operator/v1/operators", headers=bearer(signed_in)).status_code
            == 403
        )

    def test_a_token_longer_than_the_fixed_lifetime_is_rejected(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db)
        credential, _code = designate(operator_db, user)
        now = utc_now()
        forged = pyjwt.encode(
            {
                "sub": user.email,
                "role": user.role,
                "typ": "operator_break_glass",
                "bg": str(credential.id),
                "iat": int(now.timestamp()),
                "exp": int((now + dt.timedelta(hours=8)).timestamp()),
            },
            OPERATOR_JWT_SECRET,
            algorithm="HS256",
        )

        response = operator_client.get(
            "/operator/v1/tenants", headers={"Authorization": f"Bearer {forged}"}
        )

        assert response.status_code == 401

    def test_an_ordinary_operator_session_is_not_a_break_glass_session(
        self,
        operator_client: TestClient,
        operator_db: Session,
    ) -> None:
        user = make_operator(operator_db)
        signed_in = operator_client.post(
            "/operator/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        # The ordinary 8-hour token carries typ=operator, so the emergency
        # verifier refuses it — and the ordinary verifier refuses a
        # break-glass token (its own branch, its own typ).
        ordinary = pyjwt.encode(
            {
                "sub": user.email,
                "role": user.role,
                "typ": "operator",
                "iat": int(utc_now().timestamp()),
                "exp": int((utc_now() + dt.timedelta(hours=8)).timestamp()),
            },
            OPERATOR_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(security.TokenInvalidError):
            break_glass.verify_token(ordinary, secret=OPERATOR_JWT_SECRET)
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 200
        )


class TestLockout:
    def test_repeated_failures_lock_the_credential_but_not_ordinary_sign_in(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db)
        credential, code = designate(operator_db, user)

        for _ in range(5):
            assert emergency_login(operator_client, user.email, "wrong-code").status_code == 401

        # The process-local pre-lookup guard answers first; clear it to prove
        # the DURABLE per-credential lockout refuses even the right code.
        break_glass.reset_login_throttle()
        locked = emergency_login(operator_client, user.email, code)
        assert locked.status_code == 429

        operator_db.expire_all()
        stored = operator_db.get(OperatorBreakGlassCredential, credential.id)
        assert stored is not None
        assert stored.failed_login_attempts >= 5
        assert stored.locked_until is not None

        # Ordinary staff password sign-in is a different door and a different
        # counter: the emergency lockout must not lock it.
        assert (
            operator_client.post(
                "/operator/auth/login", json={"email": user.email, "password": PASSWORD}
            ).status_code
            == 200
        )

    def test_success_clears_the_failure_counter(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db)
        credential, code = designate(operator_db, user)

        for _ in range(4):
            assert emergency_login(operator_client, user.email, "wrong-code").status_code == 401
        assert emergency_login(operator_client, user.email, code).status_code == 200

        operator_db.expire_all()
        stored = operator_db.get(OperatorBreakGlassCredential, credential.id)
        assert stored is not None
        assert stored.failed_login_attempts == 0
        assert stored.locked_until is None

        # …and the window is genuinely reset: four more failures do not lock.
        for _ in range(4):
            assert emergency_login(operator_client, user.email, "wrong-code").status_code == 401
        assert emergency_login(operator_client, user.email, code).status_code == 200


class TestAuditAndAlert:
    def test_every_attempt_writes_an_audit_row_and_emits_an_alert(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        alerts = AlertRecorder()
        monkeypatch.setattr(break_glass, "operator_break_glass", alerts)
        user = make_operator(operator_db)
        credential, code = designate(operator_db, user)

        assert emergency_login(operator_client, user.email, "wrong-code").status_code == 401
        assert emergency_login(operator_client, user.email, code).status_code == 200

        rows = audit_rows(operator_db)
        assert [row.action for row in rows] == [
            break_glass.BREAK_GLASS_DENIED_ACTION,
            break_glass.BREAK_GLASS_SIGN_IN_ACTION,
        ]
        assert all(row.auth_mode == "break_glass" for row in rows)
        assert all(row.operator_email == user.email for row in rows)
        denied, granted = rows
        assert denied.detail["reason"] == "invalid_code"
        assert denied.detail["credential_id"] == str(credential.id)
        assert granted.detail["session_expires_at"]
        assert "wrong-code" not in str(denied.detail)
        assert code not in str(granted.detail)

        assert alerts.calls == [
            {
                "success": False,
                "reason": "invalid_code",
                "operator_user_id": str(user.id),
                "credential_id": str(credential.id),
            },
            {
                "success": True,
                "reason": "verified",
                "operator_user_id": str(user.id),
                "credential_id": str(credential.id),
            },
        ]

    def test_locked_attempts_are_audited_and_alerted(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        alerts = AlertRecorder()
        monkeypatch.setattr(break_glass, "operator_break_glass", alerts)
        user = make_operator(operator_db)
        credential, code = designate(operator_db, user)
        for _ in range(5):
            emergency_login(operator_client, user.email, "wrong-code")

        break_glass.reset_login_throttle()
        assert emergency_login(operator_client, user.email, code).status_code == 429

        rows = audit_rows(operator_db)
        assert rows[-1].action == break_glass.BREAK_GLASS_DENIED_ACTION
        assert alerts.calls[-1]["success"] is False
        assert alerts.calls[-1]["reason"] == "credential_locked"
        assert alerts.calls[-1]["credential_id"] == str(credential.id)

    def test_no_alert_or_audit_before_the_route_is_enabled(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        alerts = AlertRecorder()
        monkeypatch.setattr(break_glass, "operator_break_glass", alerts)
        user = make_operator(operator_db)
        _credential, code = designate(operator_db, user)

        assert emergency_login(operator_client, user.email, code).status_code == 404
        assert alerts.calls == []
        assert audit_rows(operator_db) == []
