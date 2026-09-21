"""ICAAP workspace (M1) and the governed parameters the workspace reads.

Seven tenant tables, every one ENABLE+FORCE RLS on ``organization_id``, in three
immutability tiers built from the guard functions that already exist — never
recreated here, only called:

* SEALED — ``icaap_cycles`` through ``aequoros_governed_row_guard``
  (``202608230038``). Once the cycle is frozen only its lifecycle status and the
  write-once timestamps may change, and ``package_id`` can never be rewritten.
  A send-back is therefore TWO statements: ``frozen -> returned`` alone, then
  the round/stage bookkeeping, because by then the row is unsealed. P3's
  ``return_cycle`` must be written that way; a single UPDATE raises.
* UNALTERABLE — section versions, block bindings, attachments and attachment
  withdrawals through ``aequoros_append_only_guard`` (``202607250027``) with
  UPDATE blocked and DELETE left reachable, so deleting a draft cycle still
  cascades.
* mutable — sections and data blocks, which the service locks by cycle status.

The data step seeds the governed parameters the ICAAP workspace resolves at
runtime: the filing deadline and disclosure periods, the readiness amber window,
the materiality thresholds and bands, and the planning/stress horizons the
framework's checklist quotes. Founder directive D-024 — "Don't hardcode any
number but fetch from console" — means none of these may be a literal in code or
in the framework JSON, so the JSON references the CODES and the values live here
and in the operator console from now on. As in ``202609190054`` the values are
PINNED in this file rather than imported from the live catalogue, so a later
catalogue edit cannot change what this revision seeded.

``regulatory_parameter`` is a global, non-RLS reference table, so the data step
needs no BYPASSRLS role.

Revision ID: 202609190055
Revises: 202609190054
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202609190055"
down_revision = "202609190054"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

_CYCLES = "icaap_cycles"
_TABLES: tuple[str, ...] = (
    "icaap_cycles",
    "icaap_sections",
    "icaap_section_versions",
    "icaap_data_blocks",
    "icaap_attachments",
    "icaap_block_bindings",
    "icaap_attachment_withdrawals",
)
_UNALTERABLE: tuple[str, ...] = (
    "icaap_section_versions",
    "icaap_block_bindings",
    "icaap_attachments",
    "icaap_attachment_withdrawals",
)

_CYCLE_FK_COLUMNS = ["cycle_id", "organization_id", "bank_id"]
_CYCLE_FK_TARGETS = ["icaap_cycles.id", "icaap_cycles.organization_id", "icaap_cycles.bank_id"]

_KINDS = "('annual', 'material_change', 'regulator_request', 'rehearsal')"
_STATUSES = (
    "('draft', 'in_review', 'frozen', 'board_approved', 'submitted', "
    "'acknowledged', 'returned', 'superseded', 'archived')"
)
_BASES = "('solo', 'consolidated')"
_DUE_BASES = "('framework', 'bank_set', 'regulator_set', 'timely')"
_SOURCE_KINDS = (
    "('run', 'package', 'signoff', 'plan', 'register', 'snapshot', 'computed', 'manual')"
)
_OPEN_CYCLE = "status NOT IN ('superseded', 'archived')"
_EMPTY_DOC = '\'{"type":"doc","content":[]}\''

# Argument order is aequoros_governed_row_guard's TG_ARGV[0..4].
_CYCLE_MUTABLE = (
    "package_id,board_approved_at,submitted_at,acknowledged_at,superseded_at,updated_at"
)
_CYCLE_WRITE_ONCE = "package_id,board_approved_at,submitted_at,acknowledged_at,superseded_at"
_CYCLE_SEAL_STATES = "frozen,board_approved,submitted,acknowledged,superseded"
_CYCLE_NEXT_STATES = "board_approved,submitted,acknowledged,superseded,returned"

# --- governed parameters --------------------------------------------------
PARAM_TABLE = "regulatory_parameter"
SEED_ACTOR = "platform_seed"
JURISDICTION = "GH"
SCOPE_TYPE = "institution_class"
SCOPE_KEY = "bank"
EFFECTIVE_FROM = date(2020, 1, 1)

_MATERIALITY_BANDS: dict[str, Any] = {
    "schema": "icaap-score-bands-v1",
    "bands": [
        {"key": "low", "label": "Low", "min_score": 1, "max_score": 4},
        {"key": "medium", "label": "Medium", "min_score": 5, "max_score": 9},
        {"key": "high", "label": "High", "min_score": 10, "max_score": 16},
        {"key": "very_high", "label": "Very high", "min_score": 17, "max_score": 25},
    ],
}

#: (code, value, value_json, unit, confirmation_status, citation) — pinned here.
SEEDS: tuple[tuple[str, str | None, dict[str, Any] | None, str, str, str], ...] = (
    (
        "icaap_submission_months",
        "3",
        None,
        "months",
        "pending",
        "BoG Guideline on ICAAP (Exposure Draft, February 2026) ¶72: the report is "
        "submitted within three months of the financial year end; pending final text",
    ),
    (
        "icaap_disclosure_submission_months",
        "3",
        None,
        "months",
        "pending",
        "BoG Guideline on ICAAP (Exposure Draft, February 2026) ¶82: the published "
        "results are submitted by 31 March following the year end; pending final text",
    ),
    (
        "icaap_deadline_amber_days",
        "30",
        None,
        "days",
        "pending",
        "AequorOS platform policy: days before the filing deadline from which ICAAP "
        "readiness shows amber; not a regulatory value",
    ),
    (
        "icaap_materiality_material_min_score",
        "10",
        None,
        "score",
        "pending",
        "REPRESENTATIVE: AequorOS default 5x5 materiality matrix: a risk is material at "
        "or above this likelihood x impact score; the BoG ICAAP Guideline prescribes no matrix",
    ),
    (
        "icaap_materiality_material_min_impact",
        "4",
        None,
        "score",
        "pending",
        "REPRESENTATIVE: AequorOS default 5x5 materiality matrix: a risk is material at "
        "or above this impact score whatever its likelihood; the BoG ICAAP Guideline "
        "prescribes no matrix",
    ),
    (
        "icaap_materiality_rating_bands",
        None,
        _MATERIALITY_BANDS,
        "score_bands",
        "pending",
        "REPRESENTATIVE: AequorOS default 5x5 materiality matrix rating bands over "
        "likelihood x impact scores; the BoG ICAAP Guideline prescribes no matrix",
    ),
    (
        "icaap_stress_horizon_years_min",
        "3",
        None,
        "years",
        "pending",
        "BoG Stress Testing Guideline (Exposure Draft Feb 2026) ¶68, ¶75: pre- and "
        "post-stress capital projected over at least three years; pending final text",
    ),
    (
        "icaap_capital_planning_horizon_years_min",
        "3",
        None,
        "years",
        "pending",
        "BoG Stress Testing Guideline (Exposure Draft Feb 2026) ¶68, ¶75 and "
        "Appendix II Table 5: capital projected over at least three years; pending final text",
    ),
)
PARAM_CODES = tuple(code for code, *_rest in SEEDS)


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


def _install_sealed_cycles() -> None:
    arguments = ", ".join(
        f"'{value}'"
        for value in (
            _CYCLE_MUTABLE,
            _CYCLE_WRITE_ONCE,
            "status",
            _CYCLE_SEAL_STATES,
            _CYCLE_NEXT_STATES,
        )
    )
    op.execute(
        f"""
        CREATE TRIGGER {_CYCLES}_governed_row BEFORE UPDATE ON {_CYCLES}
        FOR EACH ROW EXECUTE FUNCTION aequoros_governed_row_guard({arguments})
        """
    )
    _revoke(_CYCLES, "TRUNCATE")


def _create_cycles() -> None:
    op.create_table(
        _CYCLES,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("cycle_kind", sa.String(20), nullable=False),
        sa.Column("basis", sa.String(12), nullable=False),
        sa.Column(
            "subsidiaries_declared", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("framework_code", sa.String(60), nullable=False),
        sa.Column("framework_version", sa.String(40), nullable=False),
        sa.Column("framework_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("current_stage_seq", sa.Integer(), nullable=True),
        sa.Column("round", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("package_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("due_date_basis", sa.String(16), nullable=False),
        sa.Column("change_trigger", sa.String(40), nullable=True),
        sa.Column("change_description", sa.Text(), nullable=True),
        sa.Column("regulator_request_ref", sa.String(120), nullable=True),
        sa.Column("supersedes_cycle_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rebased_from_cycle_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("board_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("archive_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"cycle_kind IN {_KINDS}", name="ck_icaap_cycles_kind"),
        sa.CheckConstraint(f"status IN {_STATUSES}", name="ck_icaap_cycles_status"),
        sa.CheckConstraint(f"basis IN {_BASES}", name="ck_icaap_cycles_basis"),
        sa.CheckConstraint(
            f"due_date_basis IN {_DUE_BASES}", name="ck_icaap_cycles_due_date_basis"
        ),
        sa.CheckConstraint("round >= 1", name="ck_icaap_cycles_round"),
        sa.CheckConstraint("fiscal_year BETWEEN 1990 AND 2200", name="ck_icaap_cycles_fiscal_year"),
        sa.CheckConstraint(
            "due_date_basis = 'timely' OR due_date IS NOT NULL", name="ck_icaap_cycles_due_date"
        ),
        sa.CheckConstraint(
            "cycle_kind <> 'rehearsal' OR package_id IS NULL",
            name="ck_icaap_cycles_rehearsal_no_package",
        ),
        sa.CheckConstraint(
            "cycle_kind <> 'rehearsal' OR status IN "
            "('draft', 'in_review', 'returned', 'superseded', 'archived')",
            name="ck_icaap_cycles_rehearsal_never_sealed",
        ),
        sa.CheckConstraint(
            "status NOT IN ('frozen', 'board_approved', 'submitted', 'acknowledged') "
            "OR package_id IS NOT NULL",
            name="ck_icaap_cycles_sealed_has_package",
        ),
        sa.CheckConstraint(
            "cycle_kind <> 'material_change' OR change_trigger IS NOT NULL",
            name="ck_icaap_cycles_change_trigger",
        ),
        sa.CheckConstraint(
            "cycle_kind <> 'regulator_request' OR regulator_request_ref IS NOT NULL",
            name="ck_icaap_cycles_regulator_request_ref",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_cycle_id", "organization_id", "bank_id"], _CYCLE_FK_TARGETS
        ),
        sa.ForeignKeyConstraint(
            ["rebased_from_cycle_id", "organization_id", "bank_id"], _CYCLE_FK_TARGETS
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_cycles_id_org"),
        sa.UniqueConstraint("id", "organization_id", "bank_id", name="uq_icaap_cycles_id_org_bank"),
    )
    op.create_index(
        "uq_icaap_cycles_open",
        _CYCLES,
        ["organization_id", "bank_id", "cycle_kind", "as_of_date", "basis"],
        unique=True,
        postgresql_where=sa.text(_OPEN_CYCLE),
        sqlite_where=sa.text(_OPEN_CYCLE),
    )
    op.create_index(
        "ix_icaap_cycles_org_bank_fy", _CYCLES, ["organization_id", "bank_id", "fiscal_year"]
    )


def _create_sections() -> None:
    op.create_table(
        "icaap_sections",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.String(60), nullable=False),
        sa.Column("letter", sa.String(2), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("working_doc", sa.JSON(), server_default=sa.text(_EMPTY_DOC), nullable=False),
        sa.Column("working_rev", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("working_updated_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("working_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_version_no", sa.Integer(), nullable=True),
        sa.Column("committed_from_rev", sa.Integer(), nullable=True),
        sa.Column("checklist_state", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("carried_from", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.UniqueConstraint("cycle_id", "section_key", name="uq_icaap_sections_cycle_key"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_sections_id_org"),
        sa.CheckConstraint("working_rev >= 0", name="ck_icaap_sections_working_rev"),
        sa.CheckConstraint("position BETWEEN 1 AND 99", name="ck_icaap_sections_position"),
        sa.CheckConstraint(
            "committed_version_no IS NULL OR committed_version_no >= 1",
            name="ck_icaap_sections_committed_version_no",
        ),
    )
    op.create_index(
        "ix_icaap_sections_org_cycle", "icaap_sections", ["organization_id", "cycle_id"]
    )


def _create_section_versions() -> None:
    op.create_table(
        "icaap_section_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("section_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.String(60), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("source_rev", sa.Integer(), nullable=False),
        sa.Column("editor_schema_version", sa.String(40), nullable=False),
        sa.Column("doc", sa.JSON(), nullable=False),
        sa.Column("plain_text", sa.Text(), nullable=False),
        sa.Column("doc_sha256", sa.String(64), nullable=False),
        sa.Column("fact_refs", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("block_refs", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("commit_note", sa.Text(), nullable=True),
        sa.Column("ai_provenance", sa.JSON(), nullable=True),
        sa.Column("committed_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["section_id", "organization_id"],
            ["icaap_sections.id", "icaap_sections.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.UniqueConstraint("section_id", "version_no", name="uq_icaap_section_versions_no"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_section_versions_id_org"),
        sa.CheckConstraint("version_no >= 1", name="ck_icaap_section_versions_no"),
        sa.CheckConstraint("round >= 1", name="ck_icaap_section_versions_round"),
        sa.CheckConstraint("length(doc_sha256) = 64", name="ck_icaap_section_versions_sha"),
    )
    op.create_index(
        "ix_icaap_section_versions_org_cycle",
        "icaap_section_versions",
        ["organization_id", "cycle_id"],
    )


def _create_data_blocks() -> None:
    op.create_table(
        "icaap_data_blocks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("block_type", sa.String(40), nullable=False),
        sa.Column("block_key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("params", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("pin_reason", sa.Text(), nullable=True),
        sa.Column("pinned_binding_seq", sa.Integer(), nullable=True),
        sa.Column("pinned_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.UniqueConstraint("cycle_id", "block_key", name="uq_icaap_data_blocks_cycle_key"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_data_blocks_id_org"),
        sa.CheckConstraint(
            "(pin_reason IS NULL) = (pinned_binding_seq IS NULL)", name="ck_icaap_data_blocks_pin"
        ),
    )
    op.create_index(
        "ix_icaap_data_blocks_org_cycle", "icaap_data_blocks", ["organization_id", "cycle_id"]
    )


def _create_attachments() -> None:
    op.create_table(
        "icaap_attachments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_tier", sa.String(16), nullable=False),
        sa.Column("object_path", sa.String(512), nullable=False),
        sa.Column("storage_version_id", sa.String(255), nullable=True),
        sa.Column("section_key", sa.String(60), nullable=True),
        sa.Column("block_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_attachments_id_org"),
        sa.CheckConstraint("byte_size > 0", name="ck_icaap_attachments_size"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_icaap_attachments_sha"),
        sa.CheckConstraint("storage_tier = 'outputs'", name="ck_icaap_attachments_tier"),
    )
    op.create_index(
        "ix_icaap_attachments_org_cycle_kind",
        "icaap_attachments",
        ["organization_id", "cycle_id", "kind"],
    )


def _create_block_bindings() -> None:
    op.create_table(
        "icaap_block_bindings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("block_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("resolver", sa.String(40), nullable=False),
        sa.Column("resolver_version", sa.String(20), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("source_key", sa.String(300), nullable=False),
        sa.Column("source_as_of", sa.Date(), nullable=True),
        sa.Column("source_run_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_attachment_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("bound_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["block_id", "organization_id"],
            ["icaap_data_blocks.id", "icaap_data_blocks.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["evidence_attachment_id", "organization_id"],
            ["icaap_attachments.id", "icaap_attachments.organization_id"],
        ),
        sa.UniqueConstraint("block_id", "seq", name="uq_icaap_block_bindings_seq"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_block_bindings_id_org"),
        sa.CheckConstraint(f"source_kind IN {_SOURCE_KINDS}", name="ck_icaap_block_bindings_kind"),
        sa.CheckConstraint("seq >= 1", name="ck_icaap_block_bindings_seq"),
        sa.CheckConstraint("length(payload_sha256) = 64", name="ck_icaap_block_bindings_sha"),
    )
    op.create_index(
        "ix_icaap_block_bindings_org_cycle",
        "icaap_block_bindings",
        ["organization_id", "cycle_id"],
    )


def _create_withdrawals() -> None:
    op.create_table(
        "icaap_attachment_withdrawals",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("cycle_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("attachment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("withdrawn_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["attachment_id", "organization_id"],
            ["icaap_attachments.id", "icaap_attachments.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(_CYCLE_FK_COLUMNS, _CYCLE_FK_TARGETS, ondelete="CASCADE"),
        sa.UniqueConstraint("attachment_id", name="uq_icaap_attachment_withdrawals_attachment"),
        sa.UniqueConstraint("id", "organization_id", name="uq_icaap_attachment_withdrawals_id_org"),
    )


def _seed_table() -> sa.TableClause:
    return sa.table(
        PARAM_TABLE,
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("scope_type", sa.String),
        sa.column("scope_key", sa.String),
        sa.column("param_code", sa.String),
        sa.column("jurisdiction_code", sa.String),
        sa.column("value_numeric", sa.Numeric),
        sa.column("value_json", sa.JSON),
        sa.column("unit", sa.String),
        sa.column("source_citation", sa.String),
        sa.column("confirmation_status", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("status", sa.String),
        sa.column("proposed_by", sa.String),
        sa.column("approved_by", sa.String),
        sa.column("approved_at", sa.DateTime(timezone=True)),
        sa.column("change_rationale", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _codes_sql() -> str:
    return ", ".join(f"'{code}'" for code in PARAM_CODES)


def _seed_parameters() -> None:
    """Insert each code unless an APPROVED row already governs it.

    Same rule as ``202609190054``: a draft operator row governs nothing, so it
    must not suppress the seed, but it can occupy the seed's generation date —
    in which case the seed takes the day before and the draft is left alone.
    """
    bind = op.get_bind()
    governed = {
        row[0]
        for row in bind.execute(
            sa.text(
                f"SELECT param_code FROM {PARAM_TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                "AND scope_type = :scope_type AND scope_key = :scope_key "
                "AND jurisdiction_code = :jurisdiction "
                "AND status = 'approved' AND effective_to IS NULL"
            ),
            {"scope_type": SCOPE_TYPE, "scope_key": SCOPE_KEY, "jurisdiction": JURISDICTION},
        )
    }
    occupied = {
        row[0]
        for row in bind.execute(
            sa.text(
                f"SELECT param_code FROM {PARAM_TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                "AND scope_type = :scope_type AND scope_key = :scope_key "
                "AND jurisdiction_code = :jurisdiction AND effective_from = :effective_from"
            ),
            {
                "scope_type": SCOPE_TYPE,
                "scope_key": SCOPE_KEY,
                "jurisdiction": JURISDICTION,
                "effective_from": EFFECTIVE_FROM,
            },
        )
    }
    now = datetime.now(UTC)
    rows = [
        {
            "id": new_uuid4(),
            "scope_type": SCOPE_TYPE,
            "scope_key": SCOPE_KEY,
            "param_code": code,
            "jurisdiction_code": JURISDICTION,
            "value_numeric": None if value is None else Decimal(value),
            "value_json": value_json,
            "unit": unit,
            "source_citation": citation,
            "confirmation_status": status,
            "effective_from": (
                EFFECTIVE_FROM - timedelta(days=1) if code in occupied else EFFECTIVE_FROM
            ),
            "effective_to": None,
            "status": "approved",
            "proposed_by": SEED_ACTOR,
            "approved_by": SEED_ACTOR,
            "approved_at": now,
            "change_rationale": None,
            "created_at": now,
            "updated_at": now,
        }
        for code, value, value_json, unit, status, citation in SEEDS
        if code not in governed
    ]
    if rows:
        op.bulk_insert(_seed_table(), rows)


def upgrade() -> None:
    _create_cycles()
    _create_sections()
    _create_section_versions()
    _create_data_blocks()
    _create_attachments()
    _create_block_bindings()
    _create_withdrawals()

    _seed_parameters()

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        _enable_rls(table)
    for table in _UNALTERABLE:
        _install_unalterable(table)
    _install_sealed_cycles()


def downgrade() -> None:
    op.execute(
        sa.text(
            f"DELETE FROM {PARAM_TABLE} WHERE param_code IN ({_codes_sql()}) "
            f"AND proposed_by = '{SEED_ACTOR}'"
        )
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {_CYCLES}_governed_row ON {_CYCLES}")
        for table in _UNALTERABLE:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
            op.execute(f"DROP POLICY IF EXISTS {table}_no_update ON {table}")
        for table in _TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    # Children first: the cascade order is the reverse of creation.
    op.drop_table("icaap_attachment_withdrawals")
    op.drop_table("icaap_block_bindings")
    op.drop_table("icaap_attachments")
    op.drop_table("icaap_data_blocks")
    op.drop_table("icaap_section_versions")
    op.drop_table("icaap_sections")
    op.drop_table(_CYCLES)
