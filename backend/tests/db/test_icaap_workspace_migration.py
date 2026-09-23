"""The ICAAP workspace is sealed by the DATABASE, not by convention.

``202609190055`` puts three tiers under the workspace, and none of them is
provable from the migration text:

* **SEALED** — once a cycle is frozen, its text, its figures and its package
  link are the ones a Board approved. Only the lifecycle status and the
  write-once timestamps may move afterwards.
* **UNALTERABLE** — section versions, block bindings, attachments and
  withdrawals accept no UPDATE at all, so "what did this report say in March"
  survives anyone's later opinion. DELETE stays reachable, which is what lets
  a draft cycle be deleted whole.
* **RLS** — one tenant's ICAAP is invisible to another, enforced by the
  database rather than by a WHERE clause somebody has to remember.

One consequence P3 must respect is pinned here explicitly: a send-back is TWO
statements, because the guard admits the status change alone and nothing else.

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
    reason="TEST_DATABASE_URL is required for the ICAAP workspace schema checks.",
)

pytestmark = postgres_only

_BANK = "BK-ICAAP01"
_OTHER_BANK = "BK-ICAAP02"
_NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
_AS_OF = date(2025, 12, 31)
_DIGEST = "a" * 64

TABLES = (
    "icaap_cycles",
    "icaap_sections",
    "icaap_section_versions",
    "icaap_data_blocks",
    "icaap_attachments",
    "icaap_block_bindings",
    "icaap_attachment_withdrawals",
)
UNALTERABLE = (
    "icaap_section_versions",
    "icaap_block_bindings",
    "icaap_attachments",
    "icaap_attachment_withdrawals",
)


@pytest.fixture(scope="module")
def icaap_schema() -> Iterator[MigratedPostgresSchema]:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_icaap_{uuid4().hex}"
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
def connection(icaap_schema: MigratedPostgresSchema) -> Iterator[Connection]:
    with icaap_schema.app_engine.connect() as conn:
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


def _tenant(connection: Connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :org, true)"), {"org": organization_id}
    )


def _insert_cycle(  # noqa: PLR0913 - a cycle row is its named columns
    connection: Connection,
    cycle_id: UUID,
    *,
    organization_id: str = ORG_1,
    bank_id: str = _BANK,
    cycle_kind: str = "annual",
    status: str = "draft",
    package_id: UUID | None = None,
    frozen_at: datetime | None = None,
    change_trigger: str | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_cycles
              (id, organization_id, bank_id, fiscal_year, as_of_date, cycle_kind, basis,
               subsidiaries_declared, title, framework_code, framework_version,
               framework_sha256, status, round, package_id, due_date, due_date_basis,
               change_trigger, created_by, frozen_at, created_at, updated_at)
            VALUES
              (:id, :org, :bank, 2025, :as_of, :kind, 'solo', false, 'ICAAP FY2025',
               'bog_icaap', '2026.02-ed.1', :digest, :status, 1, :package, DATE '2026-03-31',
               'framework', :trigger, :actor, :frozen_at, :now, :now)
            """
        ),
        {
            "id": str(cycle_id),
            "org": organization_id,
            "bank": bank_id,
            "as_of": _AS_OF,
            "kind": cycle_kind,
            "digest": _DIGEST,
            "status": status,
            "package": None if package_id is None else str(package_id),
            "trigger": change_trigger,
            "actor": str(uuid4()),
            "frozen_at": frozen_at,
            "now": _NOW,
        },
    )


def _bypasses_rls(connection: Connection) -> bool:
    """Whether this role sees through row-level security.

    A managed Postgres often grants the owning role BYPASSRLS, and the policy
    then cannot be observed from inside the session. The policy is still
    installed (a test above asserts that), so the honest thing is to skip the
    observable half rather than assert something the environment makes
    untestable.
    """
    return bool(
        connection.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar()
    )


def _insert_package(
    connection: Connection, package_id: UUID, *, reporting_date: date = _AS_OF
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO regulatory_packages
              (id, organization_id, bank_id, return_family, return_code, reporting_date,
               frequency, basis, status, version, snapshot, source_runs, generated_by,
               generated_at, attestation_state, created_at, updated_at)
            VALUES
              (:id, :org, :bank, 'icaap_stress', 'ICAAP-STRESS', :as_of, 'annual', 'solo',
               'generated', 1, '{}'::json, '[]'::json, :actor, :now, 'unsigned', :now, :now)
            """
        ),
        {
            "id": str(package_id),
            "org": ORG_1,
            "bank": _BANK,
            "as_of": reporting_date,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_section(connection: Connection, cycle_id: UUID, section_id: UUID) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_sections
              (id, organization_id, bank_id, cycle_id, section_key, letter, position,
               working_doc, working_rev, checklist_state, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :cycle, 'executive_summary', 'a', 1,
               '{"type":"doc","content":[]}'::json, 0, '{}'::json, :now, :now)
            """
        ),
        {
            "id": str(section_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "now": _NOW,
        },
    )


def _insert_version(
    connection: Connection, cycle_id: UUID, section_id: UUID, version_id: UUID
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_section_versions
              (id, organization_id, bank_id, cycle_id, section_id, section_key, version_no,
               round, source_rev, editor_schema_version, doc, plain_text, doc_sha256,
               fact_refs, block_refs, committed_by, created_at)
            VALUES
              (:id, :org, :bank, :cycle, :section, 'executive_summary', 1, 1, 1,
               'icaap-editor-v1', '{"type":"doc"}'::json, 'text', :digest,
               '[]'::json, '[]'::json, :actor, :now)
            """
        ),
        {
            "id": str(version_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "section": str(section_id),
            "digest": _DIGEST,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_attachment(connection: Connection, cycle_id: UUID, attachment_id: UUID) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_attachments
              (id, organization_id, bank_id, cycle_id, kind, title, original_filename,
               media_type, byte_size, sha256, storage_tier, object_path, attributes,
               uploaded_by, created_at)
            VALUES
              (:id, :org, :bank, :cycle, 'senior_management_report', 'Report', 'report.pdf',
               'application/pdf', 1024, :digest, 'outputs', 'icaap/2025/x.pdf', '{}'::json,
               :actor, :now)
            """
        ),
        {
            "id": str(attachment_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "digest": _DIGEST,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


# --- structure ------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES)
def test_every_workspace_table_forces_tenant_isolation(connection: Connection, table: str) -> None:
    row = connection.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = to_regclass(:table)"
        ),
        {"table": table},
    ).one()
    assert row == (True, True), f"{table} is not ENABLE+FORCE RLS"
    policies = connection.execute(
        text("SELECT policyname FROM pg_policies WHERE tablename = :table"), {"table": table}
    ).scalars()
    assert f"{table}_tenant_isolation" in set(policies)


@pytest.mark.parametrize("table", UNALTERABLE)
def test_the_append_only_tables_carry_their_trigger_and_policy(
    connection: Connection, table: str
) -> None:
    triggers = connection.execute(
        text(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass(:table) AND NOT tgisinternal"
        ),
        {"table": table},
    ).scalars()
    assert f"{table}_append_only" in set(triggers)
    policies = connection.execute(
        text("SELECT policyname FROM pg_policies WHERE tablename = :table"), {"table": table}
    ).scalars()
    assert f"{table}_no_update" in set(policies)


def test_the_cycle_table_carries_the_governed_row_guard(connection: Connection) -> None:
    triggers = connection.execute(
        text(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass('icaap_cycles') "
            "AND NOT tgisinternal"
        )
    ).scalars()
    assert "icaap_cycles_governed_row" in set(triggers)


# --- tenant isolation -----------------------------------------------------


def test_one_tenants_icaap_is_invisible_to_another(connection: Connection) -> None:
    if _bypasses_rls(connection):
        pytest.skip("this role bypasses row-level security; the policy cannot be observed")
    mine = uuid4()
    _insert_cycle(connection, mine)
    assert connection.execute(text("SELECT count(*) FROM icaap_cycles")).scalar() == 1

    _tenant(connection, ORG_2)
    assert connection.execute(text("SELECT count(*) FROM icaap_cycles")).scalar() == 0
    assert (
        connection.execute(
            text("SELECT count(*) FROM icaap_cycles WHERE id = :id"), {"id": str(mine)}
        ).scalar()
        == 0
    )


def test_a_row_cannot_be_written_for_another_tenant(connection: Connection) -> None:
    if _bypasses_rls(connection):
        pytest.skip("this role bypasses row-level security; the policy cannot be observed")
    _refused(
        connection,
        """
        INSERT INTO icaap_cycles
          (id, organization_id, bank_id, fiscal_year, as_of_date, cycle_kind, basis,
           subsidiaries_declared, title, framework_code, framework_version, framework_sha256,
           status, round, due_date, due_date_basis, created_by, created_at, updated_at)
        VALUES
          (:id, :org, :bank, 2025, :as_of, 'annual', 'solo', false, 'Theirs', 'bog_icaap',
           '2026.02-ed.1', :digest, 'draft', 1, DATE '2026-03-31', 'framework', :actor,
           :now, :now)
        """,
        {
            "id": str(uuid4()),
            "org": ORG_2,
            "bank": _OTHER_BANK,
            "as_of": _AS_OF,
            "digest": _DIGEST,
            "actor": str(uuid4()),
            "now": _NOW,
        },
        "row-level security",
    )


# --- append-only ----------------------------------------------------------


def test_a_committed_version_can_never_be_rewritten(connection: Connection) -> None:
    cycle_id, section_id, version_id = uuid4(), uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_section(connection, cycle_id, section_id)
    _insert_version(connection, cycle_id, section_id, version_id)
    _refused(
        connection,
        "UPDATE icaap_section_versions SET plain_text = 'rewritten' WHERE id = :id",
        {"id": str(version_id)},
        "(append-only|restrict|policy|permission denied)",
    )


def test_owner_reference_lock_does_not_allow_rewriting_evidence_id(connection: Connection) -> None:
    """The owner's key-share privilege must not become permission to rewrite a key."""
    cycle_id, attachment_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_attachment(connection, cycle_id, attachment_id)
    statement = "UPDATE icaap_attachments SET id = :new_id WHERE id = :id"
    params = {"id": str(attachment_id), "new_id": str(uuid4())}
    if _bypasses_rls(connection):
        _refused(connection, statement, params, "append-only")
    else:
        # The restrictive UPDATE policy hides every row, including from its owner.
        assert connection.execute(text(statement), params).rowcount == 0
    assert (
        connection.scalar(
            text("SELECT count(*) FROM icaap_attachments WHERE id = :id"),
            {"id": str(attachment_id)},
        )
        == 1
    )


def test_an_uploaded_document_can_never_be_rewritten(connection: Connection) -> None:
    cycle_id, attachment_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_attachment(connection, cycle_id, attachment_id)
    _refused(
        connection,
        "UPDATE icaap_attachments SET sha256 = :digest WHERE id = :id",
        {"id": str(attachment_id), "digest": "b" * 64},
        "(append-only|restrict|policy|permission denied)",
    )


def test_deleting_a_draft_cycle_takes_its_children_with_it(connection: Connection) -> None:
    """DELETE stays reachable so a cycle can be removed whole."""
    cycle_id, section_id, version_id, attachment_id = uuid4(), uuid4(), uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_section(connection, cycle_id, section_id)
    _insert_version(connection, cycle_id, section_id, version_id)
    _insert_attachment(connection, cycle_id, attachment_id)
    connection.execute(text("DELETE FROM icaap_cycles WHERE id = :id"), {"id": str(cycle_id)})
    for table in ("icaap_sections", "icaap_section_versions", "icaap_attachments"):
        remaining = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        assert remaining == 0, table


# --- the seal -------------------------------------------------------------


def test_a_draft_cycle_is_freely_editable(connection: Connection) -> None:
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    connection.execute(
        text("UPDATE icaap_cycles SET title = 'Renamed' WHERE id = :id"), {"id": str(cycle_id)}
    )


def test_freezing_is_one_statement_and_seals_what_follows(connection: Connection) -> None:
    cycle_id, package_id = uuid4(), uuid4()
    _insert_package(connection, package_id)
    _insert_cycle(connection, cycle_id)
    connection.execute(
        text(
            "UPDATE icaap_cycles SET status = 'frozen', package_id = :package, "
            "frozen_at = :now, updated_at = :now WHERE id = :id"
        ),
        {"id": str(cycle_id), "package": str(package_id), "now": _NOW},
    )
    _refused(
        connection,
        "UPDATE icaap_cycles SET title = 'Renamed after the Board approved it' WHERE id = :id",
        {"id": str(cycle_id)},
        "governed and sealed",
    )


def test_a_frozen_cycle_cannot_have_its_package_rewritten(connection: Connection) -> None:
    cycle_id, package_id, other_package = uuid4(), uuid4(), uuid4()
    _insert_package(connection, package_id)
    # A second package for the same return and date would collide with the
    # current-version unique index, so this one is for the year before.
    _insert_package(connection, other_package, reporting_date=date(2024, 12, 31))
    _insert_cycle(connection, cycle_id, status="frozen", package_id=package_id, frozen_at=_NOW)
    _refused(
        connection,
        "UPDATE icaap_cycles SET package_id = :other WHERE id = :id",
        {"id": str(cycle_id), "other": str(other_package)},
        "write-once",
    )


def test_the_lifecycle_still_runs_after_the_seal(connection: Connection) -> None:
    cycle_id, package_id = uuid4(), uuid4()
    _insert_package(connection, package_id)
    _insert_cycle(connection, cycle_id, status="frozen", package_id=package_id, frozen_at=_NOW)
    connection.execute(
        text(
            "UPDATE icaap_cycles SET status = 'board_approved', board_approved_at = :now, "
            "updated_at = :now WHERE id = :id"
        ),
        {"id": str(cycle_id), "now": _NOW},
    )
    _refused(
        connection,
        "UPDATE icaap_cycles SET board_approved_at = :later WHERE id = :id",
        {"id": str(cycle_id), "later": datetime(2026, 10, 1, tzinfo=UTC)},
        "write-once",
    )


def test_a_frozen_cycle_cannot_slide_back_to_draft(connection: Connection) -> None:
    cycle_id, package_id = uuid4(), uuid4()
    _insert_package(connection, package_id)
    _insert_cycle(connection, cycle_id, status="frozen", package_id=package_id, frozen_at=_NOW)
    _refused(
        connection,
        "UPDATE icaap_cycles SET status = 'draft' WHERE id = :id",
        {"id": str(cycle_id)},
        "may not move from",
    )


def test_a_send_back_is_two_statements(connection: Connection) -> None:
    """P3's ``return_cycle`` must be written this way, or the guard raises.

    The seal admits the status change and nothing else; the round and stage
    bookkeeping belongs in a second statement, by which time the row is
    unsealed and freely editable again.
    """
    cycle_id, package_id = uuid4(), uuid4()
    _insert_package(connection, package_id)
    _insert_cycle(connection, cycle_id, status="frozen", package_id=package_id, frozen_at=_NOW)
    _refused(
        connection,
        "UPDATE icaap_cycles SET status = 'returned', round = 2 WHERE id = :id",
        {"id": str(cycle_id)},
        "governed and sealed",
    )
    connection.execute(
        text("UPDATE icaap_cycles SET status = 'returned', updated_at = :now WHERE id = :id"),
        {"id": str(cycle_id), "now": _NOW},
    )
    connection.execute(
        text("UPDATE icaap_cycles SET round = 2, frozen_at = NULL WHERE id = :id"),
        {"id": str(cycle_id)},
    )
    assert (
        connection.execute(
            text("SELECT round FROM icaap_cycles WHERE id = :id"), {"id": str(cycle_id)}
        ).scalar()
        == 2
    )


# --- the checks that keep a rehearsal a rehearsal --------------------------
#
# These two used to assert that a rehearsal cycle could NEVER carry a package or
# be sealed. Lead ruling D-068 reversed that: a rehearsal runs the full
# lifecycle, because dry-running freeze, signature and submission is the only
# way a bank can exercise the riskiest part of the regime while the regulator's
# text is pending (D-006), and forbidding it bought no safety the package-level
# constraints do not buy. Migration 202609190062 moved the guarantee from
# preventing the SAFE act to blocking the DANGEROUS one.
#
# So the assertions are restated here against the invariants that now hold,
# rather than deleted: a rehearsal freezes, and it still cannot become a filing.


def test_a_rehearsal_can_now_freeze_and_carry_its_package(connection: Connection) -> None:
    """The act D-029 exists for, which the old CHECKs made impossible."""
    package_id = uuid4()
    _insert_package(connection, package_id)
    _insert_cycle(
        connection,
        uuid4(),
        cycle_kind="rehearsal",
        status="frozen",
        package_id=package_id,
        frozen_at=_NOW,
    )


def test_a_rehearsal_package_can_never_be_acknowledged(connection: Connection) -> None:
    """The regulator's own word about a document the regulator never received."""
    package_id = uuid4()
    _insert_package(connection, package_id)
    connection.execute(
        text(
            "UPDATE regulatory_packages SET return_family='icaap', is_rehearsal=true WHERE id=:id"
        ),
        {"id": str(package_id)},
    )
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="rehearsal_never_acknowledged"):
        connection.execute(
            text("UPDATE regulatory_packages SET status='acknowledged' WHERE id=:id"),
            {"id": str(package_id)},
        )
    savepoint.rollback()


def test_a_rehearsal_package_can_never_supersede_a_real_one(
    connection: Connection,
) -> None:
    """A dry run must not retire a filing. The other direction is cross-row and
    is enforced in ``generation._supersede_prior`` — see 202609190062."""
    real_id = uuid4()
    rehearsal_id = uuid4()
    _insert_package(connection, real_id)
    # A different reporting date, because the one-current-version index now
    # keys on ``is_rehearsal`` too and both rows start out non-rehearsal.
    _insert_package(connection, rehearsal_id, reporting_date=date(2024, 12, 31))
    connection.execute(
        text(
            "UPDATE regulatory_packages SET return_family='icaap', is_rehearsal=true WHERE id=:id"
        ),
        {"id": str(rehearsal_id)},
    )
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="rehearsal_supersedes_nothing"):
        connection.execute(
            text("UPDATE regulatory_packages SET supersedes_id=:prior WHERE id=:id"),
            {"prior": str(real_id), "id": str(rehearsal_id)},
        )
    savepoint.rollback()


def test_only_an_icaap_return_can_be_a_rehearsal(connection: Connection) -> None:
    """A BSD return marked as a rehearsal would be a filing nobody filed."""
    package_id = uuid4()
    _insert_package(connection, package_id)  # family 'icaap_stress'
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="rehearsal_is_icaap"):
        connection.execute(
            text("UPDATE regulatory_packages SET is_rehearsal=true WHERE id=:id"),
            {"id": str(package_id)},
        )
    savepoint.rollback()


def test_a_sealed_cycle_must_name_its_package(connection: Connection) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="sealed_has_package"):
        _insert_cycle(connection, uuid4(), status="frozen", frozen_at=_NOW)
    savepoint.rollback()


def test_an_off_cycle_update_must_name_its_trigger(connection: Connection) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="change_trigger"):
        _insert_cycle(connection, uuid4(), cycle_kind="material_change")
    savepoint.rollback()


def test_only_one_open_cycle_per_year_kind_and_basis(connection: Connection) -> None:
    first, second = uuid4(), uuid4()
    _insert_cycle(connection, first)
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="uq_icaap_cycles_open"):
        _insert_cycle(connection, second)
    savepoint.rollback()

    connection.execute(
        text("UPDATE icaap_cycles SET status = 'archived' WHERE id = :id"), {"id": str(first)}
    )
    _insert_cycle(connection, second)


# --- the governed parameters the revision seeds ---------------------------


def test_the_revision_seeds_the_parameters_the_workspace_reads(
    connection: Connection,
) -> None:
    from app.services.icaap.parameters import ICAAP_PARAM_CODES  # noqa: PLC0415

    rows = connection.execute(
        text(
            "SELECT param_code, status, confirmation_status FROM regulatory_parameter "
            "WHERE param_code = ANY(:codes)"
        ),
        {"codes": sorted(ICAAP_PARAM_CODES)},
    ).all()
    seeded = {row[0] for row in rows}
    assert seeded == ICAAP_PARAM_CODES
    assert all(row[1] == "approved" for row in rows)
