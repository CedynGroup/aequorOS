"""The ICAAP filing plane is sealed by the DATABASE, not by convention.

``202609190058`` puts the review chain, the filed documents and the ¶82
disclosure under the same three tiers ``202609190055`` established, and none of
them is provable from the migration text:

* **UNALTERABLE** — a pinned stage, an officer's decision, a filed document and
  its withdrawal accept no UPDATE at all. "Who approved this ICAAP, on which
  text, and what was filed with it" is the question a supervisor asks; if the
  answer can be edited it is not an answer. DELETE stays reachable so deleting a
  draft cycle (or a package cascade) still works.
* **SEALED** — an approved workflow template and an approved disclosure are
  four-eyed statements with a date on them. Afterwards only the supersession and
  publication bookkeeping may move, each field write-once.
* **RLS** — one tenant's review chain is invisible to another, enforced by the
  database rather than by a WHERE clause somebody has to remember.

Two structural rules are pinned here explicitly because a service bug would
otherwise walk straight past them:

* the partial unique index on forward decisions makes two simultaneous
  approvals of the same stage in the same round a database error rather than a
  race the last writer wins;
* the widened open-cycle predicate lets a ¶74 revision start while the filed
  cycle is still, truthfully, ``submitted``. The rejected alternative was to
  re-label the filing so an index would admit its successor.

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
    reason="TEST_DATABASE_URL is required for the ICAAP filing schema checks.",
)

pytestmark = postgres_only

_BANK = "BK-ICAAPF1"
_OTHER_BANK = "BK-ICAAPF2"
_NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
_AS_OF = date(2026, 12, 31)
_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64

TABLES = (
    "icaap_workflow_templates",
    "icaap_cycle_stages",
    "regulatory_package_attachments",
    "regulatory_package_attachment_withdrawals",
    "icaap_stage_decisions",
    "icaap_disclosures",
)
UNALTERABLE = (
    "icaap_cycle_stages",
    "regulatory_package_attachments",
    "regulatory_package_attachment_withdrawals",
    "icaap_stage_decisions",
)
SEALED = ("icaap_workflow_templates", "icaap_disclosures")


@pytest.fixture(scope="module")
def filing_schema() -> Iterator[MigratedPostgresSchema]:
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_icaapfiling_{uuid4().hex}"
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
def connection(filing_schema: MigratedPostgresSchema) -> Iterator[Connection]:
    with filing_schema.app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.organization_id', :org, false)"), {"org": ORG_1})
        conn.commit()
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


#: Two enforcement layers, either may refuse first: the migration's REVOKE
#: (a non-superuser role gets InsufficientPrivilege) or, for a role that kept
#: the privilege, the append-only trigger and RESTRICTIVE policy.
_UNALTERABLE = r"append-only|permission denied|restrict|policy"


def _refused(connection: Connection, statement: str, params: dict[str, Any], match: str) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match=match):
        connection.execute(text(statement), params)
    savepoint.rollback()


def _bypasses_rls(connection: Connection) -> bool:
    """Whether this role sees through row-level security (see 202609190055's test)."""
    return bool(
        connection.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar()
    )


# --- row builders ---------------------------------------------------------


def _insert_cycle(
    connection: Connection,
    cycle_id: UUID,
    *,
    status: str = "in_review",
    package_id: UUID | None = None,
    as_of: date = _AS_OF,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_cycles
              (id, organization_id, bank_id, fiscal_year, as_of_date, cycle_kind, basis,
               subsidiaries_declared, title, framework_code, framework_version,
               framework_sha256, status, round, package_id, due_date, due_date_basis,
               created_by, created_at, updated_at)
            VALUES
              (:id, :org, :bank, 2026, :as_of, 'annual', 'solo', false, 'ICAAP FY2026',
               'bog_icaap', '2026.02-ed.1', :digest, :status, 1, :package,
               DATE '2027-03-31', 'framework', :actor, :now, :now)
            """
        ),
        {
            "id": str(cycle_id),
            "org": ORG_1,
            "bank": _BANK,
            "as_of": as_of,
            "digest": _DIGEST,
            "status": status,
            "package": None if package_id is None else str(package_id),
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_package(
    connection: Connection,
    package_id: UUID,
    *,
    return_code: str = "ICAAP-REPORT",
    family: str = "icaap",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO regulatory_packages
              (id, organization_id, bank_id, return_family, return_code, reporting_date,
               frequency, basis, status, version, snapshot, source_runs, generated_by,
               generated_at, attestation_state, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :family, :code, :as_of, 'annual', 'solo',
               'generated', 1, '{}'::json, '[]'::json, :actor, :now, 'unsigned', :now, :now)
            """
        ),
        {
            "id": str(package_id),
            "org": ORG_1,
            "bank": _BANK,
            "family": family,
            "code": return_code,
            "as_of": _AS_OF,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_template(  # noqa: PLR0913 - a row is its named columns
    connection: Connection,
    template_id: UUID,
    *,
    version: int = 1,
    status: str = "approved",
    proposed_by: UUID | None = None,
    decided_by: UUID | None = None,
) -> UUID:
    proposer = proposed_by or uuid4()
    decided = status in {"approved", "rejected", "superseded"}
    decider = decided_by if decided_by is not None else (uuid4() if decided else None)
    connection.execute(
        text(
            """
            INSERT INTO icaap_workflow_templates
              (id, organization_id, bank_id, version, status, stages, reason, proposed_by,
               submitted_at, decided_at, decided_by, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :version, :status, '[]'::json, 'Board structure',
               :proposer, :now, :decided_at, :decider, :now, :now)
            """
        ),
        {
            "id": str(template_id),
            "org": ORG_1,
            "bank": _BANK,
            "version": version,
            "status": status,
            "proposer": str(proposer),
            "decider": None if decider is None else str(decider),
            "decided_at": _NOW if status in {"approved", "superseded"} else None,
            "now": _NOW,
        },
    )
    return proposer


def _insert_stage(  # noqa: PLR0913 - a row is its named columns
    connection: Connection,
    cycle_id: UUID,
    stage_id: UUID,
    *,
    seq: int = 1,
    stage_key: str = "preparation",
    decision_kind: str = "prepare",
    source: str = "framework_default",
    template_id: UUID | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_cycle_stages
              (id, organization_id, bank_id, cycle_id, seq, stage_key, title, decision_kind,
               officer_titles, freeze_on_approve, source, template_id, created_at)
            VALUES
              (:id, :org, :bank, :cycle, :seq, :key, 'Preparation', :kind,
               '[]'::json, false, :source, :template, :now)
            """
        ),
        {
            "id": str(stage_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "seq": seq,
            "key": stage_key,
            "kind": decision_kind,
            "source": source,
            "template": None if template_id is None else str(template_id),
            "now": _NOW,
        },
    )


def _insert_decision(  # noqa: PLR0913 - a row is its named columns
    connection: Connection,
    cycle_id: UUID,
    decision_id: UUID,
    *,
    decision: str = "approved",
    stage_seq: int = 2,
    round_no: int = 1,
    return_to_seq: int | None = None,
    comment: str | None = None,
    digest: str = _DIGEST,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_stage_decisions
              (id, organization_id, bank_id, cycle_id, stage_seq, stage_key, round, decision,
               return_to_seq, review_digest, comment, decided_by, decided_by_name,
               authority, created_at)
            VALUES
              (:id, :org, :bank, :cycle, :seq, 'cro_review', :round, :decision,
               :return_to, :digest, :comment, :actor, 'Ama Mensah', '{}'::json, :now)
            """
        ),
        {
            "id": str(decision_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "seq": stage_seq,
            "round": round_no,
            "decision": decision,
            "return_to": return_to_seq,
            "digest": digest,
            "comment": comment,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_attachment(  # noqa: PLR0913 - a row is its named columns
    connection: Connection,
    package_id: UUID,
    attachment_id: UUID,
    *,
    source: str = "package_upload",
    source_attachment_id: UUID | None = None,
    gate: str = "submission",
    sha256: str = _DIGEST,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO regulatory_package_attachments
              (id, organization_id, bank_id, package_id, package_version, kind, title,
               original_filename, media_type, byte_size, sha256, storage_tier, object_path,
               source, source_attachment_id, gate, attributes, attached_by, created_at)
            VALUES
              (:id, :org, :bank, :package, 1, 'board_resolution', 'Board resolution',
               'resolution.pdf', 'application/pdf', 2048, :sha, 'outputs',
               'bog_returns/2026-12-31/x/attachments/a.pdf', :source, :source_attachment,
               :gate, '{}'::json, :actor, :now)
            """
        ),
        {
            "id": str(attachment_id),
            "org": ORG_1,
            "bank": _BANK,
            "package": str(package_id),
            "sha": sha256,
            "source": source,
            "source_attachment": (
                None if source_attachment_id is None else str(source_attachment_id)
            ),
            "gate": gate,
            "actor": str(uuid4()),
            "now": _NOW,
        },
    )


def _insert_disclosure(  # noqa: PLR0913 - a row is its named columns
    connection: Connection,
    cycle_id: UUID,
    disclosure_id: UUID,
    source_package_id: UUID,
    *,
    status: str = "approved",
    package_id: UUID | None = None,
    proposed_by: UUID | None = None,
    decided_by: UUID | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO icaap_disclosures
              (id, organization_id, bank_id, cycle_id, source_package_id, status,
               selected_section_keys, withheld, proposed_by, proposed_at, decided_by,
               decided_at, package_id, created_at, updated_at)
            VALUES
              (:id, :org, :bank, :cycle, :source, :status, '[]'::json, '[]'::json,
               :proposer, :now, :decider, :decided_at, :package, :now, :now)
            """
        ),
        {
            "id": str(disclosure_id),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "source": str(source_package_id),
            "status": status,
            "proposer": str(proposed_by or uuid4()),
            "decider": None if decided_by is None else str(decided_by),
            "decided_at": _NOW if status in {"approved", "published", "superseded"} else None,
            "package": None if package_id is None else str(package_id),
            "now": _NOW,
        },
    )


# --- structure ------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES)
def test_every_filing_table_forces_tenant_isolation(connection: Connection, table: str) -> None:
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


@pytest.mark.parametrize("table", SEALED)
def test_the_governed_tables_carry_the_seal_trigger(connection: Connection, table: str) -> None:
    triggers = connection.execute(
        text(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass(:table) AND NOT tgisinternal"
        ),
        {"table": table},
    ).scalars()
    assert f"{table}_governed_row" in set(triggers)


def test_the_package_plane_admits_the_icaap_family_and_the_word_working_copy(
    connection: Connection,
) -> None:
    package_id = uuid4()
    _insert_package(connection, package_id)
    connection.execute(
        text(
            """
            INSERT INTO regulatory_package_artifacts
              (id, organization_id, package_id, kind, object_path, checksum_sha256,
               size_bytes, created_at)
            VALUES (:id, :org, :package, 'docx_working', 'x.docx', :sha, 10, :now)
            """
        ),
        {
            "id": str(uuid4()),
            "org": ORG_1,
            "package": str(package_id),
            "sha": _DIGEST,
            "now": _NOW,
        },
    )


def test_the_signing_policy_carries_ordered_slots_defaulting_to_today(
    connection: Connection,
) -> None:
    """Every existing policy reads ``false``, which is today's behaviour."""
    policy_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO return_signing_policies
              (id, organization_id, bank_id, return_family, required_signatures,
               required_attachments, require_signature, require_signed_pdf, distinct_signers,
               effective_from, reason, created_at, updated_at)
            VALUES
              (:id, :org, :bank, 'icaap', '[]'::json, '[]'::json, true, true, true,
               DATE '2026-01-01', 'seed', :now, :now)
            """
        ),
        {"id": str(policy_id), "org": ORG_1, "bank": _BANK, "now": _NOW},
    )
    assert (
        connection.execute(
            text("SELECT ordered_slots FROM return_signing_policies WHERE id = :id"),
            {"id": str(policy_id)},
        ).scalar()
        is False
    )


# --- tenant isolation -----------------------------------------------------


def test_one_tenants_review_chain_is_invisible_to_another(connection: Connection) -> None:
    if _bypasses_rls(connection):
        pytest.skip("this role bypasses row-level security; the policy cannot be observed")
    cycle_id, stage_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_stage(connection, cycle_id, stage_id)
    assert connection.execute(text("SELECT count(*) FROM icaap_cycle_stages")).scalar() == 1

    connection.execute(text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_2})
    assert connection.execute(text("SELECT count(*) FROM icaap_cycle_stages")).scalar() == 0


# --- append-only ----------------------------------------------------------


def test_a_pinned_stage_can_never_be_rewritten(connection: Connection) -> None:
    """The chain a cycle was reviewed under is not editable mid-review."""
    cycle_id, stage_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_stage(connection, cycle_id, stage_id)
    _refused(
        connection,
        "UPDATE icaap_cycle_stages SET decision_kind = 'approve' WHERE id = :id",
        {"id": str(stage_id)},
        _UNALTERABLE,
    )


def test_a_decision_can_never_be_rewritten(connection: Connection) -> None:
    """Not even its comment: a reviewer's recorded words are evidence."""
    cycle_id, decision_id = uuid4(), uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_decision(connection, cycle_id, decision_id)
    _refused(
        connection,
        "UPDATE icaap_stage_decisions SET review_digest = :other WHERE id = :id",
        {"id": str(decision_id), "other": _OTHER_DIGEST},
        _UNALTERABLE,
    )


def test_a_filed_document_can_never_be_rewritten(connection: Connection) -> None:
    package_id, attachment_id = uuid4(), uuid4()
    _insert_package(connection, package_id)
    _insert_attachment(connection, package_id, attachment_id)
    _refused(
        connection,
        "UPDATE regulatory_package_attachments SET sha256 = :other WHERE id = :id",
        {"id": str(attachment_id), "other": _OTHER_DIGEST},
        _UNALTERABLE,
    )


def test_withdrawing_a_document_is_a_new_row_and_happens_once(connection: Connection) -> None:
    package_id, attachment_id = uuid4(), uuid4()
    _insert_package(connection, package_id)
    _insert_attachment(connection, package_id, attachment_id)
    for _ in range(2):
        savepoint = connection.begin_nested()
        try:
            connection.execute(
                text(
                    """
                    INSERT INTO regulatory_package_attachment_withdrawals
                      (id, organization_id, bank_id, package_id, attachment_id, reason,
                       withdrawn_by, created_at)
                    VALUES (:id, :org, :bank, :package, :attachment, 'wrong file', :actor, :now)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "org": ORG_1,
                    "bank": _BANK,
                    "package": str(package_id),
                    "attachment": str(attachment_id),
                    "actor": str(uuid4()),
                    "now": _NOW,
                },
            )
            savepoint.commit()
        except DatabaseError:
            savepoint.rollback()
            break
    else:  # pragma: no cover - the second insert must have been refused
        pytest.fail("an attachment was withdrawn twice")


# --- the seal -------------------------------------------------------------


def test_an_approved_workflow_template_is_frozen_except_for_supersession(
    connection: Connection,
) -> None:
    template_id = uuid4()
    _insert_template(connection, template_id)
    _refused(
        connection,
        "UPDATE icaap_workflow_templates SET stages = '[{\"seq\": 1}]'::json WHERE id = :id",
        {"id": str(template_id)},
        "governed",
    )
    _refused(
        connection,
        "UPDATE icaap_workflow_templates SET status = 'draft' WHERE id = :id",
        {"id": str(template_id)},
        "governed",
    )
    # Supersession names a real successor: the self-FK refuses a pointer to a
    # template that does not exist, so "superseded by" can never be a dead end.
    successor = uuid4()
    _refused(
        connection,
        "UPDATE icaap_workflow_templates SET status = 'superseded', superseded_at = :now, "
        "superseded_by_id = :successor, updated_at = :now WHERE id = :id",
        {"id": str(template_id), "successor": str(successor), "now": _NOW},
        "superseded_by_id",
    )
    # The successor is proposed first and approved after the incumbent steps
    # aside, which is the order the partial unique index forces.
    _insert_template(connection, successor, version=2, status="pending_approval", decided_by=None)
    connection.execute(
        text(
            "UPDATE icaap_workflow_templates SET status = 'superseded', superseded_at = :now, "
            "superseded_by_id = :successor, updated_at = :now WHERE id = :id"
        ),
        {"id": str(template_id), "successor": str(successor), "now": _NOW},
    )
    _refused(
        connection,
        "UPDATE icaap_workflow_templates SET superseded_at = :later WHERE id = :id",
        {"id": str(template_id), "later": datetime(2027, 1, 1, tzinfo=UTC)},
        "write-once",
    )


def test_only_one_approved_workflow_template_per_bank(connection: Connection) -> None:
    _insert_template(connection, uuid4(), version=1)
    _refused(
        connection,
        """
        INSERT INTO icaap_workflow_templates
          (id, organization_id, bank_id, version, status, stages, reason, proposed_by,
           decided_at, decided_by, created_at, updated_at)
        VALUES (:id, :org, :bank, 2, 'approved', '[]'::json, 'second', :proposer,
                :now, :decider, :now, :now)
        """,
        {
            "id": str(uuid4()),
            "org": ORG_1,
            "bank": _BANK,
            "proposer": str(uuid4()),
            "decider": str(uuid4()),
            "now": _NOW,
        },
        "uq_icaap_workflow_templates_active",
    )


def test_a_workflow_template_cannot_be_approved_by_its_own_proposer(
    connection: Connection,
) -> None:
    """Maker-checker at the database, not only in the service."""
    actor = uuid4()
    _refused(
        connection,
        """
        INSERT INTO icaap_workflow_templates
          (id, organization_id, bank_id, version, status, stages, reason, proposed_by,
           decided_at, decided_by, created_at, updated_at)
        VALUES (:id, :org, :bank, 1, 'approved', '[]'::json, 'mine', :actor,
                :now, :actor, :now, :now)
        """,
        {
            "id": str(uuid4()),
            "org": ORG_1,
            "bank": _BANK,
            "actor": str(actor),
            "now": _NOW,
        },
        "four_eyes",
    )


def test_an_approved_disclosure_cannot_be_repointed_at_another_package(
    connection: Connection,
) -> None:
    """The published selection names the package it was drawn from, once."""
    cycle_id, source_package_id, package_id, disclosure_id = uuid4(), uuid4(), uuid4(), uuid4()
    _insert_package(connection, source_package_id)
    _insert_package(connection, package_id, return_code="ICAAP-DISCLOSURE")
    _insert_cycle(connection, cycle_id, status="submitted", package_id=source_package_id)
    _insert_disclosure(
        connection, cycle_id, disclosure_id, source_package_id, package_id=package_id
    )
    other = uuid4()
    _insert_package(connection, other, return_code="ICAAP-UPDATE")
    _refused(
        connection,
        "UPDATE icaap_disclosures SET package_id = :other WHERE id = :id",
        {"id": str(disclosure_id), "other": str(other)},
        "write-once",
    )
    _refused(
        connection,
        "UPDATE icaap_disclosures SET selected_section_keys = '[\"a\"]'::json WHERE id = :id",
        {"id": str(disclosure_id)},
        "governed",
    )


def test_the_intended_publication_still_runs_after_the_seal(connection: Connection) -> None:
    """The seal must not block the path it exists to protect.

    A guard that refuses the one transition the product needs is worse than no
    guard, because it is found in production. ``approved -> published`` with the
    URL and the date is the whole point of the row, so it is proven here.
    """
    cycle_id, source_package_id, package_id, disclosure_id = uuid4(), uuid4(), uuid4(), uuid4()
    _insert_package(connection, source_package_id)
    _insert_package(connection, package_id, return_code="ICAAP-DISCLOSURE")
    _insert_cycle(connection, cycle_id, status="submitted", package_id=source_package_id)
    _insert_disclosure(
        connection, cycle_id, disclosure_id, source_package_id, package_id=package_id
    )
    connection.execute(
        text(
            "UPDATE icaap_disclosures SET status = 'published', "
            "published_url = 'https://bank.example/icaap-2026.pdf', "
            "published_on = DATE '2027-03-31', updated_at = :now WHERE id = :id"
        ),
        {"id": str(disclosure_id), "now": _NOW},
    )
    assert (
        connection.execute(
            text("SELECT status FROM icaap_disclosures WHERE id = :id"),
            {"id": str(disclosure_id)},
        ).scalar()
        == "published"
    )
    _refused(
        connection,
        "UPDATE icaap_disclosures SET published_url = 'https://bank.example/other.pdf' "
        "WHERE id = :id",
        {"id": str(disclosure_id)},
        "write-once",
    )


def test_an_approved_disclosure_must_name_its_package(connection: Connection) -> None:
    cycle_id, source_package_id = uuid4(), uuid4()
    _insert_package(connection, source_package_id)
    _insert_cycle(connection, cycle_id, status="submitted", package_id=source_package_id)
    _refused(
        connection,
        """
        INSERT INTO icaap_disclosures
          (id, organization_id, bank_id, cycle_id, source_package_id, status,
           selected_section_keys, withheld, proposed_by, proposed_at, decided_by, decided_at,
           created_at, updated_at)
        VALUES (:id, :org, :bank, :cycle, :source, 'approved', '[]'::json, '[]'::json,
                :proposer, :now, :decider, :now, :now, :now)
        """,
        {
            "id": str(uuid4()),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "source": str(source_package_id),
            "proposer": str(uuid4()),
            "decider": str(uuid4()),
            "now": _NOW,
        },
        "has_package",
    )


# --- the structural rules the services lean on ----------------------------


def test_two_approvals_of_the_same_stage_and_round_cannot_both_land(
    connection: Connection,
) -> None:
    """The last line against a concurrent double-approval is the index."""
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_decision(connection, cycle_id, uuid4(), decision="approved", stage_seq=2)
    _refused(
        connection,
        """
        INSERT INTO icaap_stage_decisions
          (id, organization_id, bank_id, cycle_id, stage_seq, stage_key, round, decision,
           review_digest, decided_by, decided_by_name, authority, created_at)
        VALUES (:id, :org, :bank, :cycle, 2, 'cro_review', 1, 'reviewed', :digest,
                :actor, 'Kofi Asare', '{}'::json, :now)
        """,
        {
            "id": str(uuid4()),
            "org": ORG_1,
            "bank": _BANK,
            "cycle": str(cycle_id),
            "digest": _DIGEST,
            "actor": str(uuid4()),
            "now": _NOW,
        },
        "uq_icaap_stage_decisions_forward",
    )


def test_a_return_sits_beside_the_forward_decision_it_reverses(connection: Connection) -> None:
    """``returned`` is not a forward decision, so the index leaves room for it."""
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    _insert_decision(connection, cycle_id, uuid4(), decision="approved", stage_seq=2)
    _insert_decision(
        connection,
        cycle_id,
        uuid4(),
        decision="returned",
        stage_seq=2,
        return_to_seq=1,
        comment="The concentration figures predate the December book.",
    )


def test_a_return_must_name_an_earlier_stage_and_say_why(connection: Connection) -> None:
    """Each case must be refused BY THIS CONSTRAINT, named.

    Until 2026-09-20 the pattern was the bare word ``return``, which also
    appears in the bound INSERT text that SQLAlchemy prints in the error — so
    any database error at all satisfied it, and the three cases could not be
    told apart from each other, from ``ck_icaap_stage_decisions_decision``, or
    from a foreign key.
    """
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    for return_to, comment, _case in (
        (None, "no target", "no target"),
        (2, None, "no reason given"),
        (3, "target is not earlier", "target is not earlier"),
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(DatabaseError, match="ck_icaap_stage_decisions_return"):
            _insert_decision(
                connection,
                cycle_id,
                uuid4(),
                decision="returned",
                stage_seq=2,
                return_to_seq=return_to,
                comment=comment,
            )
        savepoint.rollback()

    # The constraint is a gate, not a blanket refusal: the well-formed return
    # lands. Without this, tightening the CHECK to ``false`` would pass above.
    _insert_decision(
        connection,
        cycle_id,
        uuid4(),
        decision="returned",
        stage_seq=2,
        return_to_seq=1,
        comment="The concentration figures predate the December book.",
    )


def test_a_cycle_sourced_attachment_must_name_the_upload_it_came_from(
    connection: Connection,
) -> None:
    package_id = uuid4()
    _insert_package(connection, package_id)
    # Named, not the bare word ``origin``: that substring is in the bound INSERT
    # text too, so it was satisfied by any database error (2026-09-20).
    origin = "ck_regulatory_package_attachments_origin"
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match=origin):
        _insert_attachment(connection, package_id, uuid4(), source="icaap_cycle")
    savepoint.rollback()

    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match=origin):
        _insert_attachment(
            connection,
            package_id,
            uuid4(),
            source="package_upload",
            source_attachment_id=uuid4(),
        )
    savepoint.rollback()

    # Both well-formed pairings land, so the constraint is not simply refusing
    # every attachment.
    _insert_attachment(connection, package_id, uuid4(), source="package_upload")
    _insert_attachment(
        connection, package_id, uuid4(), source="icaap_cycle", source_attachment_id=uuid4()
    )


def test_a_bank_template_stage_must_name_its_template(connection: Connection) -> None:
    """Named: ``template`` alone also matches the bound INSERT text and the
    table name, so it convicted nothing in particular (2026-09-20)."""
    cycle_id = uuid4()
    _insert_cycle(connection, cycle_id)
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_cycle_stages_template"):
        _insert_stage(connection, cycle_id, uuid4(), source="bank_template")
    savepoint.rollback()

    # The other direction of the biconditional, and the well-formed case, so a
    # CHECK of ``false`` would not pass this test.
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="ck_icaap_cycle_stages_template"):
        _insert_stage(
            connection,
            cycle_id,
            uuid4(),
            seq=2,
            stage_key="review",
            source="framework_default",
            template_id=uuid4(),
        )
    savepoint.rollback()
    _insert_stage(
        connection, cycle_id, uuid4(), seq=2, stage_key="review", source="framework_default"
    )


def test_a_revision_can_start_while_the_filed_cycle_is_still_submitted(
    connection: Connection,
) -> None:
    """The widened open-cycle predicate (P3). The filed cycle keeps saying it is
    filed; it becomes ``superseded`` only when the revision's freeze supersedes
    its package."""
    filed_package, filed_cycle, revision_cycle = uuid4(), uuid4(), uuid4()
    _insert_package(connection, filed_package)
    _insert_cycle(connection, filed_cycle, status="submitted", package_id=filed_package)
    _insert_cycle(connection, revision_cycle, status="draft")
    assert (
        connection.execute(
            text("SELECT status FROM icaap_cycles WHERE id = :id"), {"id": str(filed_cycle)}
        ).scalar()
        == "submitted"
    )


def test_two_open_cycles_are_still_refused(connection: Connection) -> None:
    """Widening the predicate must not have opened the index altogether."""
    _insert_cycle(connection, uuid4(), status="draft")
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="uq_icaap_cycles_open"):
        _insert_cycle(connection, uuid4(), status="draft")
    savepoint.rollback()


def test_only_one_live_disclosure_per_cycle(connection: Connection) -> None:
    cycle_id, source_package_id = uuid4(), uuid4()
    _insert_package(connection, source_package_id)
    _insert_cycle(connection, cycle_id, status="submitted", package_id=source_package_id)
    _insert_disclosure(connection, cycle_id, uuid4(), source_package_id, status="draft")
    savepoint = connection.begin_nested()
    with pytest.raises(DatabaseError, match="uq_icaap_disclosures_live"):
        _insert_disclosure(connection, cycle_id, uuid4(), source_package_id, status="draft")
    savepoint.rollback()

    connection.execute(
        text("UPDATE icaap_disclosures SET status = 'rejected', updated_at = :now"),
        {"now": _NOW},
    )
    _insert_disclosure(connection, cycle_id, uuid4(), source_package_id, status="draft")
