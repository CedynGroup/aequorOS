"""Real PostgreSQL logins enforce the service-role contract, not a SQL mock."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.tenancy import TenantContext
from app.db.session import get_engine
from app.models import Bank, Job
from app.schemas.ingestion import IngestionBatchCreate, MappingConfigCreate
from app.services import assessments, documents, ingestion, job_queue
from tests.adapters.excel_csv import fixtures as workbooks
from tests.api.test_ingestion import FULL_MAPPING
from tests.conftest import FakeStorage
from tests.db import test_postgres_migrations as migrations
from tests.services.factories import ServiceFactories
from tests.support.helpers import ORG_1, ORG_2
from tests.support.inmemory_storage import InMemoryStorageClient

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
ROLES = ("aqs_api", "aqs_worker", "aqs_bi", "aqs_operator", "aqs_audit_verifier")
MATRIX = Path(__file__).with_name("database_role_grants.json")


def _schema_privilege_snapshot(connection: Connection) -> dict[str, object]:
    """Effective rights include PUBLIC/inherited grants, so they cannot hide drift."""
    privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
    if (connection.dialect.server_version_info or (0,))[0] >= 17:
        privileges.append("MAINTAIN")
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
                        "SELECT privilege FROM unnest(CAST(:privileges AS text[])) privilege "
                        "WHERE has_table_privilege(:role, :oid, privilege) ORDER BY privilege"
                    ),
                    {"role": role, "oid": oid, "privileges": privileges},
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
                "SELECT c.relname, a.attname, r.rolname, privilege, "
                "has_column_privilege(r.oid, c.oid, a.attnum, privilege || ' WITH GRANT OPTION') "
                "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN pg_roles r "
                "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) privilege "
                "WHERE n.nspname=current_schema() AND r.rolname=ANY(:roles) "
                "AND c.relkind IN ('r','p','v','m','f') AND a.attnum > 0 AND NOT a.attisdropped "
                "AND has_column_privilege(r.oid, c.oid, a.attnum, privilege) "
                "AND (NOT has_table_privilege(r.oid, c.oid, privilege) OR ("
                "has_column_privilege(r.oid, c.oid, a.attnum, privilege || ' WITH GRANT OPTION') "
                "AND NOT has_table_privilege(r.oid, c.oid, privilege || ' WITH GRANT OPTION'))) "
                "ORDER BY c.relname, a.attname, r.rolname, privilege"
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


def privilege_snapshot(connection: Connection) -> dict[str, object]:
    """Include the isolated worker view namespace in the same grant ratchet."""
    result = _schema_privilege_snapshot(connection)
    view_schema = cast(str, connection.scalar(text("SELECT aqs_worker_desk_schema()")))
    previous = cast(str, connection.scalar(text("SELECT current_setting('search_path')")))
    try:
        connection.execute(
            text("SELECT set_config('search_path', quote_ident(:schema), true)"),
            {"schema": view_schema},
        )
        result["worker_views"] = _schema_privilege_snapshot(connection)
    finally:
        connection.execute(
            text("SELECT set_config('search_path', :path, true)"), {"path": previous}
        )
    return result


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
                options = (
                    " WITH INHERIT TRUE, SET TRUE"
                    if (connection.dialect.server_version_info or (0,))[0] >= 16
                    else ""
                )
                connection.execute(text(f"GRANT {role} TO {name}{options}"))
            url = make_url(administrator.url).set(username=name, password=password)
            engines[role] = create_engine(url)
        yield engines
    finally:
        for engine in engines.values():
            engine.dispose()
        with administrator.begin() as connection:
            schema = connection.dialect.identifier_preparer.quote_identifier(
                cast(str, connection.execute(text("SELECT current_schema()")).scalar_one())
            )
            for name in names:
                # Migration replay can grant scoped read helpers to these runtime logins.
                connection.execute(
                    text(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {schema} FROM {name}")
                )
                connection.execute(text(f"DROP ROLE {name}"))


@pytest.fixture(scope="module")
def role_banks(administrator: Engine) -> dict[str, str]:
    with Session(administrator) as session:
        banks = [
            Bank(
                organization_id=org,
                name="Privilege fixture",
                short_name="Privilege",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="bank",
                institution_type="universal_bank",
            )
            for org in (ORG_1, ORG_2)
        ]
        session.add_all(banks)
        session.commit()
        return {bank.organization_id: bank.id for bank in banks}


@pytest.fixture
def api_session(role_engines: dict[str, Engine]) -> Iterator[Session]:
    with role_engines["aqs_api"].connect() as connection, connection.begin() as transaction:
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            session.info["organization_id"] = ORG_1
            yield session
        transaction.rollback()


def test_grants_match_reviewed_matrix(role_schema: migrations.MigratedPostgresSchema) -> None:
    with role_schema.app_engine.connect() as connection:
        assert privilege_snapshot(connection) == json.loads(MATRIX.read_text())


def test_matrix_detects_an_unexpected_grant(administrator: Engine) -> None:
    with administrator.connect() as connection, connection.begin():
        before = privilege_snapshot(connection)
        connection.execute(text("GRANT UPDATE ON audit_events TO aqs_api"))
        assert privilege_snapshot(connection) != before
        connection.rollback()


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "REFERENCES"])
def test_matrix_detects_public_column_grants(administrator: Engine, privilege: str) -> None:
    with administrator.connect() as connection, connection.begin():
        before = privilege_snapshot(connection)
        connection.execute(
            text(f"GRANT {privilege} (bank_id, key_id) ON bank_encryption_keys TO PUBLIC")
        )
        after = privilege_snapshot(connection)
        assert after != before
        assert after["tables"] == before["tables"]
        added = [
            row
            for row in cast(list[list[object]], after["columns"])
            if row not in cast(list[list[object]], before["columns"])
        ]
        assert added == [
            ["bank_encryption_keys", column, role, privilege, False]
            for column in ("bank_id", "key_id")
            for role in sorted(ROLES)
        ]
        connection.rollback()


def test_matrix_detects_inherited_column_grants_and_grant_options(administrator: Engine) -> None:
    with administrator.connect() as connection, connection.begin():
        before = privilege_snapshot(connection)
        parent = f"column_probe_{uuid4().hex}"
        connection.execute(text(f"CREATE ROLE {parent} NOLOGIN"))
        connection.execute(
            text(f"GRANT SELECT (key_id) ON bank_encryption_keys TO {parent} WITH GRANT OPTION")
        )
        if (connection.dialect.server_version_info or (0,))[0] >= 16:
            connection.execute(text(f"GRANT {parent} TO aqs_api WITH INHERIT TRUE, SET FALSE"))
        else:
            # PostgreSQL 15 controls inheritance on the member role. This
            # deliberate privilege drift is rolled back with the transaction.
            connection.execute(text("ALTER ROLE aqs_api INHERIT"))
            connection.execute(text(f"GRANT {parent} TO aqs_api"))
        after = privilege_snapshot(connection)
        assert after != before
        assert after["tables"] == before["tables"]
        assert ["bank_encryption_keys", "key_id", "aqs_api", "SELECT", True] in cast(
            list[list[object]], after["columns"]
        )
        connection.rollback()


@pytest.mark.parametrize("grantable", [False, True])
def test_matrix_normalizes_column_rights_already_granted_on_the_table(
    administrator: Engine, grantable: bool
) -> None:
    with administrator.connect() as connection, connection.begin():
        before = privilege_snapshot(connection)
        suffix = " WITH GRANT OPTION" if grantable else ""
        connection.execute(text(f"GRANT SELECT (bank_id) ON jobs TO aqs_api{suffix}"))
        after = privilege_snapshot(connection)
        if grantable:
            assert after != before
            assert after["tables"] == before["tables"]
            assert ["jobs", "bank_id", "aqs_api", "SELECT", True] in cast(
                list[list[object]], after["columns"]
            )
        else:
            assert after == before
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
        membership_options = (
            "m.admin_option,m.inherit_option,m.set_option"
            if (connection.dialect.server_version_info or (0,))[0] >= 16
            else "m.admin_option,pg_has_role(member.oid,parent.oid,'USAGE'),"
            "pg_has_role(member.oid,parent.oid,'MEMBER')"
        )
        assert tuple(
            connection.execute(
                text(
                    f"SELECT {membership_options} FROM pg_auth_members m "
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
        assert set(
            cast(
                list[str],
                connection.scalar(text("SELECT rolconfig FROM pg_roles WHERE rolname='aqs_bi'")),
            )
        ) == {"statement_timeout=10s", "idle_in_transaction_session_timeout=5min"}


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
def test_jobs_require_tenant_context(role_engines: dict[str, Engine], role: str) -> None:
    with role_engines[role].connect() as connection, connection.begin():
        assert connection.execute(text("SELECT id FROM organizations")).scalars().all() == []
        assert connection.execute(text("SELECT id FROM jobs")).scalars().all() == []
        assert connection.execute(text("UPDATE jobs SET status='running'")).rowcount == 0
        with pytest.raises(DBAPIError) as error, connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO jobs (id,organization_id,job_type,status,queued_at) "
                    "VALUES (:id,:org,'document_parse','queued',now())"
                ),
                {"id": uuid4(), "org": ORG_1},
            )
        assert getattr(error.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM jobs WHERE false",
        "UPDATE jobs SET organization_id='OR-DENIED01' WHERE false",
        "UPDATE jobs SET attempts=1 WHERE false",
        "UPDATE jobs SET max_attempts=100 WHERE false",
        "UPDATE jobs SET claimed_by='api' WHERE false",
        "UPDATE jobs SET job_type='official_run' WHERE false",
        "UPDATE jobs SET coalesce_key='changed' WHERE false",
        "UPDATE jobs SET entity_type='changed' WHERE false",
        "UPDATE jobs SET entity_id='changed' WHERE false",
        "UPDATE jobs SET queued_at=now() WHERE false",
        "UPDATE jobs SET id=gen_random_uuid() WHERE false",
    ],
)
def test_api_refuses_worker_and_identity_job_writes(api_session: Session, statement: str) -> None:
    with pytest.raises(DBAPIError) as error, api_session.begin_nested():
        api_session.execute(text(statement))
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


def test_api_coalesces_two_accepted_uploads_before_pickup(
    api_session: Session, role_banks: dict[str, str], tmp_path: Path
) -> None:
    ctx = TenantContext(organization_id=ORG_1)
    bank_id = role_banks[ORG_1]
    ingestion.create_mapping_config(
        api_session,
        ctx,
        bank_id,
        MappingConfigCreate(
            source_system="EXCEL_CSV",
            name="Privilege workbook",
            config=FULL_MAPPING,
            activate=True,
            reason="Test upload compatibility",
        ),
    )
    storage = InMemoryStorageClient()
    first_job_id = None
    first_run_after = None
    for name in ("first.xlsx", "second.xlsx"):
        path = workbooks.build_well_formed(tmp_path / name)
        if name == "second.xlsx":
            workbook = load_workbook(path)
            workbook["Loans"]["D2"] = 1600000.50
            workbook.save(path)
            workbook.close()
        result = ingestion.start_ingestion(
            api_session,
            ctx,
            bank_id,
            IngestionBatchCreate(
                source_system="EXCEL_CSV",
                as_of_date=workbooks.AS_OF,
                location=str(path),
                reason="Test upload compatibility",
            ),
            storage,
        )
        assert not result.reused
        assert result.batch.status == "accepted"
        jobs = api_session.scalars(
            select(Job).where(Job.coalesce_key == f"refresh:{bank_id}:{workbooks.AS_OF}")
        ).all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == "queued"
        assert job.started_at is None
        assert job.bank_id == bank_id
        assert job.payload == {"as_of_date": workbooks.AS_OF.isoformat()}
        assert job.run_after is not None
        if first_job_id is None:
            first_job_id, first_run_after = job.id, job.run_after
        else:
            assert job.id == first_job_id
            assert first_run_after is not None and job.run_after > first_run_after


def test_api_coalescing_updates_payload_schedule_and_bank(
    api_session: Session, role_banks: dict[str, str]
) -> None:
    first = job_queue.enqueue(
        api_session, ORG_1, "pipeline_refresh", payload={"first": True}, coalesce_key="probe"
    )
    api_session.commit()
    second = job_queue.enqueue(
        api_session,
        ORG_1,
        "pipeline_refresh",
        bank_id=role_banks[ORG_1],
        payload={"second": True},
        run_after=first.queued_at + timedelta(seconds=30),
        coalesce_key="probe",
    )
    api_session.commit()
    api_session.refresh(second)
    assert second.id == first.id
    assert second.payload == {"first": True, "second": True}
    assert second.bank_id == role_banks[ORG_1]
    assert second.run_after == first.queued_at + timedelta(seconds=30)


@pytest.mark.parametrize("operation", ["insert", "update", "coalesce"])
def test_api_jobs_refuse_another_tenants_bank(
    api_session: Session, role_banks: dict[str, str], operation: str
) -> None:
    job = job_queue.enqueue(
        api_session,
        ORG_1,
        "pipeline_refresh",
        bank_id=role_banks[ORG_1],
        coalesce_key="bank-scope",
    )
    with pytest.raises(DBAPIError) as error, api_session.begin_nested():
        if operation == "update":
            job.bank_id = role_banks[ORG_2]
            api_session.flush()
        else:
            job_queue.enqueue(
                api_session,
                ORG_1,
                "pipeline_refresh",
                bank_id=role_banks[ORG_2],
                coalesce_key="bank-scope" if operation == "coalesce" else None,
            )
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    api_session.refresh(job)
    assert job.bank_id == role_banks[ORG_1]


def test_api_document_parse_updates_job_lifecycle(
    api_session: Session, fake_storage: FakeStorage
) -> None:
    ctx = TenantContext(organization_id=ORG_1)
    factories = ServiceFactories(api_session, fake_storage, get_settings(), ctx)
    document = factories.documents.create_uploaded()
    result = documents.request_parse(api_session, ctx, document.id)
    api_session.expire_all()
    job = api_session.get(Job, result.job_id)
    assert job is not None
    assert result.status == job.status == "completed"
    assert job.started_at is not None
    assert job.completed_at is not None and job.completed_at >= job.started_at
    assert job.progress == {"parsed_chunks": 1}
    assert job.error is None
    assert document.parse_status == "parsed"


@pytest.mark.parametrize("fail", [False, True])
def test_api_assessment_updates_job_lifecycle(
    api_session: Session, fake_storage: FakeStorage, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    ctx = TenantContext(organization_id=ORG_1)
    factories = ServiceFactories(api_session, fake_storage, get_settings(), ctx)
    assessment = factories.assessments.create()
    if fail:

        def refuse_scoring(*args: object, **kwargs: object) -> None:
            raise ValueError("Scoring unavailable")

        monkeypatch.setattr(assessments, "run_scoring", refuse_scoring)
    result = assessments.run_assessment(api_session, ctx, assessment.id)
    api_session.expire_all()
    job = api_session.get(Job, result.job_id)
    assert job is not None
    assert result.status == job.status == ("failed" if fail else "completed")
    assert job.started_at is not None
    assert job.completed_at is not None and job.completed_at >= job.started_at
    if fail:
        assert job.error == "Scoring unavailable"
    else:
        assert cast(object, job.progress["findings_created"]) == 1
        assert job.error is None


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


@pytest.mark.parametrize("role", ROLES)
def test_future_function_requires_an_explicit_execution_grant(
    role_engines: dict[str, Engine], role: str
) -> None:
    name = f"future_private_function_{uuid4().hex}"
    with role_engines["aqs_migrator"].begin() as connection:
        connection.execute(text("SET LOCAL ROLE aqs_owner"))
        connection.execute(
            text(f"CREATE FUNCTION {name}() RETURNS integer LANGUAGE sql AS 'SELECT 42'")
        )
        assert connection.scalar(text(f"SELECT {name}()")) == 42
    try:
        with role_engines[role].connect() as connection, pytest.raises(DBAPIError) as error:
            connection.execute(text(f"SELECT {name}()"))
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
        with role_engines["aqs_migrator"].begin() as connection:
            connection.execute(text("SET LOCAL ROLE aqs_owner"))
            connection.execute(text(f"GRANT EXECUTE ON FUNCTION {name}() TO {role}"))
        with role_engines[role].connect() as connection:
            assert connection.scalar(text(f"SELECT {name}()")) == 42
    finally:
        with role_engines["aqs_migrator"].begin() as connection:
            connection.execute(text("SET LOCAL ROLE aqs_owner"))
            connection.execute(text(f"DROP FUNCTION {name}()"))


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
