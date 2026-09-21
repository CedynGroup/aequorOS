"""The shared filing review chain: Preparer -> Approver -> Validator.

Three tenant tables and three columns on ``regulatory_packages``
(``docs/filing_workflow_redesign.md`` §3, §4, §6 steps 3-4). The package plane
gets the review chain P3 built for the ICAAP, because the alternative — a third
hardcoded status — is the mistake that produced the redesign: "Approver" was
hardcoded and the next bank's process did not fit it.

Tiers, built from the guard functions that already exist and never recreated
here:

* SEALED — ``filing_workflow_templates`` once approved, through
  ``aequoros_governed_row_guard`` (``202608230038``). A bank's approved filing
  chain is a four-eyed statement with a date on it; afterwards only the
  supersession bookkeeping may move, each field write-once.
* UNALTERABLE — ``package_workflow_stages`` and ``package_stage_decisions``,
  through ``aequoros_append_only_guard`` (``202607250027``) with UPDATE blocked
  and DELETE left reachable so a package cascade still works. An officer's
  decision is evidence: it is corrected by a NEW row, never by an edit.
* mutable — none.

The three columns on ``regulatory_packages``:

* ``checks_passed`` — the MACHINE validation result, which replaces the
  ``validated`` status as the authority on it. Validation gates ENTRY to the
  chain; it is not a stage and not a person.
* ``current_stage_seq`` / ``workflow_round`` — where the chain is. ``NULL``
  means no chain has been pinned. ``pending_approval`` and ``approved`` become
  projections of this.

**No stored digest moves.** Nothing here touches ``content_digest``,
``certification_digest``, ``register_state_digest``, ``snapshot_sha256`` or
``input_hash``, and no snapshot is rewritten.

**Terminal packages are left alone.** ``submitted``, ``acknowledged``,
``rejected``, ``declined`` and ``superseded`` rows get ``checks_passed``
(truthful bookkeeping, read by nothing that can change them) and no chain at
all. What was filed was filed.

**In-flight packages are mapped onto the default template.** A package at
``pending_approval`` or ``approved`` has its chain pinned and is placed at the
stage its recorded history implies, and the decisions already recorded in
``regulatory_package_approvals`` — real actors, real reasons, real times — are
PROJECTED into ``package_stage_decisions`` rather than invented. An ``approved``
package therefore lands at the Validator's stage: its Approver approved it, and
under the new rule that is not authority to file.

**The data step is RLS-aware and verifies itself.** Alembic runs as the
tenant-scoped app role: it owns these tables but has no BYPASSRLS, and
``regulatory_packages``, ``regulatory_package_approvals`` and ``users`` are all
FORCE RLS with no ``app.organization_id`` set during a migration, so an unscoped
UPDATE reports rowcount 0 and fails silently (audit P0-18; the trap that bit
``202608150013``). The step therefore runs inside
``app.db.session.force_rls_suspended`` over EVERY table it reads or writes —
suspending only ``regulatory_packages`` would leave the decision projection
seeing nothing, which is the same silent miss one table across — and then
compares its own row counts against what it expected, RAISING if they differ.
The symptom of a silent miss would be an officer unable to approve a return
that is mid-filing, so it must not be silent. DDL is transactional in Postgres,
so any failure rolls the whole migration back.

Revision ID: 202609200064
Revises: 202609200063
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202609200064"
down_revision = "202609200063"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

#: Creation order = FK order; the downgrade reverses it.
_TABLES: tuple[str, ...] = (
    "filing_workflow_templates",
    "package_workflow_stages",
    "package_stage_decisions",
)
_UNALTERABLE: tuple[str, ...] = ("package_workflow_stages", "package_stage_decisions")
_SEALED: dict[str, tuple[str, str, str, str, str]] = {
    "filing_workflow_templates": (
        "superseded_at,superseded_by_id,updated_at",
        "superseded_at,superseded_by_id",
        "status",
        "approved,superseded",
        "superseded",
    ),
}

_PACKAGE_FK_TARGETS = ["regulatory_packages.id", "regulatory_packages.organization_id"]
_BANK_FK_TARGETS = ["banks.id", "banks.organization_id"]
_TEMPLATE_FK_TARGETS = [
    "filing_workflow_templates.id",
    "filing_workflow_templates.organization_id",
]

_TEMPLATE_STATUSES = "('draft', 'pending_approval', 'approved', 'rejected', 'superseded')"
_DECISION_KINDS = "('prepare', 'review', 'approve')"
_STAGE_SOURCES = "('platform_default', 'bank_template')"
_STAGE_DECISIONS = "('submitted', 'reviewed', 'approved', 'returned')"
_FORWARD_DECISION = "decision IN ('submitted', 'reviewed', 'approved')"
_ACTIVE_TEMPLATE = "status = 'approved'"

#: The default chain, frozen here as the migration saw it. The live definition
#: is ``app/domain/filing/workflow.DEFAULT_STAGES``; a migration that imported
#: it would change meaning whenever the product did.
_DEFAULT_STAGES: tuple[dict[str, Any], ...] = (
    {"seq": 1, "stage_key": "preparation", "title": "Preparer", "kind": "prepare",
     "transmit": False},
    {"seq": 2, "stage_key": "approval", "title": "Approver", "kind": "approve",
     "transmit": False},
    {"seq": 3, "stage_key": "validation", "title": "Validator", "kind": "approve",
     "transmit": True},
)

#: Statuses that are finished with. Never given a chain, never re-projected.
_TERMINAL = ("submitted", "acknowledged", "rejected", "declined", "superseded")
#: Statuses whose packages are mid-review and must be mapped onto the chain.
_IN_REVIEW = ("pending_approval", "approved")


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def _revoke(table: str, privileges: str) -> None:
    op.execute(f"REVOKE {privileges} ON {table} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
            LOOP
                EXECUTE format('REVOKE {privileges} ON {table} FROM %I', r.rolname);
            END LOOP;
        END $$
        """
    )


def _install_unalterable(table: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION aequoros_append_only_guard()"
    )
    _revoke(table, "UPDATE, TRUNCATE")
    op.execute(
        f"CREATE POLICY {table}_no_update ON {table} AS RESTRICTIVE FOR UPDATE TO PUBLIC "
        f"USING (false) WITH CHECK (false)"
    )
    op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at SET DEFAULT now()")


def _install_sealed(table: str, arguments: tuple[str, str, str, str, str]) -> None:
    rendered = ", ".join(f"'{value}'" for value in arguments)
    op.execute(
        f"""
        CREATE TRIGGER {table}_governed_row BEFORE UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION aequoros_governed_row_guard({rendered})
        """
    )
    _revoke(table, "TRUNCATE")


def _create_templates() -> None:
    op.create_table(
        "filing_workflow_templates",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("stages", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN {_TEMPLATE_STATUSES}", name="ck_filing_workflow_templates_status"
        ),
        sa.CheckConstraint("version >= 1", name="ck_filing_workflow_templates_version"),
        sa.CheckConstraint(
            "decided_by IS NULL OR decided_by <> proposed_by",
            name="ck_filing_workflow_templates_four_eyes",
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'superseded') OR decided_at IS NOT NULL",
            name="ck_filing_workflow_templates_decided",
        ),
        sa.ForeignKeyConstraint(["bank_id", "organization_id"], _BANK_FK_TARGETS),
        sa.ForeignKeyConstraint(["superseded_by_id", "organization_id"], _TEMPLATE_FK_TARGETS),
        sa.UniqueConstraint("id", "organization_id", name="uq_filing_workflow_templates_id_org"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "version", name="uq_filing_workflow_templates_version"
        ),
    )
    op.create_index(
        "uq_filing_workflow_templates_active",
        "filing_workflow_templates",
        ["organization_id", "bank_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_TEMPLATE),
        sqlite_where=sa.text(_ACTIVE_TEMPLATE),
    )


def _create_stages() -> None:
    op.create_table(
        "package_workflow_stages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("package_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("decision_kind", sa.String(12), nullable=False),
        sa.Column("officer_titles", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "transmit_on_approve", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("template_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"decision_kind IN {_DECISION_KINDS}",
            name="ck_package_workflow_stages_decision_kind",
        ),
        sa.CheckConstraint("seq BETWEEN 1 AND 20", name="ck_package_workflow_stages_seq"),
        sa.CheckConstraint(
            f"source IN {_STAGE_SOURCES}", name="ck_package_workflow_stages_source"
        ),
        sa.CheckConstraint(
            "(source = 'bank_template') = (template_id IS NOT NULL)",
            name="ck_package_workflow_stages_template",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"], _PACKAGE_FK_TARGETS, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["bank_id", "organization_id"], _BANK_FK_TARGETS),
        sa.ForeignKeyConstraint(["template_id", "organization_id"], _TEMPLATE_FK_TARGETS),
        sa.UniqueConstraint("package_id", "seq", name="uq_package_workflow_stages_seq"),
        sa.UniqueConstraint("package_id", "stage_key", name="uq_package_workflow_stages_key"),
        sa.UniqueConstraint("id", "organization_id", name="uq_package_workflow_stages_id_org"),
    )
    op.create_index(
        "ix_package_workflow_stages_org_package",
        "package_workflow_stages",
        ["organization_id", "package_id"],
    )


def _create_decisions() -> None:
    op.create_table(
        "package_stage_decisions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("package_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_seq", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(60), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("return_to_seq", sa.Integer(), nullable=True),
        sa.Column("review_digest", sa.String(64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decided_by_name", sa.String(200), nullable=False),
        sa.Column("officer_title", sa.String(200), nullable=True),
        sa.Column("authority", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"decision IN {_STAGE_DECISIONS}", name="ck_package_stage_decisions_decision"
        ),
        sa.CheckConstraint("round >= 1", name="ck_package_stage_decisions_round"),
        sa.CheckConstraint(
            "stage_seq BETWEEN 1 AND 20", name="ck_package_stage_decisions_stage_seq"
        ),
        sa.CheckConstraint(
            "length(review_digest) = 64", name="ck_package_stage_decisions_digest"
        ),
        sa.CheckConstraint(
            "decision <> 'returned' OR (return_to_seq IS NOT NULL AND return_to_seq >= 1 "
            "AND return_to_seq < stage_seq AND comment IS NOT NULL)",
            name="ck_package_stage_decisions_return",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"], _PACKAGE_FK_TARGETS, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["bank_id", "organization_id"], _BANK_FK_TARGETS),
        sa.UniqueConstraint("id", "organization_id", name="uq_package_stage_decisions_id_org"),
    )
    op.create_index(
        "uq_package_stage_decisions_forward",
        "package_stage_decisions",
        ["package_id", "round", "stage_seq"],
        unique=True,
        postgresql_where=sa.text(_FORWARD_DECISION),
        sqlite_where=sa.text(_FORWARD_DECISION),
    )
    op.create_index(
        "ix_package_stage_decisions_org_package",
        "package_stage_decisions",
        ["organization_id", "package_id"],
    )


def _add_package_columns() -> None:
    op.add_column(
        "regulatory_packages",
        sa.Column("checks_passed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "regulatory_packages", sa.Column("current_stage_seq", sa.Integer(), nullable=True)
    )
    op.add_column(
        "regulatory_packages",
        sa.Column("workflow_round", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _frozen_review_digest(row: Any) -> str:
    """The review digest as ``domain/filing/review_digest`` computed it today.

    Frozen rather than imported: a migration that imported the live formula
    would change meaning whenever the product did, and a decision's digest is a
    historical statement.
    """
    report = row.validation_report or {}
    if isinstance(report, str):  # pragma: no cover - JSON column on some dialects
        report = json.loads(report)
    findings = report.get("findings") or report.get("results") or []
    body = {
        "schema": "filing-review-digest-v1",
        "return": [
            row.return_code,
            row.reporting_date.isoformat(),
            row.basis,
            row.version,
        ],
        "content_digest": row.content_digest,
        "snapshot_sha256": row.snapshot_sha256,
        "register_state_digest": row.register_state_digest,
        "checks_passed": True,
        "checks": {
            "error_count": report.get("error_count"),
            "findings": sorted(
                [
                    str(entry.get("rule_id") or entry.get("code") or ""),
                    str(entry.get("severity") or ""),
                    str(entry.get("message") or ""),
                ]
                for entry in findings
                if isinstance(entry, dict)
            ),
            "passed": bool(report.get("passed", False)),
            "warning_count": report.get("warning_count"),
        },
    }
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


#: Every FORCE-RLS table this step reads or writes. ``users`` and
#: ``regulatory_package_approvals`` are on the list because the decision
#: projection reads them: suspending only ``regulatory_packages`` would leave
#: the projection seeing nothing and writing nothing, which is the same silent
#: miss one table across.
_BACKFILL_TABLES: tuple[str, ...] = (
    "regulatory_packages",
    "regulatory_package_approvals",
    "users",
    "package_workflow_stages",
    "package_stage_decisions",
)


def _backfill_organization(bind: Any, organization_id: str) -> None:
    """One tenant's packages: the bookkeeping column, then the in-review chains.

    The suspension is named HERE, beside the statements it protects, rather than
    only in the caller — a function that states its own requirement cannot be
    broken by a new caller that forgot. It nests harmlessly:
    ``force_rls_suspended`` lifts only what is still FORCEd, so an inner block
    inside an outer one suspends nothing and restores nothing.
    """
    in_review = ", ".join(f"'{value}'" for value in _IN_REVIEW)
    terminal = ", ".join(f"'{value}'" for value in _TERMINAL)

    with force_rls_suspended(bind, *_BACKFILL_TABLES):
        expected = bind.execute(
            sa.text("SELECT count(*) FROM regulatory_packages WHERE organization_id = :org"),
            {"org": organization_id},
        ).scalar_one()
        # Truthful bookkeeping for every row, terminal ones included: a package
        # that got past 'generated' did so with a clean report, and one that
        # never did carries its report's own answer.
        result = bind.execute(
            sa.text(
                "UPDATE regulatory_packages SET checks_passed = "
                "(COALESCE((validation_report ->> 'passed')::boolean, false) "
                f"OR status IN ('validated', {in_review}, {terminal})) "
                "WHERE organization_id = :org"
            ),
            {"org": organization_id},
        )
        if result.rowcount != expected:
            # Loud, not silent. A backfill that cannot see the rows it must
            # update would leave every in-review return without a chain, and the
            # first symptom would be an officer unable to approve a return that
            # is already mid-filing.
            message = (
                f"filing chain backfill saw {result.rowcount} of {expected} packages "
                f"for {organization_id}: row-level security is hiding them. Re-run "
                "this migration with the BYPASSRLS worker role (WORKER_DATABASE_URL)."
            )
            raise RuntimeError(message)

        packages = bind.execute(
            sa.text(
                "SELECT id, organization_id, bank_id, status, return_code, "
                "reporting_date, basis, version, content_digest, snapshot_sha256, "
                "register_state_digest, validation_report FROM regulatory_packages "
                f"WHERE organization_id = :org AND status IN ({in_review}) ORDER BY id"
            ),
            {"org": organization_id},
        ).fetchall()

        for package in packages:
            digest = _frozen_review_digest(package)
            for stage in _DEFAULT_STAGES:
                bind.execute(
                    sa.text(
                        "INSERT INTO package_workflow_stages (id, organization_id, "
                        "bank_id, package_id, seq, stage_key, title, decision_kind, "
                        "officer_titles, transmit_on_approve, source, created_at) VALUES "
                        "(gen_random_uuid(), :org, :bank, :package, :seq, :key, :title, "
                        ":kind, '[]'::json, :transmit, 'platform_default', now())"
                    ),
                    {
                        "org": package.organization_id,
                        "bank": package.bank_id,
                        "package": package.id,
                        "seq": stage["seq"],
                        "key": stage["stage_key"],
                        "title": stage["title"],
                        "kind": stage["kind"],
                        "transmit": stage["transmit"],
                    },
                )
            # The decisions already recorded are PROJECTED, not invented: a
            # 'requested' row is the Preparer's submission, an 'approved' row is
            # the Approver's approval, each with its own actor, reason and time.
            approvals = bind.execute(
                sa.text(
                    "SELECT a.action, a.actor_user_id, a.reason, a.occurred_at, "
                    "COALESCE(u.display_name, 'Name not recorded') AS display_name, "
                    "u.job_title FROM regulatory_package_approvals a "
                    "LEFT JOIN users u ON u.id = a.actor_user_id "
                    "WHERE a.package_id = :package "
                    "AND a.action IN ('requested', 'approved') ORDER BY a.occurred_at"
                ),
                {"package": package.id},
            ).fetchall()
            seen: set[int] = set()
            for approval in approvals:
                seq = 1 if approval.action == "requested" else 2
                if seq in seen:
                    continue
                seen.add(seq)
                stage = _DEFAULT_STAGES[seq - 1]
                bind.execute(
                    sa.text(
                        "INSERT INTO package_stage_decisions (id, organization_id, "
                        "bank_id, package_id, stage_seq, stage_key, round, decision, "
                        "review_digest, comment, decided_by, decided_by_name, "
                        "officer_title, authority, created_at) VALUES "
                        "(gen_random_uuid(), :org, :bank, :package, :seq, :key, 1, "
                        ":decision, :digest, :comment, :actor, :name, :title, "
                        "'{}'::json, :at)"
                    ),
                    {
                        "org": package.organization_id,
                        "bank": package.bank_id,
                        "package": package.id,
                        "seq": seq,
                        "key": stage["stage_key"],
                        "decision": "submitted" if seq == 1 else "approved",
                        "digest": digest,
                        "comment": approval.reason,
                        "actor": approval.actor_user_id,
                        "name": approval.display_name,
                        "title": approval.job_title,
                        "at": approval.occurred_at,
                    },
                )
            # Where the return now waits. An 'approved' package lands at the
            # Validator: its Approver approved it, and that is no longer
            # authority to file.
            bind.execute(
                sa.text(
                    "UPDATE regulatory_packages SET current_stage_seq = :seq WHERE id = :id"
                ),
                {"seq": 3 if package.status == "approved" else 2, "id": package.id},
            )
            pinned = bind.execute(
                sa.text("SELECT count(*) FROM package_workflow_stages WHERE package_id = :id"),
                {"id": package.id},
            ).scalar_one()
            if pinned != len(_DEFAULT_STAGES):  # pragma: no cover - the inserts just ran
                message = (
                    f"filing chain backfill pinned {pinned} stages on package "
                    f"{package.id}; the in-review return would have no chain to be "
                    "approved on."
                )
                raise RuntimeError(message)


def _backfill() -> None:
    """Map every existing package onto the new model. See the module docstring."""
    bind = op.get_bind()
    with force_rls_suspended(bind, *_BACKFILL_TABLES):
        organizations = bind.execute(
            sa.text("SELECT DISTINCT organization_id FROM regulatory_packages ORDER BY 1")
        ).fetchall()
        for row in organizations:
            _backfill_organization(bind, row[0])


def upgrade() -> None:
    _create_templates()
    _create_stages()
    _create_decisions()
    _add_package_columns()

    if op.get_bind().dialect.name != "postgresql":
        return
    # The data step runs BEFORE the new tables carry RLS policies, so its
    # inserts are unfiltered; the tables it reads are not, which is why it sets
    # the tenant GUC per organization and verifies its own row counts.
    _backfill()

    for table in _TABLES:
        _enable_rls(table)
    for table in _UNALTERABLE:
        _install_unalterable(table)
    for table, arguments in _SEALED.items():
        _install_sealed(table, arguments)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in _SEALED:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_governed_row ON {table}")
        for table in _UNALTERABLE:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
            op.execute(f"DROP POLICY IF EXISTS {table}_no_update ON {table}")
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("package_stage_decisions")
    op.drop_table("package_workflow_stages")
    op.drop_table("filing_workflow_templates")
    op.drop_column("regulatory_packages", "workflow_round")
    op.drop_column("regulatory_packages", "current_stage_seq")
    op.drop_column("regulatory_packages", "checks_passed")
