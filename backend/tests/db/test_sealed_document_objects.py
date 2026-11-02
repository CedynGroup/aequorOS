"""Stable identities are bank scoped, append-only evidence on real PostgreSQL."""

from collections.abc import Iterator
from typing import Protocol, cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError

from app.db.base import Base
from app.storage.sealed_documents import DocumentFamily
from tests.db.test_bank_organization_integrity import (
    BANK,
    ORG,
    OTHER_BANK,
    OTHER_ORG,
    bank_scope_scenario,
    pytestmark,
)
from tests.db.test_canonical_bank_integrity import SIBLING_BANK
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrate_isolated_postgres_schema,
)
from tests.support.sealed_documents import SOURCES, insert_source

__all__ = ["pytestmark"]
TABLES = ("sealed_document_objects", *(value[1] for value in SOURCES.values()))


@pytest.fixture(scope="module")
def sealed_schema() -> Iterator[MigratedPostgresSchema]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield from migrate_isolated_postgres_schema(monkeypatch, downgrade=False)


@pytest.fixture
def connection(sealed_schema: MigratedPostgresSchema) -> Iterator[Connection]:
    with sealed_schema.app_engine.connect() as conn, conn.begin() as transaction:
        bank_scope_scenario(conn)
        conn.execute(
            Base.metadata.tables["banks"]
            .insert()
            .values(
                id=SIBLING_BANK,
                organization_id=ORG,
                name="Sibling",
                short_name="Sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="bank",
                institution_type="universal_bank",
            )
        )
        for bank in (BANK, SIBLING_BANK):
            conn.execute(
                sa.text("UPDATE banks SET storage_slug = :slug WHERE id = :id"),
                {"slug": bank.lower(), "id": bank},
            )
        yield conn
        transaction.rollback()


def object_row(bank: str = BANK, organization: str = ORG) -> dict[str, object]:
    identifier = uuid4()
    return dict(
        id=identifier,
        organization_id=organization,
        bank_id=bank,
        storage_slug=bank.lower(),
        storage_tier="outputs",
        physical_key=f"sealed/v1/{identifier}",
        sha256="a" * 64,
        byte_length=128,
        context_format="plaintext-v1",
        context_path="legacy/archive.pdf",
        original_version_id="original-provider-version",
        published_by=uuid4(),
    )


def insert_object(connection: Connection, **overrides: object) -> UUID:
    row = object_row()
    row.update(overrides)
    connection.execute(Base.metadata.tables["sealed_document_objects"].insert().values(**row))
    return cast(UUID, row["id"])


def bind(
    connection: Connection, family: DocumentFamily, source: UUID, document: UUID, bank: str = BANK
) -> None:
    connection.execute(
        Base.metadata.tables[SOURCES[family][1]]
        .insert()
        .values(
            source_id=source,
            organization_id=ORG,
            bank_id=bank,
            object_id=document,
            bound_by=uuid4(),
        )
    )


def refuse(connection: Connection, statement: sa.Executable) -> None:
    with connection.begin_nested() as savepoint:
        with pytest.raises(DatabaseError):
            connection.execute(statement)
        savepoint.rollback()


@pytest.mark.parametrize("family", list(DocumentFamily))
def test_bindings_preserve_sources_and_refuse_scope_or_evidence_substitution(
    connection: Connection,
    family: DocumentFamily,
) -> None:
    source = insert_source(connection, family, ORG, BANK, "a" * 64, 128, "legacy/archive.pdf")
    table = Base.metadata.tables[SOURCES[family][0]]
    before = connection.execute(sa.select(table).where(table.c.id == source)).one()
    document = insert_object(connection)
    sibling = object_row(SIBLING_BANK)
    connection.execute(Base.metadata.tables["sealed_document_objects"].insert().values(**sibling))
    with connection.begin_nested() as savepoint:
        with pytest.raises(DatabaseError):
            bind(connection, family, source, cast(UUID, sibling["id"]), SIBLING_BANK)
        savepoint.rollback()
    for overrides in (
        {"sha256": "b" * 64},
        {"byte_length": 127},
        {"context_path": "another.pdf"},
        {"original_version_id": "different-version"},
    ):
        wrong = insert_object(connection, **overrides)
        with connection.begin_nested() as savepoint:
            with pytest.raises(DatabaseError, match="recorded evidence"):
                bind(connection, family, source, wrong)
            savepoint.rollback()
    bind(connection, family, source, document)
    assert connection.execute(sa.select(table).where(table.c.id == source)).one() == before
    with connection.begin_nested() as savepoint:
        with pytest.raises(DatabaseError):
            bind(connection, family, source, document)
        savepoint.rollback()
    binding = Base.metadata.tables[SOURCES[family][1]]
    for statement in (
        binding.update().values(object_id=cast(UUID, sibling["id"])),
        binding.delete(),
        sa.text(f"TRUNCATE {binding.name} CASCADE"),
    ):
        refuse(connection, statement)
    if family is DocumentFamily.SUPERVISORY_LETTER:
        with connection.begin_nested() as worker_scope:
            runtime = f"sealed_letter_worker_{uuid4().hex}"
            schema = connection.dialect.identifier_preparer.quote_identifier(
                str(connection.scalar(sa.text("SELECT current_schema()")))
            )
            connection.execute(sa.text(f'CREATE ROLE "{runtime}" NOLOGIN'))
            connection.execute(sa.text(f'GRANT "{runtime}" TO CURRENT_USER'))
            connection.execute(sa.text(f'GRANT USAGE ON SCHEMA {schema} TO "{runtime}"'))
            connection.execute(
                sa.text(f'GRANT SELECT, UPDATE ON bank_supervisory_addons TO "{runtime}"')
            )
            connection.execute(sa.text(f'SET LOCAL ROLE "{runtime}"'))
            assert not connection.scalar(
                sa.text(
                    "SELECT has_table_privilege(current_user, "
                    "'supervisory_letter_document_bindings', 'SELECT')"
                )
            )
            connection.execute(
                table.update()
                .where(table.c.id == source)
                .values(letter_reference="Updated draft reference")
            )
            refuse(
                connection,
                table.update().where(table.c.id == source).values(letter_sha256="b" * 64),
            )
            worker_scope.rollback()
        for column, value in (
            ("letter_sha256", "b" * 64),
            ("letter_byte_size", 127),
            ("letter_object_path", "changed.pdf"),
            ("bank_id", SIBLING_BANK),
        ):
            refuse(connection, table.update().where(table.c.id == source).values(**{column: value}))
    # The real FK blocks deleting a source even if its own deletion policy allows it.
    refuse(connection, table.delete().where(table.c.id == source))


@pytest.mark.parametrize("table", TABLES)
def test_force_rls_hides_other_tenant_and_refuses_inserts(
    connection: Connection, table: str
) -> None:
    document = insert_object(connection)
    for family in DocumentFamily:
        source = insert_source(connection, family, ORG, BANK, "a" * 64, 128, "legacy/archive.pdf")
        bind(connection, family, source, document)
    connection.execute(
        sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": OTHER_ORG}
    )
    assert connection.scalar(sa.text(f"SELECT count(*) FROM {table}")) == 0
    if table == "sealed_document_objects":
        refuse(connection, Base.metadata.tables[table].insert().values(**object_row()))
    else:
        refuse(
            connection,
            Base.metadata.tables[table]
            .insert()
            .values(
                source_id=uuid4(),
                organization_id=ORG,
                bank_id=BANK,
                object_id=document,
                bound_by=uuid4(),
            ),
        )


def test_catalogue_constraints_and_immutability(connection: Connection) -> None:
    for overrides in (
        {"bank_id": OTHER_BANK},
        {"storage_slug": SIBLING_BANK.lower()},
        {"sha256": "z" * 64},
        {"byte_length": -1},
        {"physical_key": "editable.pdf"},
        {"context_format": "unknown"},
        {"context_format": "aws-esdk-object-v1"},
        {"envelope_id": uuid4()},
    ):
        row = object_row()
        row.update(overrides)
        refuse(connection, Base.metadata.tables["sealed_document_objects"].insert().values(**row))
    insert_object(connection)
    table = Base.metadata.tables["sealed_document_objects"]
    for statement in (
        table.update().values(sha256="b" * 64),
        table.delete(),
        sa.text("TRUNCATE sealed_document_objects CASCADE"),
    ):
        refuse(connection, statement)


def test_envelope_dependency_cannot_cross_banks_of_one_organization(connection: Connection) -> None:
    key, envelope = uuid4(), uuid4()
    connection.execute(
        Base.metadata.tables["bank_encryption_keys"]
        .insert()
        .values(
            id=key,
            organization_id=ORG,
            bank_id=SIBLING_BANK,
            storage_slug=SIBLING_BANK.lower(),
            provider="aws_kms",
            key_id="synthetic-key",
            region="us-east-1",
            owner_account="111122223333",
            status="active",
        )
    )
    connection.execute(
        Base.metadata.tables["object_key_envelopes"]
        .insert()
        .values(
            id=envelope,
            organization_id=ORG,
            bank_id=SIBLING_BANK,
            context_digest="a" * 64,
            wrapped_key=b"synthetic",
            provider="aws_kms",
            key_id="synthetic-key",
            region="us-east-1",
            owner_account="111122223333",
        )
    )
    row = object_row()
    row.update(context_format="aws-esdk-object-v1", envelope_id=envelope)
    refuse(connection, Base.metadata.tables["sealed_document_objects"].insert().values(**row))


class Migration(Protocol):
    def upgrade(self) -> None: ...
    def downgrade(self) -> None: ...


def test_migration_round_trip_and_restrictive_grants(sealed_schema: MigratedPostgresSchema) -> None:
    script = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202611010105")
    assert script is not None
    migration = cast(Migration, script.module)
    with sealed_schema.app_engine.connect() as conn, conn.begin() as transaction:
        for table in TABLES:
            for privilege in ("SELECT", "INSERT"):
                assert conn.scalar(
                    sa.text("SELECT has_table_privilege('aqs_api', :t, :p)"),
                    {"t": table, "p": privilege},
                )
            for role in ("aqs_api", "aqs_worker", "aqs_bi", "aqs_operator", "aqs_audit_verifier"):
                for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                    assert not conn.scalar(
                        sa.text("SELECT has_table_privilege(:r, :t, :p)"),
                        {"r": role, "t": table, "p": privilege},
                    )
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
            assert all(
                conn.scalar(sa.text("SELECT to_regclass(:t)"), {"t": t}) is None for t in TABLES
            )
            migration.upgrade()
            assert all(conn.scalar(sa.text("SELECT to_regclass(:t)"), {"t": t}) for t in TABLES)
        transaction.rollback()
