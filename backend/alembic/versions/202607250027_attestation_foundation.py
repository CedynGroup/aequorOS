"""attestation foundation: append-only evidence, digests, artifact versions

Revision ID: 202607250027
Revises: 202607240026

Phase 0 of docs/attestation_esignature.md §6 — the evidential foundation a
signature can defensibly sit on. Four things happen here:

1. **Append-only, enforced by the database** (gap G1). Until now "append-only"
   was a convention in application code: the app role owned ``audit_events``
   with UPDATE, DELETE and TRUNCATE, there were zero triggers in the database,
   and the single RLS policy was ``FOR ALL`` — it scoped writes to a tenant but
   did not forbid them. This migration installs, on every evidence table:
   a ``BEFORE UPDATE OR DELETE`` trigger that raises, a ``REVOKE UPDATE,
   DELETE, TRUNCATE`` from the app and worker roles, and ``server_default now()``
   on ``created_at`` so the database — not an application host's unmonitored
   wall clock — stamps arrival.

2. **The attestation tables**: signer identities, signer keys, signing
   authorisations, signing policies, and the signature records themselves.

3. **Append-only artifact versions** (gap G2). The existing artifact row is
   upserted in place by the exporter, so "the artifact as filed" cannot be
   resolved from the database. ``regulatory_artifact_versions`` appends one row
   per export and records the object-store ``version_id``.

4. **Attestation columns on the package**: ``content_digest`` (a true content
   fingerprint — ``snapshot_sha256`` embeds ``generated_at`` and therefore
   seals a version, gap G13), ``register_state_digest`` (master-data
   provenance for the LRT packs that bind no engine run, gap G16), and the
   certification/void lifecycle.

Postgres-only for the enforcement parts: SQLite has no roles or triggers of
this shape, and the hermetic suite builds its schema from the models. The
Postgres-gated migration tests exercise the real thing.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607250027"
down_revision = "202607240026"
branch_labels = None
depends_on = None

# Evidence tables. Two enforcement tiers, and the difference is deliberate:
#
#   IMMUTABLE — no UPDATE, no DELETE, no TRUNCATE. The master audit trail.
#   Nothing in the product deletes an audit event, so nothing legitimate
#   breaks by forbidding it outright.
#
#   UNALTERABLE — no UPDATE, no TRUNCATE; DELETE reachable only by deleting the
#   owning package (ON DELETE CASCADE). No production path deletes a package;
#   the demo-seed fixture does, which is why an outright DELETE ban would break
#   reseeding. Crucially, ALTERATION and FORGERY remain impossible: a row can
#   never be rewritten, and a new one cannot be fabricated without the signer's
#   HSM key. A removed row is *detectable* — the per-tenant hash chain breaks,
#   and the (immutable) audit trail retains the signature event.
#
# regulatory_package_approvals / regulatory_submission_events are deliberately
# NOT trigger-guarded: they are CASCADE children the fixture purges, and the
# authoritative attestation evidence is now the signature chain plus the audit
# trail, both of which are guarded.
IMMUTABLE_TABLES = ("audit_events",)
UNALTERABLE_TABLES = (
    "attestation_signatures",
    "signer_identities",
    "regulatory_artifact_versions",
)
APPEND_ONLY_TABLES = IMMUTABLE_TABLES + UNALTERABLE_TABLES

_GUARD_FUNCTION = "aequoros_append_only_guard"


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


def _install_append_only(table: str, *, block_delete: bool) -> None:
    events = "UPDATE OR DELETE" if block_delete else "UPDATE"
    op.execute(
        f"""
        CREATE TRIGGER {table}_append_only
        BEFORE {events} ON {table}
        FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}()
        """
    )
    # Belt and braces: even if a future migration drops the trigger, the role
    # lacks the privilege. TRUNCATE bypasses row triggers entirely, so revoking
    # it is never optional.
    _revoke(table, "UPDATE, DELETE, TRUNCATE" if block_delete else "UPDATE, TRUNCATE")
    # Restrictive RLS ANDs with every permissive policy, so no future
    # permissive policy can re-admit what this forbids.
    using = "false" if block_delete else "true"
    op.execute(
        f"""
        CREATE POLICY {table}_no_update ON {table}
        AS RESTRICTIVE FOR UPDATE TO PUBLIC
        USING (false) WITH CHECK (false)
        """
    )
    if block_delete:
        op.execute(
            f"""
            CREATE POLICY {table}_no_delete ON {table}
            AS RESTRICTIVE FOR DELETE TO PUBLIC
            USING ({using})
            """
        )


def upgrade() -> None:  # noqa: PLR0915 - one linear schema wave, kept readable
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # -- 1. attestation tables ---------------------------------------------
    op.create_table(
        "signer_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("signer_id", sa.String(length=24), nullable=False),
        sa.Column("derivation_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        # RESTRICT: a person provisioned to sign cannot be deleted out from
        # under their signatures (gap G10).
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_signer_identities_user_id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_signer_identities_id_org"),
    )
    op.create_index(
        "uq_signer_identities_signer_id", "signer_identities", ["signer_id"], unique=True
    )
    op.create_index(
        "ix_signer_identities_organization_id", "signer_identities", ["organization_id"]
    )

    op.create_table(
        "signer_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("signer_id", sa.String(length=24), nullable=False),
        sa.Column("backend", sa.String(length=16), nullable=False),
        sa.Column("key_ref", sa.String(length=255), nullable=False),
        sa.Column("algorithm", sa.String(length=40), nullable=False),
        sa.Column("certificate_pem", sa.Text(), nullable=False),
        sa.Column("certificate_sha256", sa.String(length=64), nullable=False),
        sa.Column("certificate_chain_pem", sa.Text(), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'rotated', 'revoked')", name="ck_signer_keys_status"
        ),
        sa.CheckConstraint(
            "backend IN ('pkcs11', 'kms', 'software')", name="ck_signer_keys_backend"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_signer_keys_id_org"),
    )
    op.create_index("ix_signer_keys_organization_id", "signer_keys", ["organization_id"])
    op.create_index("ix_signer_keys_signer_id_status", "signer_keys", ["signer_id", "status"])

    op.create_table(
        "signing_authorizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("signer_id", sa.String(length=24), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("signing_role", sa.String(length=16), nullable=False),
        sa.Column("certification_digest", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auth_evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "signing_role IN ('preparer', 'approver', 'board', 'witness')",
            name="ck_signing_authorizations_role",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_signing_authorizations_id_org"),
    )
    op.create_index(
        "uq_signing_authorizations_token_hash",
        "signing_authorizations",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_signing_authorizations_org_package",
        "signing_authorizations",
        ["organization_id", "package_id"],
    )

    op.create_table(
        "return_signing_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=True),
        sa.Column("return_code", sa.String(length=40), nullable=True),
        sa.Column("return_family", sa.String(length=40), nullable=True),
        sa.Column("basis", sa.String(length=16), nullable=True),
        sa.Column("required_signatures", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("required_attachments", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("require_signature", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("require_signed_pdf", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("distinct_signers", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "return_code IS NOT NULL OR return_family IS NOT NULL",
            name="ck_return_signing_policies_scope",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_return_signing_policies_id_org"),
    )
    op.create_index(
        "ix_return_signing_policies_scope",
        "return_signing_policies",
        ["organization_id", "bank_id", "return_code", "return_family"],
    )

    op.create_table(
        "regulatory_artifact_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("object_path", sa.String(length=512), nullable=False),
        sa.Column("storage_version_id", sa.String(length=255), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_artifact_versions_id_org"
        ),
    )
    op.create_index(
        "ix_regulatory_artifact_versions_org_package_kind",
        "regulatory_artifact_versions",
        ["organization_id", "package_id", "kind"],
    )

    op.create_table(
        "attestation_signatures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("package_version", sa.Integer(), nullable=False),
        sa.Column("signing_role", sa.String(length=16), nullable=False),
        sa.Column("officer_title", sa.String(length=120), nullable=True),
        sa.Column("signer_id", sa.String(length=24), nullable=False),
        sa.Column("signer_user_id", sa.Uuid(), nullable=False),
        sa.Column("signer_display_name", sa.String(length=255), nullable=True),
        sa.Column("binding_class", sa.String(length=16), nullable=False),
        sa.Column("certification_digest", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=True),
        sa.Column("register_state_digest", sa.String(length=64), nullable=True),
        sa.Column("signed_source_runs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("attestation_payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("signature_method", sa.String(length=40), nullable=False),
        sa.Column("signature_value", sa.LargeBinary(), nullable=False),
        sa.Column("certificate_pem", sa.Text(), nullable=False),
        sa.Column("certificate_sha256", sa.String(length=64), nullable=False),
        sa.Column("certificate_chain_pem", sa.Text(), nullable=True),
        sa.Column("tsa_token", sa.LargeBinary(), nullable=True),
        sa.Column("tsa_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tsa_url", sa.String(length=255), nullable=True),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("auth_evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("artifact_version_id", sa.Uuid(), nullable=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("attestation_cycle", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "signing_role IN ('preparer', 'approver', 'board', 'witness')",
            name="ck_attestation_signatures_role",
        ),
        sa.CheckConstraint(
            "signature_method IN ('pades_b_lta', 'pades_b_lt', "
            "'detached_ecdsa_p256_sha256', 'detached_rsa_pss_sha256')",
            name="ck_attestation_signatures_method",
        ),
        sa.CheckConstraint(
            "binding_class IN ('engine_run', 'master_data')",
            name="ck_attestation_signatures_binding_class",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_attestation_signatures_id_org"),
    )
    op.create_index(
        "ix_attestation_signatures_org_package",
        "attestation_signatures",
        ["organization_id", "package_id"],
    )
    op.create_index(
        "ix_attestation_signatures_signer_id", "attestation_signatures", ["signer_id"]
    )

    # -- 2. attestation columns on the package -----------------------------
    op.add_column(
        "regulatory_packages", sa.Column("content_digest", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "regulatory_packages",
        sa.Column("register_state_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "regulatory_packages",
        sa.Column(
            "attestation_state", sa.String(length=24), nullable=False, server_default="unsigned"
        ),
    )
    op.add_column(
        "regulatory_packages",
        sa.Column("attestation_cycle", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "regulatory_packages",
        sa.Column("certification_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "regulatory_packages", sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "regulatory_packages",
        sa.Column("fully_certified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "regulatory_packages", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "regulatory_packages", sa.Column("void_reason", sa.String(length=500), nullable=True)
    )
    op.create_check_constraint(
        "ck_regulatory_packages_attestation_state",
        "regulatory_packages",
        "attestation_state IN ('unsigned', 'preparer_certified', 'fully_certified', 'void')",
    )

    if not is_postgres:
        return

    # -- 3. RLS on the new tenant tables (mirrors the platform pattern) ----
    for table in (
        "signer_identities",
        "signer_keys",
        "signing_authorizations",
        "return_signing_policies",
        "regulatory_artifact_versions",
        "attestation_signatures",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            FOR ALL
            USING (
                (organization_id)::text
                = NULLIF(current_setting('app.organization_id', true), '')
            )
            WITH CHECK (
                (organization_id)::text
                = NULLIF(current_setting('app.organization_id', true), '')
            )
            """
        )

    # -- 4. append-only enforcement (gap G1) -------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'append-only table %: % is not permitted (attestation evidence)',
                TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in IMMUTABLE_TABLES:
        _install_append_only(table, block_delete=True)
    for table in UNALTERABLE_TABLES:
        _install_append_only(table, block_delete=False)

    # The database stamps arrival time, not an application host's wall clock.
    for table in APPEND_ONLY_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at SET DEFAULT now()")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in APPEND_ONLY_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
            op.execute(f"DROP POLICY IF EXISTS {table}_no_update ON {table}")
            op.execute(f"DROP POLICY IF EXISTS {table}_no_delete ON {table}")
            op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at DROP DEFAULT")
            op.execute(f"GRANT UPDATE, DELETE, TRUNCATE ON {table} TO CURRENT_USER")
        op.execute(f"DROP FUNCTION IF EXISTS {_GUARD_FUNCTION}()")

    op.drop_constraint(
        "ck_regulatory_packages_attestation_state", "regulatory_packages", type_="check"
    )
    for column in (
        "void_reason",
        "voided_at",
        "fully_certified_at",
        "certified_at",
        "certification_digest",
        "attestation_cycle",
        "attestation_state",
        "register_state_digest",
        "content_digest",
    ):
        op.drop_column("regulatory_packages", column)

    op.drop_table("attestation_signatures")
    op.drop_table("regulatory_artifact_versions")
    op.drop_table("return_signing_policies")
    op.drop_table("signing_authorizations")
    op.drop_table("signer_keys")
    op.drop_table("signer_identities")
