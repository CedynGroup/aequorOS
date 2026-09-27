"""The BI commentary draft row: one AI request, and the evidence of what left.

Revision ID: 202609270071
Revises: 202609270070

``ai_commentary_drafts`` records one AI commentary request about one institution
and one date. It is named for the FEATURE and tabled in the AI plane, beside
``ai_commentary_settings``, because those are two different questions:

* the SURFACE is BI — a reader on a BI pack asks for commentary on the figures
  the query path just served them;
* the ROW is AI EGRESS EVIDENCE — exactly which minimised, pseudonymised payload
  left the process, under which consent version, which deployment approval,
  which prompt and which model, and what came back.

Its sibling is the consent row that gates it, not a mart. The split is also
ENFORCED rather than stylistic: ``tests/architecture/test_bi_plane_boundary.py``
derives the tables BI may write from the ``bi_``-prefixed tables of
``Base.metadata`` and separately asserts that set equals what ``app/models/bi.py``
declares, so a ``bi_``-named table declared elsewhere breaks the derivation. The
consequence is deliberate: nothing under ``app/services/bi`` writes this row —
``app/jobs/bi_commentary.py`` does, exactly as ``app/jobs/bi_export.py`` owns the
``audit_events`` write for the same reason.

Why the columns are shaped this way
-----------------------------------
``payload`` holds EXACTLY what was sent, so an auditor never has to reconstruct
it, and ``payload_sha256`` fixes it. ``fact_bindings`` maps each ``{{F:...}}``
placeholder to the platform's own figure and NEVER leaves the process: it is what
the read path resolves placeholders from, which is why a descriptor-only tenant
still SEES its numbers. Descriptor-only governs egress, not display.
``fallback_paragraphs`` is the deterministic commentary, composed from the same
fact sheet at request time and written BEFORE the model is called, so an
unusable model outcome needs no second code path and no second answer.

The nullable JSON columns carry ``none_as_null`` in the model, and that is
load-bearing here rather than tidiness: ``JSON`` writes Python ``None`` as the
JSON value ``null``, which is not SQL NULL, so ``output IS NULL`` would be false
for a row holding nothing and BOTH output CHECKs below would be defeated — one
passing for a ``validated`` row with no draft, the other refusing a legitimate
cancelled one. The same defect was found and fixed in three shipped tables in the
same change (``8aba3cb2``).

``uq_ai_commentary_drafts_inflight`` is a PARTIAL unique index over the queued
and running statuses, so one reader cannot have two requests in flight for the
same institution and date while any number of completed ones remain for the
audit trail. Postgres gets it as a partial index; SQLite takes the same
predicate, which is why the model states both dialects.

There is no data step, so no ``force_rls_suspended``: the table is new and
empty. RLS is the standard tenant pair, as on every tenant table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609270071"
down_revision = "202609270070"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

TABLE = "ai_commentary_drafts"

#: Vocabularies PINNED here rather than imported, like every other revision in
#: this chain, so a later model edit cannot change what this revision created.
#: The Postgres suite asserts the migrated CHECKs still admit the model's values.
_STATUSES = (
    "'queued', 'running', 'validated', 'rejected_validation', "
    "'refused', 'failed', 'rate_limited', 'cancelled'"
)
_NO_OUTPUT_STATUSES = "'refused', 'rate_limited', 'cancelled'"
_IN_FLIGHT_STATUSES = "'queued', 'running'"
_PAYLOAD_MODES = "'standard', 'descriptor_only'"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("compare_to", sa.Date(), nullable=False),
        sa.Column("catalogue_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("payload_mode", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("fact_sheet_hash", sa.String(length=64), nullable=False),
        sa.Column("fact_bindings", sa.JSON(), nullable=False),
        sa.Column("entity_keys", sa.JSON(), nullable=False),
        sa.Column("fallback_paragraphs", sa.JSON(), nullable=False),
        sa.Column("fact_count", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_requested", sa.String(length=80), nullable=False),
        sa.Column("effort", sa.String(length=8), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("fallbacks_mode", sa.String(length=16), nullable=False),
        sa.Column("consent_version", sa.String(length=40), nullable=False),
        sa.Column("deployment_approval_ref", sa.String(length=120), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_served", sa.String(length=80), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("stop_reason", sa.String(length=40), nullable=True),
        sa.Column("refusal_category", sa.String(length=40), nullable=True),
        sa.Column("failure_code", sa.String(length=40), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        sa.UniqueConstraint("id", "organization_id", name=f"uq_{TABLE}_id_org"),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name=f"ck_{TABLE}_status"),
        sa.CheckConstraint(f"payload_mode IN ({_PAYLOAD_MODES})", name=f"ck_{TABLE}_mode"),
        sa.CheckConstraint("compare_to < as_of", name=f"ck_{TABLE}_comparison"),
        sa.CheckConstraint("fact_count >= 0", name=f"ck_{TABLE}_fact_count"),
        sa.CheckConstraint("length(payload_sha256) = 64", name=f"ck_{TABLE}_payload_sha"),
        sa.CheckConstraint("length(fact_sheet_hash) = 64", name=f"ck_{TABLE}_sheet_hash"),
        sa.CheckConstraint(
            f"status IN ({_IN_FLIGHT_STATUSES}) OR completed_at IS NOT NULL",
            name=f"ck_{TABLE}_completed",
        ),
        # A validated draft HAS output; a refusal, a rate-limit and a cancellation
        # never do. Both depend on `none_as_null` on the column — see the docstring.
        sa.CheckConstraint(
            "status <> 'validated' OR output IS NOT NULL",
            name=f"ck_{TABLE}_validated_output",
        ),
        sa.CheckConstraint(
            f"NOT (status IN ({_NO_OUTPUT_STATUSES})) OR output IS NULL",
            name=f"ck_{TABLE}_no_output",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name=f"fk_{TABLE}_bank",
        ),
    )
    op.create_index(
        f"ix_{TABLE}_bank_as_of", TABLE, ["organization_id", "bank_id", "as_of", "created_at"]
    )
    op.create_index(f"ix_{TABLE}_org_created", TABLE, ["organization_id", "created_at"])
    op.create_index(
        f"ix_{TABLE}_requester", TABLE, ["organization_id", "requested_by", "created_at"]
    )
    # One in-flight request per reader per institution per date; completed rows
    # accumulate freely, because they are the audit trail.
    op.create_index(
        f"uq_{TABLE}_inflight",
        TABLE,
        ["organization_id", "bank_id", "as_of", "requested_by"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_IN_FLIGHT_STATUSES})"),
        sqlite_where=sa.text(f"status IN ({_IN_FLIGHT_STATUSES})"),
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {TABLE}_tenant_isolation ON {TABLE} FOR ALL "
        f"USING ((organization_id)::text = {_TENANT_ID_EXPR}) "
        f"WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.drop_table(TABLE)
