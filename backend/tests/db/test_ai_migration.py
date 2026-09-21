"""The AI evidence is sealed by the DATABASE, not by the service.

``202609190060`` is the one migration whose subject matter is data leaving the
platform, so what it must guarantee is different from the rest of the schema:

* **SEALED** — a suggestion row records exactly which pseudonymised fact sheet
  went to which model, under which consent version and which deployment
  approval, and what came back. It moves ``queued -> running -> terminal`` and
  then the database refuses to change it. Evidence a service bug could rewrite
  afterwards is not evidence.
* **UNALTERABLE** — a human's decision to insert or discard AI text takes no
  UPDATE at all. It is corrected by a new decision, never by an edit. DELETE
  stays reachable so deleting a draft cycle still works.
* **A refusal can never carry output.** The CHECK is the last line under the
  product's central promise: a draft that was refused or cancelled shows a
  status, and there is no row shape in which it could show text.
* **RLS** — one tenant's AI evidence is invisible to another, in the database
  rather than in a WHERE clause somebody has to remember.

Postgres-gated: SQLite has neither these triggers nor row-level security, and
the hermetic suite runs no migration at all.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError

from alembic import command
from tests.api.helpers import ORG_1, ORG_2
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the AI schema checks.",
)

_BANK = "BK-AIDRFT1"
_OTHER_BANK = "BK-AIDRFT2"
_NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
_AS_OF = date(2026, 12, 31)
_DIGEST = "a" * 64
_SHEET = {"schema": "icaap-ai-fact-sheet-v1", "mode": "standard", "facts": []}

TABLES = ("ai_commentary_settings", "icaap_ai_suggestions", "icaap_ai_suggestion_decisions")


@pytest.fixture(scope="module")
def ai_schema() -> Iterator[MigratedPostgresSchema]:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_aidraft_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    app_engine = create_engine(database_url)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        command.upgrade(alembic_config_for_app(), "head")
        with app_engine.connect() as connection:
            for organization, bank in ((ORG_1, _BANK), (ORG_2, _OTHER_BANK)):
                connection.execute(
                    text("SELECT set_config('app.organization_id', :org, false)"),
                    {"org": organization},
                )
                connection.execute(
                    text(
                        "INSERT INTO organizations (id, name, created_at, updated_at) "
                        "VALUES (:org, 'AI tenant', now(), now())"
                    ),
                    {"org": organization},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO banks
                          (id, organization_id, name, short_name, currency, jurisdiction_code,
                           license_type, institution_type, created_at, updated_at)
                        VALUES
                          (:bank, :org, 'AI Bank', 'AIB', 'GHS', 'GH',
                           'universal', 'universal_bank', now(), now())
                        """
                    ),
                    {"bank": bank, "org": organization},
                )
            connection.commit()
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
    finally:
        monkeypatch.undo()
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def connection(ai_schema: MigratedPostgresSchema) -> Iterator[Connection]:
    with ai_schema.app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.organization_id', :org, false)"), {"org": ORG_1})
        conn.commit()
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def _refused(connection: Connection, statement: str, params: dict[str, Any], match: str) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match=match):
        connection.execute(text(statement), params)
    savepoint.rollback()


def _bypasses_rls(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar()
    )


# --- row builders -----------------------------------------------------------


def _insert_cycle(
    connection: Connection, cycle_id: UUID, *, organization: str = ORG_1, bank: str = _BANK
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_cycles
              (id, organization_id, bank_id, fiscal_year, as_of_date, cycle_kind, basis,
               subsidiaries_declared, title, framework_code, framework_version,
               framework_sha256, status, round, due_date, due_date_basis,
               created_by, created_at, updated_at)
            VALUES
              (:id, :org, :bank, 2026, :as_of, 'annual', 'solo', false, 'ICAAP FY2026',
               'bog_icaap', '2026.02-ed.1', :digest, 'draft', 1,
               DATE '2027-03-31', 'framework', :actor, :now, :now)
            """
        ),
        {
            "id": str(cycle_id),
            "org": organization,
            "bank": bank,
            "as_of": _AS_OF,
            "digest": _DIGEST,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_section(
    connection: Connection,
    section_id: UUID,
    cycle_id: UUID,
    *,
    organization: str = ORG_1,
    bank: str = _BANK,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_sections
              (id, organization_id, bank_id, cycle_id, section_key, title, letter, position,
               working_doc, working_rev, requirement_states, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :cycle, 'executive_summary', 'Executive summary', 'A', 1,
               '{"type":"doc","content":[]}'::json, 0, '{}'::json, :now, :now)
            """
        ),
        {
            "id": str(section_id),
            "org": organization,
            "bank": bank,
            "cycle": str(cycle_id),
            "now": _NOW,
        },
    )


def _insert_suggestion(  # noqa: PLR0913 - the row is its explicit parts
    connection: Connection,
    suggestion_id: UUID,
    cycle_id: UUID,
    section_id: UUID,
    *,
    status: str = "queued",
    organization: str = ORG_1,
    bank: str = _BANK,
    requested_by: UUID | None = None,
    output: str | None = None,
    completed: bool = False,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_ai_suggestions
              (id, organization_id, bank_id, cycle_id, section_id, section_key, cycle_round,
               status, requested_by, fact_sheet_mode, fact_sheet, fact_sheet_sha256,
               fact_bindings, entity_keys, framework_code, framework_version, framework_sha256,
               prompt_version, prompt_sha256, model_requested, effort, max_output_tokens,
               fallbacks_mode, consent_version, fallback_used, validation_errors,
               output, completed_at, created_at)
            VALUES
              (:id, :org, :bank, :cycle, :section, 'executive_summary', 1,
               :status, :actor, 'standard', CAST(:sheet AS json), :digest,
               '{}'::json, '[]'::json, 'bog_icaap', '2026.02-ed.1', :digest,
               'icaap-draft-v1', :digest, 'claude-opus-5', 'high', 16000,
               'default', 'ai-consent-2026-09-v1', false, '[]'::json,
               CAST(:output AS json), :completed, :now)
            """
        ),
        {
            "id": str(suggestion_id),
            "org": organization,
            "bank": bank,
            "cycle": str(cycle_id),
            "section": str(section_id),
            "status": status,
            "actor": str(requested_by or uuid4()),
            "sheet": json.dumps(_SHEET),
            "digest": _DIGEST,
            "output": output,
            "completed": _NOW if completed else None,
            "now": _NOW,
        },
    )


def _insert_decision(
    connection: Connection,
    decision_id: UUID,
    cycle_id: UUID,
    suggestion_id: UUID,
    *,
    decision: str = "accepted",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_ai_suggestion_decisions
              (id, organization_id, bank_id, cycle_id, suggestion_id, decision,
               paragraph_indexes, acknowledged_stale, decided_by, created_at)
            VALUES
              (:id, :org, :bank, :cycle, :suggestion, :decision,
               '[0]'::json, false, :actor, :now)
            """
        ),
        {
            "id": str(decision_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "suggestion": str(suggestion_id),
            "decision": decision,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


@pytest.fixture
def seeded(connection: Connection) -> tuple[UUID, UUID]:
    cycle_id, section_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_section(connection, section_id, cycle_id)
    return cycle_id, section_id


# --- SEALED -----------------------------------------------------------------


def test_a_suggestion_moves_queued_then_running_then_terminal(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    cycle_id, section_id = seeded
    suggestion_id = uuid4()
    _insert_suggestion(connection, suggestion_id, cycle_id, section_id)
    connection.execute(
        text("UPDATE icaap_ai_suggestions SET status = 'running' WHERE id = :id"),
        {"id": str(suggestion_id)},
    )
    connection.execute(
        text(
            "UPDATE icaap_ai_suggestions SET status = 'validated', completed_at = :now, "
            "output = CAST(:output AS json) WHERE id = :id"
        ),
        {"id": str(suggestion_id), "now": _NOW, "output": json.dumps({"paragraphs": []})},
    )
    assert (
        connection.execute(
            text("SELECT status FROM icaap_ai_suggestions WHERE id = :id"),
            {"id": str(suggestion_id)},
        ).scalar()
        == "validated"
    )


def test_a_terminal_suggestion_can_never_be_rewritten(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    """The evidence of what left the platform is frozen once it is complete."""
    cycle_id, section_id = seeded
    suggestion_id = uuid4()
    _insert_suggestion(
        connection,
        suggestion_id,
        cycle_id,
        section_id,
        status="validated",
        output=json.dumps({"paragraphs": []}),
        completed=True,
    )
    for column, value in (
        ("output", "CAST('{\"paragraphs\": [1]}' AS json)"),
        ("fact_sheet", "CAST('{}' AS json)"),
        ("model_requested", "'some-other-model'"),
        ("consent_version", "'forged'"),
    ):
        _refused(
            connection,
            f"UPDATE icaap_ai_suggestions SET {column} = {value} WHERE id = :id",
            {"id": str(suggestion_id)},
            "immutable|sealed|governed|cannot",
        )


def test_a_terminal_status_can_never_move_again(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    """A refused draft cannot be re-labelled validated by a later write."""
    cycle_id, section_id = seeded
    suggestion_id = uuid4()
    _insert_suggestion(
        connection, suggestion_id, cycle_id, section_id, status="refused", completed=True
    )
    _refused(
        connection,
        "UPDATE icaap_ai_suggestions SET status = 'validated' WHERE id = :id",
        {"id": str(suggestion_id)},
        "immutable|sealed|governed|cannot",
    )


# --- the CHECKs behind the product promise ----------------------------------


def test_a_refused_or_cancelled_row_can_never_carry_output(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    """There is no row shape in which a refusal could show text."""
    cycle_id, section_id = seeded
    for status in ("refused", "rate_limited", "cancelled"):
        savepoint = connection.begin_nested()
        with pytest.raises(DatabaseError, match="ck_icaap_ai_suggestions_no_output"):
            _insert_suggestion(
                connection,
                uuid4(),
                cycle_id,
                section_id,
                status=status,
                output=json.dumps({"paragraphs": [{"text": "leaked"}]}),
                completed=True,
            )
        savepoint.rollback()


def test_a_validated_row_must_carry_output(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    cycle_id, section_id = seeded
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_ai_suggestions_validated_output"):
        _insert_suggestion(
            connection, uuid4(), cycle_id, section_id, status="validated", completed=True
        )
    savepoint.rollback()


def test_a_terminal_row_must_record_when_it_finished(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    cycle_id, section_id = seeded
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_ai_suggestions_completed"):
        _insert_suggestion(connection, uuid4(), cycle_id, section_id, status="failed")
    savepoint.rollback()


def test_one_in_flight_request_per_user_per_section(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    """The debounce's race guard, where a concurrent request cannot slip past."""
    cycle_id, section_id = seeded
    actor = uuid4()
    _insert_suggestion(connection, uuid4(), cycle_id, section_id, requested_by=actor)
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="uq_icaap_ai_suggestions_inflight"):
        _insert_suggestion(connection, uuid4(), cycle_id, section_id, requested_by=actor)
    savepoint.rollback()


def test_a_finished_request_frees_the_in_flight_slot(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    cycle_id, section_id = seeded
    actor = uuid4()
    _insert_suggestion(
        connection,
        uuid4(),
        cycle_id,
        section_id,
        requested_by=actor,
        status="refused",
        completed=True,
    )
    _insert_suggestion(connection, uuid4(), cycle_id, section_id, requested_by=actor)


# --- UNALTERABLE ------------------------------------------------------------


def test_a_decision_takes_no_update_at_all(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    cycle_id, section_id = seeded
    suggestion_id, decision_id = uuid4(), uuid4()
    _insert_suggestion(
        connection,
        suggestion_id,
        cycle_id,
        section_id,
        status="validated",
        output=json.dumps({"paragraphs": []}),
        completed=True,
    )
    _insert_decision(connection, decision_id, cycle_id, suggestion_id)
    _refused(
        connection,
        "UPDATE icaap_ai_suggestion_decisions SET decision = 'rejected' WHERE id = :id",
        {"id": str(decision_id)},
        "append-only|immutable|cannot|restrict",
    )


def test_one_decision_per_suggestion(connection: Connection, seeded: tuple[UUID, UUID]) -> None:
    cycle_id, section_id = seeded
    suggestion_id = uuid4()
    _insert_suggestion(
        connection,
        suggestion_id,
        cycle_id,
        section_id,
        status="validated",
        output=json.dumps({"paragraphs": []}),
        completed=True,
    )
    _insert_decision(connection, uuid4(), cycle_id, suggestion_id)
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="uq_icaap_ai_suggestion_decisions_one"):
        _insert_decision(connection, uuid4(), cycle_id, suggestion_id, decision="rejected")
    savepoint.rollback()


def test_deleting_a_cycle_cascades_through_both_tables(
    connection: Connection, seeded: tuple[UUID, UUID]
) -> None:
    """UNALTERABLE, not IMMUTABLE: a draft cycle must remain deletable."""
    cycle_id, section_id = seeded
    suggestion_id = uuid4()
    _insert_suggestion(
        connection,
        suggestion_id,
        cycle_id,
        section_id,
        status="validated",
        output=json.dumps({"paragraphs": []}),
        completed=True,
    )
    _insert_decision(connection, uuid4(), cycle_id, suggestion_id)
    connection.execute(text("DELETE FROM icaap_cycles WHERE id = :id"), {"id": str(cycle_id)})
    for table in ("icaap_ai_suggestions", "icaap_ai_suggestion_decisions"):
        assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, (
            f"{table} did not cascade"
        )


# --- consent ----------------------------------------------------------------


def test_a_tenant_cannot_be_enabled_without_recorded_consent(connection: Connection) -> None:
    """Consent is not a UI step a direct SQL write could skip."""
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_ai_commentary_settings_consent"):
        connection.execute(
            text(
                """
                INSERT INTO ai_commentary_settings
                  (id, organization_id, enabled, enabled_features, descriptor_only,
                   updated_by, created_at, updated_at)
                VALUES (:id, :org, true, '[]'::json, true, :actor, :now, :now)
                """
            ),
            {"id": str(uuid4()), "org": ORG_1, "actor": str(uuid4()), "now": _NOW},
        )
    savepoint.rollback()


def test_the_switch_stays_flippable(connection: Connection) -> None:
    """Mutable on purpose: the history is the append-only audit trail."""
    row_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO ai_commentary_settings
              (id, organization_id, enabled, enabled_features, descriptor_only,
               consent_version, consented_by, consented_at, updated_by, created_at, updated_at)
            VALUES (:id, :org, true, '["icaap_drafting"]'::json, true,
                    'ai-consent-2026-09-v1', :actor, :now, :actor, :now, :now)
            """
        ),
        {"id": str(row_id), "org": ORG_1, "actor": str(uuid4()), "now": _NOW},
    )
    connection.execute(
        text("UPDATE ai_commentary_settings SET enabled = false WHERE id = :id"),
        {"id": str(row_id)},
    )
    assert (
        connection.execute(
            text("SELECT enabled FROM ai_commentary_settings WHERE id = :id"),
            {"id": str(row_id)},
        ).scalar()
        is False
    )


# --- RLS --------------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES)
def test_every_ai_table_is_force_rls(ai_schema: MigratedPostgresSchema, table: str) -> None:
    with ai_schema.app_engine.connect() as connection:
        enabled, forced = connection.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
    assert enabled and forced, f"{table} is not ENABLE+FORCE row level security"


def test_one_tenants_ai_evidence_is_invisible_to_another(
    ai_schema: MigratedPostgresSchema,
) -> None:
    with ai_schema.app_engine.connect() as connection:
        if _bypasses_rls(connection):
            pytest.skip("this role bypasses RLS; isolation is untestable from it")
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, false)"), {"org": ORG_1}
        )
        cycle_id, section_id = uuid4(), uuid4()
        _insert_cycle(connection, cycle_id)
        _insert_section(connection, section_id, cycle_id)
        _insert_suggestion(connection, uuid4(), cycle_id, section_id)
        connection.commit()
        try:
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, false)"), {"org": ORG_2}
            )
            assert (
                connection.execute(text("SELECT count(*) FROM icaap_ai_suggestions")).scalar() == 0
            )
        finally:
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, false)"), {"org": ORG_1}
            )
            connection.execute(
                text("DELETE FROM icaap_cycles WHERE id = :id"), {"id": str(cycle_id)}
            )
            connection.commit()
