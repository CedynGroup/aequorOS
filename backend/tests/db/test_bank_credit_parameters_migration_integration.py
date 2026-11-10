"""Bank-local CREDIT cutover preserves values, not shared write authority."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date
from typing import cast
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from alembic import command
from app.core import audit_attribution
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.identity.service import authentication, authorization
from app.models import Organization, User
from tests.support.database import managed_engine
from tests.support.postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    forward_migrated_postgres_schema,
    postgres_administrator_url,
    postgres_schema_url,
)

__all__ = ["forward_migrated_postgres_schema"]
pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required"),
]
PREVIOUS = "202611070115"
ORG = "OR-CREDM001"
OTHER = "OR-CREDM002"
EMPTY = "OR-CREDM003"
BANK = "BK-CREDM001"
SIBLING = "BK-CREDM002"
FOREIGN = "BK-CREDM003"
DIFFERENT_JURISDICTION = "BK-CREDM004"
UNCONFIGURED = "BK-CREDM005"
TABLES = (
    "bank_concentration_limit",
    "bank_credit_threshold",
    "bank_ecl_assumption",
    "bank_crm_haircut",
)
ARCHIVES = (
    "param_concentration_limit",
    "param_credit_threshold",
    "param_ecl_assumption",
    "param_crm_haircut",
)
AUTHORITY = ("organizations", "banks", "users", "authorization_bindings", "refresh_tokens")


class TableSpec:
    """The columns and probe values that differ per CREDIT register."""

    def __init__(
        self,
        local: str,
        archive: str,
        value_column: str,
        specific: str,
        row: dict[str, object],
    ) -> None:
        self.local = local
        self.archive = archive
        self.value_column = value_column
        self.specific = specific
        self.row = row


SPECS = (
    TableSpec(
        "bank_concentration_limit",
        "param_concentration_limit",
        "value",
        "dimension, limit_kind, bucket_key, value",
        # A named bucket, not NULL: Postgres treats NULLs as distinct in a
        # unique key, so only a named bucket pins uq_..._scope in the probe.
        {
            "dimension": "sector",
            "limit_kind": "share_of_book_pct",
            "bucket_key": "cp:SYNTH",
            "value": 25,
        },
    ),
    TableSpec(
        "bank_credit_threshold",
        "param_credit_threshold",
        "value_pct",
        "threshold_code, value_pct",
        {"threshold_code": "npl_board_trigger_pct", "value_pct": 7},
    ),
    TableSpec(
        "bank_ecl_assumption",
        "param_ecl_assumption",
        "pd_pct",
        "segment, stage, pd_pct, lgd_pct",
        {"segment": "ALL", "stage": 1, "pd_pct": 2, "lgd_pct": 40},
    ),
    TableSpec(
        "bank_crm_haircut",
        "param_crm_haircut",
        "haircut_pct",
        "collateral_class, haircut_pct",
        {"collateral_class": "CASH", "haircut_pct": 5},
    ),
)
NOTED_TABLES = {"bank_ecl_assumption", "bank_crm_haircut"}


def _spec(table: str) -> TableSpec:
    for spec in SPECS:
        if spec.local == table:
            return spec
    raise AssertionError(f"Unknown bank-local CREDIT table {table}")


def _values(
    connection: Connection,
    statement: TextClause,
    params: dict[str, object] | None = None,
) -> list[object]:
    # Raw SQLAlchemy scalar results have no static column types at this boundary.
    return list(cast("list[object]", connection.execute(statement, params or {}).scalars().all()))


def _scalar(
    connection: Connection,
    statement: TextClause,
    params: dict[str, object] | None = None,
) -> object:
    # Raw SQLAlchemy scalar results have no static column types at this boundary.
    return cast(object, connection.scalar(statement, params or {}))


def _bank_scopes(connection: Connection, organization: str) -> list[tuple[str, str, str]]:
    # Validate the untyped raw-SQL row boundary before iterating typed bank scopes.
    rows = cast(
        object,
        connection.execute(
            text("SELECT id, jurisdiction_code, institution_type FROM banks "
                 "WHERE organization_id = :org"),
            {"org": organization},
        )
        .tuples()
        .all(),
    )
    return TypeAdapter(list[tuple[str, str, str]]).validate_python(rows)


def _tenant(connection: Connection, organization: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :org, true)"), {"org": organization}
    )


def _snapshot(schema: MigratedPostgresSchema, tables: tuple[str, ...]) -> list[str]:
    snapshots: list[str] = []
    with schema.app_engine.begin() as connection:
        for org in (ORG, OTHER, EMPTY):
            _tenant(connection, org)
            for table in tables:
                scope_column = "id" if table == "organizations" else "organization_id"
                value = _scalar(
                    connection,
                    text(
                        "SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY id)::text, '[]') "
                        f"FROM {table} t WHERE {scope_column} = :org"
                    ),
                    {"org": org},
                )
                assert isinstance(value, str)
                snapshots.append(value)
    return snapshots


def _audit_definitions(schema: MigratedPostgresSchema) -> list[str]:
    with schema.app_engine.connect() as connection:
        return [
            str(value)
            for value in _values(
                connection,
                text(
                    "SELECT pg_get_functiondef(oid) FROM pg_proc "
                    "WHERE pronamespace = current_schema()::regnamespace AND proname IN "
                    "('bank_audit_previous_link', 'bank_audit_inspection_context') "
                    "ORDER BY proname"
                ),
            )
        ]


def _audit_security(schema: MigratedPostgresSchema) -> str:
    with schema.app_engine.connect() as connection:
        result = _scalar(
            connection,
            text(
                "SELECT jsonb_agg(jsonb_build_array(proname, proowner::regrole::text, "
                "proacl::text, provolatile, prosecdef, proconfig) ORDER BY proname)::text "
                "FROM pg_proc WHERE pronamespace = current_schema()::regnamespace "
                "AND proname IN ('bank_audit_previous_link', 'bank_audit_inspection_context')"
            ),
        )
        assert isinstance(result, str)
        return result


def _seed_shared(schema: MigratedPostgresSchema) -> None:
    for org in (ORG, OTHER, EMPTY):
        with Session(schema.app_engine) as db:
            db.info["organization_id"] = org
            db.add(Organization(id=org, name=f"Synthetic migration {org}"))
            db.flush()
            user = User(organization_id=org, email=f"{org}@example.test", role="analyst")
            db.add(user)
            db.flush()
            authorization.create_role_binding(
                db,
                organization_id=org,
                principal_user_id=user.id,
                principal_type=PrincipalType.HUMAN,
                role_bundle=RoleBundle.ANALYST,
                scope=authorization.BindingScope(
                    InstitutionScope.ORGANIZATION,
                    None,
                    ModuleScope.CREDIT,
                    SensitivityScope.RESTRICTED,
                ),
                grantor=authorization.GrantorRef(GrantorType.SYSTEM, "migration-regression"),
                reason="Existing explicit authority",
                commit=False,
            )
            _ = authentication.issue_tokens(db, user)
            db.commit()
    with schema.app_engine.begin() as connection:
        for bank, org, jurisdiction in (
            (BANK, ORG, "GH"),
            (SIBLING, ORG, "GH"),
            (FOREIGN, OTHER, "GH"),
            (DIFFERENT_JURISDICTION, ORG, "NG"),
            (UNCONFIGURED, OTHER, "NG"),
        ):
            _tenant(connection, org)
            connection.execute(
                text(
                    "INSERT INTO banks (id, organization_id, name, short_name, currency, "
                    "jurisdiction_code, license_type, institution_type, created_at, updated_at) "
                    "SELECT :bank, :org, :bank, :bank, currency_code, code, 'bank', "
                    "'universal_bank', now(), now() FROM jurisdictions WHERE code = :jurisdiction"
                ),
                {"bank": bank, "org": org, "jurisdiction": jurisdiction},
            )
        for org, jurisdiction, offset in (
            (ORG, "GH", 0),
            (ORG, "NG", 10),
            (OTHER, "GH", 20),
            (EMPTY, "GH", 30),
        ):
            _tenant(connection, org)
            for year, _value in ((2024, 17), (2026, 19), (2030, 23)):
                effective_to = date(year + 1, 12, 31) if year == 2024 else None
                for spec in SPECS:
                    notes = f"Preserve generation {year}/{offset}"
                    columns = ", ".join(
                        (
                            "id",
                            "organization_id",
                            "jurisdiction_code",
                            spec.specific,
                            "effective_from",
                            "effective_to",
                            "approved_by",
                            "approval_timestamp",
                            *(() if spec.local not in NOTED_TABLES else ("notes",)),
                            "created_at",
                            "updated_at",
                        )
                    )
                    placeholders = ", ".join(
                        (
                            ":id",
                            ":org",
                            ":jurisdiction",
                            *(f":{name}" for name in spec.specific.split(", ")),
                            ":start",
                            ":end",
                            "'Board evidence'",
                            "'2024-01-02 10:11:12+00'",
                            *(() if spec.local not in NOTED_TABLES else (":notes",)),
                            "'2024-01-01 01:02:03+00'",
                            "'2024-01-02 04:05:06+00'",
                        )
                    )
                    params: dict[str, object] = {
                        "id": uuid4(),
                        "org": org,
                        "jurisdiction": jurisdiction,
                        **spec.row,
                        "start": date(year, 1, 1),
                        "end": effective_to,
                        "notes": notes,
                    }
                    connection.execute(
                        text(f"INSERT INTO {spec.archive} ({columns}) VALUES ({placeholders})"),
                        params,
                    )


@pytest.fixture
def bank_local_schema(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> Iterator[MigratedPostgresSchema]:
    schema = forward_migrated_postgres_schema
    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS)
    _seed_shared(schema)
    before = _snapshot(schema, (*AUTHORITY, *ARCHIVES, "audit_events"))
    audit_security = _audit_security(schema)
    command.upgrade(config, "head")
    assert schema.tables(set(TABLES)) == set(TABLES), "bank-local CREDIT storage is missing"
    assert _snapshot(schema, (*AUTHORITY, *ARCHIVES, "audit_events")) == before
    assert _audit_security(schema) == audit_security
    yield schema


def test_all_generations_are_copied_only_to_matching_existing_banks(
    bank_local_schema: MigratedPostgresSchema,
) -> None:
    schema = bank_local_schema
    for org in (ORG, OTHER, EMPTY):
        with schema.app_engine.begin() as connection:
            _tenant(connection, org)
            for spec in SPECS:
                expected = connection.execute(
                    text(
                        "SELECT b.id, (to_jsonb(p) - 'id')::text FROM "
                        f"{spec.archive} p JOIN banks b ON b.organization_id = p.organization_id "
                        "AND b.jurisdiction_code = p.jurisdiction_code "
                        "ORDER BY b.id, p.effective_from"
                    )
                ).all()
                actual = connection.execute(
                    text(
                        "SELECT bank_id, (to_jsonb(p) - 'id' - 'bank_id')::text "
                        f"FROM {spec.local} p ORDER BY bank_id, effective_from"
                    )
                ).all()
                assert actual == expected
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) FROM {spec.local} WHERE bank_id = :bank"),
                        {"bank": UNCONFIGURED},
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text(
                            f"SELECT count(*) FROM {spec.local} p"
                            f" JOIN {spec.archive} a ON a.id = p.id"
                        )
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) = count(DISTINCT id) FROM {spec.local}")
                    )
                    is True
                )
            for bank, jurisdiction, _kind in _bank_scopes(connection, org):
                for spec in SPECS:
                    for as_of in (date(2024, 6, 1), date(2026, 6, 1), date(2031, 6, 1)):
                        params = {"bank": bank, "jurisdiction": jurisdiction, "as_of": as_of}
                        effective = (
                            "effective_from <= :as_of AND "
                            "(effective_to IS NULL OR effective_to > :as_of) "
                            "ORDER BY effective_from DESC LIMIT 1"
                        )
                        assert connection.scalar(
                            text(
                                f"SELECT {spec.value_column} FROM {spec.local} "
                                "WHERE bank_id = :bank AND jurisdiction_code = :jurisdiction "
                                f"AND {effective}"
                            ),
                            params,
                        ) == connection.scalar(
                            text(
                                f"SELECT {spec.value_column} FROM {spec.archive} "
                                "WHERE jurisdiction_code = :jurisdiction "
                                f"AND {effective}"
                            ),
                            params,
                        )


def test_bank_local_integrity_rls_retention_and_archive_freeze(
    bank_local_schema: MigratedPostgresSchema,
) -> None:
    schema = bank_local_schema
    before = _snapshot(schema, ARCHIVES)
    with schema.app_engine.connect() as connection, connection.begin():
        _tenant(connection, ORG)
        assert connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one() == (False, False)
        for spec in SPECS:
            local, archive = spec.local, spec.archive
            names = {
                f"pk_{local}",
                f"fk_{local}_org_bank",
                f"fk_{local}_organization_id",
                f"uq_{local}_scope",
            }
            if local == "bank_ecl_assumption":
                names.add(f"ck_{local}_stage")
            if local == "bank_concentration_limit":
                names.update({f"ck_{local}_dimension", f"ck_{local}_kind"})
            assert schema.constraints(names) == names
            shape = text(
                "SELECT attname, format_type(atttypid, atttypmod), attnotnull FROM pg_attribute "
                "WHERE attrelid = CAST(:table AS regclass) AND attnum > 0 AND NOT attisdropped "
                "AND attname <> 'bank_id' ORDER BY attname"
            )
            assert connection.execute(shape, {"table": local}).all() == connection.execute(
                shape,
                {"table": archive},
            ).all()
            assert connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:table AS regclass)"
                ),
                {"table": local},
            ).one() == (True, True)
            assert connection.scalar(text(f"SELECT count(*) FROM {local}")) == 9
            _tenant(connection, OTHER)
            # Only FOREIGN shares a seeded jurisdiction; UNCONFIGURED (NG) has
            # no shared generation, so it correctly receives nothing.
            assert connection.scalar(text(f"SELECT count(*) FROM {local}")) == 3
            _tenant(connection, "")
            assert connection.scalar(text(f"SELECT count(*) FROM {local}")) == 0
            _tenant(connection, ORG)
            columns = [
                str(value)
                for value in _values(
                    connection,
                    text(
                        "SELECT attname FROM pg_attribute WHERE attrelid = "
                        "CAST(:table AS regclass) AND attnum > 0 AND NOT attisdropped "
                        "ORDER BY attnum"
                    ),
                    {"table": local},
                )
            ]
            for replacement, message in (
                ({"id": "gen_random_uuid()"}, f"uq_{local}_scope"),
                ({"id": "gen_random_uuid()", "bank_id": f"'{FOREIGN}'"}, f"fk_{local}_org_bank"),
                ({"id": "gen_random_uuid()", "bank_id": "NULL"}, "not-null"),
                (
                    {"id": "gen_random_uuid()", "organization_id": f"'{OTHER}'"},
                    "row-level security",
                ),
            ):
                select = ", ".join(replacement.get(column, column) for column in columns)
                with pytest.raises(DBAPIError, match=message), connection.begin_nested():
                    connection.execute(
                        text(
                            f"INSERT INTO {local} ({', '.join(columns)}) SELECT {select} "
                            f"FROM {local} WHERE bank_id = :bank LIMIT 1"
                        ),
                        {"bank": BANK},
                    )
            for statement in (
                f"UPDATE {local} SET approved_by = 'rewritten'",
                f"DELETE FROM {local}",
                f"TRUNCATE {local}",
                f"UPDATE {archive} SET effective_to = DATE '2029-12-31'",
                f"UPDATE {archive} SET effective_to = DATE '2029-12-31' WHERE false",
                f"INSERT INTO {archive} SELECT * FROM {archive} LIMIT 1",
                f"DELETE FROM {archive}",
                f"TRUNCATE {archive}",
            ):
                with (
                    pytest.raises(DBAPIError, match="sealed|retained evidence|append-only"),
                    connection.begin_nested(),
                ):
                    # Owners can restore ACLs: the actual DB guards must still refuse.
                    connection.execute(
                        text(
                            f"GRANT INSERT, UPDATE, DELETE, TRUNCATE ON {local}, {archive} "
                            "TO CURRENT_USER"
                        )
                    )
                    connection.execute(text(statement))
            # Only retirement metadata may change in a bank-local approved generation.
            with connection.begin_nested() as savepoint:
                connection.execute(
                    text(
                        f"UPDATE {local} SET effective_to = '2029-12-31', updated_at = now() "
                        "WHERE bank_id = :bank AND effective_from = '2026-01-01'"
                    ),
                    {"bank": BANK},
                )
                assert (
                    connection.scalar(
                        text(
                            f"SELECT effective_to FROM {local} WHERE bank_id = :bank "
                            "AND effective_from = '2026-01-01'"
                        ),
                        {"bank": SIBLING},
                    )
                    is None
                )
                savepoint.rollback()
            assert connection.execute(
                text(
                    "SELECT has_table_privilege('aqs_api', :table, 'SELECT'), "
                    "has_table_privilege('aqs_api', :table, 'INSERT'), "
                    "has_table_privilege('aqs_api', :table, 'UPDATE'), "
                    "has_table_privilege('aqs_api', :table, 'DELETE'), "
                    "has_table_privilege('aqs_api', :table, 'TRUNCATE')"
                ),
                {"table": local},
            ).one() == (True, True, True, False, False)
            assert connection.execute(
                text(
                    "SELECT has_table_privilege('aqs_api', :table, 'INSERT'), "
                    "has_table_privilege('aqs_api', :table, 'UPDATE')"
                ),
                {"table": archive},
            ).one() == (False, False)
    assert _snapshot(schema, ARCHIVES) == before


def test_clean_rollback_replays_without_consulting_application_ownership(
    bank_local_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = bank_local_schema
    before = _snapshot(schema, (*AUTHORITY, *ARCHIVES, "audit_events"))
    definitions = _audit_definitions(schema)
    command.downgrade(alembic_config_for_app(), PREVIOUS)
    assert schema.tables(set(TABLES)) == set()
    assert _snapshot(schema, (*AUTHORITY, *ARCHIVES, "audit_events")) == before

    def refuse_current_builder(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Migration consulted mutable application attribution")

    for name in ("subject_bank_sql", "staff_bank_sql", "organization_binding_sql", "uuid_sql"):
        monkeypatch.setattr(audit_attribution, name, refuse_current_builder)
    monkeypatch.setitem(audit_attribution.SUBJECT_TABLES, "future_subject", "future_table")
    command.upgrade(alembic_config_for_app(), "head")
    assert _audit_definitions(schema) == definitions
    assert _snapshot(schema, (*AUTHORITY, *ARCHIVES, "audit_events")) == before


@pytest.mark.parametrize("spec", SPECS, ids=lambda spec: spec.local)
@pytest.mark.parametrize("write", ["retire", "new_generation"])
def test_downgrade_refuses_bank_local_divergence(
    bank_local_schema: MigratedPostgresSchema,
    spec: TableSpec,
    write: str,
) -> None:
    schema = bank_local_schema
    before = _snapshot(schema, ARCHIVES)
    with schema.app_engine.begin() as connection:
        _tenant(connection, ORG)
        if write == "retire":
            connection.execute(
                text(
                    f"UPDATE {spec.local} SET effective_to = '2029-12-31', updated_at = now() "
                    "WHERE bank_id = :bank AND effective_from = '2026-01-01'"
                ),
                {"bank": BANK},
            )
        else:
            noted = spec.local in NOTED_TABLES
            connection.execute(
                text(
                    f"INSERT INTO {spec.local} SELECT gen_random_uuid(), organization_id, "
                    "bank_id, jurisdiction_code, DATE '2040-01-01', NULL, approved_by, "
                    f"approval_timestamp, {spec.specific}, "
                    + (":notes, " if noted else "")
                    + f"created_at, updated_at FROM {spec.local} "
                    "WHERE bank_id = :bank LIMIT 1"
                ),
                {"bank": BANK, "notes": "A divergent future generation"},
            )
    local = _snapshot(schema, TABLES)
    with pytest.raises((RuntimeError, DBAPIError), match="bank-local credit.*diverg"):
        command.downgrade(alembic_config_for_app(), PREVIOUS)
    assert _snapshot(schema, TABLES) == local
    assert _snapshot(schema, ARCHIVES) == before


@pytest.fixture
def credit_administrator(
    bank_local_schema: MigratedPostgresSchema,
    request: pytest.FixtureRequest,
) -> Iterator[Engine]:
    url = postgres_schema_url(postgres_administrator_url(), bank_local_schema.schema_name)
    engine = managed_engine(request, url, administrative=True)
    try:
        yield engine
    finally:
        engine.dispose()


def test_audit_reader_attributes_new_subjects_directly_but_keeps_shared_history(
    credit_administrator: Engine,
) -> None:
    with credit_administrator.connect() as connection, connection.begin():
        _tenant(connection, ORG)
        session = uuid4()
        connection.execute(
            text(
                "INSERT INTO operator_inspector_sessions (id, organization_id, started_by, "
                "mode, reason, started_at, expires_at, support_reference) VALUES "
                "(:id, :org, 'staff@example.test', 'consent', 'CREDIT migration regression', "
                "now(), now() + interval '1 hour', 'SYNTHETIC-CREDIT')"
            ),
            {"id": session, "org": ORG},
        )
        evidence: dict[str, object] = {
            "act_operator": "staff@example.test",
            "impersonation_session_id": str(session),
        }
        ids: dict[str, str] = {}
        for kind, entity, detail in (
            ("bank_ecl_assumption", BANK, evidence),
            ("bank_crm_haircut", BANK, evidence),
            ("bank_concentration_limit", BANK, evidence),
            ("bank_credit_threshold", BANK, evidence),
            ("param_ecl_assumption", BANK, evidence),
            ("conflicting", BANK, {**evidence, "bank_id": SIBLING}),
            ("foreign", FOREIGN, evidence),
        ):
            event = uuid4()
            ids[kind] = str(event)
            connection.execute(
                text(
                    "INSERT INTO audit_events (id, organization_id, event_type, entity_type, "
                    "entity_id, details, created_at) VALUES (:id, :org, 'CREDIT.test', :kind, "
                    ":entity, CAST(:details AS jsonb), now())"
                ),
                {
                    "id": event,
                    "org": ORG,
                    "entity": entity,
                    "details": json.dumps(detail),
                    "kind": "bank_ecl_assumption" if kind in {"conflicting", "foreign"} else kind,
                },
            )
        # Execute the real FORCE-RLS policies and SECURITY DEFINER functions as API.
        connection.execute(text("SET LOCAL ROLE aqs_api"))
        expectations: tuple[tuple[str, set[str]], ...] = (
            (
                BANK,
                {
                    "bank_ecl_assumption",
                    "bank_crm_haircut",
                    "bank_concentration_limit",
                    "bank_credit_threshold",
                    "param_ecl_assumption",
                },
            ),
            (SIBLING, {"param_ecl_assumption"}),
            (FOREIGN, set()),
        )
        for bank, expected in expectations:
            connection.execute(
                text("SELECT set_config('app.audit_bank_id', :bank, true)"),
                {"bank": bank},
            )
            visible = {
                str(value)
                for value in _values(
                    connection,
                    text(
                        "SELECT event_id FROM audit_chain_entries WHERE event_id = ANY("
                        "CAST(:ids AS uuid[]))"
                    ),
                    {"ids": list(ids.values())},
                )
            }
            assert visible == {ids[kind] for kind in expected}
            for kind, event in ids.items():
                assert connection.scalar(
                    text(
                        "SELECT bank_audit_previous_link(c.stream, c.sequence, c.previous_hash) "
                        "FROM audit_chain_entries c WHERE event_id = CAST(:event AS uuid)"
                    ),
                    {"event": event},
                ) is (True if kind in expected else None)
                context = _scalar(
                    connection,
                    text(
                        "SELECT bank_audit_inspection_context('audit_events', CAST(:event AS uuid))"
                    ),
                    {"event": event},
                )
                assert context == (
                    {
                        "reason": "CREDIT migration regression",
                        "support_reference": "SYNTHETIC-CREDIT",
                    }
                    if bank == BANK and kind in expected
                    else None
                )


def test_downgrade_restores_liquidity_audit_readers(
    bank_local_schema: MigratedPostgresSchema,
    credit_administrator: Engine,
) -> None:
    """Rolling back CREDIT keeps the 202611040110 liquidity readers working.

    The liquidity writers attribute the bank directly in ``entity_id`` with no
    ``details.bank_id``; a CREDIT downgrade must not fall back to the frozen
    202610120100 predicates that recognize neither bank-local liquidity type.
    """
    with credit_administrator.connect() as connection, connection.begin():
        _tenant(connection, ORG)
        session = uuid4()
        connection.execute(
            text(
                "INSERT INTO operator_inspector_sessions (id, organization_id, started_by, "
                "mode, reason, started_at, expires_at, support_reference) VALUES "
                "(:id, :org, 'staff@example.test', 'consent', 'CREDIT downgrade regression', "
                "now(), now() + interval '1 hour', 'SYNTHETIC-CREDIT-DOWNGRADE')"
            ),
            {"id": session, "org": ORG},
        )
        evidence: dict[str, object] = {
            "act_operator": "staff@example.test",
            "impersonation_session_id": str(session),
        }
        ids: dict[str, str] = {}
        for kind, entity, detail in (
            ("bank_liquidity_threshold", BANK, evidence),
            ("bank_liquidity_haircut", BANK, evidence),
            ("param_liquidity_threshold", BANK, evidence),
            ("foreign", FOREIGN, evidence),
        ):
            event = uuid4()
            ids[kind] = str(event)
            connection.execute(
                text(
                    "INSERT INTO audit_events (id, organization_id, event_type, entity_type, "
                    "entity_id, details, created_at) VALUES (:id, :org, 'LIQ.test', :kind, "
                    ":entity, CAST(:details AS jsonb), now())"
                ),
                {
                    "id": event,
                    "org": ORG,
                    "entity": entity,
                    "details": json.dumps(detail),
                    "kind": "bank_liquidity_threshold" if kind == "foreign" else kind,
                },
            )

    command.downgrade(alembic_config_for_app(), PREVIOUS)

    expected_visible = (
        ids["bank_liquidity_threshold"],
        ids["bank_liquidity_haircut"],
        ids["param_liquidity_threshold"],
    )
    with credit_administrator.connect() as connection, connection.begin():
        _tenant(connection, ORG)
        connection.execute(text("SET LOCAL ROLE aqs_api"))
        connection.execute(
            text("SELECT set_config('app.audit_bank_id', :bank, true)"), {"bank": BANK}
        )
        visible = {
            str(value)
            for value in _values(
                connection,
                text(
                    "SELECT event_id FROM audit_chain_entries WHERE event_id = ANY("
                    "CAST(:ids AS uuid[]))"
                ),
                {"ids": list(ids.values())},
            )
        }
        assert visible == set(expected_visible)
        for kind in ("bank_liquidity_threshold", "bank_liquidity_haircut"):
            assert connection.scalar(
                text(
                    "SELECT bank_audit_previous_link(c.stream, c.sequence, c.previous_hash) "
                    "FROM audit_chain_entries c WHERE event_id = CAST(:event AS uuid)"
                ),
                {"event": ids[kind]},
            ) is True
            context = _scalar(
                connection,
                text(
                    "SELECT bank_audit_inspection_context('audit_events', CAST(:event AS uuid))"
                ),
                {"event": ids[kind]},
            )
            assert context == {
                "reason": "CREDIT downgrade regression",
                "support_reference": "SYNTHETIC-CREDIT-DOWNGRADE",
            }
