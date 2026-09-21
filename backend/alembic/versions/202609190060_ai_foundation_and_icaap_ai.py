"""Governed AI drafting (M4): tenant consent, suggestions, decisions.

Three tables, in the three tiers the platform already uses, built from the guard
functions that exist and never recreated here:

* ``ai_commentary_settings`` — **mutable**. One row per organisation holding the
  tenant switch and the consent it rests on. Deliberately not governed: a
  kill-switch that the database could refuse to flip would not be a kill-switch.
  Its history is the append-only ``audit_events`` trail.
* ``icaap_ai_suggestions`` — **SEALED** through ``aequoros_governed_row_guard``
  (``202608230038``). Inserted ``queued``, one move to ``running``, then one
  update to a terminal status carrying every result column; frozen afterwards.
  The row is the evidence of what left the platform on a bank's behalf — which
  pseudonymised fact sheet, under which consent version and deployment approval,
  to which model, and what came back. Evidence that can be edited later is not
  evidence.
* ``icaap_ai_suggestion_decisions`` — **UNALTERABLE** through
  ``aequoros_append_only_guard`` (``202607250027``), UPDATE blocked and DELETE
  left reachable so a cycle cascade still works. A human's decision to insert or
  discard AI text is corrected by a new decision, never by an edit. (Full
  IMMUTABLE would make a cycle undeletable — the same reasoning as P1's section
  versions.)

The CHECK on the settings table is the substantive rule: a row cannot be
``enabled`` without a consent version, a consenting user and a timestamp, so
consent is not a UI step a direct SQL write could skip.

DDL only: no data step, so nothing here needs a BYPASSRLS role.

Revision ID: 202609190060
Revises: 202609190059
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609190060"
down_revision = "202609190059"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_SETTINGS = "ai_commentary_settings"
_SUGGESTIONS = "icaap_ai_suggestions"
_DECISIONS = "icaap_ai_suggestion_decisions"
#: Creation order = FK order; the downgrade reverses it.
_TABLES: tuple[str, ...] = (_SETTINGS, _SUGGESTIONS, _DECISIONS)

_STATUSES = (
    "'queued', 'running', 'validated', 'rejected_validation', 'refused', "
    "'failed', 'rate_limited', 'cancelled'"
)
_TERMINAL = "validated,rejected_validation,refused,failed,rate_limited,cancelled"
_NO_OUTPUT = "'refused', 'rate_limited', 'cancelled'"
_MODES = "'standard', 'descriptor_only'"
_DECISION_KINDS = "'accepted', 'rejected'"
_ACTIVE = "status IN ('queued', 'running')"

_CYCLE_FK_COLUMNS = ["cycle_id", "organization_id", "bank_id"]
_CYCLE_FK_TARGETS = ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"]

#: ``aequoros_governed_row_guard`` TG_ARGV[0..4]: (mutable columns, write-once
#: columns, seal column, seal states, states a sealed row may still move to).
#: No next states: a terminal AI status is final. The result columns are
#: write-once so the single finalising UPDATE can fill them and nothing can
#: rewrite them afterwards.
_SUGGESTION_GUARD = (
    "status,completed_at,model_served,fallback_used,request_id,stop_reason,"
    "refusal_category,failure_code,latency_ms,output,output_sha256,"
    "validation_errors,usage,job_id",
    "completed_at,model_served,request_id,stop_reason,refusal_category,"
    "output,output_sha256,usage",
    "status",
    _TERMINAL,
    "",
)


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
    """UPDATE blocked; DELETE stays reachable so a cycle cascade works."""
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


def _create_settings() -> None:
    op.create_table(
        _SETTINGS,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("enabled_features", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "descriptor_only", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("consent_version", sa.String(40), nullable=True),
        sa.Column("consented_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", name="uq_ai_commentary_settings_org"),
        sa.CheckConstraint(
            "NOT enabled OR (consent_version IS NOT NULL AND consented_by IS NOT NULL "
            "AND consented_at IS NOT NULL)",
            name="ck_ai_commentary_settings_consent",
        ),
    )


def _create_suggestions() -> None:
    op.create_table(
        _SUGGESTIONS,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("section_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.String(60), nullable=False),
        sa.Column("cycle_round", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("fact_sheet_mode", sa.String(16), nullable=False),
        sa.Column("fact_sheet", sa.JSON(), nullable=False),
        sa.Column("fact_sheet_sha256", sa.String(64), nullable=False),
        sa.Column("fact_bindings", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("entity_keys", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("framework_code", sa.String(40), nullable=False),
        sa.Column("framework_version", sa.String(40), nullable=False),
        sa.Column("framework_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("model_requested", sa.String(80), nullable=False),
        sa.Column("effort", sa.String(8), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("fallbacks_mode", sa.String(16), nullable=False),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("deployment_approval_ref", sa.String(120), nullable=True),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_served", sa.String(80), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("request_id", sa.String(120), nullable=True),
        sa.Column("stop_reason", sa.String(40), nullable=True),
        sa.Column("refusal_category", sa.String(40), nullable=True),
        sa.Column("failure_code", sa.String(40), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("output_sha256", sa.String(64), nullable=True),
        sa.Column("validation_errors", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["section_id", "organization_id"],
            ["icaap_sections.id", "icaap_sections.organization_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_ai_suggestions_id_org"),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name="ck_icaap_ai_suggestions_status"),
        sa.CheckConstraint(
            f"fact_sheet_mode IN ({_MODES})", name="ck_icaap_ai_suggestions_mode"
        ),
        sa.CheckConstraint(
            "length(fact_sheet_sha256) = 64", name="ck_icaap_ai_suggestions_sheet_sha"
        ),
        sa.CheckConstraint("cycle_round >= 1", name="ck_icaap_ai_suggestions_round"),
        sa.CheckConstraint(
            "status IN ('queued', 'running') OR completed_at IS NOT NULL",
            name="ck_icaap_ai_suggestions_completed",
        ),
        sa.CheckConstraint(
            "status <> 'validated' OR output IS NOT NULL",
            name="ck_icaap_ai_suggestions_validated_output",
        ),
        # A refusal produced no draft and a cancelled request was never sent;
        # neither may carry model output, whatever a caller passes.
        sa.CheckConstraint(
            f"NOT (status IN ({_NO_OUTPUT})) OR output IS NULL",
            name="ck_icaap_ai_suggestions_no_output",
        ),
    )
    op.create_index(
        "ix_icaap_ai_suggestions_section",
        _SUGGESTIONS,
        ["organization_id", "cycle_id", "section_key", "created_at"],
    )
    op.create_index(
        "ix_icaap_ai_suggestions_requester",
        _SUGGESTIONS,
        ["organization_id", "requested_by", "created_at"],
    )
    op.create_index(
        "ix_icaap_ai_suggestions_org_created", _SUGGESTIONS, ["organization_id", "created_at"]
    )
    # One in-flight request per user per section: the debounce's policy AND its
    # race guard, enforced where a concurrent request cannot slip past it.
    op.create_index(
        "uq_icaap_ai_suggestions_inflight",
        _SUGGESTIONS,
        ["section_id", "requested_by"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE),
        sqlite_where=sa.text(_ACTIVE),
    )


def _create_decisions() -> None:
    op.create_table(
        _DECISIONS,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("suggestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column(
            "paragraph_indexes", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("inserted_doc_sha256", sa.String(64), nullable=True),
        sa.Column("resulting_working_rev", sa.Integer(), nullable=True),
        sa.Column(
            "acknowledged_stale", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["suggestion_id", "organization_id"],
            [f"{_SUGGESTIONS}.id", f"{_SUGGESTIONS}.organization_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("suggestion_id", name="uq_icaap_ai_suggestion_decisions_one"),
        sa.CheckConstraint(
            f"decision IN ({_DECISION_KINDS})", name="ck_icaap_ai_decisions_decision"
        ),
    )
    op.create_index(
        "ix_icaap_ai_decisions_org_cycle", _DECISIONS, ["organization_id", "cycle_id"]
    )


def upgrade() -> None:
    _create_settings()
    _create_suggestions()
    _create_decisions()

    _install_sealed(_SUGGESTIONS, _SUGGESTION_GUARD)
    _install_unalterable(_DECISIONS)
    for table in _TABLES:
        _enable_rls(table)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_DECISIONS}_append_only ON {_DECISIONS}")
    op.execute(f"DROP POLICY IF EXISTS {_DECISIONS}_no_update ON {_DECISIONS}")
    op.execute(f"DROP TRIGGER IF EXISTS {_SUGGESTIONS}_governed_row ON {_SUGGESTIONS}")
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_icaap_ai_decisions_org_cycle", table_name=_DECISIONS)
    op.drop_table(_DECISIONS)
    op.drop_index("uq_icaap_ai_suggestions_inflight", table_name=_SUGGESTIONS)
    op.drop_index("ix_icaap_ai_suggestions_org_created", table_name=_SUGGESTIONS)
    op.drop_index("ix_icaap_ai_suggestions_requester", table_name=_SUGGESTIONS)
    op.drop_index("ix_icaap_ai_suggestions_section", table_name=_SUGGESTIONS)
    op.drop_table(_SUGGESTIONS)
    op.drop_table(_SETTINGS)
