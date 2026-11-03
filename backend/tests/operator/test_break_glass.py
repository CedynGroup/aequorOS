"""Audited break-glass emergency staff sign-in.

Covers the contract the operator plane promises: default-off enablement,
explicit row designation, the row's role and nothing more, a short fixed
session that revokes immediately, its own durable lockout, and one audit row
plus one warning log for every attempt — success or failure.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Never, cast

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_operator_settings, get_settings
from app.db.base import utc_now
from app.identity.service import auth_throttle
from app.models import (
    OperatorAuditLog,
    OperatorBreakGlassCredential,
    OperatorUser,
)
from app.operator.services import break_glass
from scripts import manage_break_glass
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


class WarningRecorder:
    """Records the service warning hook calls for one test."""

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

    @pytest.mark.parametrize("disable_first", [False, True])
    def test_reissuing_revokes_existing_sessions(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
        disable_first: bool,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db)
        credential, old_code = designate(operator_db, user)
        signed_in = emergency_login(operator_client, user.email, old_code)
        assert signed_in.status_code == 200
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 200
        )

        if disable_first:
            break_glass.disable_credential(operator_db, credential)
            operator_db.commit()
        rotated, new_code = designate(operator_db, user)

        assert rotated.id == credential.id
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 401
        )
        assert emergency_login(operator_client, user.email, old_code).status_code == 401
        renewed = emergency_login(operator_client, user.email, new_code)
        assert renewed.status_code == 200
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(renewed)).status_code
            == 200
        )

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
        assert credential.rotated_at is not None
        forged = pyjwt.encode(
            {
                "sub": user.email,
                "role": user.role,
                "typ": "operator_break_glass",
                "bg": str(credential.id),
                "rotated_at": break_glass.rotation_marker(credential.rotated_at),
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

    def test_a_session_without_a_rotation_marker_is_rejected(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db)
        credential, _code = designate(operator_db, user)
        now = utc_now()
        legacy = pyjwt.encode(
            {
                "sub": user.email,
                "role": user.role,
                "typ": "operator_break_glass",
                "bg": str(credential.id),
                "iat": int(now.timestamp()),
                "exp": int((now + dt.timedelta(minutes=15)).timestamp()),
            },
            OPERATOR_JWT_SECRET,
            algorithm="HS256",
        )

        assert (
            operator_client.get(
                "/operator/v1/tenants", headers={"Authorization": f"Bearer {legacy}"}
            ).status_code
            == 401
        )

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
        assert locked.status_code == 401
        assert error_message(locked) == "Invalid emergency access credentials."

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

    def test_failures_are_indistinguishable_across_lockout_windows(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        monkeypatch.setenv("AUTH_MAX_FAILED_LOGINS", "5")
        monkeypatch.setenv("AUTH_LOCKOUT_SECONDS", "900")
        get_settings.cache_clear()
        moment = utc_now()
        monkeypatch.setattr(break_glass, "utc_now", lambda: moment)
        monkeypatch.setattr(auth_throttle, "utc_now", lambda: moment)
        designated = make_operator(operator_db)
        _credential, code = designate(operator_db, designated)
        undesignated = make_operator(operator_db, "undesignated@aequoros.com")
        inactive = make_operator(operator_db, "inactive@aequoros.com")
        designate(operator_db, inactive)
        inactive.is_active = False
        disabled = make_operator(operator_db, "disabled@aequoros.com")
        disabled_credential, _disabled_code = designate(operator_db, disabled)
        break_glass.disable_credential(operator_db, disabled_credential)
        operator_db.commit()
        emails = [
            designated.email,
            undesignated.email,
            inactive.email,
            disabled.email,
            "unknown@aequoros.com",
        ]

        def assert_refused(email: str, attempted_code: str) -> None:
            response = emergency_login(operator_client, email, attempted_code)
            assert response.status_code == 401
            assert error_message(response) == "Invalid emergency access credentials."

        for _ in range(5):
            for email in emails:
                assert_refused(email, "wrong-code")
        before_throttled = len(audit_rows(operator_db))
        for email in emails:
            assert_refused(email, code)
        assert len(audit_rows(operator_db)) == before_throttled

        moment += dt.timedelta(minutes=6)
        for email in emails:
            assert_refused(email, code)
        rows = audit_rows(operator_db)
        for email in emails:
            attempts = [row for row in rows if row.operator_email == email]
            assert attempts[-1].detail["reason"] == (
                "credential_locked" if email == designated.email else "not_designated"
            )

        moment += dt.timedelta(minutes=10)
        assert emergency_login(operator_client, designated.email, code).status_code == 200
        stored = break_glass.credential_for_user(operator_db, designated.id)
        assert stored is not None
        operator_db.refresh(stored)
        assert stored.failed_login_attempts == 0
        assert stored.locked_until is None

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


class TestAuditAndLogging:
    def test_every_attempt_writes_an_audit_row_and_emits_a_warning(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        warnings = WarningRecorder()
        monkeypatch.setattr(break_glass, "operator_break_glass", warnings)
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

        assert warnings.calls == [
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

    def test_audit_api_reads_emergency_attempts_and_actions(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        user = make_operator(operator_db, role="operator_admin")
        _credential, code = designate(operator_db, user)
        assert emergency_login(operator_client, user.email, "wrong-code").status_code == 401
        signed_in = emergency_login(operator_client, user.email, code)
        assert signed_in.status_code == 200
        assert (
            operator_client.get("/operator/v1/tenants", headers=bearer(signed_in)).status_code
            == 200
        )

        response = operator_client.get("/operator/v1/audit", headers=bearer(signed_in))

        assert response.status_code == 200
        body = cast("dict[str, object]", response.json())
        rows = cast("list[dict[str, object]]", body["items"])
        assert {row["action"] for row in rows} == {
            break_glass.BREAK_GLASS_DENIED_ACTION,
            break_glass.BREAK_GLASS_SIGN_IN_ACTION,
            "tenants.list",
        }
        assert all(row["auth_mode"] == "break_glass" for row in rows)
        assert all(row["operator_email"] == user.email for row in rows)

    def test_locked_attempts_are_audited_and_logged(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enable(monkeypatch)
        warnings = WarningRecorder()
        monkeypatch.setattr(break_glass, "operator_break_glass", warnings)
        user = make_operator(operator_db)
        credential, code = designate(operator_db, user)
        for _ in range(5):
            emergency_login(operator_client, user.email, "wrong-code")

        break_glass.reset_login_throttle()
        assert emergency_login(operator_client, user.email, code).status_code == 401

        rows = audit_rows(operator_db)
        assert rows[-1].action == break_glass.BREAK_GLASS_DENIED_ACTION
        assert warnings.calls[-1]["success"] is False
        assert warnings.calls[-1]["reason"] == "credential_locked"
        assert warnings.calls[-1]["credential_id"] == str(credential.id)

    def test_no_warning_or_audit_before_the_route_is_enabled(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        warnings = WarningRecorder()
        monkeypatch.setattr(break_glass, "operator_break_glass", warnings)
        user = make_operator(operator_db)
        _credential, code = designate(operator_db, user)

        assert emergency_login(operator_client, user.email, code).status_code == 404
        assert warnings.calls == []
        assert audit_rows(operator_db) == []


class TestCredentialCli:
    @pytest.mark.parametrize(
        ("command", "url_setting"),
        [
            (command, source)
            for command in ("status", "disable", "issue")
            for source in ("OPERATOR_DATABASE_URL", "DATABASE_URL")
        ],
    )
    def test_commands_forward_tls_policy_to_the_database_driver(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        command: str,
        url_setting: str,
    ) -> None:
        ca_bundle = str(tmp_path / "recovery-ca.pem")
        monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "0")
        monkeypatch.setenv("TLS_CA_BUNDLE", ca_bundle)
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://operator@fallback.example/control?sslmode=verify-full",
        )
        if url_setting == "OPERATOR_DATABASE_URL":
            monkeypatch.setenv(
                url_setting,
                "postgresql+psycopg://operator@operator.example/control?sslmode=verify-full",
            )
        get_settings.cache_clear()
        get_operator_settings.cache_clear()
        flags = [] if command == "issue" else [f"--{command}"]
        monkeypatch.setattr(
            "sys.argv", ["manage_break_glass.py", "--email", "ama@aequoros.com", *flags]
        )
        connections: list[dict[str, object]] = []

        class ConnectionCaptured(Exception):
            pass

        def connect(*_args: object, **kwargs: object) -> Never:
            connections.append(kwargs)
            raise ConnectionCaptured

        monkeypatch.setattr("psycopg.connect", connect)

        with pytest.raises(ConnectionCaptured):
            manage_break_glass.main()

        assert len(connections) == 1
        assert connections[0]["host"] == (
            "operator.example" if url_setting == "OPERATOR_DATABASE_URL" else "fallback.example"
        )
        assert connections[0]["sslrootcert"] == ca_bundle
        assert connections[0]["sslmode"] == "verify-full"
        assert connections[0]["ssl_min_protocol_version"] == "TLSv1.2"
        assert connections[0]["gssencmode"] == "disable"
        assert connections[0]["application_name"] == "aequoros-manage-break-glass"
