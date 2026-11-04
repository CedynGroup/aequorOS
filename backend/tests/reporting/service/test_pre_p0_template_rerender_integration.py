"""Re-exporting a pre-P0 package gives the same bytes as before (decision D-021).

ICAAP P0 changed the wording of six templates. A package generated BEFORE that
must re-export exactly as it did, signed or not — the rule "existing sealed
packages stay untouched" covers the artifact, not only the snapshot.

The fixtures under ``tests/fixtures/pre_p0_packages/`` were produced by the
BASELINE code (``ca294f04``, the commit before P0) over the deterministic
canonical book: each holds a real generated package (snapshot, source runs,
identity) and the SHA-256 of the PDF, XLSX, CSV (and, where the return has one,
the working XLSX) the baseline renderer produced for it. This test stores each
package in the database as it would exist today and re-exports it through the
production export path; every artifact must hash identically.

Regenerating a fixture is only ever done from a checkout of the baseline
commit, never from the current code — the hashes are the historical record.

This module's other-suite cases live in ``test_pre_p0_template_rerender.py``.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, RegulatoryPackage
from app.reporting.service.exports import export_package
from app.reporting.service.generation import snapshot_content_hash
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.reporting.service.test_pre_p0_template_rerender import CODES, _fixture
from tests.support.inmemory_storage import InMemoryStorageClient
from tests.support.sealed_documents import published_bytes

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    monkeypatch.setattr("app.reporting.service.exports.get_storage_client", lambda: client)
    return client


def _store(db: Session, record: dict[str, Any]) -> RegulatoryPackage:
    """The pre-P0 package as it sits in the database today."""
    snapshot = record["snapshot"]
    package = RegulatoryPackage(
        id=UUID(record["package_id"]),
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        return_family=record["return_family"],
        return_code=record["return_code"],
        reporting_date=date.fromisoformat(record["reporting_date"]),
        frequency=record["frequency"],
        basis=record["basis"],
        status="generated",
        version=record["version"],
        snapshot=snapshot,
        source_runs=record["source_runs"],
        generated_by=DEMO_USER_ID,
        generated_at=datetime.fromisoformat(record["generated_at"]),
        snapshot_sha256=snapshot_content_hash(snapshot),
    )
    db.add(package)
    db.commit()
    return package


def _stored_bytes(db: Session, storage: InMemoryStorageClient, object_path: str) -> bytes:
    slug = db.scalar(select(Bank.storage_slug).where(Bank.id == SAMPLE_BANK_ID))
    assert slug
    return published_bytes(db, storage, slug=slug, context_path=object_path)


@pytest.mark.parametrize("code", CODES)
def test_a_pre_p0_package_re_exports_byte_identically(
    db_session: Session, storage: InMemoryStorageClient, code: str
) -> None:
    materialize_canonical_test_book(db_session)
    record = _fixture(code)
    package = _store(db_session, record)
    expected: dict[str, str] = record["baseline_sha256"]
    kinds = {"pdf": "pdf", "xlsx": "xlsx", "csv": "csv", "xlsx_working": "xlsx_working"}
    checked = 0
    for key, digest in expected.items():
        kind = kinds.get(key, "csv")  # the CSV key is its extension (csv / zip)
        artifact = export_package(db_session, MAKER, package, kind)  # type: ignore[arg-type]
        payload = _stored_bytes(db_session, storage, artifact.object_path)
        assert hashlib.sha256(payload).hexdigest() == digest, (code, key)
        checked += 1
    assert checked >= 3, code
