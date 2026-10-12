"""Real PostgreSQL logins enforce the service-role contract, not a SQL mock."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError

from app.db.session import get_engine
from tests.db import test_postgres_migrations as migrations
from tests.support.helpers import ORG_1, ORG_2

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
ROLES = ("aqs_api", "aqs_worker", "aqs_bi", "aqs_operator", "aqs_audit_verifier")
MATRIX = Path(__file__).with_name("database_role_grants.json")


def privilege_snapshot(connection: Connection) -> dict[str, object]:
    """Effective rights include PUBLIC/inherited grants, so they cannot hide drift."""
    tables: dict[str, dict[str, list[str]]] = {}
    for name, oid in cast(
        list[tuple[str, int]],
        connection.execute(
            text(
                "SELECT relname, c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND relkind IN ('r','p','v','m','f') "
                "ORDER BY relname"
            )
        ).all(),
    ):
        tables[name] = {}
        for role in ROLES:
            rights = cast(
                list[str],
                connection.execute(
                    text(
                        "SELECT privilege FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',"
                        "'TRUNCATE','REFERENCES','TRIGGER','MAINTAIN']) privilege "
                        "WHERE has_table_privilege(:role, :oid, privilege) ORDER BY privilege"
                    ),
                    {"role": role, "oid": oid},
                )
                .scalars()
                .all(),
            )
            if rights:
                tables[name][role] = rights
    columns = cast(
        list[tuple[str, str, str, str, bool]],
        connection.execute(
            text(
                "SELECT c.relname, a.attname, r.rolname, acl.privilege_type, acl.is_grantable "
                "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN LATERAL aclexplode(a.attacl) acl JOIN pg_roles r ON r.oid=acl.grantee "
                "WHERE n.nspname=current_schema() AND r.rolname=ANY(:roles) "
                "ORDER BY c.relname, a.attname, r.rolname, acl.privilege_type"
            ),
            {"roles": list(ROLES)},
        ).all(),
    )
    functions = cast(
        list[tuple[str, str, str]],
        connection.execute(
            text(
                "SELECT p.proname, oidvectortypes(p.proargtypes), r.rolname FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN pg_roles r "
                "WHERE n.nspname=current_schema() AND r.rolname=ANY(:roles) "
                "AND has_function_privilege(r.oid,p.oid,'EXECUTE') "
                "ORDER BY p.proname, oidvectortypes(p.proargtypes), r.rolname"
            ),
            {"roles": list(ROLES)},
        ).all(),
    )
    sequences = cast(
        list[tuple[str, str, str]],
        connection.execute(
            text(
                "SELECT c.relname, r.rolname, privilege FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN pg_roles r "
                "CROSS JOIN unnest(ARRAY['SELECT','UPDATE','USAGE']) privilege "
                "WHERE n.nspname=current_schema() AND c.relkind='S' AND r.rolname=ANY(:roles) "
                "AND has_sequence_privilege(r.oid,c.oid,privilege) ORDER BY "
                "c.relname,r.rolname,privilege"
            ),
            {"roles": list(ROLES)},
        ).all(),
    )
    return {
        "tables": tables,
        "columns": [list(row) for row in columns],
        "functions": [list(row) for row in functions],
        "sequences": [list(row) for row in sequences],
    }


@pytest.fixture(scope="module")
def role_schema() -> Iterator[migrations.MigratedPostgresSchema]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "1")
        yield from migrations.migrate_isolated_postgres_schema(monkeypatch, downgrade=False)


@pytest.fixture(scope="module")
def administrator(role_schema: migrations.MigratedPostgresSchema) -> Iterator[Engine]:
    url = migrations.postgres_schema_url(
        migrations.postgres_administrator_url(), role_schema.schema_name
    )
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for org in (ORG_1, ORG_2):
                connection.execute(
                    text(
                        "INSERT INTO organizations (id,name,created_at,updated_at) "
                        "VALUES (:org,'Privilege fixture',now(),now())"
                    ),
                    {"org": org},
                )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def role_engines(administrator: Engine) -> Iterator[dict[str, Engine]]:
    engines: dict[str, Engine] = {}
    names: list[str] = []
    try:
        for role in (*ROLES, "aqs_migrator"):
            name, password = f"role_probe_{uuid4().hex}", uuid4().hex
            with administrator.begin() as connection:
                # Generated hex identifiers/passwords contain no SQL metacharacters.
                connection.execute(
                    text(
                        f"CREATE ROLE {name} LOGIN PASSWORD '{password}' "
                        "INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION"
                    )
                )
                names.append(name)
                connection.execute(text(f"GRANT {role} TO {name} WITH INHERIT TRUE, SET TRUE"))
            url = make_url(administrator.url).set(username=name, password=password)
            engines[role] = create_engine(url)
        yield engines
    finally:
        for engine in engines.values():
            engine.dispose()
        with administrator.begin() as connection:
            for name in names:
                connection.execute(text(f"DROP ROLE {name}"))


def test_grants_match_reviewed_matrix(role_schema: migrations.MigratedPostgresSchema) -> None:
    with role_schema.app_engine.connect() as connection:
        assert privilege_snapshot(connection) == json.loads(MATRIX.read_text())


def test_matrix_detects_an_unexpected_grant(administrator: Engine) -> None:
    with administrator.connect() as connection, connection.begin():
        before = privilege_snapshot(connection)
        connection.execute(text("GRANT UPDATE ON audit_events TO aqs_api"))
        assert privilege_snapshot(connection) != before
        connection.rollback()


def test_runtime_attributes_ownership_membership_and_grant_options(administrator: Engine) -> None:
    with administrator.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
                "rolreplication, "
                "rolbypassrls FROM pg_roles WHERE rolname=ANY(:roles)"
            ),
            {"roles": [*ROLES, "aqs_owner", "aqs_migrator"]},
        ).all()
        assert len(rows) == 7
        for row in rows:
            assert tuple(row[1:]) == (row[0] != "aqs_owner", False, False, False, False, False)
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
                    "WHERE r.rolname=ANY(:roles)"
                ),
                {"roles": list(ROLES)},
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pg_roles WHERE rolname=ANY(:roles) AND rolinherit"),
                {"roles": [*ROLES, "aqs_owner", "aqs_migrator"]},
            )
            == 0
        )
        assert tuple(
            connection.execute(
                text(
                    "SELECT m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m "
                    "JOIN pg_roles member ON member.oid=m.member "
                    "JOIN pg_roles parent ON parent.oid=m.roleid "
                    "WHERE member.rolname='aqs_migrator' AND parent.rolname='aqs_owner'"
                )
            ).one()
        ) == (False, False, True)
        for role in ROLES:
            assert not connection.scalar(
                text("SELECT pg_has_role(:role,'aqs_owner','MEMBER')"), {"role": role}
            )
            assert not connection.scalar(
                text("SELECT pg_has_role(:role,'aequoros_audit_owner','MEMBER')"), {"role": role}
            )
            assert not connection.scalar(
                text("SELECT has_schema_privilege(:role,current_schema(),'CREATE')"), {"role": role}
            )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_shdepend d JOIN pg_roles r ON r.oid=d.refobjid "
                    "WHERE d.refclassid='pg_authid'::regclass AND d.deptype='o' AND "
                    "r.rolname=ANY(:roles)"
                ),
                {"roles": list(ROLES)},
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "CROSS JOIN LATERAL aclexplode(c.relacl) acl JOIN pg_roles r ON "
                    "r.oid=acl.grantee "
                    "WHERE n.nspname=current_schema() AND r.rolname=ANY(:roles) AND "
                    "acl.is_grantable"
                ),
                {"roles": list(ROLES)},
            )
            == 0
        )
        assert connection.scalar(text("SELECT rolconfig FROM pg_roles WHERE rolname='aqs_bi'")) == [
            "statement_timeout=10s"
        ]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE forbidden_ddl (id integer)",
        "ALTER TABLE organizations ADD COLUMN forbidden integer",
        "SET ROLE aqs_owner",
        "SET ROLE aequoros_audit_owner",
        "CREATE ROLE forbidden_role",
        "TRUNCATE audit_events",
        "DELETE FROM audit_events",
        "UPDATE audit_events SET event_type='changed'",
        "UPDATE regulatory_packages SET snapshot='{}'",
        "DELETE FROM regulatory_packages",
    ],
)
def test_runtime_login_refuses_forbidden_operations(
    role_engines: dict[str, Engine], role: str, statement: str
) -> None:
    with role_engines[role].connect() as connection, connection.begin():
        with pytest.raises(DBAPIError) as error:
            connection.execute(text(statement))
        assert getattr(error.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize("role", ["aqs_api", "aqs_worker"])
def test_tenant_read_write_and_audit_append_are_isolated(
    role_engines: dict[str, Engine], role: str
) -> None:
    with role_engines[role].connect() as connection, connection.begin():
        connection.execute(
            text("SELECT set_config('app.organization_id',:org,true)"), {"org": ORG_1}
        )
        assert connection.execute(
            text("SELECT id FROM organizations ORDER BY id")
        ).scalars().all() == [ORG_1]
        assert (
            connection.execute(
                text("UPDATE organizations SET name='Permitted tenant edit' WHERE id=:org"),
                {"org": ORG_1},
            ).rowcount
            == 1
        )
        assert (
            connection.execute(
                text("UPDATE organizations SET name='Forbidden tenant edit' WHERE id=:org"),
                {"org": ORG_2},
            ).rowcount
            == 0
        )
        connection.execute(
            text(
                "INSERT INTO audit_events (id,organization_id,event_type,details,created_at) "
                "VALUES (:id,:org,'role.probe','{}',now())"
            ),
            {"id": uuid4(), "org": ORG_1},
        )
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO audit_events (id,organization_id,event_type,details,created_at) "
                    "VALUES (:id,:org,'role.probe','{}',now())"
                ),
                {"id": uuid4(), "org": ORG_2},
            )
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
        connection.rollback()


@pytest.mark.parametrize("role", ["aqs_bi", "aqs_operator", "aqs_audit_verifier"])
def test_read_only_roles_refuse_tenant_writes(role_engines: dict[str, Engine], role: str) -> None:
    with role_engines[role].connect() as connection, connection.begin(), pytest.raises(DBAPIError):
        connection.execute(
            text(
                "INSERT INTO organizations (id,name,created_at,updated_at) "
                "VALUES ('OR-DENIED01','Denied',now(),now())"
            )
        )


@pytest.mark.parametrize("role", ["aqs_api", "aqs_worker"])
def test_jobs_require_tenant_context_and_only_worker_can_advance_them(
    role_engines: dict[str, Engine], role: str
) -> None:
    with role_engines[role].connect() as connection, connection.begin():
        assert connection.execute(text("SELECT id FROM organizations")).scalars().all() == []
        assert connection.execute(text("SELECT id FROM jobs")).scalars().all() == []
        if role == "aqs_api":
            with pytest.raises(DBAPIError) as error:
                connection.execute(text("UPDATE jobs SET status='running' WHERE false"))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
        else:
            assert (
                connection.execute(text("UPDATE jobs SET status='running' WHERE false")).rowcount
                == 0
            )


def test_bi_and_verifier_have_their_specific_read_surfaces(role_engines: dict[str, Engine]) -> None:
    with role_engines["aqs_bi"].connect() as connection:
        connection.execute(text("SELECT * FROM bi_dim_date LIMIT 1"))
        with pytest.raises(DBAPIError):
            connection.execute(text("SELECT * FROM users LIMIT 1"))
    with role_engines["aqs_audit_verifier"].connect() as connection:
        for table in (
            "audit_events",
            "operator_audit_log",
            "audit_chain_entries",
            "audit_chain_heads",
        ):
            connection.execute(text(f"SELECT * FROM {table} LIMIT 1"))
        with pytest.raises(DBAPIError):
            connection.execute(text("SELECT * FROM organizations LIMIT 1"))


def test_migrator_sets_owner_and_future_objects_have_no_implicit_app_grants(
    role_engines: dict[str, Engine],
) -> None:
    with role_engines["aqs_migrator"].connect() as connection, connection.begin():
        connection.execute(text("SET ROLE aqs_owner"))
        connection.execute(text("CREATE TABLE future_private_table (id integer)"))
        assert (
            connection.scalar(
                text(
                    "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE "
                    "oid='future_private_table'::regclass"
                )
            )
            == "aqs_owner"
        )
        for role in ROLES:
            assert not connection.scalar(
                text("SELECT has_table_privilege(:role,'future_private_table','SELECT')"),
                {"role": role},
            )
        connection.rollback()


def test_shared_url_pools_have_distinct_application_names(
    role_schema: migrations.MigratedPostgresSchema,
) -> None:
    url = role_schema.app_engine.url.render_as_string(hide_password=False)
    engines = [
        get_engine(url, name) for name in ("aequoros-api", "aequoros-worker", "aequoros-operator")
    ]
    try:
        assert len(set(engines)) == 3
        for engine, name in zip(
            engines, ("aequoros-api", "aequoros-worker", "aequoros-operator"), strict=True
        ):
            with engine.connect() as connection:
                assert connection.scalar(text("SHOW application_name")) == name
    finally:
        for engine in engines:
            engine.dispose()
        get_engine.cache_clear()
