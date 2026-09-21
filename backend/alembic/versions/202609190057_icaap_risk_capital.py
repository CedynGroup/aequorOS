"""ICAAP risk & capital (M2): register, appetite, Pillar 2, reconciliation, review.

Twelve tenant tables, every one ENABLE+FORCE RLS on ``organization_id``, in the
same three immutability tiers ``202609190055`` established — built from the
guard functions that already exist, never recreated here:

* SEALED — ``bank_supervisory_addons`` once confirmed and ``icaap_audit_reviews``
  once finalised, through ``aequoros_governed_row_guard`` (``202608230038``).
  A supervisory add-on is the REGULATOR's number; a finalised independent review
  is a reviewer's opinion on a date. Neither is corrected by an edit — the
  guard admits only the supersession/withdrawal bookkeeping afterwards.
* UNALTERABLE — Pillar 2 item revisions, challenges and challenge responses,
  through ``aequoros_append_only_guard`` (``202607250027``) with UPDATE blocked
  and DELETE left reachable, so deleting a draft cycle still cascades.
* mutable — the register, appetite metrics, the Pillar 2 head rows, allocations,
  reconciliation lines and control explanations, which the service locks by
  cycle status.

Two database-level rules are worth naming because they are the ones a service
bug would otherwise walk past:

* ``ck_bank_supervisory_addons_four_eyes`` refuses ``confirmed_by = created_by``,
  so the maker-checker on a regulator-imposed add-on holds even against a direct
  SQL write;
* ``ck_icaap_resources_reconciliation_lines_explained`` refuses a resources line
  whose internal amount differs from the regulatory one, or which counts an
  ineligible component, without an explanation (REG-ICAAP-027) — the whole point
  of that statement.

DDL only: no data step, so the DML scanner in ``test_migration_rls_guard.py``
passes unedited, and nothing here needs a BYPASSRLS role.

Revision ID: 202609190057
Revises: 202609190056
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609190057"
down_revision = "202609190056"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

#: Creation order = FK order; the downgrade reverses it.
_TABLES: tuple[str, ...] = (
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
_UNALTERABLE: tuple[str, ...] = (
    "icaap_pillar2_item_revisions",
    "icaap_challenges",
    "icaap_challenge_responses",
)
#: table -> aequoros_governed_row_guard TG_ARGV[0..4]:
#: (mutable columns, write-once columns, seal column, seal states, next states).
_SEALED: dict[str, tuple[str, str, str, str, str]] = {
    "bank_supervisory_addons": (
        "effective_to,superseded_at,superseded_by_addon_id,withdrawn_at,withdrawn_by,"
        "withdrawal_reason,updated_at",
        "effective_to,superseded_at,superseded_by_addon_id,withdrawn_at,withdrawn_by,"
        "withdrawal_reason",
        "status",
        "active,superseded,withdrawn",
        "superseded,withdrawn",
    ),
    "icaap_audit_reviews": (
        "superseded_at,superseded_by_review_id,updated_at",
        "superseded_at,superseded_by_review_id",
        "status",
        "finalised,superseded",
        "superseded",
    ),
}


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
    """UPDATE blocked; DELETE stays reachable so a cycle cascade still works."""
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


def _create_tables() -> None:
    op.create_table(
        "icaap_risk_assessments",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("risk_key", sa.String(length=80), nullable=False),
        sa.Column("category_key", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("is_custom", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("likelihood_score", sa.Integer(), nullable=True),
        sa.Column("impact_score", sa.Integer(), nullable=True),
        sa.Column("controls_summary", sa.Text(), nullable=True),
        sa.Column("materiality_score", sa.Integer(), nullable=True),
        sa.Column("rating_key", sa.String(length=24), nullable=True),
        sa.Column(
            "matrix_verdict",
            sa.String(length=16),
            server_default=sa.text("'unassessed'"),
            nullable=False,
        ),
        sa.Column(
            "verdict", sa.String(length=16), server_default=sa.text("'unassessed'"), nullable=False
        ),
        sa.Column(
            "verdict_source",
            sa.String(length=8),
            server_default=sa.text("'matrix'"),
            nullable=False,
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("materiality_rationale", sa.Text(), nullable=True),
        sa.Column("thresholds_digest", sa.String(length=64), nullable=True),
        sa.Column("pillar1_coverage", sa.String(length=8), nullable=False),
        sa.Column("p29_class", sa.String(length=32), nullable=False),
        sa.Column(
            "pillar2_treatment",
            sa.String(length=24),
            server_default=sa.text("'undecided'"),
            nullable=False,
        ),
        sa.Column("pillar1_coverage_rationale", sa.Text(), nullable=True),
        sa.Column("owner_function", sa.String(length=120), nullable=True),
        sa.Column("row_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", sa.Uuid(), nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "matrix_verdict IN ('unassessed', 'material', 'not_material')",
            name="ck_icaap_risk_assessments_matrix_verdict",
        ),
        sa.CheckConstraint(
            "pillar1_coverage IN ('full', 'partial', 'none')",
            name="ck_icaap_risk_assessments_pillar1_coverage",
        ),
        sa.CheckConstraint(
            "pillar2_treatment <> 'fully_covered_by_pillar1' OR pillar1_coverage_rationale IS NOT "
            "NULL",
            name="ck_icaap_risk_assessments_coverage_rationale",
        ),
        sa.CheckConstraint(
            "pillar2_treatment IN ('undecided', 'quantified', 'fully_covered_by_pillar1', "
            "'not_capitalised', 'not_material')",
            name="ck_icaap_risk_assessments_treatment",
        ),
        sa.CheckConstraint(
            "verdict IN ('unassessed', 'material', 'not_material')",
            name="ck_icaap_risk_assessments_verdict",
        ),
        sa.CheckConstraint(
            "verdict_source <> 'override' OR override_reason IS NOT NULL",
            name="ck_icaap_risk_assessments_override_reason",
        ),
        sa.CheckConstraint(
            "verdict_source IN ('matrix', 'override')",
            name="ck_icaap_risk_assessments_verdict_source",
        ),
        sa.CheckConstraint(
            "impact_score IS NULL OR impact_score >= 1", name="ck_icaap_risk_assessments_impact"
        ),
        sa.CheckConstraint(
            "likelihood_score IS NULL OR likelihood_score >= 1",
            name="ck_icaap_risk_assessments_likelihood",
        ),
        sa.CheckConstraint(
            "retired_at IS NULL OR is_custom", name="ck_icaap_risk_assessments_retire_custom"
        ),
        sa.CheckConstraint("row_rev >= 0", name="ck_icaap_risk_assessments_row_rev"),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "risk_key", name="uq_icaap_risk_assessments_cycle_key"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_risk_assessments_id_org"),
    )
    op.create_index(
        "ix_icaap_risk_assessments_org_cycle",
        "icaap_risk_assessments",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_table(
        "icaap_appetite_metrics",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("metric_key", sa.String(length=60), nullable=False),
        sa.Column("risk_key", sa.String(length=80), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("is_custom", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("measure_kind", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("appetite_value", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("tolerance_value", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("capacity_value", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("regulatory_param_code", sa.String(length=64), nullable=True),
        sa.Column("board_register_code", sa.String(length=64), nullable=True),
        sa.Column("value_source", sa.String(length=16), nullable=True),
        sa.Column("source_block_type", sa.String(length=40), nullable=True),
        sa.Column("source_fact_key", sa.String(length=64), nullable=True),
        sa.Column("manual_value", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("manual_evidence_attachment_id", sa.Uuid(), nullable=True),
        sa.Column("prior_value", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("prior_value_label", sa.String(length=80), nullable=True),
        sa.Column("qualitative_statement", sa.Text(), nullable=False),
        sa.Column("board_approval_reference", sa.String(length=200), nullable=True),
        sa.Column("board_approved_on", sa.Date(), nullable=True),
        sa.Column("row_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", sa.Uuid(), nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(measure_kind = 'qualitative' AND direction IS NULL AND appetite_value IS NULL AND "
            "tolerance_value IS NULL AND capacity_value IS NULL AND value_source IS NULL) OR "
            "(measure_kind = 'quantitative' AND direction IS NOT NULL AND unit IS NOT NULL AND "
            "appetite_value IS NOT NULL AND tolerance_value IS NOT NULL AND capacity_value IS NOT "
            "NULL AND value_source IS NOT NULL)",
            name="ck_icaap_appetite_metrics_measure",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('floor', 'ceiling')",
            name="ck_icaap_appetite_metrics_direction",
        ),
        sa.CheckConstraint(
            "measure_kind = 'qualitative' OR (direction = 'floor' AND appetite_value >= "
            "tolerance_value AND tolerance_value >= capacity_value) OR (direction = 'ceiling' AND "
            "appetite_value <= tolerance_value AND tolerance_value <= capacity_value)",
            name="ck_icaap_appetite_metrics_ordering",
        ),
        sa.CheckConstraint(
            "measure_kind IN ('quantitative', 'qualitative')",
            name="ck_icaap_appetite_metrics_measure_kind",
        ),
        sa.CheckConstraint(
            "unit IS NULL OR unit IN ('percent', 'ratio', 'amount', 'count', 'multiplier', "
            "'years')",
            name="ck_icaap_appetite_metrics_unit",
        ),
        sa.CheckConstraint(
            "value_source IS NULL OR (value_source = 'block_fact' AND source_block_type IS NOT "
            "NULL AND source_fact_key IS NOT NULL) OR (value_source = 'manual' AND manual_value IS "
            "NOT NULL)",
            name="ck_icaap_appetite_metrics_source",
        ),
        sa.CheckConstraint(
            "value_source IS NULL OR value_source IN ('block_fact', 'manual')",
            name="ck_icaap_appetite_metrics_value_source_vocab",
        ),
        sa.CheckConstraint("row_rev >= 0", name="ck_icaap_appetite_metrics_row_rev"),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["manual_evidence_attachment_id", "organization_id"],
            ["icaap_attachments.id", "icaap_attachments.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_appetite_metrics_id_org"),
    )
    op.create_index(
        "ix_icaap_appetite_metrics_org_cycle",
        "icaap_appetite_metrics",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_index(
        "uq_icaap_appetite_metrics_active",
        "icaap_appetite_metrics",
        ["cycle_id", "metric_key"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
        sqlite_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "icaap_pillar2_items",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.String(length=80), nullable=False),
        sa.Column("risk_key", sa.String(length=80), nullable=False),
        sa.Column("category_key", sa.String(length=60), nullable=False),
        sa.Column("component_key", sa.String(length=60), nullable=False),
        sa.Column("table5_row", sa.String(length=40), nullable=True),
        sa.Column("method", sa.String(length=40), nullable=False),
        sa.Column("method_version", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "input_mode",
            sa.String(length=24),
            server_default=sa.text("'bound_blocks'"),
            nullable=False,
        ),
        sa.Column(
            "method_status",
            sa.String(length=20),
            server_default=sa.text("'not_computed'"),
            nullable=False,
        ),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("basis", sa.String(length=32), nullable=True),
        sa.Column("basis_value", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("baseline_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("stressed_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("baseline_derivation", sa.String(length=32), nullable=True),
        sa.Column("stressed_derivation", sa.String(length=32), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("inputs_digest", sa.String(length=64), nullable=True),
        sa.Column("scenario_definition", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("zero_amount_justification", sa.Text(), nullable=True),
        sa.Column(
            "evidence_attachment_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("evidence_reference", sa.String(length=300), nullable=True),
        sa.Column("current_revision_no", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("approved_revision_no", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", sa.Uuid(), nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "baseline_derivation IS NULL OR baseline_derivation IN ('method', 'adopted', 'manual', "
            "'same_as_baseline', 'same_as_stressed', 'max_of_baseline_and_scenario', "
            "'not_assessed', 'not_applicable')",
            name="ck_icaap_pillar2_items_baseline_derivation",
        ),
        sa.CheckConstraint(
            "basis IS NULL OR basis IN ('pct_total_rwa', 'pct_credit_rwa', "
            "'pct_pillar1_credit_capital', 'absolute')",
            name="ck_icaap_pillar2_items_basis",
        ),
        sa.CheckConstraint(
            "component_key <> 'diversification_benefit' OR ((baseline_amount IS NULL OR "
            "baseline_amount <= 0) AND source = 'judgemental')",
            name="ck_icaap_pillar2_items_diversification_negative",
        ),
        sa.CheckConstraint(
            "component_key = 'diversification_benefit' OR ((baseline_amount IS NULL OR "
            "baseline_amount >= 0) AND (stressed_amount IS NULL OR stressed_amount >= 0))",
            name="ck_icaap_pillar2_items_non_negative",
        ),
        sa.CheckConstraint(
            "input_mode IN ('bound_blocks', 'manual_with_evidence')",
            name="ck_icaap_pillar2_items_input_mode",
        ),
        sa.CheckConstraint(
            "method NOT IN ('fx_nop_addon', 'operational_scenario_net_p1', "
            "'sovereign_stress_addon', 'irrbb_interim_delta_eve') OR method_status IN "
            "('not_computed', 'not_computable') OR scenario_definition IS NOT NULL",
            name="ck_icaap_pillar2_items_scenario_required",
        ),
        sa.CheckConstraint(
            "method_status <> 'not_capitalised' OR (baseline_amount IS NULL AND stressed_amount IS "
            "NULL)",
            name="ck_icaap_pillar2_items_not_capitalised",
        ),
        sa.CheckConstraint(
            "method_status IN ('not_computed', 'computed', 'interim_non_sf', 'incomplete', "
            "'not_computable', 'not_capitalised')",
            name="ck_icaap_pillar2_items_method_status",
        ),
        sa.CheckConstraint(
            "method_status NOT IN ('computed', 'interim_non_sf') OR (baseline_amount IS NOT NULL "
            "AND basis IS NOT NULL)",
            name="ck_icaap_pillar2_items_computed_has_amount",
        ),
        sa.CheckConstraint(
            "source IN ('icaap_method', 'capital_plan', 'stress_overlay', 'supervisory', "
            "'judgemental')",
            name="ck_icaap_pillar2_items_source",
        ),
        sa.CheckConstraint(
            "stressed_derivation IS NULL OR stressed_derivation IN ('method', 'adopted', 'manual', "
            "'same_as_baseline', 'same_as_stressed', 'max_of_baseline_and_scenario', "
            "'not_assessed', 'not_applicable')",
            name="ck_icaap_pillar2_items_stressed_derivation",
        ),
        sa.CheckConstraint(
            "table5_row IS NOT NULL OR method = 'not_capitalised' OR component_key = "
            "'diversification_benefit'",
            name="ck_icaap_pillar2_items_table5",
        ),
        sa.CheckConstraint(
            "(approved_by IS NULL) = (approved_revision_no IS NULL) AND (approved_revision_no IS "
            "NULL OR approved_revision_no <= current_revision_no)",
            name="ck_icaap_pillar2_items_approval",
        ),
        sa.CheckConstraint("current_revision_no >= 0", name="ck_icaap_pillar2_items_revision_no"),
        sa.CheckConstraint(
            "inputs_digest IS NULL OR length(inputs_digest) = 64",
            name="ck_icaap_pillar2_items_inputs_digest",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_pillar2_items_id_org"),
    )
    op.create_index(
        "ix_icaap_pillar2_items_org_cycle",
        "icaap_pillar2_items",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_index(
        "uq_icaap_pillar2_items_active",
        "icaap_pillar2_items",
        ["cycle_id", "item_key"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
        sqlite_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "icaap_pillar2_item_revisions",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("change_kind", sa.String(length=16), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("computation", sa.JSON(), nullable=True),
        sa.Column("inputs_digest", sa.String(length=64), nullable=True),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "change_kind IN ('created', 'edited', 'computed', 'adopted', 'retired')",
            name="ck_icaap_pillar2_item_revisions_change_kind",
        ),
        sa.CheckConstraint(
            "inputs_digest IS NULL OR length(inputs_digest) = 64",
            name="ck_icaap_pillar2_item_revisions_inputs_digest",
        ),
        sa.CheckConstraint(
            "length(snapshot_sha256) = 64", name="ck_icaap_pillar2_item_revisions_sha"
        ),
        sa.CheckConstraint("revision_no >= 1", name="ck_icaap_pillar2_item_revisions_no"),
        sa.CheckConstraint("round >= 1", name="ck_icaap_pillar2_item_revisions_round"),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "organization_id"],
            ["icaap_pillar2_items.id", "icaap_pillar2_items.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_pillar2_item_revisions_id_org"),
        sa.UniqueConstraint("item_id", "revision_no", name="uq_icaap_pillar2_item_revisions_no"),
    )
    op.create_index(
        "ix_icaap_pillar2_item_revisions_org_cycle",
        "icaap_pillar2_item_revisions",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_table(
        "bank_supervisory_addons",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column(
            "status", sa.String(length=12), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("letter_reference", sa.String(length=120), nullable=False),
        sa.Column("letter_date", sa.Date(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("applies_to_basis", sa.String(length=12), nullable=False),
        sa.Column("table5_row", sa.String(length=40), nullable=True),
        sa.Column("component_key", sa.String(length=60), nullable=True),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("basis_value", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("letter_original_filename", sa.String(length=255), nullable=False),
        sa.Column("letter_media_type", sa.String(length=120), nullable=False),
        sa.Column("letter_byte_size", sa.BigInteger(), nullable=False),
        sa.Column("letter_sha256", sa.String(length=64), nullable=False),
        sa.Column("letter_storage_tier", sa.String(length=16), nullable=False),
        sa.Column("letter_object_path", sa.String(length=512), nullable=False),
        sa.Column("letter_storage_version_id", sa.String(length=255), nullable=True),
        sa.Column("supersedes_addon_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_addon_id", sa.Uuid(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by", sa.Uuid(), nullable=True),
        sa.Column("withdrawal_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "applies_to_basis IN ('solo', 'consolidated', 'both')",
            name="ck_bank_supervisory_addons_applies_to",
        ),
        sa.CheckConstraint(
            "basis IN ('pct_total_rwa', 'pct_credit_rwa', 'pct_pillar1_credit_capital', "
            "'absolute')",
            name="ck_bank_supervisory_addons_basis",
        ),
        sa.CheckConstraint(
            "letter_storage_tier = 'outputs'", name="ck_bank_supervisory_addons_letter_tier"
        ),
        sa.CheckConstraint(
            "status <> 'superseded' OR superseded_at IS NOT NULL",
            name="ck_bank_supervisory_addons_superseded",
        ),
        sa.CheckConstraint(
            "status <> 'withdrawn' OR (withdrawn_at IS NOT NULL AND withdrawal_reason IS NOT NULL)",
            name="ck_bank_supervisory_addons_withdrawn",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'superseded', 'withdrawn')",
            name="ck_bank_supervisory_addons_status",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'withdrawn') OR confirmed_by IS NOT NULL",
            name="ck_bank_supervisory_addons_active_confirmed",
        ),
        sa.CheckConstraint("basis_value >= 0", name="ck_bank_supervisory_addons_basis_value"),
        sa.CheckConstraint(
            "confirmed_by IS NULL OR confirmed_by <> created_by",
            name="ck_bank_supervisory_addons_four_eyes",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_bank_supervisory_addons_effective",
        ),
        sa.CheckConstraint(
            "length(letter_sha256) = 64", name="ck_bank_supervisory_addons_letter_sha"
        ),
        sa.CheckConstraint("letter_byte_size > 0", name="ck_bank_supervisory_addons_letter_size"),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_addon_id", "organization_id", "bank_id"],
            [
                "bank_supervisory_addons.id",
                "bank_supervisory_addons.organization_id",
                "bank_supervisory_addons.bank_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", "bank_id", name="uq_bank_supervisory_addons_id_org_bank"
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_bank_supervisory_addons_id_org"),
    )
    op.create_index(
        "ix_bank_supervisory_addons_org_bank_status",
        "bank_supervisory_addons",
        ["organization_id", "bank_id", "status"],
        unique=False,
    )
    op.create_table(
        "icaap_capital_allocations",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("unit_key", sa.String(length=60), nullable=False),
        sa.Column("unit_label", sa.String(length=200), nullable=False),
        sa.Column("unit_kind", sa.String(length=16), nullable=False),
        sa.Column("risk_line_key", sa.String(length=80), nullable=False),
        sa.Column("driver_kind", sa.String(length=16), nullable=False),
        sa.Column("driver_value", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("allocation_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "driver_kind IN ('rwa_share', 'exposure_share', 'manual_pct')",
            name="ck_icaap_capital_allocations_driver_kind",
        ),
        sa.CheckConstraint(
            "unit_kind IN ('business_line', 'legal_entity', 'risk_type')",
            name="ck_icaap_capital_allocations_unit_kind",
        ),
        sa.CheckConstraint("driver_value >= 0", name="ck_icaap_capital_allocations_driver_value"),
        sa.CheckConstraint(
            "length(allocation_digest) = 64", name="ck_icaap_capital_allocations_digest"
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id", "unit_key", "risk_line_key", name="uq_icaap_capital_allocations_unit_line"
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_capital_allocations_id_org"),
    )
    op.create_index(
        "ix_icaap_capital_allocations_org_cycle",
        "icaap_capital_allocations",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_table(
        "icaap_requirement_reconciliation_lines",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("line_key", sa.String(length=80), nullable=False),
        sa.Column("line_group", sa.String(length=24), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("table5_row", sa.String(length=40), nullable=True),
        sa.Column("pillar1_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("pillar2_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("internal_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("regulatory_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("supervisory_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("difference", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("explanation_required", sa.Boolean(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("explanation_by", sa.Uuid(), nullable=True),
        sa.Column("explanation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("explanation_values_digest", sa.String(length=64), nullable=True),
        sa.Column("computed_digest", sa.String(length=64), nullable=False),
        sa.Column("computation", sa.JSON(), nullable=False),
        sa.Column("computed_by", sa.Uuid(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "line_group IN ('pillar1', 'pillar2', 'buffer', 'supervisory_unattributed', "
            "'diversification', 'total')",
            name="ck_icaap_requirement_reconciliation_lines_group",
        ),
        sa.CheckConstraint(
            "length(computed_digest) = 64", name="ck_icaap_requirement_reconciliation_lines_digest"
        ),
        sa.CheckConstraint(
            "position >= 1", name="ck_icaap_requirement_reconciliation_lines_position"
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id", "line_key", name="uq_icaap_requirement_reconciliation_lines_key"
        ),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_icaap_requirement_reconciliation_lines_id_org"
        ),
    )
    op.create_index(
        "ix_icaap_requirement_reconciliation_lines_org_cycle",
        "icaap_requirement_reconciliation_lines",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_table(
        "icaap_resources_reconciliation_lines",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("line_key", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=24), nullable=False),
        sa.Column("regulatory_component_key", sa.String(length=80), nullable=True),
        sa.Column("regulatory_amount", sa.Numeric(precision=28, scale=4), nullable=True),
        sa.Column("internal_amount", sa.Numeric(precision=28, scale=4), nullable=False),
        sa.Column("regulatory_eligible", sa.Boolean(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("evidence_attachment_id", sa.Uuid(), nullable=True),
        sa.Column("source_binding_ref", sa.JSON(), nullable=True),
        sa.Column("row_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "origin IN ('regulatory_component', 'manual')",
            name="ck_icaap_resources_reconciliation_lines_origin",
        ),
        sa.CheckConstraint(
            "tier IN ('cet1', 'at1', 'tier2', 'deduction', 'other')",
            name="ck_icaap_resources_reconciliation_lines_tier",
        ),
        sa.CheckConstraint(
            "(regulatory_eligible AND regulatory_amount IS NOT NULL AND internal_amount = "
            "regulatory_amount) OR explanation IS NOT NULL",
            name="ck_icaap_resources_reconciliation_lines_explained",
        ),
        sa.CheckConstraint(
            "position >= 1", name="ck_icaap_resources_reconciliation_lines_position"
        ),
        sa.CheckConstraint("row_rev >= 0", name="ck_icaap_resources_reconciliation_lines_row_rev"),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_attachment_id", "organization_id"],
            ["icaap_attachments.id", "icaap_attachments.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id", "line_key", name="uq_icaap_resources_reconciliation_lines_key"
        ),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_icaap_resources_reconciliation_lines_id_org"
        ),
    )
    op.create_index(
        "ix_icaap_resources_reconciliation_lines_org_cycle",
        "icaap_resources_reconciliation_lines",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_table(
        "icaap_control_explanations",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("control_code", sa.String(length=40), nullable=False),
        sa.Column("comparison_key", sa.String(length=120), nullable=False),
        sa.Column("values_digest", sa.String(length=64), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("explained_by", sa.Uuid(), nullable=False),
        sa.Column("explained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "control_code IN ('pillar2_source_consistency')",
            name="ck_icaap_control_explanations_code",
        ),
        sa.CheckConstraint(
            "length(values_digest) = 64", name="ck_icaap_control_explanations_digest"
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id",
            "control_code",
            "comparison_key",
            name="uq_icaap_control_explanations_comparison",
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_control_explanations_id_org"),
    )
    op.create_index(
        "ix_icaap_control_explanations_org_cycle",
        "icaap_control_explanations",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_table(
        "icaap_audit_reviews",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=12), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("review_kind", sa.String(length=24), nullable=False),
        sa.Column("reviewer_function", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("frequency_statement", sa.Text(), nullable=False),
        sa.Column("performed_from", sa.Date(), nullable=True),
        sa.Column("performed_on", sa.Date(), nullable=False),
        sa.Column("period_covered", sa.Text(), nullable=True),
        sa.Column("reviewed_cycle_id", sa.Uuid(), nullable=True),
        sa.Column("overall_opinion", sa.String(length=32), nullable=False),
        sa.Column("findings", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("report_attachment_id", sa.Uuid(), nullable=True),
        sa.Column("independence_statement", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("finalised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalised_by", sa.Uuid(), nullable=True),
        sa.Column("supersedes_review_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_review_id", sa.Uuid(), nullable=True),
        sa.Column("row_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "overall_opinion IN ('satisfactory', 'satisfactory_with_findings', "
            "'needs_improvement', 'unsatisfactory')",
            name="ck_icaap_audit_reviews_opinion",
        ),
        sa.CheckConstraint(
            "review_kind IN ('internal_audit', 'external_audit', 'independent_validation', "
            "'other_independent')",
            name="ck_icaap_audit_reviews_kind",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR finalised_at IS NOT NULL", name="ck_icaap_audit_reviews_finalised"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'finalised', 'superseded')", name="ck_icaap_audit_reviews_status"
        ),
        sa.CheckConstraint(
            "performed_from IS NULL OR performed_from <= performed_on",
            name="ck_icaap_audit_reviews_performed",
        ),
        sa.CheckConstraint("row_rev >= 0", name="ck_icaap_audit_reviews_row_rev"),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_attachment_id", "organization_id"],
            ["icaap_attachments.id", "icaap_attachments.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_review_id", "organization_id"],
            ["icaap_audit_reviews.id", "icaap_audit_reviews.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_audit_reviews_id_org"),
    )
    op.create_index(
        "ix_icaap_audit_reviews_org_cycle",
        "icaap_audit_reviews",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_table(
        "icaap_challenges",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_no", sa.Integer(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("raised_in", sa.String(length=32), nullable=False),
        sa.Column("raised_by_name", sa.String(length=200), nullable=False),
        sa.Column("raised_on", sa.Date(), nullable=False),
        sa.Column("meeting_reference", sa.String(length=200), nullable=True),
        sa.Column("target_kind", sa.String(length=24), nullable=False),
        sa.Column("target_ref", sa.String(length=120), nullable=True),
        sa.Column("challenge_text", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("minutes_attachment_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "raised_in IN ('board', 'board_risk_committee', 'board_audit_committee', "
            "'senior_management', 'chief_risk_officer', 'internal_audit', 'other')",
            name="ck_icaap_challenges_forum",
        ),
        sa.CheckConstraint(
            "severity IN ('high', 'medium', 'low')", name="ck_icaap_challenges_severity"
        ),
        sa.CheckConstraint(
            "target_kind IN ('cycle', 'section', 'risk', 'appetite_metric', 'pillar2_item', "
            "'reconciliation', 'stress')",
            name="ck_icaap_challenges_target_kind",
        ),
        sa.CheckConstraint("challenge_no >= 1", name="ck_icaap_challenges_no"),
        sa.CheckConstraint("round >= 1", name="ck_icaap_challenges_round"),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["minutes_attachment_id", "organization_id"],
            ["icaap_attachments.id", "icaap_attachments.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "challenge_no", name="uq_icaap_challenges_no"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_challenges_id_org"),
    )
    op.create_index(
        "ix_icaap_challenges_org_cycle",
        "icaap_challenges",
        ["organization_id", "cycle_id"],
        unique=False,
    )
    op.create_table(
        "icaap_challenge_responses",
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("response_no", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("change_references", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("responder_function", sa.String(length=120), nullable=False),
        sa.Column("responded_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('accepted_changed', 'accepted_no_change', 'rejected_with_rationale', "
            "'deferred')",
            name="ck_icaap_challenge_responses_outcome",
        ),
        sa.CheckConstraint("response_no >= 1", name="ck_icaap_challenge_responses_no"),
        sa.ForeignKeyConstraint(
            ["challenge_id", "organization_id"],
            ["icaap_challenges.id", "icaap_challenges.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id", "organization_id", "bank_id"],
            ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge_id", "response_no", name="uq_icaap_challenge_responses_no"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_challenge_responses_id_org"),
    )
    op.create_index(
        "ix_icaap_challenge_responses_org_cycle",
        "icaap_challenge_responses",
        ["organization_id", "cycle_id"],
        unique=False,
    )


def upgrade() -> None:
    _create_tables()
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
    for table in reversed(_TABLES):
        op.drop_table(table)
