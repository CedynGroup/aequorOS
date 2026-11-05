"""Bank-local LIQ cutover preserves values, not shared write authority."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
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
from tests.db.test_postgres_migrations_integration import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    forward_migrated_postgres_schema,
    postgres_administrator_url,
    postgres_schema_url,
)
from tests.support.database import managed_engine

__all__ = ["forward_migrated_postgres_schema"]
pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required"),
]
PREVIOUS = "202611040108"
ORG = "OR-LIQM0001"
OTHER = "OR-LIQM0002"
EMPTY = "OR-LIQM0003"
BANK = "BK-LIQM0001"
SIBLING = "BK-LIQM0002"
SDI = "BK-LIQM0003"
FOREIGN = "BK-LIQM0004"
DIFFERENT_JURISDICTION = "BK-LIQM0005"
UNCONFIGURED = "BK-LIQM0006"
TABLES = ("bank_liquidity_threshold", "bank_liquidity_haircut")
ARCHIVES = ("param_liquidity_threshold", "param_liquidity_haircut")
AUTHORITY = ("organizations", "banks", "users", "authorization_bindings", "refresh_tokens")


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
            text(
                "SELECT id, jurisdiction_code, institution_type FROM banks "
                "WHERE organization_id = :org"
            ),
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
                    ModuleScope.LIQUIDITY,
                    SensitivityScope.RESTRICTED,
                ),
                grantor=authorization.GrantorRef(GrantorType.SYSTEM, "migration-regression"),
                reason="Existing explicit authority",
                commit=False,
            )
            _ = authentication.issue_tokens(db, user)
            db.commit()
    with schema.app_engine.begin() as connection:
        for bank, org, jurisdiction, kind in (
            (BANK, ORG, "GH", "universal_bank"),
            (SIBLING, ORG, "GH", "financial_holding_company"),
            (SDI, ORG, "GH", "savings_and_loans"),
            (FOREIGN, OTHER, "GH", "universal_bank"),
            (DIFFERENT_JURISDICTION, ORG, "NG", "universal_bank"),
            (UNCONFIGURED, OTHER, "NG", "universal_bank"),
        ):
            _tenant(connection, org)
            connection.execute(
                text(
                    "INSERT INTO banks (id, organization_id, name, short_name, currency, "
                    "jurisdiction_code, license_type, institution_type, created_at, updated_at) "
                    "SELECT :bank, :org, :bank, :bank, currency_code, code, 'bank', :kind, "
                    "now(), now() FROM jurisdictions WHERE code = :jurisdiction"
                ),
                {"bank": bank, "org": org, "kind": kind, "jurisdiction": jurisdiction},
            )
        for org, jurisdiction, institution_class, offset in (
            (ORG, "GH", "bank", 0),
            (ORG, "GH", "sdi", 10),
            (ORG, "NG", "bank", 20),
            (OTHER, "GH", "bank", 30),
            (EMPTY, "GH", "bank", 40),
        ):
            _tenant(connection, org)
            for year, value in ((2024, 17), (2026, 19), (2030, 23)):
                connection.execute(
                    text(
                        "INSERT INTO param_liquidity_threshold (id, organization_id, "
                        "jurisdiction_code, institution_class, threshold_code, threshold_pct, "
                        "effective_from, effective_to, approved_by, approval_timestamp, notes, "
                        "created_at, updated_at) VALUES (:id, :org, :jurisdiction, :class, "
                        "'liquid_assets_total_deposits', :value, :start, :end, 'Board evidence', "
                        "'2024-01-02 10:11:12+00', :notes, '2024-01-01 01:02:03+00', "
                        "'2024-01-02 04:05:06+00')"
                    ),
                    {
                        "id": uuid4(),
                        "org": org,
                        "jurisdiction": jurisdiction,
                        "class": institution_class,
                        "value": Decimal(value + offset),
                        "start": date(year, 1, 1),
                        "end": date(year + 1, 12, 31) if year == 2024 else None,
                        "notes": f"Preserve generation {year}/{offset}",
                    },
                )
                # Haircuts are shared across institution classes, not duplicated by class.
                if institution_class == "sdi":
                    continue
                connection.execute(
                    text(
                        "INSERT INTO param_liquidity_haircut (id, organization_id, "
                        "jurisdiction_code, asset_class, haircut_pct, "
                        "effective_from, effective_to, "
                        "approved_by, approval_timestamp, notes, created_at, updated_at) "
                        "VALUES (:id, :org, :jurisdiction, 'government_bond', :value, :start, "
                        ":end, 'Management evidence', '2024-01-02 10:11:12+00', :notes, "
                        "'2024-01-01 01:02:03+00', '2024-01-02 04:05:06+00')"
                    ),
                    {
                        "id": uuid4(),
                        "org": org,
                        "jurisdiction": jurisdiction,
                        "value": Decimal(value + offset) / 100,
                        "start": date(year, 1, 1),
                        "end": date(year + 1, 12, 31) if year == 2024 else None,
                        "notes": f"Preserve haircut {year}/{offset}",
                    },
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
    assert schema.tables(set(TABLES)) == set(TABLES), "bank-local LIQ storage is missing"
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
            for local, archive in zip(TABLES, ARCHIVES, strict=True):
                class_match = (
                    "AND p.institution_class = t.institution_class"
                    if local.endswith("threshold")
                    else ""
                )
                expected = connection.execute(
                    text(
                        "SELECT b.id, (to_jsonb(p) - 'id')::text FROM "
                        f"{archive} p JOIN banks b ON b.organization_id = p.organization_id "
                        "AND b.jurisdiction_code = p.jurisdiction_code JOIN institution_types t "
                        f"ON t.type_code = b.institution_type {class_match} "
                        "ORDER BY b.id, p.effective_from"
                    )
                ).all()
                actual = connection.execute(
                    text(
                        f"SELECT bank_id, (to_jsonb(p) - 'id' - 'bank_id')::text FROM {local} p "
                        "ORDER BY bank_id, effective_from"
                    )
                ).all()
                assert actual == expected
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) FROM {local} WHERE bank_id = :bank"),
                        {"bank": UNCONFIGURED},
                    )
                    == 0
                )
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) FROM {local} p JOIN {archive} a ON a.id = p.id")
                    )
                    == 0
                )
                assert (
                    connection.scalar(text(f"SELECT count(*) = count(DISTINCT id) FROM {local}"))
                    is True
                )
            for bank, jurisdiction, kind in _bank_scopes(connection, org):
                for local, archive, value_column in (
                    (TABLES[0], ARCHIVES[0], "threshold_pct"),
                    (TABLES[1], ARCHIVES[1], "haircut_pct"),
                ):
                    class_match = (
                        "AND institution_class = (SELECT institution_class FROM institution_types "
                        "WHERE type_code = :kind)"
                        if local.endswith("threshold")
                        else ""
                    )
                    for as_of in (date(2024, 6, 1), date(2026, 6, 1), date(2031, 6, 1)):
                        params = {
                            "bank": bank,
                            "jurisdiction": jurisdiction,
                            "kind": kind,
                            "as_of": as_of,
                        }
                        effective = (
                            "effective_from <= :as_of AND "
                            "(effective_to IS NULL OR effective_to >= :as_of) "
                            "ORDER BY effective_from DESC LIMIT 1"
                        )
                        assert connection.scalar(
                            text(
                                f"SELECT {value_column} FROM {local} WHERE bank_id = :bank AND "
                                + effective
                            ),
                            params,
                        ) == connection.scalar(
                            text(
                                f"SELECT {value_column} FROM {archive} WHERE "
                                "jurisdiction_code = :jurisdiction "
                                + class_match
                                + " AND "
                                + effective
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
        for local, archive in zip(TABLES, ARCHIVES, strict=True):
            names = {
                f"pk_{local}",
                f"fk_{local}_org_bank",
                f"fk_{local}_organization_id",
                f"uq_{local}_scope",
            }
            if local.endswith("threshold"):
                names.add(f"ck_{local}_institution_class")
            assert schema.constraints(names) == names
            shape = text(
                "SELECT attname, format_type(atttypid, atttypmod), attnotnull FROM pg_attribute "
                "WHERE attrelid = CAST(:table AS regclass) AND attnum > 0 AND NOT attisdropped "
                "AND attname <> 'bank_id' ORDER BY attname"
            )
            assert (
                connection.execute(shape, {"table": local}).all()
                == connection.execute(
                    shape,
                    {"table": archive},
                ).all()
            )
            assert connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:table AS regclass)"
                ),
                {"table": local},
            ).one() == (True, True)
            assert connection.scalar(text(f"SELECT count(*) FROM {local}")) == 12
            _tenant(connection, OTHER)
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
                f"UPDATE {local} SET notes = 'rewritten'",
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


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("write", ["retire", "new_generation"])
def test_downgrade_refuses_bank_local_divergence(
    bank_local_schema: MigratedPostgresSchema,
    table: str,
    write: str,
) -> None:
    schema = bank_local_schema
    before = _snapshot(schema, ARCHIVES)
    with schema.app_engine.begin() as connection:
        _tenant(connection, ORG)
        if write == "retire":
            connection.execute(
                text(
                    f"UPDATE {table} SET effective_to = '2029-12-31', updated_at = now() "
                    "WHERE bank_id = :bank AND effective_from = '2026-01-01'"
                ),
                {"bank": BANK},
            )
        else:
            connection.execute(
                text(
                    f"INSERT INTO {table} SELECT gen_random_uuid(), organization_id, bank_id, "
                    "jurisdiction_code, DATE '2040-01-01', NULL, approved_by, "
                    "approval_timestamp, "
                    + (
                        "institution_class, threshold_code, threshold_pct"
                        if table.endswith("threshold")
                        else "asset_class, haircut_pct"
                    )
                    + f", notes, created_at, updated_at FROM {table} "
                    "WHERE bank_id = :bank LIMIT 1"
                ),
                {"bank": BANK},
            )
    local = _snapshot(schema, TABLES)
    with pytest.raises((RuntimeError, DBAPIError), match="bank-local liquidity.*diverg"):
        command.downgrade(alembic_config_for_app(), PREVIOUS)
    assert _snapshot(schema, TABLES) == local
    assert _snapshot(schema, ARCHIVES) == before


@pytest.fixture
def liquidity_administrator(
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
    liquidity_administrator: Engine,
) -> None:
    with liquidity_administrator.connect() as connection, connection.begin():
        _tenant(connection, ORG)
        session = uuid4()
        connection.execute(
            text(
                "INSERT INTO operator_inspector_sessions (id, organization_id, started_by, "
                "mode, reason, started_at, expires_at, support_reference) VALUES "
                "(:id, :org, 'staff@example.test', 'consent', 'LIQ migration regression', "
                "now(), now() + interval '1 hour', 'SYNTHETIC-LIQ')"
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
            ("param_liquidity_haircut", BANK, evidence),
            ("conflicting", BANK, {**evidence, "bank_id": SIBLING}),
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
                    "kind": "bank_liquidity_threshold"
                    if kind in {"conflicting", "foreign"}
                    else kind,
                },
            )
        # Execute the real FORCE-RLS policies and SECURITY DEFINER functions as API.
        connection.execute(text("SET LOCAL ROLE aqs_api"))
        expectations: tuple[tuple[str, set[str]], ...] = (
            (
                BANK,
                {
                    "bank_liquidity_threshold",
                    "bank_liquidity_haircut",
                    "param_liquidity_threshold",
                    "param_liquidity_haircut",
                },
            ),
            (SIBLING, {"param_liquidity_threshold", "param_liquidity_haircut"}),
            (SDI, {"param_liquidity_haircut"}),
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
                    {"reason": "LIQ migration regression", "support_reference": "SYNTHETIC-LIQ"}
                    if bank == BANK and kind in expected
                    else None
                )
