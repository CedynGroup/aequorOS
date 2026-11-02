"""Add immutable sealed-object identities without editing recorded references.

Revision ID: 202611010105
Revises: 202610240103
"""

from typing import cast

import sqlalchemy as sa

from alembic import op

revision = "202611010105"
down_revision = "202610240103"
branch_labels = None
depends_on = None

BINDINGS = (
    ("artifact_document_bindings", "regulatory_artifact_versions", "artifact_binding"),
    (
        "package_attachment_document_bindings",
        "regulatory_package_attachments",
        "package_attachment_binding",
    ),
    ("icaap_attachment_document_bindings", "icaap_attachments", "icaap_attachment_binding"),
    (
        "supervisory_letter_document_bindings",
        "bank_supervisory_addons",
        "supervisory_letter_binding",
    ),
)


def upgrade() -> None:
    op.create_table(
        "sealed_document_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("bank_id", sa.String(16), nullable=False),
        sa.Column("storage_slug", sa.String(255), nullable=False),
        sa.Column("storage_tier", sa.String(16), nullable=False),
        sa.Column("physical_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("context_format", sa.String(32), nullable=False),
        sa.Column("context_path", sa.String(512), nullable=False),
        sa.Column("envelope_id", sa.Uuid()),
        sa.Column("original_version_id", sa.String(255)),
        sa.Column("published_by", sa.Uuid(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["envelope_id", "bank_id", "organization_id"],
            [
                "object_key_envelopes.id",
                "object_key_envelopes.bank_id",
                "object_key_envelopes.organization_id",
            ],
        ),
        sa.UniqueConstraint("id", "organization_id", "bank_id", name="uq_sealed_object_scope"),
        sa.UniqueConstraint(
            "storage_slug", "storage_tier", "physical_key", name="uq_sealed_object_location"
        ),
        sa.CheckConstraint("storage_tier = 'outputs'", name="ck_sealed_object_tier"),
        sa.CheckConstraint("byte_length >= 0", name="ck_sealed_object_length"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_sealed_object_sha"),
        sa.CheckConstraint("physical_key = 'sealed/v1/' || id::text", name="ck_sealed_object_key"),
        sa.CheckConstraint(
            "length(context_path) > 0 AND length(storage_slug) > 0", name="ck_sealed_object_context"
        ),
        sa.CheckConstraint(
            "(context_format = 'aws-esdk-object-v1' AND envelope_id IS NOT NULL) OR "
            "(context_format = 'plaintext-v1' AND envelope_id IS NULL)",
            name="ck_sealed_object_format",
        ),
    )
    op.create_index(
        "ix_sealed_objects_org_bank", "sealed_document_objects", ["organization_id", "bank_id"]
    )
    # PostgreSQL's FK checks need a key-share lock as the referenced table's
    # owner. Older append-only migrations revoked even the owner's UPDATE(id).
    op.execute("""
        DO $$ DECLARE owner_name text; BEGIN
            SELECT pg_get_userbyid(relowner) INTO owner_name FROM pg_class
                WHERE oid = 'regulatory_artifact_versions'::regclass;
            EXECUTE format('GRANT UPDATE (id) ON regulatory_artifact_versions TO %I', owner_name);
        END $$
    """)
    for table, source, prefix in BINDINGS:
        op.create_table(
            table,
            sa.Column("source_id", sa.Uuid(), primary_key=True),
            sa.Column("organization_id", sa.String(16), nullable=False),
            sa.Column("bank_id", sa.String(16), nullable=False),
            sa.Column("object_id", sa.Uuid(), nullable=False),
            sa.Column("bound_by", sa.Uuid(), nullable=False),
            sa.Column(
                "bound_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["source_id", "organization_id"], [f"{source}.id", f"{source}.organization_id"]
            ),
            sa.ForeignKeyConstraint(
                ["object_id", "organization_id", "bank_id"],
                [
                    "sealed_document_objects.id",
                    "sealed_document_objects.organization_id",
                    "sealed_document_objects.bank_id",
                ],
            ),
        )
        op.create_index(f"ix_{prefix}_org_bank", table, ["organization_id", "bank_id"])
        op.create_index(f"ix_{prefix}_object", table, ["object_id", "organization_id", "bank_id"])

    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote_identifier
    schema = quote(cast(str, connection.exec_driver_sql("SELECT current_schema()").scalar_one()))
    # Invoker functions retain FORCE RLS; every lookup also has explicit bank scope.
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_sealed_object_validate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, {schema}, pg_temp AS $fn$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM {schema}.banks b WHERE b.id = NEW.bank_id
                AND b.organization_id = NEW.organization_id AND b.storage_slug = NEW.storage_slug)
            THEN RAISE EXCEPTION 'sealed object bank storage context mismatch'; END IF;
            RETURN NEW;
        END $fn$
    """)
    op.execute(
        "CREATE TRIGGER sealed_object_validate BEFORE INSERT ON sealed_document_objects "
        "FOR EACH ROW EXECUTE FUNCTION aqs_sealed_object_validate()"
    )
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_document_binding_validate() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, {schema}, pg_temp AS $fn$
        DECLARE document {schema}.sealed_document_objects; source record;
        BEGIN
            SELECT * INTO document FROM {schema}.sealed_document_objects
                WHERE id = NEW.object_id AND organization_id = NEW.organization_id
                AND bank_id = NEW.bank_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'sealed document object not found'; END IF;
            IF TG_TABLE_NAME = 'artifact_document_bindings' THEN
                SELECT p.bank_id, v.checksum_sha256 AS sha256, v.size_bytes AS byte_length,
                    v.object_path AS context_path, 'outputs' AS tier,
                    v.storage_version_id AS version
                INTO source FROM {schema}.regulatory_artifact_versions v
                JOIN {schema}.regulatory_packages p ON p.id = v.package_id
                    AND p.organization_id = v.organization_id
                WHERE v.id = NEW.source_id AND v.organization_id = NEW.organization_id
                    AND p.bank_id = NEW.bank_id;
            ELSIF TG_TABLE_NAME = 'package_attachment_document_bindings' THEN
                SELECT bank_id, sha256, byte_size AS byte_length, object_path AS context_path,
                    storage_tier AS tier, storage_version_id AS version INTO source
                FROM {schema}.regulatory_package_attachments WHERE id = NEW.source_id
                    AND organization_id = NEW.organization_id AND bank_id = NEW.bank_id;
            ELSIF TG_TABLE_NAME = 'icaap_attachment_document_bindings' THEN
                SELECT bank_id, sha256, byte_size AS byte_length, object_path AS context_path,
                    storage_tier AS tier, storage_version_id AS version INTO source
                FROM {schema}.icaap_attachments WHERE id = NEW.source_id
                    AND organization_id = NEW.organization_id AND bank_id = NEW.bank_id;
            ELSIF TG_TABLE_NAME = 'supervisory_letter_document_bindings' THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended('sealed-letter:' || NEW.source_id::text, 0));
                SELECT bank_id, letter_sha256 AS sha256, letter_byte_size AS byte_length,
                    letter_object_path AS context_path, letter_storage_tier AS tier,
                    letter_storage_version_id AS version INTO source
                FROM {schema}.bank_supervisory_addons WHERE id = NEW.source_id
                    AND organization_id = NEW.organization_id AND bank_id = NEW.bank_id;
            ELSE RAISE EXCEPTION 'unknown document binding family'; END IF;
            IF NOT FOUND OR source.bank_id IS DISTINCT FROM document.bank_id
                OR source.sha256 IS DISTINCT FROM document.sha256
                OR source.byte_length IS DISTINCT FROM document.byte_length
                OR source.context_path IS DISTINCT FROM document.context_path
                OR source.tier IS DISTINCT FROM document.storage_tier
                OR source.version IS DISTINCT FROM document.original_version_id
            THEN RAISE EXCEPTION 'sealed document binding does not match recorded evidence'; END IF;
            RETURN NEW;
        END $fn$
    """)
    # Draft supervisory rows are editable. Once their letter is bound, its
    # recorded evidence must stay fixed as well. Serialize with binding inserts.
    # The worker retains existing source-row updates without catalogue SELECT;
    # this narrow definer checks the binding under tenant FORCE RLS on its behalf.
    op.execute(f"""
        CREATE FUNCTION {schema}.aqs_bound_letter_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET row_security = on
        SET search_path = pg_catalog, {schema}, pg_temp AS $fn$
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended('sealed-letter:' || OLD.id::text, 0));
            IF ROW(NEW.id, NEW.organization_id, NEW.bank_id, NEW.letter_object_path,
                   NEW.letter_storage_tier, NEW.letter_sha256, NEW.letter_byte_size)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.organization_id, OLD.bank_id, OLD.letter_object_path,
                   OLD.letter_storage_tier, OLD.letter_sha256, OLD.letter_byte_size)
               AND EXISTS (SELECT 1 FROM {schema}.supervisory_letter_document_bindings
                   WHERE source_id = OLD.id AND organization_id = OLD.organization_id
                       AND bank_id = OLD.bank_id)
            THEN RAISE EXCEPTION 'bound supervisory letter is immutable'; END IF;
            RETURN NEW;
        END $fn$
    """)
    op.execute(
        "CREATE TRIGGER bound_letter_guard BEFORE UPDATE ON bank_supervisory_addons "
        "FOR EACH ROW EXECUTE FUNCTION aqs_bound_letter_guard()"
    )
    for table, _, _ in BINDINGS:
        op.execute(
            f"CREATE TRIGGER document_binding_validate BEFORE INSERT ON {table} FOR EACH "
            f"ROW EXECUTE FUNCTION aqs_document_binding_validate()"
        )
    for table in ("sealed_document_objects", *(item[0] for item in BINDINGS)):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant ON {table} USING (organization_id = "
            f"NULLIF(current_setting('app.organization_id', true), '')) WITH CHECK "
            f"(organization_id = NULLIF(current_setting('app.organization_id', true), ''))"
        )
        op.execute(
            f"CREATE TRIGGER sealed_document_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION aequoros_append_only_guard()"
        )
        op.execute(
            f"CREATE TRIGGER sealed_document_no_truncate BEFORE TRUNCATE ON {table} FOR "
            f"EACH STATEMENT EXECUTE FUNCTION aequoros_append_only_guard()"
        )
        op.execute(
            f"REVOKE ALL ON {table} FROM PUBLIC, aqs_api, aqs_worker, aqs_bi, aqs_operator, "
            f"aqs_audit_verifier"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO aqs_api")
    for function in (
        "aqs_sealed_object_validate",
        "aqs_document_binding_validate",
        "aqs_bound_letter_guard",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function}() FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function}() TO aqs_api")


def downgrade() -> None:
    op.execute("DROP TRIGGER bound_letter_guard ON bank_supervisory_addons")
    op.execute("DROP FUNCTION aqs_bound_letter_guard()")
    for table, _, _ in reversed(BINDINGS):
        op.drop_table(table)
    op.drop_table("sealed_document_objects")
    op.execute("DROP FUNCTION aqs_document_binding_validate()")
    op.execute("DROP FUNCTION aqs_sealed_object_validate()")
    op.execute("""
        DO $$ DECLARE owner_name text; BEGIN
            SELECT pg_get_userbyid(relowner) INTO owner_name FROM pg_class
                WHERE oid = 'regulatory_artifact_versions'::regclass;
            EXECUTE format('REVOKE UPDATE (id) ON regulatory_artifact_versions FROM %I',
                owner_name);
        END $$
    """)
