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
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, RegulatoryPackage
from app.services.regulatory_reporting.exports import export_package
from app.services.regulatory_reporting.generation import snapshot_content_hash
from app.services.regulatory_reporting.registry import get_definition
from app.services.regulatory_reporting.templates import (
    TEMPLATE_REVISIONS,
    build_rendered_return,
    get_template,
    template_for_snapshot,
)
from app.services.regulatory_reporting.templates_legacy import LEGACY_TEMPLATES
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.storage.inmemory import InMemoryStorageClient

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID, authorization_version=1
)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "pre_p0_packages"
CODES = ("CAR-RWA", "IRRBB-PILOT", "ICAAP-STRESS-APPENDIX2", "SDI-STRESS-ANNUAL", "STRESS-PACK")


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.services.regulatory_reporting.exports.get_storage_client", lambda: client
    )
    return client


def _fixture(code: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{code}.json").read_text(encoding="utf-8"))


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
    for obj in storage.list(slug, "outputs"):
        if obj.location.object_path == object_path:
            return storage.read(obj.location)[1].read()
    raise AssertionError(object_path)


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


@pytest.mark.parametrize("code", CODES)
def test_a_pre_p0_snapshot_selects_the_frozen_text(code: str) -> None:
    record = _fixture(code)
    definition = get_definition(code)
    assert definition is not None
    current = get_template(definition.template_id)
    assert current is not None
    assert definition.template_id in TEMPLATE_REVISIONS
    frozen = template_for_snapshot(current, record["snapshot"])
    assert frozen is LEGACY_TEMPLATES[definition.template_id]
    assert frozen is not current
    rendered = build_rendered_return(
        current,
        record["snapshot"],
        record["source_runs"],
        package_id=record["package_id"],
        package_version=record["version"],
    )
    assert rendered.template is frozen
    assert rendered.report_notes == ()


def test_a_new_snapshot_selects_the_current_text() -> None:
    record = _fixture("ICAAP-STRESS-APPENDIX2")
    snapshot = dict(record["snapshot"])
    metadata = dict(snapshot["metadata"])
    current = get_template("bog-icaap-stress-appendix2-v1")
    assert current is not None
    # Stamped but without the governed-minimum provenance its neutral headers
    # rely on: still the frozen text (it would otherwise state no minimum).
    metadata["template_revision"] = TEMPLATE_REVISIONS["bog-icaap-stress-appendix2-v1"]
    snapshot["metadata"] = metadata
    assert template_for_snapshot(current, snapshot) is not current
    metadata["parameter_provenance"] = []
    assert template_for_snapshot(current, snapshot) is current
    # A template P0 never revised is always its own text.
    lmt = get_template("bog-lmt-liquidity-v1")
    assert lmt is not None
    assert template_for_snapshot(lmt, {"metadata": {}}) is lmt
