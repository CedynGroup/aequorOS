"""The ICAAP risk & capital tables are sealed by the DATABASE, not by convention.

``202609190057`` puts twelve tables under the same three tiers the workspace
uses, and none of them is provable from the migration text:

* **SEALED** — a supervisory add-on is the REGULATOR's number and a finalised
  independent review is a reviewer's opinion on a date. Once authoritative,
  only the supersession/withdrawal bookkeeping may move; a correction is a new
  row, never an edit. The add-on additionally carries its maker-checker rule as
  a CHECK, so ``confirmed_by = created_by`` is refused even by direct SQL.
* **UNALTERABLE** — Pillar 2 item revisions, challenges and responses accept no
  UPDATE at all, so "what did the approver approve" and "what did the Board
  ask" survive anyone's later opinion. DELETE stays reachable, which is what
  lets a draft cycle be deleted whole.
* **RLS** — one tenant's ICAAP assessment is invisible to another, enforced by
  the database rather than by a WHERE clause somebody has to remember.

The CHECKs that carry regulatory meaning are pinned here too: the appetite
ordering in both directions, the mandatory explanation on a resources line that
does not tie out (REG-ICAAP-027), the negative-and-judgemental-only
diversification benefit, and the scenario definition a scenario method must
record.

Postgres-gated. SQLite has neither triggers of this shape nor row-level
security, and the hermetic suite runs no migration at all.
"""

from __future__ import annotations

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

postgres_only = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the ICAAP risk & capital schema checks.",
)

pytestmark = postgres_only

_BANK = "BK-ICAAPP2"
_OTHER_BANK = "BK-ICAAPP3"
_NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
_AS_OF = date(2025, 12, 31)
_DIGEST = "a" * 64
_PARAMETERS_REVISION = "202609190056"

TABLES = (
    "icaap_risk_assessments",
    "icaap_appetite_metrics",
    "icaap_pillar2_items",
    "icaap_pillar2_item_revisions",
    "bank_supervisory_addons",
    "icaap_capital_allocations",
    "icaap_requirement_reconciliation_lines",
    "icaap_resources_reconciliation_lines",
    "icaap_control_explanations",
    "icaap_audit_reviews",
    "icaap_challenges",
    "icaap_challenge_responses",
)
UNALTERABLE = (
    "icaap_pillar2_item_revisions",
    "icaap_challenges",
    "icaap_challenge_responses",
)
SEALED = ("bank_supervisory_addons", "icaap_audit_reviews")


@pytest.fixture(scope="module")
def p2_schema() -> Iterator[MigratedPostgresSchema]:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_icaap_p2_{uuid4().hex}"
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
                        "VALUES (:org, 'ICAAP tenant', now(), now())"
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
                          (:bank, :org, 'ICAAP Bank', 'ICAAP', 'GHS', 'GH',
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
def connection(p2_schema: MigratedPostgresSchema) -> Iterator[Connection]:
    with p2_schema.app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.organization_id', :org, false)"), {"org": ORG_1})
        conn.commit()
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def _tenant(connection: Connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :org, true)"), {"org": organization_id}
    )


def _refused(connection: Connection, statement: str, params: dict[str, Any], match: str) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match=match):
        connection.execute(text(statement), params)
    savepoint.rollback()


def _bypasses_rls(connection: Connection) -> bool:
    """Whether this role sees through row-level security.

    A managed Postgres often grants the owning role BYPASSRLS, and the policy
    then cannot be observed from inside the session. The policy's PRESENCE is
    asserted unconditionally above; only the observable half is skipped.
    """
    return bool(
        connection.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar()
    )


# --- fixtures for rows ----------------------------------------------------


def _insert_cycle(
    connection: Connection,
    cycle_id: UUID,
    *,
    organization_id: str = ORG_1,
    bank_id: str = _BANK,
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
              (:id, :org, :bank, 2025, :as_of, 'rehearsal', 'solo', false, 'ICAAP FY2025',
               'bog_icaap', '2026.02-ed.1', :digest, 'draft', 1, DATE '2026-03-31',
               'framework', :actor, :now, :now)
            """
        ),
        {
            "id": str(cycle_id),
            "org": organization_id,
            "bank": bank_id,
            "as_of": _AS_OF,
            "digest": _DIGEST,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_item(  # noqa: PLR0913 - a Pillar 2 item is its named columns
    connection: Connection,
    cycle_id: UUID,
    item_id: UUID,
    *,
    organization_id: str = ORG_1,
    bank_id: str = _BANK,
    component_key: str = "credit_concentration",
    # Defaults to the component, as the service does. Overridden where a case
    # needs two rows of the SAME component, so the partial unique index on
    # (cycle_id, item_key) cannot fire before the CHECK under test.
    item_key: str | None = None,
    source: str = "icaap_method",
    method: str = "benchmark_mapped",
    method_status: str = "computed",
    baseline: str | None = "18.72",
    basis: str | None = "pct_pillar1_credit_capital",
    scenario: str | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_pillar2_items
              (id, organization_id, bank_id, cycle_id, item_key, risk_key, category_key,
               component_key, table5_row, method, source, input_mode, method_status,
               basis, basis_value, baseline_amount, baseline_derivation, currency,
               scenario_definition, evidence_attachment_ids, current_revision_no,
               created_by, updated_by, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :cycle, :item_key, 'credit', 'credit', :component,
               'row_2', :method, :source, 'bound_blocks', :status, :basis, 18,
               :baseline, 'method', 'GHS', CAST(:scenario AS json), '[]'::json, 1,
               :actor, :actor, :now, :now)
            """
        ),
        {
            "id": str(item_id),
            "org": organization_id,
            "bank": bank_id,
            "cycle": str(cycle_id),
            "component": component_key,
            "item_key": component_key if item_key is None else item_key,
            "method": method,
            "source": source,
            "status": method_status,
            "basis": basis,
            "baseline": baseline,
            "scenario": scenario,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_revision(
    connection: Connection, cycle_id: UUID, item_id: UUID, revision_id: UUID
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_pillar2_item_revisions
              (id, organization_id, bank_id, cycle_id, item_id, revision_no, change_kind,
               round, snapshot, snapshot_sha256, created_by, created_at)
            VALUES
              (:id, :org, :bank, :cycle, :item, 1, 'computed', 1, '{"amount": "18.72"}'::json,
               :digest, :actor, :now)
            """
        ),
        {
            "id": str(revision_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "item": str(item_id),
            "digest": _DIGEST,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_addon(  # noqa: PLR0913 - an add-on row is its named columns
    connection: Connection,
    addon_id: UUID,
    *,
    created_by: UUID,
    confirmed_by: UUID | None = None,
    status: str = "draft",
    organization_id: str = ORG_1,
    bank_id: str = _BANK,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO bank_supervisory_addons
              (id, organization_id, bank_id, status, letter_reference, letter_date,
               effective_from, applies_to_basis, basis, basis_value, currency,
               letter_original_filename, letter_media_type, letter_byte_size, letter_sha256,
               letter_storage_tier, letter_object_path, created_by, confirmed_by,
               confirmed_at, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :status, 'BSD/2026/017', DATE '2026-02-10',
               DATE '2026-03-01', 'solo', 'pct_total_rwa', 2, 'GHS',
               'letter.pdf', 'application/pdf', 2048, :digest, 'outputs',
               'icaap/supervisory/x.pdf', :created_by, :confirmed_by, :confirmed_at,
               :now, :now)
            """
        ),
        {
            "id": str(addon_id),
            "org": organization_id,
            "bank": bank_id,
            "status": status,
            "digest": _DIGEST,
            "created_by": str(created_by),
            "confirmed_by": None if confirmed_by is None else str(confirmed_by),
            "confirmed_at": None if confirmed_by is None else _NOW,
            "now": _NOW,
        },
    )


def _insert_review(
    connection: Connection,
    cycle_id: UUID,
    review_id: UUID,
    *,
    status: str = "draft",
    finalised: bool = False,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_audit_reviews
              (id, organization_id, bank_id, cycle_id, status, review_kind, reviewer_function,
               scope, frequency_statement, performed_on, overall_opinion, findings,
               independence_statement, recorded_by, finalised_at, finalised_by, row_rev,
               created_at, updated_at)
            VALUES
              (:id, :org, :bank, :cycle, :status, 'internal_audit', 'Internal Audit',
               'Full ICAAP', 'Annually', DATE '2026-02-28', 'satisfactory', '[]'::json,
               'No involvement in preparation', :actor, :finalised_at, :finalised_by, 0,
               :now, :now)
            """
        ),
        {
            "id": str(review_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "status": status,
            "actor": str(uuid4()),
            "finalised_at": _NOW if finalised else None,
            "finalised_by": str(uuid4()) if finalised else None,
            "now": _NOW,
        },
    )


def _insert_challenge(connection: Connection, cycle_id: UUID, challenge_id: UUID) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_challenges
              (id, organization_id, bank_id, cycle_id, challenge_no, round, raised_in,
               raised_by_name, raised_on, target_kind, challenge_text, severity,
               recorded_by, created_at)
            VALUES
              (:id, :org, :bank, :cycle, 1, 1, 'board_risk_committee', 'A. Director',
               DATE '2026-02-20', 'pillar2_item', 'Why is the add-on so low?', 'high',
               :actor, :now)
            """
        ),
        {
            "id": str(challenge_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_response(
    connection: Connection, cycle_id: UUID, challenge_id: UUID, response_id: UUID
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_challenge_responses
              (id, organization_id, bank_id, cycle_id, challenge_id, response_no, outcome,
               response_text, change_references, responder_function, responded_by, created_at)
            VALUES
              (:id, :org, :bank, :cycle, :challenge, 1, 'accepted_changed',
               'Recomputed on the full obligor vector.', '[]'::json, 'CRO', :actor, :now)
            """
        ),
        {
            "id": str(response_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "challenge": str(challenge_id),
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_appetite(  # noqa: PLR0913 - an appetite triple is its named columns
    connection: Connection,
    cycle_id: UUID,
    metric_id: UUID,
    *,
    direction: str,
    appetite: str,
    tolerance: str,
    capacity: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_appetite_metrics
              (id, organization_id, bank_id, cycle_id, metric_key, label, is_custom,
               measure_kind, unit, direction, appetite_value, tolerance_value,
               capacity_value, value_source, manual_value, qualitative_statement,
               row_rev, created_by, updated_by, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :cycle, :key, 'Total capital ratio', false,
               'quantitative', 'percent', :direction, :appetite, :tolerance, :capacity,
               'manual', 14, 'The Board keeps capital above the regulatory minimum.',
               0, :actor, :actor, :now, :now)
            """
        ),
        {
            "id": str(metric_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "key": f"metric_{metric_id.hex[:8]}",
            "direction": direction,
            "appetite": appetite,
            "tolerance": tolerance,
            "capacity": capacity,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_resources_line(  # noqa: PLR0913 - a resources line is its named columns
    connection: Connection,
    cycle_id: UUID,
    line_id: UUID,
    *,
    regulatory_amount: str | None,
    internal_amount: str,
    eligible: bool,
    explanation: str | None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_resources_reconciliation_lines
              (id, organization_id, bank_id, cycle_id, line_key, position, label, tier,
               origin, regulatory_amount, internal_amount, regulatory_eligible,
               explanation, row_rev, created_by, updated_by, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :cycle, :key, 1, 'CET1', 'cet1', 'manual',
               :regulatory, :internal, :eligible, :explanation, 0, :actor, :actor,
               :now, :now)
            """
        ),
        {
            "id": str(line_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "key": f"line_{line_id.hex[:8]}",
            "regulatory": regulatory_amount,
            "internal": internal_amount,
            "eligible": eligible,
            "explanation": explanation,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


# --- structure ------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES)
def test_every_table_forces_tenant_isolation(connection: Connection, table: str) -> None:
    row = connection.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = to_regclass(:table)"
        ),
        {"table": table},
    ).one()
    assert row == (True, True), table
    policies = set(
        connection.scalars(
            text("SELECT policyname FROM pg_policies WHERE tablename = :table"),
            {"table": table},
        )
    )
    assert f"{table}_tenant_isolation" in policies, table


@pytest.mark.parametrize("table", UNALTERABLE)
def test_append_only_tables_carry_the_guard_and_the_restrictive_policy(
    connection: Connection, table: str
) -> None:
    triggers = set(
        connection.scalars(
            text("SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass(:table)"),
            {"table": table},
        )
    )
    assert f"{table}_append_only" in triggers, table
    policies = set(
        connection.scalars(
            text("SELECT policyname FROM pg_policies WHERE tablename = :table"),
            {"table": table},
        )
    )
    assert f"{table}_no_update" in policies, table


@pytest.mark.parametrize("table", SEALED)
def test_sealed_tables_carry_the_governed_row_guard(connection: Connection, table: str) -> None:
    triggers = set(
        connection.scalars(
            text("SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass(:table)"),
            {"table": table},
        )
    )
    assert f"{table}_governed_row" in triggers, table


def test_one_tenants_risk_capital_data_is_invisible_to_another(connection: Connection) -> None:
    if _bypasses_rls(connection):
        pytest.skip("the test role holds BYPASSRLS; the policy cannot be observed here")
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_item(connection, cycle_id, uuid4())
    assert connection.scalar(text("SELECT count(*) FROM icaap_pillar2_items")) == 1
    _tenant(connection, ORG_2)
    assert connection.scalar(text("SELECT count(*) FROM icaap_pillar2_items")) == 0


def test_a_row_cannot_be_written_for_another_tenant(connection: Connection) -> None:
    if _bypasses_rls(connection):
        pytest.skip("the test role holds BYPASSRLS; the policy cannot be observed here")
    other_cycle = uuid4()
    _tenant(connection, ORG_2)
    _insert_cycle(connection, other_cycle, organization_id=ORG_2, bank_id=_OTHER_BANK)
    _tenant(connection, ORG_1)
    _refused(
        connection,
        "INSERT INTO icaap_challenges (id, organization_id, bank_id, cycle_id, challenge_no,"
        " round, raised_in, raised_by_name, raised_on, target_kind, challenge_text, severity,"
        " recorded_by, created_at) VALUES (:id, :org, :bank, :cycle, 1, 1, 'board', 'X',"
        " DATE '2026-02-20', 'cycle', 'text', 'low', :actor, :now)",
        {
            "id": str(uuid4()),
            "org": ORG_2,
            "bank": _OTHER_BANK,
            "cycle": str(other_cycle),
            "actor": str(uuid4()),
            "now": _NOW,
        },
        "row-level security|violates row-level security",
    )


# --- UNALTERABLE ----------------------------------------------------------


def test_a_pillar2_revision_can_never_be_updated(connection: Connection) -> None:
    cycle_id, item_id, revision_id = uuid4(), uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_item(connection, cycle_id, item_id)
    _insert_revision(connection, cycle_id, item_id, revision_id)
    _refused(
        connection,
        "UPDATE icaap_pillar2_item_revisions SET note = 'revised' WHERE id = :id",
        {"id": str(revision_id)},
        "append-only|cannot be modified|restrict",
    )


def test_a_challenge_and_its_response_can_never_be_updated(connection: Connection) -> None:
    cycle_id, challenge_id, response_id = uuid4(), uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_challenge(connection, cycle_id, challenge_id)
    _insert_response(connection, cycle_id, challenge_id, response_id)
    _refused(
        connection,
        "UPDATE icaap_challenges SET severity = 'low' WHERE id = :id",
        {"id": str(challenge_id)},
        "append-only|cannot be modified|restrict",
    )
    _refused(
        connection,
        "UPDATE icaap_challenge_responses SET outcome = 'deferred' WHERE id = :id",
        {"id": str(response_id)},
        "append-only|cannot be modified|restrict",
    )


def test_deleting_a_draft_cycle_still_cascades_through_the_unalterable_rows(
    connection: Connection,
) -> None:
    """DELETE stays reachable on purpose: an append-only row must not make a
    draft cycle undeletable."""
    cycle_id, item_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_item(connection, cycle_id, item_id)
    _insert_revision(connection, cycle_id, item_id, uuid4())
    challenge_id = uuid4()
    _insert_challenge(connection, cycle_id, challenge_id)
    _insert_response(connection, cycle_id, challenge_id, uuid4())
    connection.execute(text("DELETE FROM icaap_cycles WHERE id = :id"), {"id": str(cycle_id)})
    for table in ("icaap_pillar2_items", "icaap_pillar2_item_revisions", "icaap_challenges"):
        assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table


# --- SEALED: supervisory add-ons -----------------------------------------


def test_a_draft_addon_is_editable_and_a_second_person_confirms_it(
    connection: Connection,
) -> None:
    addon_id, maker, checker = uuid4(), uuid4(), uuid4()
    _insert_addon(connection, addon_id, created_by=maker)
    connection.execute(
        text("UPDATE bank_supervisory_addons SET basis_value = 3 WHERE id = :id"),
        {"id": str(addon_id)},
    )
    connection.execute(
        text(
            "UPDATE bank_supervisory_addons SET status = 'active', confirmed_by = :checker, "
            "confirmed_at = :now WHERE id = :id"
        ),
        {"id": str(addon_id), "checker": str(checker), "now": _NOW},
    )
    assert (
        connection.scalar(
            text("SELECT status FROM bank_supervisory_addons WHERE id = :id"),
            {"id": str(addon_id)},
        )
        == "active"
    )


def test_the_database_refuses_an_addon_confirmed_by_its_own_creator(
    connection: Connection,
) -> None:
    """Maker-checker on a regulator-imposed number, enforced below the service."""
    addon_id, maker = uuid4(), uuid4()
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="four_eyes"):
        _insert_addon(connection, addon_id, created_by=maker, confirmed_by=maker, status="active")
    savepoint.rollback()


def test_an_active_addon_cannot_be_rewritten_or_unconfirmed(connection: Connection) -> None:
    addon_id, maker, checker = uuid4(), uuid4(), uuid4()
    _insert_addon(connection, addon_id, created_by=maker, confirmed_by=checker, status="active")
    _refused(
        connection,
        "UPDATE bank_supervisory_addons SET basis_value = 9 WHERE id = :id",
        {"id": str(addon_id)},
        "governed and sealed",
    )
    _refused(
        connection,
        "UPDATE bank_supervisory_addons SET status = 'draft' WHERE id = :id",
        {"id": str(addon_id)},
        "may not move from active to draft",
    )


def test_an_active_addon_moves_to_superseded_and_its_stamps_are_write_once(
    connection: Connection,
) -> None:
    addon_id, successor_id, maker, checker = uuid4(), uuid4(), uuid4(), uuid4()
    _insert_addon(connection, addon_id, created_by=maker, confirmed_by=checker, status="active")
    _insert_addon(connection, successor_id, created_by=maker, confirmed_by=checker, status="active")
    connection.execute(
        text(
            "UPDATE bank_supervisory_addons SET status = 'superseded', superseded_at = :now, "
            "superseded_by_addon_id = :successor WHERE id = :id"
        ),
        {"id": str(addon_id), "successor": str(successor_id), "now": _NOW},
    )
    _refused(
        connection,
        "UPDATE bank_supervisory_addons SET superseded_by_addon_id = :other WHERE id = :id",
        {"id": str(addon_id), "other": str(uuid4())},
        "write-once",
    )


# --- SEALED: audit reviews ------------------------------------------------


def test_a_finalised_review_is_corrected_by_superseding_it_never_by_editing(
    connection: Connection,
) -> None:
    cycle_id, review_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_review(connection, cycle_id, review_id)
    connection.execute(
        text(
            "UPDATE icaap_audit_reviews SET status = 'finalised', finalised_at = :now, "
            "finalised_by = :actor WHERE id = :id"
        ),
        {"id": str(review_id), "now": _NOW, "actor": str(uuid4())},
    )
    _refused(
        connection,
        "UPDATE icaap_audit_reviews SET overall_opinion = 'unsatisfactory' WHERE id = :id",
        {"id": str(review_id)},
        "governed and sealed",
    )
    successor = uuid4()
    _insert_review(connection, cycle_id, successor)
    connection.execute(
        text(
            "UPDATE icaap_audit_reviews SET status = 'superseded', superseded_at = :now, "
            "superseded_by_review_id = :successor WHERE id = :id"
        ),
        {"id": str(review_id), "successor": str(successor), "now": _NOW},
    )
    assert (
        connection.scalar(
            text("SELECT status FROM icaap_audit_reviews WHERE id = :id"), {"id": str(review_id)}
        )
        == "superseded"
    )


def test_a_finalised_review_must_carry_its_finalisation_timestamp(
    connection: Connection,
) -> None:
    cycle_id, review_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_audit_reviews_finalised"):
        _insert_review(connection, cycle_id, review_id, status="finalised", finalised=False)
    savepoint.rollback()


# --- the CHECKs that carry regulatory meaning ----------------------------


def test_appetite_ordering_is_enforced_in_both_directions(connection: Connection) -> None:
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    # A floor: appetite >= tolerance >= capacity (capacity is the weakest point).
    _insert_appetite(
        connection,
        cycle_id,
        uuid4(),
        direction="floor",
        appetite="16",
        tolerance="14.5",
        capacity="13",
    )
    # Equality is allowed — a Board may set appetite at the tolerance.
    _insert_appetite(
        connection,
        cycle_id,
        uuid4(),
        direction="floor",
        appetite="14",
        tolerance="14",
        capacity="13",
    )
    # A ceiling orders the other way.
    _insert_appetite(
        connection,
        cycle_id,
        uuid4(),
        direction="ceiling",
        appetite="5",
        tolerance="7",
        capacity="10",
    )
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_appetite_metrics_ordering"):
        _insert_appetite(
            connection,
            cycle_id,
            uuid4(),
            direction="floor",
            appetite="13",
            tolerance="14.5",
            capacity="16",
        )
    savepoint.rollback()
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_appetite_metrics_ordering"):
        _insert_appetite(
            connection,
            cycle_id,
            uuid4(),
            direction="ceiling",
            appetite="10",
            tolerance="7",
            capacity="5",
        )
    savepoint.rollback()


def test_a_resources_line_that_does_not_tie_out_needs_an_explanation(
    connection: Connection,
) -> None:
    """REG-ICAAP-027 is the whole point of the statement, so it is a CHECK."""
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_resources_line(
        connection,
        cycle_id,
        uuid4(),
        regulatory_amount="250",
        internal_amount="250",
        eligible=True,
        explanation=None,
    )
    _insert_resources_line(
        connection,
        cycle_id,
        uuid4(),
        regulatory_amount="250",
        internal_amount="260",
        eligible=True,
        explanation="Unaudited profit counted internally.",
    )
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_resources_reconciliation_lines_explained"):
        _insert_resources_line(
            connection,
            cycle_id,
            uuid4(),
            regulatory_amount="250",
            internal_amount="260",
            eligible=True,
            explanation=None,
        )
    savepoint.rollback()
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_resources_reconciliation_lines_explained"):
        _insert_resources_line(
            connection,
            cycle_id,
            uuid4(),
            regulatory_amount=None,
            internal_amount="10",
            eligible=False,
            explanation=None,
        )
    savepoint.rollback()


def test_a_diversification_benefit_is_negative_and_judgemental_and_nothing_else_is(
    connection: Connection,
) -> None:
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_item(
        connection,
        cycle_id,
        uuid4(),
        component_key="diversification_benefit",
        source="judgemental",
        method="manual_entry",
        baseline="-5",
    )
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_pillar2_items_diversification_negative"):
        _insert_item(
            connection,
            cycle_id,
            uuid4(),
            component_key="diversification_benefit",
            item_key="diversification_benefit_2",
            source="icaap_method",
            method="manual_entry",
            baseline="-5",
        )
    savepoint.rollback()
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_pillar2_items_non_negative"):
        _insert_item(connection, cycle_id, uuid4(), baseline="-1")
    savepoint.rollback()


def test_a_scenario_method_must_record_its_scenario(connection: Connection) -> None:
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_item(
        connection,
        cycle_id,
        uuid4(),
        component_key="fx_risk",
        method="fx_nop_addon",
        scenario='{"depreciation_pct": "30"}',
    )
    # Not yet computed is exempt: the definition arrives with the computation.
    _insert_item(
        connection,
        cycle_id,
        uuid4(),
        component_key="fx_risk_2",
        method="fx_nop_addon",
        method_status="not_computed",
        baseline=None,
        basis=None,
    )
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_pillar2_items_scenario_required"):
        _insert_item(
            connection,
            cycle_id,
            uuid4(),
            component_key="fx_risk_3",
            method="fx_nop_addon",
        )
    savepoint.rollback()


def test_an_approval_names_a_revision_and_cannot_run_ahead_of_the_head(
    connection: Connection,
) -> None:
    cycle_id, item_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_item(connection, cycle_id, item_id)
    connection.execute(
        text(
            "UPDATE icaap_pillar2_items SET approved_by = :actor, approved_revision_no = 1, "
            "approved_at = :now WHERE id = :id"
        ),
        {"id": str(item_id), "actor": str(uuid4()), "now": _NOW},
    )
    _refused(
        connection,
        "UPDATE icaap_pillar2_items SET approved_revision_no = 9 WHERE id = :id",
        {"id": str(item_id)},
        "ck_icaap_pillar2_items_approval",
    )
    _refused(
        connection,
        "UPDATE icaap_pillar2_items SET approved_by = NULL WHERE id = :id",
        {"id": str(item_id)},
        "ck_icaap_pillar2_items_approval",
    )


def test_a_computed_item_must_carry_an_amount_and_a_basis(connection: Connection) -> None:
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_pillar2_items_computed_has_amount"):
        _insert_item(connection, cycle_id, uuid4(), baseline=None)
    savepoint.rollback()


# --- the migration itself -------------------------------------------------


def test_the_migration_is_reversible_and_leaves_nothing_behind(
    p2_schema: MigratedPostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Up, down, up — on the schema this module already migrated.

    Placed last on purpose: it drops and rebuilds the twelve tables, and every
    other case in this file has already run against them.

    The module fixture's ``DATABASE_URL`` does not survive into a test body —
    the root conftest blanks it per test so a developer's ``.env`` can never
    leak into the hermetic suite — so alembic is pointed at this schema again
    here, from the engine that is already connected to it.
    """
    monkeypatch.setenv(
        "DATABASE_URL", p2_schema.app_engine.url.render_as_string(hide_password=False)
    )
    clear_database_caches()
    config = alembic_config_for_app()
    command.downgrade(config, _PARAMETERS_REVISION)
    with p2_schema.app_engine.begin() as connection:
        remaining = [
            table
            for table in TABLES
            if connection.scalar(text("SELECT to_regclass(:table)"), {"table": table}) is not None
        ]
        leftover_policies = set(
            connection.scalars(
                text("SELECT policyname FROM pg_policies WHERE tablename = ANY(:tables)"),
                {"tables": list(TABLES)},
            )
        )
    assert remaining == []
    assert leftover_policies == set()

    command.upgrade(config, "head")
    with p2_schema.app_engine.begin() as connection:
        rebuilt = [
            table
            for table in TABLES
            if connection.scalar(text("SELECT to_regclass(:table)"), {"table": table}) is not None
        ]
        forced = connection.execute(
            text(
                "SELECT count(*) FROM pg_class WHERE oid = ANY(ARRAY(SELECT to_regclass(t) "
                "FROM unnest(CAST(:tables AS text[])) AS t)) AND relforcerowsecurity"
            ),
            {"tables": list(TABLES)},
        ).scalar()
    assert rebuilt == list(TABLES)
    assert forced == len(TABLES)
    clear_database_caches()
