"""Every retained filing child rejects both alteration and deletion."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError

from app.db.base import Base, utc_now
from app.db.session import force_rls_suspended
from tests.db import test_governance_append_only as governance_fixtures
from tests.db.test_signed_filing_locks import insert_package
from tests.support.helpers import ORG_1

governance_schema = governance_fixtures.governance_schema
tenant_connection = governance_fixtures.connection

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
TABLES = (
    "attestation_signatures",
    "regulatory_artifact_versions",
    "regulatory_package_approvals",
    "regulatory_submission_events",
    "package_stage_decisions",
    "package_workflow_stages",
    "regulatory_package_attachments",
    "regulatory_package_attachment_withdrawals",
)


@pytest.fixture
def connection(tenant_connection: Connection) -> Iterator[Connection]:
    with force_rls_suspended(tenant_connection, *TABLES, "regulatory_packages"):
        tenant_connection.execute(text("GRANT DELETE ON regulatory_packages TO CURRENT_USER"))
        for table in TABLES:
            tenant_connection.execute(
                text(f"GRANT UPDATE, DELETE, TRUNCATE ON {table} TO CURRENT_USER")
            )
        yield tenant_connection


def _attachment(connection: Connection, package: UUID) -> UUID:
    identifier = uuid4()
    connection.execute(
        insert(Base.metadata.tables["regulatory_package_attachments"]).values(
            id=identifier,
            organization_id=ORG_1,
            bank_id="BK-SEAL001",
            package_id=package,
            package_version=1,
            kind="board_resolution",
            title="Synthetic resolution",
            original_filename="synthetic.pdf",
            media_type="application/pdf",
            byte_size=7,
            sha256="a" * 64,
            storage_tier="outputs",
            object_path="synthetic.pdf",
            source="package_upload",
            gate="optional",
            attached_by=uuid4(),
        )
    )
    return identifier


@pytest.mark.parametrize("table", TABLES)
def test_filing_children_are_immutable(connection: Connection, table: str) -> None:
    package, identifier = insert_package(connection), uuid4()
    common: dict[str, object] = {"id": identifier, "organization_id": ORG_1, "package_id": package}
    extras: dict[str, dict[str, object]] = {
        "regulatory_artifact_versions": {
            "kind": "pdf",
            "object_path": "synthetic.pdf",
            "checksum_sha256": "a" * 64,
            "size_bytes": 7,
        },
        "regulatory_package_approvals": {"action": "approved", "actor_user_id": uuid4()},
        "regulatory_submission_events": {"channel": "manual", "event": "submitted"},
        "package_workflow_stages": {
            "bank_id": "BK-SEAL001",
            "seq": 1,
            "stage_key": "preparation",
            "title": "Preparer",
            "decision_kind": "prepare",
            "source": "platform_default",
        },
        "package_stage_decisions": {
            "bank_id": "BK-SEAL001",
            "stage_seq": 1,
            "stage_key": "preparation",
            "round": 1,
            "decision": "submitted",
            "review_digest": "a" * 64,
            "decided_by": uuid4(),
            "decided_by_name": "Synthetic Officer",
        },
        "attestation_signatures": {
            "bank_id": "BK-SEAL001",
            "package_version": 1,
            "signing_role": "preparer",
            "signer_id": "synthetic-signer",
            "signer_user_id": uuid4(),
            "binding_class": "master_data",
            "certification_digest": "a" * 64,
            "content_digest": "a" * 64,
            "statement": "Synthetic certification",
            "attestation_payload": {},
            "payload_digest": "a" * 64,
            "signature_method": "detached_rsa_pss_sha256",
            "signature_value": b"synthetic",
            "certificate_pem": "synthetic",
            "certificate_sha256": "a" * 64,
            "declared_at": utc_now(),
            "prev_hash": "0" * 64,
            "entry_hash": "a" * 64,
        },
    }
    if table == "regulatory_package_attachments":
        identifier = _attachment(connection, package)
    else:
        if table == "regulatory_package_attachment_withdrawals":
            extras[table] = {
                "bank_id": "BK-SEAL001",
                "attachment_id": _attachment(connection, package),
                "reason": "Synthetic withdrawal",
                "withdrawn_by": uuid4(),
            }
        connection.execute(insert(Base.metadata.tables[table]).values(**common, **extras[table]))
    for operation in (
        f"UPDATE {table} SET id = id WHERE id = :id",
        f"DELETE FROM {table} WHERE id = :id",
        f"TRUNCATE {table} CASCADE",
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(DatabaseError, match="append-only"):
            connection.execute(text(operation), {"id": identifier})
        savepoint.rollback()
    if table == "attestation_signatures":
        # Persisted signature evidence, rather than a mutable projection,
        # keeps the deletion lock alive after certification is withdrawn.
        savepoint = connection.begin_nested()
        with pytest.raises(DatabaseError, match="read-only"):
            connection.execute(
                text("DELETE FROM regulatory_packages WHERE id = :id"), {"id": package}
            )
        savepoint.rollback()
