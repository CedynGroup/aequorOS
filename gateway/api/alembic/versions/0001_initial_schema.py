# pyright: standard
"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-21
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_daily_stats",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("total", sa.BigInteger(), nullable=False),
        sa.Column("server_errors", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_api_daily_stats")),
    )
    op.create_table(
        "audit_entries",
        sa.Column("seq", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_ref", sa.String(length=120), nullable=False),
        sa.Column("actor_label", sa.String(length=200), nullable=False),
        sa.Column("actor_via", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("action", sa.String(length=200), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("object_ref", sa.String(length=120), nullable=False),
        sa.Column("institution_code", sa.String(length=8), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("signature_kid", sa.String(length=120), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_audit_entries")),
        sa.UniqueConstraint("entry_hash", name=op.f("uq_audit_entries_entry_hash")),
    )
    op.create_index(
        op.f("ix_audit_entries_institution_code"),
        "audit_entries",
        ["institution_code"],
        unique=False,
    )
    op.create_index("ix_audit_entries_object_ref", "audit_entries", ["object_ref"], unique=False)
    op.create_index("ix_audit_entries_occurred_at", "audit_entries", ["occurred_at"], unique=False)
    op.create_table(
        "audit_verifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("intact", sa.Boolean(), nullable=False),
        sa.Column("entries", sa.BigInteger(), nullable=False),
        sa.Column("head_hash", sa.String(length=64), nullable=False),
        sa.Column("first_bad_seq", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_verifications")),
    )
    op.create_index(
        op.f("ix_audit_verifications_verified_at"),
        "audit_verifications",
        ["verified_at"],
        unique=False,
    )
    op.create_table(
        "counters",
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_counters")),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("client_ref", sa.String(length=120), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint("client_ref", "key", name=op.f("uq_idempotency_records_client_ref")),
    )
    op.create_table(
        "institutions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=True),
        sa.Column("software_declared", sa.String(length=120), nullable=True),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.Column("supervisor_subject", sa.String(length=120), nullable=True),
        sa.Column("portal_users", sa.Integer(), nullable=False),
        sa.Column("portal_mfa", sa.Boolean(), nullable=False),
        sa.Column("registered_by", sa.String(length=120), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("live_since", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_institutions")),
        sa.UniqueConstraint("code", name=op.f("uq_institutions_code")),
    )
    op.create_table(
        "operator_access_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("operator_id", sa.String(length=80), nullable=False),
        sa.Column("operator_email", sa.String(length=200), nullable=False),
        sa.Column("operator_name", sa.String(length=200), nullable=False),
        sa.Column("operator_title", sa.String(length=200), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("excludes", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("incident_ref", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("decided_by_name", sa.String(length=200), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deny_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operator_access_requests")),
        sa.UniqueConstraint("number", name=op.f("uq_operator_access_requests_number")),
    )
    op.create_table(
        "return_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("cadence", sa.String(length=20), nullable=False),
        sa.Column("applies_to", sa.JSON(), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("auto_accept", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_definitions")),
        sa.UniqueConstraint("code", name=op.f("uq_return_definitions_code")),
    )
    op.create_table(
        "staff_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mfa", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_staff_sessions_token_hash")),
    )
    op.create_index(op.f("ix_staff_sessions_subject"), "staff_sessions", ["subject"], unique=False)
    op.create_table(
        "staff_users",
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("short_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("groups", sa.JSON(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("subject", name=op.f("pk_staff_users")),
    )
    op.create_table(
        "used_client_assertions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("jti", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_used_client_assertions")),
        sa.UniqueConstraint("client_id", "jti", name=op.f("uq_used_client_assertions_client_id")),
    )
    op.create_table(
        "api_clients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("institution_id", sa.String(length=36), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("certificate_pem", sa.Text(), nullable=False),
        sa.Column("certificate_thumbprint", sa.String(length=64), nullable=False),
        sa.Column("certificate_serial", sa.String(length=64), nullable=False),
        sa.Column("certificate_not_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("jwks", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name=op.f("fk_api_clients_institution_id_institutions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_clients")),
        sa.UniqueConstraint(
            "certificate_thumbprint", name=op.f("uq_api_clients_certificate_thumbprint")
        ),
        sa.UniqueConstraint("client_id", name=op.f("uq_api_clients_client_id")),
    )
    op.create_index(
        op.f("ix_api_clients_institution_id"), "api_clients", ["institution_id"], unique=False
    )
    op.create_table(
        "institution_contacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("institution_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name=op.f("fk_institution_contacts_institution_id_institutions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_institution_contacts")),
    )
    op.create_index(
        op.f("ix_institution_contacts_institution_id"),
        "institution_contacts",
        ["institution_id"],
        unique=False,
    )
    op.create_table(
        "obligations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("institution_id", sa.String(length=36), nullable=False),
        sa.Column("return_code", sa.String(length=40), nullable=False),
        sa.Column("reporting_date", sa.Date(), nullable=False),
        sa.Column("basis", sa.String(length=20), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("granted_by", sa.String(length=120), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name=op.f("fk_obligations_institution_id_institutions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_obligations")),
        sa.UniqueConstraint(
            "institution_id",
            "return_code",
            "reporting_date",
            "basis",
            name=op.f("uq_obligations_institution_id"),
        ),
    )
    op.create_index(
        op.f("ix_obligations_institution_id"), "obligations", ["institution_id"], unique=False
    )
    op.create_table(
        "return_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("definition_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("first_reporting_date", sa.Date(), nullable=False),
        sa.Column("last_reporting_date", sa.Date(), nullable=True),
        sa.Column("schema", sa.JSON(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("schema_sha256", sa.String(length=64), nullable=False),
        sa.Column("rules_sha256", sa.String(length=64), nullable=False),
        sa.Column("rule_count", sa.Integer(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("notice_days", sa.Integer(), nullable=False),
        sa.Column("breaking", sa.Boolean(), nullable=False),
        sa.Column("drafted_by", sa.String(length=120), nullable=False),
        sa.Column("drafted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["return_definitions.id"],
            name=op.f("fk_return_versions_definition_id_return_definitions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_versions")),
    )
    op.create_index(
        op.f("ix_return_versions_definition_id"), "return_versions", ["definition_id"], unique=False
    )
    op.create_table(
        "signing_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("institution_id", sa.String(length=36), nullable=False),
        sa.Column("kid", sa.String(length=120), nullable=False),
        sa.Column("public_jwk", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("custody_note", sa.String(length=300), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registered_by", sa.String(length=120), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name=op.f("fk_signing_keys_institution_id_institutions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signing_keys")),
        sa.UniqueConstraint("kid", name=op.f("uq_signing_keys_kid")),
    )
    op.create_index(
        op.f("ix_signing_keys_institution_id"), "signing_keys", ["institution_id"], unique=False
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("receipt_id", sa.String(length=20), nullable=False),
        sa.Column("institution_id", sa.String(length=36), nullable=False),
        sa.Column("return_code", sa.String(length=40), nullable=False),
        sa.Column("return_version", sa.String(length=20), nullable=False),
        sa.Column("reporting_date", sa.Date(), nullable=False),
        sa.Column("basis", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("supersedes_receipt_id", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("environment", sa.String(length=20), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=True),
        sa.Column("certificate_thumbprint", sa.String(length=64), nullable=True),
        sa.Column("certificate_not_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signing_kid", sa.String(length=120), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("declaration", sa.JSON(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("declaration_sha256", sa.String(length=64), nullable=False),
        sa.Column("software_declared", sa.String(length=120), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_ms", sa.Integer(), nullable=False),
        sa.Column("ledger_seq", sa.BigInteger(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_subject", sa.String(length=120), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name=op.f("fk_submissions_institution_id_institutions"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["submissions.id"],
            name=op.f("fk_submissions_supersedes_id_submissions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submissions")),
        sa.UniqueConstraint("receipt_id", name=op.f("uq_submissions_receipt_id")),
    )
    op.create_index(
        "ix_submissions_key",
        "submissions",
        ["institution_id", "return_code", "reporting_date", "basis"],
        unique=False,
    )
    op.create_index("ix_submissions_received_at", "submissions", ["received_at"], unique=False)
    op.create_table(
        "supervisory_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("institution_id", sa.String(length=36), nullable=False),
        sa.Column("author_subject", sa.String(length=120), nullable=False),
        sa.Column("author_name", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name=op.f("fk_supervisory_notes_institution_id_institutions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supervisory_notes")),
    )
    op.create_index(
        op.f("ix_supervisory_notes_institution_id"),
        "supervisory_notes",
        ["institution_id"],
        unique=False,
    )
    op.create_table(
        "queries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("institution_id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("receipt_id", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_by", sa.String(length=120), nullable=False),
        sa.Column("opened_by_name", sa.String(length=200), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=120), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name=op.f("fk_queries_institution_id_institutions"),
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["submissions.id"], name=op.f("fk_queries_submission_id_submissions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_queries")),
        sa.UniqueConstraint("number", name=op.f("uq_queries_number")),
    )
    op.create_index(op.f("ix_queries_institution_id"), "queries", ["institution_id"], unique=False)
    op.create_index(op.f("ix_queries_submission_id"), "queries", ["submission_id"], unique=False)
    op.create_table(
        "receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("receipt_id", sa.String(length=20), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("receipt_jws", sa.Text(), nullable=False),
        sa.Column("kid", sa.String(length=120), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_receipts_submission_id_submissions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipts")),
        sa.UniqueConstraint("receipt_id", name=op.f("uq_receipts_receipt_id")),
        sa.UniqueConstraint("submission_id", name=op.f("uq_receipts_submission_id")),
    )
    op.create_table(
        "return_version_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("step", sa.String(length=20), nullable=False),
        sa.Column("actor_subject", sa.String(length=120), nullable=False),
        sa.Column("actor_name", sa.String(length=200), nullable=False),
        sa.Column("role_label", sa.String(length=80), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["return_versions.id"],
            name=op.f("fk_return_version_approvals_version_id_return_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_version_approvals")),
    )
    op.create_index(
        op.f("ix_return_version_approvals_version_id"),
        "return_version_approvals",
        ["version_id"],
        unique=False,
    )
    op.create_table(
        "query_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("author", sa.String(length=20), nullable=False),
        sa.Column("author_label", sa.String(length=200), nullable=False),
        sa.Column("author_role", sa.String(length=120), nullable=True),
        sa.Column("author_ref", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("require_resubmission", sa.Boolean(), nullable=False),
        sa.Column("resubmission_receipt_id", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["query_id"], ["queries.id"], name=op.f("fk_query_messages_query_id_queries")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_messages")),
    )
    op.create_index(
        op.f("ix_query_messages_query_id"), "query_messages", ["query_id"], unique=False
    )

    if op.get_bind().dialect.name == "postgresql":
        _postgres_upgrade()


EVIDENCE_TABLES = ("submissions", "receipts", "query_messages", "return_versions", "signing_keys")


def _postgres_upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION srg_audit_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_entries is append-only: % refused', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_entries_append_only BEFORE UPDATE OR DELETE ON audit_entries "
        "FOR EACH ROW EXECUTE FUNCTION srg_audit_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_entries_no_truncate BEFORE TRUNCATE ON audit_entries "
        "FOR EACH STATEMENT EXECUTE FUNCTION srg_audit_append_only()"
    )
    role = _app_role()
    if role:
        apply_grants(role)


def _app_role() -> str | None:
    import os

    from alembic import context

    x_args = context.get_x_argument(as_dictionary=True)
    role = x_args.get("app_role") or os.environ.get("GATEWAY_DB_APP_ROLE")
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("app_role must be a plain lower-case identifier")
    return role or None


def apply_grants(role: str) -> None:
    """The API role reads and writes, never changes or deletes audit entries, and never
    deletes filed evidence."""
    op.execute(f"GRANT USAGE ON SCHEMA {_schema()} TO {role}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {_schema()} TO {role}"
    )
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_entries FROM {role}")
    for table in EVIDENCE_TABLES:
        op.execute(f"REVOKE DELETE, TRUNCATE ON {table} FROM {role}")


def _schema() -> str:
    schema = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar()
    if not isinstance(schema, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError("unexpected schema name")
    return schema


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_entries_no_truncate ON audit_entries")
        op.execute("DROP TRIGGER IF EXISTS audit_entries_append_only ON audit_entries")
        op.execute("DROP FUNCTION IF EXISTS srg_audit_append_only()")
    op.drop_index(op.f("ix_query_messages_query_id"), table_name="query_messages")
    op.drop_table("query_messages")
    op.drop_index(
        op.f("ix_return_version_approvals_version_id"), table_name="return_version_approvals"
    )
    op.drop_table("return_version_approvals")
    op.drop_table("receipts")
    op.drop_index(op.f("ix_queries_submission_id"), table_name="queries")
    op.drop_index(op.f("ix_queries_institution_id"), table_name="queries")
    op.drop_table("queries")
    op.drop_index(op.f("ix_supervisory_notes_institution_id"), table_name="supervisory_notes")
    op.drop_table("supervisory_notes")
    op.drop_index("ix_submissions_received_at", table_name="submissions")
    op.drop_index("ix_submissions_key", table_name="submissions")
    op.drop_table("submissions")
    op.drop_index(op.f("ix_signing_keys_institution_id"), table_name="signing_keys")
    op.drop_table("signing_keys")
    op.drop_index(op.f("ix_return_versions_definition_id"), table_name="return_versions")
    op.drop_table("return_versions")
    op.drop_index(op.f("ix_obligations_institution_id"), table_name="obligations")
    op.drop_table("obligations")
    op.drop_index(op.f("ix_institution_contacts_institution_id"), table_name="institution_contacts")
    op.drop_table("institution_contacts")
    op.drop_index(op.f("ix_api_clients_institution_id"), table_name="api_clients")
    op.drop_table("api_clients")
    op.drop_table("used_client_assertions")
    op.drop_table("staff_users")
    op.drop_index(op.f("ix_staff_sessions_subject"), table_name="staff_sessions")
    op.drop_table("staff_sessions")
    op.drop_table("return_definitions")
    op.drop_table("operator_access_requests")
    op.drop_table("institutions")
    op.drop_table("idempotency_records")
    op.drop_table("counters")
    op.drop_index(op.f("ix_audit_verifications_verified_at"), table_name="audit_verifications")
    op.drop_table("audit_verifications")
    op.drop_index("ix_audit_entries_occurred_at", table_name="audit_entries")
    op.drop_index("ix_audit_entries_object_ref", table_name="audit_entries")
    op.drop_index(op.f("ix_audit_entries_institution_code"), table_name="audit_entries")
    op.drop_table("audit_entries")
    op.drop_table("api_daily_stats")
