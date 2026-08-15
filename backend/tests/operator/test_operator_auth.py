"""Staff email+password auth (the client-model rebuild): login endpoint,
operator-JWT context branch, OIDC row parity, and account management."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_operator_settings
from app.db.base import utc_now
from app.models import OperatorAuditLog, OperatorUser
from app.operator.services import operator_auth
from tests.operator.conftest import OPERATOR_JWT_SECRET, operator_headers

PASSWORD = "correct horse battery staple"


def make_operator(
    db: Session,
    email: str = "ama@aequoros.com",
    *,
    role: str = "developer",
    password: str | None = PASSWORD,
    active: bool = True,
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


def login(client: TestClient, email: str, password: str) -> Any:
    return client.post("/operator/auth/login", json={"email": email, "password": password})


def bearer(response: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# -- POST /operator/auth/login ---------------------------------------------------
class TestLogin:
    def test_success_returns_token_identity_and_stamps_last_login(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db, role="operator_admin")
        response = login(operator_client, "ama@aequoros.com", PASSWORD)
        assert response.status_code == 200
        body = response.json()
        assert body["operator"] == {
            "email": "ama@aequoros.com",
            "display_name": "Ama Staff",
            "role": "operator_admin",
        }
        claims = operator_auth.verify_operator_token(
            body["access_token"], secret=OPERATOR_JWT_SECRET
        )
        assert claims["sub"] == "ama@aequoros.com"
        assert claims["role"] == "operator_admin"
        assert claims["typ"] == "operator"
        # expires_at mirrors the 8h claim.
        expires_at = dt.datetime.fromisoformat(body["expires_at"])
        assert abs((expires_at - utc_now()).total_seconds() - 8 * 3600) < 60

        operator_db.expire_all()
        row = operator_db.scalar(
            select(OperatorUser).where(OperatorUser.email == "ama@aequoros.com")
        )
        assert row is not None and row.last_login_at is not None

    def test_email_is_case_insensitive(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        response = login(operator_client, "AMA@AequorOS.com", PASSWORD)
        assert response.status_code == 200

    def test_wrong_password_unknown_email_inactive_and_sso_only_are_identical_401s(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        make_operator(operator_db, "off@aequoros.com", active=False)
        make_operator(operator_db, "sso@aequoros.com", password=None)
        responses = [
            login(operator_client, "ama@aequoros.com", "wrong"),
            login(operator_client, "nobody@aequoros.com", PASSWORD),
            login(operator_client, "off@aequoros.com", PASSWORD),
            login(operator_client, "sso@aequoros.com", PASSWORD),
        ]
        # One generic failure — no user enumeration through status or message.
        for response in responses:
            assert response.status_code == 401
            assert response.json()["error"]["message"] == "Invalid email or password."

    def test_missing_secret_is_503_naming_the_setting(
        self,
        operator_client: TestClient,
        operator_db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_operator(operator_db)
        monkeypatch.setenv("OPERATOR_JWT_SECRET", "")
        get_operator_settings.cache_clear()
        response = login(operator_client, "ama@aequoros.com", PASSWORD)
        assert response.status_code == 503
        assert "OPERATOR_JWT_SECRET" in response.json()["error"]["message"]

    def test_five_failures_rate_limit_the_email_even_with_the_right_password(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        for _ in range(5):
            assert login(operator_client, "ama@aequoros.com", "wrong").status_code == 401
        locked = login(operator_client, "ama@aequoros.com", PASSWORD)
        assert locked.status_code == 429
        # Another identity from the same client is not collaterally locked.
        make_operator(operator_db, "kofi@aequoros.com")
        assert login(operator_client, "kofi@aequoros.com", PASSWORD).status_code == 200

    def test_success_clears_the_failure_counter(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        for _ in range(4):
            login(operator_client, "ama@aequoros.com", "wrong")
        assert login(operator_client, "ama@aequoros.com", PASSWORD).status_code == 200
        # The slate is clean: four more failures still do not lock.
        for _ in range(4):
            assert login(operator_client, "ama@aequoros.com", "wrong").status_code == 401
        assert login(operator_client, "ama@aequoros.com", PASSWORD).status_code == 200


# -- operator-JWT branch of get_operator_context ----------------------------------
class TestJwtContext:
    def test_valid_token_authenticates_api_calls(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        session = login(operator_client, "ama@aequoros.com", PASSWORD)
        response = operator_client.get("/operator/v1/tenants", headers=bearer(session))
        assert response.status_code == 200

    def test_expired_token_rejected(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        token, _ = operator_auth.mint_operator_token(
            email="ama@aequoros.com",
            role="developer",
            secret=OPERATOR_JWT_SECRET,
            now=utc_now() - dt.timedelta(hours=9),
        )
        response = operator_client.get("/operator/v1/tenants", headers=operator_headers(token))
        assert response.status_code == 401

    def test_wrong_typ_rejected(self, operator_client: TestClient, operator_db: Session) -> None:
        make_operator(operator_db)
        now = utc_now()
        forged = pyjwt.encode(
            {
                "sub": "ama@aequoros.com",
                "role": "developer",
                "typ": "access",  # not an operator token
                "iat": int(now.timestamp()),
                "exp": int((now + dt.timedelta(hours=1)).timestamp()),
            },
            OPERATOR_JWT_SECRET,
            algorithm="HS256",
        )
        response = operator_client.get("/operator/v1/tenants", headers=operator_headers(forged))
        assert response.status_code == 401

    def test_deactivation_kills_live_sessions(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        row = make_operator(operator_db)
        session = login(operator_client, "ama@aequoros.com", PASSWORD)
        row.is_active = False
        operator_db.commit()
        response = operator_client.get("/operator/v1/tenants", headers=bearer(session))
        assert response.status_code == 401

    def test_stale_role_claim_rejected(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        # The row is the authority: a token minted for a role the row no
        # longer carries dies immediately (forces a fresh sign-in).
        make_operator(operator_db, role="developer")
        token, _ = operator_auth.mint_operator_token(
            email="ama@aequoros.com", role="operator_admin", secret=OPERATOR_JWT_SECRET
        )
        response = operator_client.get("/operator/v1/tenants", headers=operator_headers(token))
        assert response.status_code == 401


# -- OIDC row parity (secondary path takes the row's word) -------------------------
@pytest.fixture
def oidc_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_OIDC_ISSUER", "https://accounts.google.com")
    monkeypatch.setenv("OPERATOR_OIDC_CLIENT_ID", "console-client-id")
    get_operator_settings.cache_clear()


def fake_oidc_verifier(monkeypatch: pytest.MonkeyPatch, email: str) -> None:
    def _fake(_token: str, *, issuer: str, audience: str) -> dict[str, Any]:
        _ = issuer, audience
        return {"email": email, "email_verified": True, "sub": "google-sub"}

    monkeypatch.setattr(security, "verify_oidc_id_token", _fake)


class TestOidcRowParity:
    def test_deactivated_row_blocks_sso_sign_in(
        self,
        operator_client: TestClient,
        operator_db: Session,
        oidc_configured: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_operator(operator_db, "gone@aequoros.com", active=False)
        fake_oidc_verifier(monkeypatch, "gone@aequoros.com")
        response = operator_client.get(
            "/operator/v1/tenants", headers=operator_headers("some-id-token")
        )
        assert response.status_code == 401

    def test_row_role_governs_sso_sessions(
        self,
        operator_client: TestClient,
        operator_db: Session,
        oidc_configured: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_operator(operator_db, "root@aequoros.com", role="operator_admin")
        fake_oidc_verifier(monkeypatch, "root@aequoros.com")
        response = operator_client.get(
            "/operator/v1/operators", headers=operator_headers("some-id-token")
        )
        assert response.status_code == 200

    def test_allow_listed_email_without_row_stays_developer(
        self,
        operator_client: TestClient,
        operator_db: Session,
        oidc_configured: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _ = operator_db
        fake_oidc_verifier(monkeypatch, "newhire@aequoros.com")
        headers = operator_headers("some-id-token")
        # Documented allow-list behavior: authenticates …
        assert operator_client.get("/operator/v1/tenants", headers=headers).status_code == 200
        # … but with the base role, so account management is out of reach.
        assert operator_client.get("/operator/v1/operators", headers=headers).status_code == 403


# -- /operator/v1/operators management ---------------------------------------------
class TestOperatorManagement:
    def test_bootstrap_dev_session_creates_first_operator_on_empty_table(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        assert operator_db.scalar(select(OperatorUser)) is None
        response = operator_client.post(
            "/operator/v1/operators",
            json={
                "email": "First@AequorOS.com",
                "display_name": "First Admin",
                "role": "operator_admin",
            },
            headers=operator_headers(),  # dev token — operator_admin by definition
        )
        assert response.status_code == 201
        body = response.json()
        assert body["operator"]["email"] == "first@aequoros.com"  # normalized
        # The one-time password works exactly once as a credential…
        assert (
            login(operator_client, "first@aequoros.com", body["one_time_password"]).status_code
            == 200
        )
        # …and never leaks into the append-only audit trail.
        entries = operator_db.scalars(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "operators.create")
        ).all()
        assert len(entries) == 1
        assert entries[0].detail == {"email": "first@aequoros.com", "role": "operator_admin"}
        assert body["one_time_password"] not in json.dumps(entries[0].detail)

    def test_duplicate_email_is_409(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        response = operator_client.post(
            "/operator/v1/operators",
            json={"email": "ama@aequoros.com", "display_name": "Dup", "role": "developer"},
            headers=operator_headers(),
        )
        assert response.status_code == 409

    def test_developer_role_gets_403_on_every_management_endpoint(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)  # developer
        headers = bearer(login(operator_client, "ama@aequoros.com", PASSWORD))
        assert operator_client.get("/operator/v1/operators", headers=headers).status_code == 403
        assert (
            operator_client.post(
                "/operator/v1/operators",
                json={"email": "x@aequoros.com", "display_name": "X", "role": "developer"},
                headers=headers,
            ).status_code
            == 403
        )
        for action in ("reset-password", "deactivate", "reactivate"):
            assert (
                operator_client.post(
                    f"/operator/v1/operators/ama@aequoros.com/{action}", headers=headers
                ).status_code
                == 403
            ), action
        # The developer still uses the ordinary surfaces.
        assert operator_client.get("/operator/v1/tenants", headers=headers).status_code == 200

    def test_reset_password_rotates_the_credential(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        response = operator_client.post(
            "/operator/v1/operators/ama@aequoros.com/reset-password",
            headers=operator_headers(),
        )
        assert response.status_code == 200
        new_password = response.json()["one_time_password"]
        assert login(operator_client, "ama@aequoros.com", PASSWORD).status_code == 401
        assert login(operator_client, "ama@aequoros.com", new_password).status_code == 200

    def test_deactivate_reactivate_lifecycle(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        make_operator(operator_db)
        deactivated = operator_client.post(
            "/operator/v1/operators/ama@aequoros.com/deactivate", headers=operator_headers()
        )
        assert deactivated.status_code == 200
        assert deactivated.json()["is_active"] is False
        assert login(operator_client, "ama@aequoros.com", PASSWORD).status_code == 401

        reactivated = operator_client.post(
            "/operator/v1/operators/ama@aequoros.com/reactivate", headers=operator_headers()
        )
        assert reactivated.status_code == 200
        assert reactivated.json()["is_active"] is True
        assert login(operator_client, "ama@aequoros.com", PASSWORD).status_code == 200

        actions = sorted(
            operator_db.scalars(
                select(OperatorAuditLog.action).where(OperatorAuditLog.action.like("operators.%"))
            ).all()
        )
        assert actions == ["operators.deactivate", "operators.reactivate"]

    def test_self_deactivation_refused(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        # A password-session admin cannot deactivate their own account…
        make_operator(operator_db, "root@aequoros.com", role="operator_admin")
        headers = bearer(login(operator_client, "root@aequoros.com", PASSWORD))
        response = operator_client.post(
            "/operator/v1/operators/root@aequoros.com/deactivate", headers=headers
        )
        assert response.status_code == 409
        # …and neither can the dev identity when a row matches its email.
        make_operator(operator_db, "dev@aequoros.com", role="operator_admin")
        response = operator_client.post(
            "/operator/v1/operators/dev@aequoros.com/deactivate", headers=operator_headers()
        )
        assert response.status_code == 409

    def test_unknown_email_is_404(self, operator_client: TestClient, operator_db: Session) -> None:
        _ = operator_db
        response = operator_client.post(
            "/operator/v1/operators/nobody@aequoros.com/reset-password",
            headers=operator_headers(),
        )
        assert response.status_code == 404

    def test_rank_hierarchy_super_admin_over_operator_admin(
        self, operator_client: TestClient, operator_db: Session
    ) -> None:
        # An operator_admin may manage developers and peers, but NOT a
        # super_admin (or mint one) — only a super_admin outranks a
        # super_admin. The founder's super_admin tier is unrestricted.
        make_operator(operator_db, "admin@aequoros.com", role="operator_admin")
        make_operator(operator_db, "boss@aequoros.com", role="super_admin")
        admin_headers = bearer(login(operator_client, "admin@aequoros.com", PASSWORD))

        # operator_admin cannot deactivate a super_admin, nor reset its password.
        assert (
            operator_client.post(
                "/operator/v1/operators/boss@aequoros.com/deactivate", headers=admin_headers
            ).status_code
            == 403
        )
        assert (
            operator_client.post(
                "/operator/v1/operators/boss@aequoros.com/reset-password", headers=admin_headers
            ).status_code
            == 403
        )
        # operator_admin cannot MINT a super_admin.
        assert (
            operator_client.post(
                "/operator/v1/operators",
                json={"email": "x@aequoros.com", "display_name": "X", "role": "super_admin"},
                headers=admin_headers,
            ).status_code
            == 403
        )
        # The dev/local session is super_admin — it can do all of the above.
        assert (
            operator_client.post(
                "/operator/v1/operators/boss@aequoros.com/reset-password",
                headers=operator_headers(),
            ).status_code
            == 200
        )
        assert (
            operator_client.post(
                "/operator/v1/operators",
                json={"email": "peer@aequoros.com", "display_name": "Peer", "role": "super_admin"},
                headers=operator_headers(),
            ).status_code
            == 201
        )
