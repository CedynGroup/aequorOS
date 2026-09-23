"""Restore owner key-share locks on immutable ICAAP evidence.

Foreign-key checks lock rows as their table owner. PostgreSQL requires
an UPDATE privilege on at least one column for FOR KEY SHARE, even though it
writes nothing. Keep application-role revocations, restrictive UPDATE policies
and immutable-row triggers; restore only the owner's id-column privilege.
"""

from alembic import op

revision = "202609210066"
down_revision = "202609200065"
branch_labels = None
depends_on = None

# FK checks lock both referenced rows and referencing rows during deletion.
_TABLES = (
    "icaap_section_versions",
    "icaap_block_bindings",
    "icaap_attachments",
    "icaap_attachment_withdrawals",
    "icaap_pillar2_item_revisions",
    "icaap_challenges",
    "icaap_challenge_responses",
    "icaap_cycle_stages",
    "regulatory_package_attachments",
    "regulatory_package_attachment_withdrawals",
    "icaap_stage_decisions",
    "icaap_ai_suggestion_decisions",
    "package_workflow_stages",
    "package_stage_decisions",
)


def _owner_privilege(action: str, preposition: str) -> None:
    for table in _TABLES:
        op.execute(
            f"""
            DO $$
            DECLARE owner_name text;
            BEGIN
                SELECT pg_get_userbyid(relowner) INTO owner_name
                FROM pg_class WHERE oid = '{table}'::regclass;
                EXECUTE format('{action} UPDATE (id) ON {table} {preposition} %I', owner_name);
            END $$
            """
        )


def upgrade() -> None:
    _owner_privilege("GRANT", "TO")


def downgrade() -> None:
    _owner_privilege("REVOKE", "FROM")
