"""Sign-in works for a database login that does not bypass row-level security.

Production's API credential, and ``aqs_api`` after its cutover, read every account
and SSO connection as absent until a tenant is chosen, and sign-in begins with no
tenant. Every test here signs in on a fresh, unscoped session running as
``aqs_api``: exactly the position the pre-sign-in routes are in. The shared auth
fixtures pre-scope that session to the tenant, which is how #564 passed CI while
nobody could sign in against production's roles.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.identity.models.sso_connection import SsoConnection
from app.identity.models.user import User
from app.identity.service import authentication, pre_tenant, sso_config
from tests.conftest import seed_demo_tenants
from tests.support import postgres_migrations as migrations
from tests.support.database import database_connections
from tests.support.helpers import ORG_1, USER_1

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Migrated PostgreSQL required"
)

EMAIL = "demo.user.one@example.test"
PASSWORD = "synthetic-sign-in-fixture-password"
ISSUER = "https://idp.example.test"
CLIENT_ID = "synthetic-client"
LOOKUPS = (
    pre_tenant.LOGIN_SIGNATURE,
    pre_tenant.SSO_SIGNATURE,
    pre_tenant.ENABLED_SSO_SIGNATURE,
    pre_tenant.FAILURE_SIGNATURE,
)


@dataclass(frozen=True)
class Roles:
    administrator: Engine
    api: Engine


@pytest.fixture(scope="module")
def roles() -> Iterator[Roles]:
    """Seed as administrator; sign in as aqs_api through the production chain."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("APP_ENV", "test")
        patch.setenv("TLS_ALLOW_PLAINTEXT", "1")
        for schema in migrations.migrate_isolated_postgres_schema(patch, downgrade=False):
            administrator_url = migrations.postgres_administrator_url()
            with (
                database_connections(
                    migrations.postgres_schema_url(administrator_url, schema.schema_name)
                ) as administrator,
                database_connections(
                    migrations.postgres_schema_url(
                        administrator_url, schema.schema_name, role="aqs_api"
                    )
                ) as api,
            ):
                administrator_engine = administrator.engine()
                seed_demo_tenants(administrator_engine)
                with Session(administrator_engine) as db:
                    user = db.get(User, USER_1)
                    assert user is not None
                    user.password_hash = hash_password(PASSWORD)
                    db.add(
                        SsoConnection(
                            organization_id=ORG_1,
                            issuer=ISSUER,
                            client_id=CLIENT_ID,
                            allowed_email_domains=[],
                            enabled=True,
                        )
                    )
                    db.commit()
                yield Roles(administrator=administrator_engine, api=api.engine())


def test_the_api_login_reads_no_account_before_a_tenant_is_chosen(roles: Roles) -> None:
    with Session(roles.api) as db:
        assert db.scalar(text("SELECT current_user")) == "aqs_api"
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(SsoConnection)) == 0


def test_password_sign_in_resolves_its_tenant_without_reading_others(roles: Roles) -> None:
    with Session(roles.api) as db:
        tokens = authentication.login_with_password(db, email=EMAIL, password=PASSWORD)
        assert db.info["organization_id"] == ORG_1
    assert tokens.access_token
    assert tokens.refresh_token


def test_wrong_password_is_refused_and_counted_in_the_tenant(roles: Roles) -> None:
    with Session(roles.api) as db, pytest.raises(HTTPException) as refusal:
        _ = authentication.login_with_password(db, email=EMAIL, password="not-the-password")
    assert refusal.value.status_code == 401
    with Session(roles.administrator) as db:
        user = db.get(User, USER_1)
        assert user is not None
        assert user.failed_login_attempts >= 1
        user.failed_login_attempts = 0
        db.commit()


def test_unknown_email_is_refused_and_audited_without_a_tenant(roles: Roles) -> None:
    with Session(roles.api) as db, pytest.raises(HTTPException) as refusal:
        _ = authentication.login_with_password(
            db, email="no-such-account@example.test", password=PASSWORD
        )
    assert refusal.value.status_code == 401
    with Session(roles.administrator) as db:
        rows = db.execute(
            text(
                "SELECT organization_id, actor_user_id, event_type FROM audit_events "
                "WHERE entity_type = 'authentication_attempt'"
            )
        ).all()
    assert (None, None, "authentication.password.failed") in [tuple(row) for row in rows]


def test_refresh_and_logout_run_inside_the_token_tenant(roles: Roles) -> None:
    with Session(roles.api) as db:
        issued = authentication.login_with_password(db, email=EMAIL, password=PASSWORD)
    with Session(roles.api) as db:
        rotated = authentication.refresh_tokens(db, refresh_token=issued.refresh_token)
    with Session(roles.api) as db:
        authentication.logout(db, refresh_token=rotated.refresh_token)
    with Session(roles.api) as db, pytest.raises(HTTPException) as refusal:
        _ = authentication.refresh_tokens(db, refresh_token=rotated.refresh_token)
    assert refusal.value.status_code == 401


def test_sso_routing_enters_the_one_enabled_connection_tenant(roles: Roles) -> None:
    with Session(roles.api) as db:
        pre_tenant.enter_enabled_sso_organization(db)
        assert db.info["organization_id"] == ORG_1
        assert sso_config.resolve_client_config(db) is not None
    with Session(roles.api) as db:
        pre_tenant.enter_sso_organization(db, issuer=ISSUER + "/", audience=[CLIENT_ID, "x"])
        connection = sso_config.find_enabled_by_issuer_audience(
            db, issuer=ISSUER + "/", audience=[CLIENT_ID, "x"]
        )
        assert connection is not None
        assert connection.organization_id == ORG_1


def test_only_the_api_logins_may_execute_the_sign_in_lookups(roles: Roles) -> None:
    expected = {
        "aqs_api": True,
        "aqs_worker": False,
        "aqs_bi": False,
        "aqs_operator": False,
        "aqs_audit_verifier": False,
    }
    with Session(roles.administrator) as db:
        for signature in LOOKUPS:
            privileges = {
                role: db.scalar(
                    text("SELECT has_function_privilege(:role, to_regprocedure(:fn), 'EXECUTE')"),
                    {"role": role, "fn": signature},
                )
                for role in expected
            }
            assert privileges == expected, signature


def test_the_failure_recorder_refuses_anything_but_an_unattributed_sign_in_failure(
    roles: Roles,
) -> None:
    with Session(roles.api) as db, pytest.raises(Exception, match="Only unattributed sign-in"):
        _ = db.execute(
            text(
                "SELECT aqs_auth_record_unattributed_failure("
                "'authentication.password.succeeded', gen_random_uuid(), '{}'::jsonb, now())"
            )
        )
