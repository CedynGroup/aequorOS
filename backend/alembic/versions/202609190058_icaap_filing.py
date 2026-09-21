"""ICAAP filing (M3): the review chain, package attachments and the ¶82 disclosure.

Six tenant tables and three constraint widenings. The workspace (``202609190055``)
holds what the ICAAP SAYS; this holds who agreed to it, what was filed with it,
and what the bank published — the half a supervisor actually asks about. So the
same three tiers apply, built from the guard functions that already exist and
never recreated here:

* SEALED — ``icaap_workflow_templates`` once approved and ``icaap_disclosures``
  once approved, through ``aequoros_governed_row_guard`` (``202608230038``).
  A bank's approved review chain and an approved disclosure selection are both
  four-eyed statements with a date on them; afterwards only the supersession /
  publication bookkeeping may move, each field write-once.
* UNALTERABLE — ``icaap_cycle_stages``, ``icaap_stage_decisions``,
  ``regulatory_package_attachments`` and
  ``regulatory_package_attachment_withdrawals``, through
  ``aequoros_append_only_guard`` (``202607250027``) with UPDATE blocked and
  DELETE left reachable, so deleting a draft cycle (or a package cascade) still
  works. An officer's decision and the document filed beside a return are
  evidence: they are corrected by a NEW row, never by an edit.
* mutable — none. Everything here is either evidence or governed.

Three constraint changes carry the family into the existing plane:

* ``ck_regulatory_packages_return_family`` admits ``icaap`` — the filing family
  (report, ¶74 update, ¶82 disclosure), distinct from ``icaap_stress``, which is
  the Appendix II annex filed inside it (D-011);
* ``ck_regulatory_package_artifacts_kind`` admits ``docx_working`` — the Word
  working copy of an ICAAP report, never signed and never filed, exactly as
  ``xlsx_working`` is for a BSD form;
* ``return_signing_policies.ordered_slots`` records whether the slots must be
  signed in order. Existing rows are ``false``, which is today's behaviour for
  every two-signer return.

``uq_icaap_cycles_open`` is also recreated with a wider predicate. P1 kept a
cycle in the index until it was superseded or archived, which would mean a ¶74
revision could not be started while the filed cycle was still, truthfully,
``submitted``. The alternative — re-labelling a filing so an index would admit
its successor — is not available: what was filed was filed. So submitted and
acknowledged cycles leave the index, and the filed cycle becomes ``superseded``
only when the revision's freeze supersedes its package.

DDL only: no data step, so the DML scanner in ``test_migration_rls_guard.py``
passes unedited and nothing here needs a BYPASSRLS role. The governed parameters
the filing plane reads are seeded separately by ``202609190059``.

Revision ID: 202609190058
Revises: 202609190057
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609190058"
down_revision = "202609190057"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

#: Creation order = FK order; the downgrade reverses it.
_TABLES: tuple[str, ...] = (
    "icaap_workflow_templates",
    "icaap_cycle_stages",
    "regulatory_package_attachments",
    "regulatory_package_attachment_withdrawals",
    "icaap_stage_decisions",
    "icaap_disclosures",
)
_UNALTERABLE: tuple[str, ...] = (
    "icaap_cycle_stages",
    "regulatory_package_attachments",
    "regulatory_package_attachment_withdrawals",
    "icaap_stage_decisions",
)
#: table -> aequoros_governed_row_guard TG_ARGV[0..4]:
#: (mutable columns, write-once columns, seal column, seal states, next states).
_SEALED: dict[str, tuple[str, str, str, str, str]] = {
    "icaap_workflow_templates": (
        "superseded_at,superseded_by_id,updated_at",
        "superseded_at,superseded_by_id",
        "status",
        "approved,superseded",
        "superseded",
    ),
    "icaap_disclosures": (
        "package_id,published_url,published_on,superseded_at,updated_at",
        "package_id,published_url,published_on,superseded_at",
        "status",
        "approved,published,superseded",
        "published,superseded",
    ),
}

_CYCLE_FK_COLUMNS = ["cycle_id", "organization_id", "bank_id"]
_CYCLE_FK_TARGETS = ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"]
_TEMPLATE_FK_TARGETS = ["icaap_workflow_templates.id", "icaap_workflow_templates.organization_id"]
_PACKAGE_FK_TARGETS = ["regulatory_packages.id", "regulatory_packages.organization_id"]
_BANK_FK_TARGETS = ["banks.id", "banks.organization_id"]

_TEMPLATE_STATUSES = "('draft', 'pending_approval', 'approved', 'rejected', 'superseded')"
_DECISION_KINDS = "('prepare', 'review', 'approve', 'attest')"
_STAGE_SOURCES = "('framework_default', 'bank_template')"
_STAGE_DECISIONS = (
    "('submitted', 'reviewed', 'approved', 'returned', 'frozen', 'attested', "
    "'attested_by_resolution')"
)
_FORWARD_DECISION = "decision IN ('submitted', 'reviewed', 'approved')"
_ATTACHMENT_SOURCES = "('package_upload', 'icaap_cycle')"
_ATTACHMENT_GATES = "('freeze', 'submission', 'optional')"
_DISCLOSURE_STATUSES = (
    "('draft', 'pending_approval', 'approved', 'published', 'rejected', 'superseded')"
)
_LIVE_DISCLOSURE = "status NOT IN ('rejected', 'superseded')"
_ACTIVE_TEMPLATE = "status = 'approved'"

# --- constraint widenings --------------------------------------------------
_FAMILY_TABLE = "regulatory_packages"
_FAMILY_CONSTRAINT = "ck_regulatory_packages_return_family"
_FAMILIES_BEFORE = (
    "'liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate', "
    "'large_exposures', 'dbk', 'stress', 'bsd', 'sdi', 'credit'"
)
_FAMILIES_AFTER = f"{_FAMILIES_BEFORE}, 'icaap'"

_KIND_TABLE = "regulatory_package_artifacts"
_KIND_CONSTRAINT = "ck_regulatory_package_artifacts_kind"
_KINDS_BEFORE = "'xlsx', 'csv', 'pdf', 'xlsx_working'"
_KINDS_AFTER = f"{_KINDS_BEFORE}, 'docx_working'"

_OPEN_CYCLE_BEFORE = "status NOT IN ('superseded', 'archived')"
_OPEN_CYCLE_AFTER = "status NOT IN ('submitted', 'acknowledged', 'superseded', 'archived')"
_OPEN_CYCLE_COLUMNS = ["organization_id", "bank_id", "cycle_kind", "as_of_date", "basis"]


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
    """UPDATE blocked; DELETE stays reachable so a cycle/package cascade works."""
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


def _create_workflow_templates() -> None:
    op.create_table(
        "icaap_workflow_templates",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("stages", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("framework_code", sa.String(60), nullable=True),
        sa.Column("framework_version", sa.String(40), nullable=True),
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
            f"status IN {_TEMPLATE_STATUSES}", name="ck_icaap_workflow_templates_status"
        ),
        sa.CheckConstraint("version >= 1", name="ck_icaap_workflow_templates_version"),
        sa.CheckConstraint(
            "decided_by IS NULL OR decided_by <> proposed_by",
            name="ck_icaap_workflow_templates_four_eyes",
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'superseded') OR decided_at IS NOT NULL",
            name="ck_icaap_workflow_templates_decided",
        ),
        sa.ForeignKeyConstraint(["bank_id", "organization_id"], _BANK_FK_TARGETS),
        sa.ForeignKeyConstraint(["superseded_by_id", "organization_id"], _TEMPLATE_FK_TARGETS),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_workflow_templates_id_org"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "version", name="uq_icaap_workflow_templates_version"
        ),
    )
    op.create_index(
        "uq_icaap_workflow_templates_active",
        "icaap_workflow_templates",
        ["organization_id", "bank_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_TEMPLATE),
        sqlite_where=sa.text(_ACTIVE_TEMPLATE),
    )


def _create_cycle_stages() -> None:
    op.create_table(
        "icaap_cycle_stages",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("decision_kind", sa.String(12), nullable=False),
        sa.Column("officer_titles", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "freeze_on_approve", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("template_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"decision_kind IN {_DECISION_KINDS}", name="ck_icaap_cycle_stages_decision_kind"
        ),
        sa.CheckConstraint("seq BETWEEN 1 AND 20", name="ck_icaap_cycle_stages_seq"),
        sa.CheckConstraint(f"source IN {_STAGE_SOURCES}", name="ck_icaap_cycle_stages_source"),
        sa.CheckConstraint(
            "(source = 'bank_template') = (template_id IS NOT NULL)",
            name="ck_icaap_cycle_stages_template",
        ),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id", "organization_id"], _TEMPLATE_FK_TARGETS),
        sa.UniqueConstraint("cycle_id", "seq", name="uq_icaap_cycle_stages_seq"),
        sa.UniqueConstraint("cycle_id", "stage_key", name="uq_icaap_cycle_stages_key"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_cycle_stages_id_org"),
    )
    op.create_index(
        "ix_icaap_cycle_stages_org_cycle", "icaap_cycle_stages", ["organization_id", "cycle_id"]
    )


def _create_package_attachments() -> None:
    op.create_table(
        "regulatory_package_attachments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("package_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("package_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_tier", sa.String(16), nullable=False),
        sa.Column("object_path", sa.String(512), nullable=False),
        sa.Column("storage_version_id", sa.String(255), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_attachment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("gate", sa.String(12), nullable=False),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("attached_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("byte_size > 0", name="ck_regulatory_package_attachments_size"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_regulatory_package_attachments_sha"),
        sa.CheckConstraint(
            "storage_tier = 'outputs'", name="ck_regulatory_package_attachments_tier"
        ),
        sa.CheckConstraint(
            f"source IN {_ATTACHMENT_SOURCES}", name="ck_regulatory_package_attachments_source"
        ),
        sa.CheckConstraint(
            f"gate IN {_ATTACHMENT_GATES}", name="ck_regulatory_package_attachments_gate"
        ),
        sa.CheckConstraint(
            "(source = 'icaap_cycle') = (source_attachment_id IS NOT NULL)",
            name="ck_regulatory_package_attachments_origin",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"], _PACKAGE_FK_TARGETS, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["bank_id", "organization_id"], _BANK_FK_TARGETS),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_package_attachments_id_org"
        ),
    )
    op.create_index(
        "ix_regulatory_package_attachments_org_package_kind",
        "regulatory_package_attachments",
        ["organization_id", "package_id", "kind"],
    )


def _create_attachment_withdrawals() -> None:
    op.create_table(
        "regulatory_package_attachment_withdrawals",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("package_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("attachment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("withdrawn_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["attachment_id", "organization_id"],
            [
                "regulatory_package_attachments.id",
                "regulatory_package_attachments.organization_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"], _PACKAGE_FK_TARGETS, ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "attachment_id", name="uq_regulatory_package_attachment_withdrawals_attachment"
        ),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_package_attachment_withdrawals_id_org"
        ),
    )


def _create_stage_decisions() -> None:
    op.create_table(
        "icaap_stage_decisions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("stage_seq", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(60), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("return_to_seq", sa.Integer(), nullable=True),
        sa.Column("review_digest", sa.String(64), nullable=False),
        sa.Column("package_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("signature_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("package_attachment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decided_by_name", sa.String(200), nullable=False),
        sa.Column("officer_title", sa.String(200), nullable=True),
        sa.Column("authority", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"decision IN {_STAGE_DECISIONS}", name="ck_icaap_stage_decisions_decision"
        ),
        sa.CheckConstraint("round >= 1", name="ck_icaap_stage_decisions_round"),
        sa.CheckConstraint("stage_seq BETWEEN 1 AND 20", name="ck_icaap_stage_decisions_stage_seq"),
        sa.CheckConstraint("length(review_digest) = 64", name="ck_icaap_stage_decisions_digest"),
        sa.CheckConstraint(
            "decision <> 'returned' OR (return_to_seq IS NOT NULL AND return_to_seq >= 1 "
            "AND return_to_seq < stage_seq AND comment IS NOT NULL)",
            name="ck_icaap_stage_decisions_return",
        ),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["package_id", "organization_id"], _PACKAGE_FK_TARGETS),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_stage_decisions_id_org"),
    )
    op.create_index(
        "uq_icaap_stage_decisions_forward",
        "icaap_stage_decisions",
        ["cycle_id", "round", "stage_seq"],
        unique=True,
        postgresql_where=sa.text(_FORWARD_DECISION),
        sqlite_where=sa.text(_FORWARD_DECISION),
    )
    op.create_index(
        "ix_icaap_stage_decisions_org_cycle",
        "icaap_stage_decisions",
        ["organization_id", "cycle_id"],
    )


def _create_disclosures() -> None:
    op.create_table(
        "icaap_disclosures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_package_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'draft'"), nullable=False),
        sa.Column(
            "selected_section_keys", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("withheld", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("proposed_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("package_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("published_url", sa.String(500), nullable=True),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN {_DISCLOSURE_STATUSES}", name="ck_icaap_disclosures_status"),
        sa.CheckConstraint(
            "decided_by IS NULL OR decided_by <> proposed_by",
            name="ck_icaap_disclosures_four_eyes",
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'published') OR package_id IS NOT NULL",
            name="ck_icaap_disclosures_has_package",
        ),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_package_id", "organization_id"], _PACKAGE_FK_TARGETS),
        sa.ForeignKeyConstraint(["package_id", "organization_id"], _PACKAGE_FK_TARGETS),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_disclosures_id_org"),
    )
    op.create_index(
        "uq_icaap_disclosures_live",
        "icaap_disclosures",
        ["cycle_id"],
        unique=True,
        postgresql_where=sa.text(_LIVE_DISCLOSURE),
        sqlite_where=sa.text(_LIVE_DISCLOSURE),
    )
    op.create_index(
        "ix_icaap_disclosures_org_cycle", "icaap_disclosures", ["organization_id", "cycle_id"]
    )


def _replace_family(families: str) -> None:
    with op.batch_alter_table(_FAMILY_TABLE) as batch:
        batch.drop_constraint(_FAMILY_CONSTRAINT, type_="check")
        batch.create_check_constraint(_FAMILY_CONSTRAINT, f"return_family IN ({families})")


def _replace_kind(kinds: str) -> None:
    with op.batch_alter_table(_KIND_TABLE) as batch:
        batch.drop_constraint(_KIND_CONSTRAINT, type_="check")
        batch.create_check_constraint(_KIND_CONSTRAINT, f"kind IN ({kinds})")


def _replace_open_cycle_index(predicate: str) -> None:
    op.drop_index("uq_icaap_cycles_open", table_name="icaap_cycles")
    op.create_index(
        "uq_icaap_cycles_open",
        "icaap_cycles",
        _OPEN_CYCLE_COLUMNS,
        unique=True,
        postgresql_where=sa.text(predicate),
        sqlite_where=sa.text(predicate),
    )


def upgrade() -> None:
    _replace_family(_FAMILIES_AFTER)
    _replace_kind(_KINDS_AFTER)
    op.add_column(
        "return_signing_policies",
        sa.Column("ordered_slots", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    _replace_open_cycle_index(_OPEN_CYCLE_AFTER)

    _create_workflow_templates()
    _create_cycle_stages()
    _create_package_attachments()
    _create_attachment_withdrawals()
    _create_stage_decisions()
    _create_disclosures()

    if op.get_bind().dialect.name != "postgresql":
        return
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
    # Children first: the cascade order is the reverse of creation.
    op.drop_table("icaap_disclosures")
    op.drop_table("icaap_stage_decisions")
    op.drop_table("regulatory_package_attachment_withdrawals")
    op.drop_table("regulatory_package_attachments")
    op.drop_table("icaap_cycle_stages")
    op.drop_table("icaap_workflow_templates")

    _replace_open_cycle_index(_OPEN_CYCLE_BEFORE)
    op.drop_column("return_signing_policies", "ordered_slots")
    # Deliberately no DELETE of icaap / docx_working rows: a downgrade that
    # erased filed packages or their artifacts would destroy evidence to make a
    # constraint fit. If such rows exist the narrowing FAILS, which is the
    # honest outcome (same rule as 202609010050).
    _replace_kind(_KINDS_BEFORE)
    _replace_family(_FAMILIES_BEFORE)
