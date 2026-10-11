"""Seal filing content and completed calculation inputs at the database.

Revision ID: 202610100091
Revises: 202610100090
"""

from alembic import op

revision = "202610100091"
down_revision = "202610100090"
branch_labels = None
depends_on = None

CONTENT_COLUMNS = (
    "organization_id",
    "bank_id",
    "return_family",
    "return_code",
    "reporting_date",
    "frequency",
    "basis",
    "version",
    "supersedes_id",
    "snapshot",
    "source_runs",
    "snapshot_sha256",
    "is_rehearsal",
    "generated_by",
    "generated_at",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    columns = ",".join(CONTENT_COLUMNS)
    op.execute(f"""
        CREATE FUNCTION aequoros_signed_package_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE signed boolean; col text;
        BEGIN
            signed := OLD.attestation_state <> 'unsigned' OR EXISTS (
                SELECT 1 FROM attestation_signatures s
                WHERE s.package_id = OLD.id AND s.organization_id = OLD.organization_id
            );
            IF TG_OP = 'DELETE' THEN
                IF signed THEN RAISE EXCEPTION 'signed filing is read-only'; END IF;
                RETURN OLD;
            END IF;
            -- Generation is an INSERT. Financial content has no UPDATE path,
            -- including on an unsigned correction: corrections mint versions.
            FOREACH col IN ARRAY string_to_array('{columns}', ',') LOOP
                IF to_jsonb(NEW)->col IS DISTINCT FROM to_jsonb(OLD)->col THEN
                    RAISE EXCEPTION 'filing content is read-only: %', col;
                END IF;
            END LOOP;
            IF OLD.id IS DISTINCT FROM NEW.id OR
               (signed AND OLD.content_digest IS NOT NULL AND
                OLD.content_digest IS DISTINCT FROM NEW.content_digest) THEN
                RAISE EXCEPTION 'signed filing is read-only';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER regulatory_packages_content_lock BEFORE UPDATE OR DELETE
          ON regulatory_packages FOR EACH ROW EXECUTE FUNCTION aequoros_signed_package_guard();
        CREATE FUNCTION aequoros_completed_run_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status IN ('succeeded', 'failed') THEN
                RAISE EXCEPTION 'completed calculation input snapshot is read-only';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER regulatory_runs_completed_lock BEFORE UPDATE OR DELETE
          ON regulatory_runs FOR EACH ROW EXECUTE FUNCTION aequoros_completed_run_guard();
    """)
    # Revoke table UPDATE before granting only lifecycle columns. Merely
    # revoking column grants cannot override an existing table-level grant.
    op.execute(f"""
        REVOKE UPDATE, DELETE, TRUNCATE ON regulatory_packages FROM PUBLIC;
        REVOKE DELETE, TRUNCATE ON regulatory_runs FROM PUBLIC;
        DO $$ DECLARE r record; allowed text; BEGIN
            SELECT string_agg(quote_ident(column_name), ',') INTO allowed
              FROM information_schema.columns
              WHERE table_schema = current_schema() AND table_name = 'regulatory_packages'
                AND column_name <> ALL(string_to_array('{columns}', ','));
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper LOOP
                IF has_table_privilege(r.rolname, 'regulatory_packages', 'UPDATE') THEN
                    EXECUTE format('REVOKE UPDATE ON regulatory_packages FROM %I', r.rolname);
                    -- Includes id for FK key-share locks; the trigger forbids
                    -- changing it. Does not expose financial-content columns.
                    EXECUTE format('GRANT UPDATE (%s) ON regulatory_packages TO %I',
                                   allowed, r.rolname);
                END IF;
                EXECUTE format('REVOKE DELETE, TRUNCATE ON regulatory_packages, '
                               'regulatory_runs FROM %I',
                               r.rolname);
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER regulatory_packages_content_lock ON regulatory_packages")
    op.execute("DROP TRIGGER regulatory_runs_completed_lock ON regulatory_runs")
    op.execute("DROP FUNCTION aequoros_signed_package_guard()")
    op.execute("DROP FUNCTION aequoros_completed_run_guard()")
    # Older data migrations must run as the trusted migration owner. This does
    # not restore blanket permissions to application or verification roles.
    op.execute(
        "GRANT UPDATE, DELETE, TRUNCATE ON regulatory_packages, regulatory_runs TO CURRENT_USER"
    )
